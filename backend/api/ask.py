"""Quick questions, answered by several models side by side, with no repo.

Two kinds of contender:

* **Harness CLIs** (`claude`, `codex`) run headlessly on the host and reuse
  whatever one-time login the CLI already holds -- a Claude or ChatGPT
  subscription works, no API key needed. Subscription credentials are only
  meant for the official CLIs, so this module never reads them itself; it
  just runs the CLI. Each run happens in a fresh empty temporary directory
  with the CLI's tools disabled (claude) or its sandbox read-only (codex),
  so a question can't touch anything on the host.
* **Model APIs** (`openrouter`, `ollama`) are called directly over their
  OpenAI-compatible chat-completions endpoints.

Asks are ephemeral by design: they live in an in-memory registry, capped to
the most recent few, and vanish on restart. `POST /asks` returns at once and
the UI polls `GET /asks/{id}`, the same shape `clone.py` uses -- a CLI answer
takes seconds, longer than a request should stay open.

Every function that talks to a contender is total: a failure (not logged
in, unknown model, HTTP 402, a timeout) becomes that contender's `error`, so
one bad contender never takes the others down with it.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import httpx

from backend.api.ollama import list_ollama_models
from backend.logging import log
from backend.model_catalog import catalog
from backend.orchestrator.config import KNOWN_MODELS
from backend.orchestrator.process import CommandError, CommandTimeout, RunOptions, run

if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.api.models import AskRequest

AskProvider = Literal["claude", "codex", "openrouter", "ollama"]
AnswerStatus = Literal["pending", "done", "error"]

# A CLI answer is normally a few seconds; past this the CLI is stuck (or
# waiting on a login prompt that stdin=/dev/null turned into a hang).
CLI_TIMEOUT_SECONDS = 180.0
HTTP_TIMEOUT_SECONDS = 180.0

# The CLIs have no output cap flag; the APIs get this one so a runaway
# answer can't run up a bill.
API_MAX_TOKENS = 2048

# Enough to scroll back through a session's worth of questions, small enough
# that memory use stays trivial.
MAX_TRACKED_ASKS = 50

# Error text kept from a failed CLI or HTTP response: enough to read the
# reason, never a whole log.
_ERROR_CHARS = 600

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_KEY_ENVS = ("OPENROUTER_API_KEY", "OPEN_ROUTER_KEY")
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"

CLAUDE_SUGGESTED_MODELS = (
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
    "claude-fable-5-1",
)
OPENROUTER_SUGGESTED_MODELS = ("nvidia/nemotron-3.5-lightning:free",)

PROVIDER_LABELS: dict[str, str] = {
    "claude": "Claude Code",
    "codex": "Codex",
    "openrouter": "OpenRouter",
    "ollama": "Ollama (local)",
}

# Runs one question through every contender at once; contenders beyond this
# queue. Module-level so tests can swap in a synchronous executor.
_executor: Executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="ask")


def _default_http_client() -> httpx.Client:
    return httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)


# Swapped in tests for a client on an `httpx.MockTransport`.
http_client_factory: Callable[[], httpx.Client] = _default_http_client


@dataclass
class Answer:
    """One contender's answer, or why there isn't one.

    Token counts are None when the contender didn't report them, which the
    UI must show as "no data", never as 0 -- the same rule as
    `orchestrator.metrics.Usage`.
    """

    provider: str
    model: str
    status: AnswerStatus = "pending"
    text: str = ""
    error: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

    def as_dict(self) -> dict[str, object]:
        """Renders as JSON for the API."""
        return {
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "text": self.text,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass
class Ask:
    """One question and every contender's answer to it."""

    ask_id: str
    question: str
    created_at: float
    answers: list[Answer]

    def as_dict(self) -> dict[str, object]:
        """Renders as JSON for the API."""
        return {
            "ask_id": self.ask_id,
            "question": self.question,
            "created_at": self.created_at,
            "answers": [a.as_dict() for a in self.answers],
        }


@dataclass
class _AskRegistry:
    """In-memory record of recent asks; the oldest drop off past the cap."""

    asks: OrderedDict[str, Ask] = field(default_factory=OrderedDict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def put(self, ask: Ask) -> None:
        with self.lock:
            self.asks[ask.ask_id] = ask
            while len(self.asks) > MAX_TRACKED_ASKS:
                self.asks.popitem(last=False)

    def get(self, ask_id: str) -> dict[str, object] | None:
        # Rendered under the lock so a worker finishing mid-read can't hand
        # back a half-updated answer.
        with self.lock:
            ask = self.asks.get(ask_id)
            return ask.as_dict() if ask is not None else None

    def update(self, answer: Answer, result: Answer) -> None:
        with self.lock:
            answer.status = result.status
            answer.text = result.text
            answer.error = result.error
            answer.latency_ms = result.latency_ms
            answer.input_tokens = result.input_tokens
            answer.output_tokens = result.output_tokens


_registry = _AskRegistry()


def get_ask(ask_id: str) -> dict[str, object] | None:
    """Returns a tracked ask as JSON, or None if unknown (or aged out)."""
    return _registry.get(ask_id)


# -- Provider discovery ------------------------------------------------------


def find_cli(name: str) -> str | None:
    """Locates a harness CLI, including the user-local install dir.

    The official claude installer puts the binary in `~/.local/bin`, which a
    server started from an IDE or a service manager often lacks on PATH.

    Args:
        name: The executable name ("claude" or "codex").

    Returns:
        An absolute path, or None if it isn't installed.
    """
    found = shutil.which(name)
    if found:
        return found
    local = Path.home() / ".local" / "bin" / name
    return str(local) if local.is_file() and os.access(local, os.X_OK) else None


def openrouter_key() -> str | None:
    """Returns the OpenRouter key from the environment, if any is set."""
    for env in OPENROUTER_KEY_ENVS:
        value = os.environ.get(env)
        if value:
            return value
    return None


def ollama_base_url() -> str:
    """Returns Ollama's OpenAI-compatible base URL.

    Honors OLLAMA_HOST like the ollama CLI does. The backend runs on the
    host, so the loopback default reaches it directly (sandboxes need
    host.docker.internal; this does not).
    """
    host = os.environ.get("OLLAMA_HOST", "").strip() or DEFAULT_OLLAMA_HOST
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    return f"{host.rstrip('/')}/v1"


def list_providers() -> list[dict[str, object]]:
    """Describes each provider and whether this host can use it right now.

    Returns:
        One entry per provider: `configured`, a `hint` saying how to fix it
        when it isn't, and model suggestions for the UI's datalist.
    """
    claude_ok = find_cli("claude") is not None
    codex_ok = find_cli("codex") is not None
    ollama = list_ollama_models()
    ollama_models = ollama.get("models")
    return [
        {
            "provider": "claude",
            "label": PROVIDER_LABELS["claude"],
            "configured": claude_ok,
            "hint": "Uses your Claude subscription login (run `claude` once and log in)."
            if claude_ok
            else "Install Claude Code and log in once with `claude`.",
            "suggested_models": catalog(
                "claude", sorted(set(CLAUDE_SUGGESTED_MODELS) | set(KNOWN_MODELS.get("claude", ())))
            )["models"],
            "model_source": catalog("claude")["source"],
            "models_updated_at": catalog("claude")["updated_at"],
        },
        {
            "provider": "codex",
            "label": PROVIDER_LABELS["codex"],
            "configured": codex_ok,
            "hint": "Uses your ChatGPT login (run `codex login` once)."
            if codex_ok
            else "Install the codex CLI and run `codex login` once.",
            "suggested_models": catalog("codex", sorted(KNOWN_MODELS.get("codex", ())))["models"]
            if codex_ok
            else [],
            "model_source": catalog("codex")["source"],
            "models_updated_at": catalog("codex")["updated_at"],
        },
        {
            "provider": "openrouter",
            "label": PROVIDER_LABELS["openrouter"],
            "configured": openrouter_key() is not None,
            "hint": "Set OPENROUTER_API_KEY (or OPEN_ROUTER_KEY) for the backend.",
            "suggested_models": list(OPENROUTER_SUGGESTED_MODELS),
        },
        {
            "provider": "ollama",
            "label": PROVIDER_LABELS["ollama"],
            "configured": bool(ollama.get("available")),
            "hint": "Start Ollama on this machine and pull a model.",
            "suggested_models": list(ollama_models) if isinstance(ollama_models, list) else [],
            "model_source": catalog("ollama")["source"],
            "models_updated_at": catalog("ollama")["updated_at"],
        },
    ]


# -- Harness CLIs --------------------------------------------------------------


def build_claude_ask_cmd(binary: str, model: str, question: str) -> list[str]:
    """Builds a one-shot, tool-less `claude --print` invocation.

    `--tools ""` disables every built-in tool so the model can only answer;
    `--strict-mcp-config` skips the user's MCP servers; nothing is saved as
    a resumable session. `--` keeps a question starting with "-" from being
    read as a flag (verified against the installed CLI).
    """
    return [
        binary,
        "--print",
        "--model",
        model,
        "--output-format",
        "json",
        "--tools",
        "",
        "--no-session-persistence",
        "--strict-mcp-config",
        "--",
        question,
    ]


def build_codex_ask_cmd(binary: str, model: str, question: str) -> list[str]:
    """Builds a one-shot, read-only `codex exec` invocation.

    `--skip-git-repo-check` because it runs in an empty temp directory,
    `--sandbox read-only` so it can't write anywhere, `--ephemeral` so no
    session is persisted. Flags verified against the installed CLI.
    """
    return [
        binary,
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--model",
        model,
        "--",
        question,
    ]


def _last_json_object(stdout: str) -> dict | None:
    """Returns the last line of `stdout` that parses as a JSON object."""
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _dict_at(obj: dict, key: str) -> dict:
    """Returns `obj[key]` when it is a dict, else an empty one."""
    value = obj.get(key)
    return value if isinstance(value, dict) else {}


def _str_at(obj: dict, key: str) -> str:
    """Returns `obj[key]` when it is a string, else ""."""
    value = obj.get(key)
    return value if isinstance(value, str) else ""


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _truncate(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _ERROR_CHARS else text[:_ERROR_CHARS] + "…"


def parse_claude_output(stdout: str, stderr: str, returncode: int) -> Answer:
    """Parses `claude --print --output-format json` into an Answer.

    Input tokens include cache reads and writes: the harness's own system
    prompt is mostly cached, and leaving those out would understate what a
    question actually cost.

    Returns:
        An Answer with status "done" or "error"; provider/model unset.
    """
    result = _last_json_object(stdout)
    if result is None:
        detail = stderr.strip() or stdout.strip() or f"claude exited with code {returncode}"
        return Answer(provider="", model="", status="error", error=_truncate(detail))
    usage = _dict_at(result, "usage")
    parts = [
        _int_or_none(usage.get(key))
        for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    ]
    known = [p for p in parts if p is not None]
    text = _str_at(result, "result")
    answer = Answer(
        provider="",
        model="",
        input_tokens=sum(known) if known else None,
        output_tokens=_int_or_none(usage.get("output_tokens")),
    )
    if result.get("is_error") or returncode != 0:
        answer.status = "error"
        answer.error = _truncate(text or stderr or f"claude exited with code {returncode}")
    else:
        answer.status = "done"
        answer.text = text
    return answer


def _codex_error_message(raw: object) -> str:
    """Unwraps codex's error text, which is often a JSON API error in a string."""
    if not isinstance(raw, str):
        return "codex reported an error"
    try:
        nested = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(nested, dict):
        inner = nested.get("error")
        if isinstance(inner, dict) and isinstance(inner.get("message"), str):
            return inner["message"]
    return raw


def parse_codex_output(stdout: str, stderr: str, returncode: int) -> Answer:
    """Parses `codex exec --json` JSONL into an Answer.

    The answer is every `agent_message` item, in order. `item.completed`
    items of type "error" are warnings (e.g. missing model metadata) and are
    ignored; a `turn.failed` or top-level `error` event is the real failure.

    Returns:
        An Answer with status "done" or "error"; provider/model unset.
    """
    messages: list[str] = []
    failure: str | None = None
    answer = Answer(provider="", model="")
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        item = event.get("item")
        if kind == "item.completed" and isinstance(item, dict):
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                messages.append(item["text"])
        elif kind == "turn.completed" and isinstance(event.get("usage"), dict):
            answer.input_tokens = _int_or_none(event["usage"].get("input_tokens"))
            answer.output_tokens = _int_or_none(event["usage"].get("output_tokens"))
        elif kind == "turn.failed" and isinstance(event.get("error"), dict):
            failure = _codex_error_message(event["error"].get("message"))
        elif kind == "error":
            failure = failure or _codex_error_message(event.get("message"))
    if failure is not None or (not messages and returncode != 0):
        answer.status = "error"
        answer.error = _truncate(
            failure or stderr or stdout or f"codex exited with code {returncode}"
        )
    else:
        answer.status = "done"
        answer.text = "\n\n".join(messages)
    return answer


def _ask_cli(provider: str, model: str, question: str) -> Answer:
    """Runs one harness CLI in an empty temp dir and parses its answer."""
    binary = find_cli(provider)
    if binary is None:
        return Answer(provider, model, status="error", error=f"{provider} CLI not found")
    if provider == "claude":
        cmd = build_claude_ask_cmd(binary, model, question)
        parse = parse_claude_output
    else:
        cmd = build_codex_ask_cmd(binary, model, question)
        parse = parse_codex_output
    with tempfile.TemporaryDirectory(prefix="agentshowdown-ask-") as scratch:
        try:
            result = run(
                cmd,
                options=RunOptions(
                    check=False,
                    timeout=CLI_TIMEOUT_SECONDS,
                    cwd=scratch,
                    # The question is user text; keep it out of the log line.
                    redact=(question,),
                ),
            )
        except CommandTimeout:
            return Answer(
                provider,
                model,
                status="error",
                error=f"{provider} gave no answer within {CLI_TIMEOUT_SECONDS:.0f}s",
            )
        except (CommandError, OSError) as exc:
            return Answer(provider, model, status="error", error=_truncate(str(exc)))
    return parse(result.stdout or "", result.stderr or "", result.returncode)


# -- Model APIs ----------------------------------------------------------------


def _api_error(response: httpx.Response) -> str:
    """Pulls a readable reason out of an OpenAI-style error response."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            return f"HTTP {response.status_code}: {err['message']}"
        if isinstance(err, str):
            return f"HTTP {response.status_code}: {err}"
    return f"HTTP {response.status_code}: {_truncate(response.text) or response.reason_phrase}"


def parse_chat_completion(body: object) -> Answer:
    """Parses an OpenAI-compatible chat-completions body into an Answer."""
    answer = Answer(provider="", model="")
    if not isinstance(body, dict):
        answer.status = "error"
        answer.error = "response was not a JSON object"
        return answer
    choices = body.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    usage = _dict_at(body, "usage")
    answer.input_tokens = _int_or_none(usage.get("prompt_tokens"))
    answer.output_tokens = _int_or_none(usage.get("completion_tokens"))
    if not isinstance(content, str):
        answer.status = "error"
        answer.error = "response carried no answer text"
        return answer
    answer.status = "done"
    answer.text = content
    return answer


def _ask_api(provider: str, model: str, question: str) -> Answer:
    """Calls one OpenAI-compatible chat-completions endpoint."""
    if provider == "openrouter":
        base_url = OPENROUTER_BASE_URL
        key = openrouter_key()
        if key is None:
            return Answer(provider, model, status="error", error="no OpenRouter key set")
        headers = {"Authorization": f"Bearer {key}"}
    else:
        base_url = ollama_base_url()
        headers = {}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": question}],
        "max_tokens": API_MAX_TOKENS,
    }
    try:
        with http_client_factory() as client:
            response = client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
    except httpx.TimeoutException:
        return Answer(
            provider,
            model,
            status="error",
            error=f"no answer within {HTTP_TIMEOUT_SECONDS:.0f}s",
        )
    except httpx.HTTPError as exc:
        return Answer(provider, model, status="error", error=_truncate(f"request failed: {exc}"))
    if response.status_code >= httpx.codes.BAD_REQUEST:
        log.warning(
            "ask_api_http_error",
            provider=provider,
            model=model,
            status_code=response.status_code,
        )
        return Answer(provider, model, status="error", error=_truncate(_api_error(response)))
    try:
        body = response.json()
    except ValueError:
        return Answer(provider, model, status="error", error="response was not JSON")
    return parse_chat_completion(body)


# -- Orchestration -------------------------------------------------------------


def answer_one(provider: str, model: str, question: str) -> Answer:
    """Gets one contender's answer, timed, never raising.

    Returns:
        The Answer, with provider, model and latency filled in.
    """
    started = time.monotonic()
    try:
        if provider in ("claude", "codex"):
            result = _ask_cli(provider, model, question)
        else:
            result = _ask_api(provider, model, question)
    except Exception as exc:  # a worker must always record an outcome
        log.error("ask_contender_crashed", provider=provider, model=model, error=repr(exc))
        result = Answer(provider, model, status="error", error="internal error -- see the logs")
    result.provider = provider
    result.model = model
    result.latency_ms = int((time.monotonic() - started) * 1000)
    if result.status == "error":
        log.warning(
            "ask_contender_failed",
            provider=provider,
            model=model,
            latency_ms=result.latency_ms,
        )
    return result


def _work(answer: Answer, question: str) -> None:
    _registry.update(answer, answer_one(answer.provider, answer.model, question))


def unconfigured(request: AskRequest) -> list[str]:
    """Returns the requested providers this host can't use right now."""
    configured = {p["provider"] for p in list_providers() if p["configured"]}
    requested = {c.provider for c in request.contenders}
    return sorted(requested - configured)


def start_ask(request: AskRequest) -> str:
    """Records an ask and starts every contender on the worker pool.

    Returns:
        The new ask's id, to poll with `get_ask`.
    """
    ask = Ask(
        ask_id=uuid.uuid4().hex[:12],
        question=request.question,
        created_at=time.time(),
        answers=[Answer(c.provider, c.model) for c in request.contenders],
    )
    _registry.put(ask)
    log.info(
        "ask_started",
        ask_id=ask.ask_id,
        contenders=[f"{a.provider}/{a.model}" for a in ask.answers],
        question_chars=len(request.question),
    )
    for answer in ask.answers:
        _executor.submit(_work, answer, request.question)
    return ask.ask_id
