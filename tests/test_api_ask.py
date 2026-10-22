"""Tests for quick questions: one prompt asked of several models, no repo."""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import Executor, Future
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from backend.api import ask as ask_module
from backend.api.ask import (
    MAX_TRACKED_ASKS,
    Answer,
    Ask,
    parse_chat_completion,
    parse_claude_output,
    parse_codex_output,
)
from backend.api.paths import home_dir
from backend.main import app
from backend.model_catalog import CatalogUpdates
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Callable

client = TestClient(app)

OPENROUTER_KEY = "or-test-do-not-leak"  # fake value; the test is that it never leaks

# Shapes captured from the real CLIs on this host (trimmed).
CLAUDE_OK = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "Paris.",
        "usage": {
            "input_tokens": 10,
            "cache_creation_input_tokens": 900,
            "cache_read_input_tokens": 6300,
            "output_tokens": 4,
        },
    }
)
CLAUDE_NOT_LOGGED_IN = json.dumps(
    {"type": "result", "is_error": True, "result": "Not logged in · Please run /login"}
)
CODEX_OK = "\n".join(
    [
        '{"type":"thread.started","thread_id":"t1"}',
        '{"type":"item.completed","item":{"id":"item_0","type":"error",'
        '"message":"Model metadata for `x` not found."}}',
        '{"type":"turn.started"}',
        '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"Paris"}}',
        '{"type":"turn.completed","usage":{"input_tokens":12110,"cached_input_tokens":9728,'
        '"output_tokens":9}}',
    ]
)
CODEX_UNSUPPORTED_MODEL = "\n".join(
    [
        '{"type":"thread.started","thread_id":"t1"}',
        '{"type":"turn.started"}',
        '{"type":"turn.failed","error":{"message":"{\\"type\\":\\"error\\",\\"status\\":400,'
        '\\"error\\":{\\"type\\":\\"invalid_request_error\\",\\"message\\":\\"The \'gpt-5.5\' '
        'model is not supported when using Codex with a ChatGPT account.\\"}}"}}',
    ]
)


class _InlineExecutor(Executor):
    """Runs submitted work immediately, so an ask is finished when POST returns."""

    def submit(self, fn: Callable, /, *args: object, **kwargs: object) -> Future:
        future: Future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as exc:  # surfaced through the future, like a real executor
            future.set_exception(exc)
        return future


class _FakeCli:
    """Stands in for `subprocess.run`, answering per CLI with canned stdout."""

    def __init__(self, outputs: dict[str, tuple[str, int]]) -> None:
        self.outputs = outputs
        self.calls: list[dict[str, object]] = []

    def __call__(self, cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        self.calls.append({"cmd": cmd, "cwd": kwargs.get("cwd")})
        name = Path(cmd[0]).name
        stdout, code = self.outputs[name]
        return subprocess.CompletedProcess(cmd, code, stdout=stdout, stderr="")


@pytest.fixture(autouse=True)
def _providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every provider configured, work run inline, no real network or CLIs."""
    monkeypatch.setattr(ask_module, "_executor", _InlineExecutor())
    monkeypatch.setattr(ask_module, "find_cli", lambda name: f"/opt/bin/{name}")
    monkeypatch.setattr(
        ask_module,
        "list_ollama_models",
        lambda: {"available": True, "models": ["qwen2.5-coder:32b"]},
    )
    monkeypatch.setattr(ask_module, "_registry", ask_module._AskRegistry())
    monkeypatch.setenv("OPENROUTER_API_KEY", OPENROUTER_KEY)
    monkeypatch.delenv("OPEN_ROUTER_KEY", raising=False)


def _mock_http(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    monkeypatch.setattr(
        ask_module,
        "http_client_factory",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _chat_ok(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        },
    )


def _ask(contenders: list[dict[str, str]], question: str = "Capital of France?") -> dict:
    response = client.post("/asks", json={"question": question, "contenders": contenders})
    assert response.status_code == HTTPStatus.ACCEPTED, response.text
    result = client.get(f"/asks/{response.json()['ask_id']}")
    assert result.status_code == HTTPStatus.OK
    return result.json()


def test_one_question_is_answered_by_every_contender(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeCli({"claude": (CLAUDE_OK, 0), "codex": (CODEX_OK, 0)})
    monkeypatch.setattr(subprocess, "run", fake)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _chat_ok("Paris!")

    _mock_http(monkeypatch, handler)

    ask = _ask(
        [
            {"provider": "claude", "model": "claude-haiku-5-5"},
            {"provider": "codex", "model": "gpt-5.6-luna"},
            {"provider": "openrouter", "model": "nvidia/nemotron-3.5-lightning:free"},
        ]
    )

    claude, codex, openrouter = ask["answers"]
    assert ask["question"] == "Capital of France?"
    assert (claude["status"], claude["text"]) == ("done", "Paris.")
    # Cached harness-prompt tokens count toward input: they were really sent.
    assert claude["input_tokens"] == 10 + 900 + 6300
    assert claude["output_tokens"] == 4
    # codex's metadata warning item is not mistaken for a failure.
    assert (codex["status"], codex["text"], codex["output_tokens"]) == ("done", "Paris", 9)
    assert (openrouter["status"], openrouter["text"]) == ("done", "Paris!")
    assert (openrouter["input_tokens"], openrouter["output_tokens"]) == (7, 3)
    assert all(isinstance(a["latency_ms"], int) for a in ask["answers"])
    assert str(seen[0].url) == "https://openrouter.ai/api/v1/chat/completions"
    assert json.loads(seen[0].content)["model"] == "nvidia/nemotron-3.5-lightning:free"


def test_clis_run_tool_less_in_an_empty_scratch_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeCli({"claude": (CLAUDE_OK, 0), "codex": (CODEX_OK, 0)})
    monkeypatch.setattr(subprocess, "run", fake)

    _ask(
        [
            {"provider": "claude", "model": "claude-haiku-5-5"},
            {"provider": "codex", "model": "gpt-5.6-luna"},
        ],
        question="-rf / is that a flag?",
    )

    claude_call, codex_call = fake.calls
    claude_cmd = claude_call["cmd"]
    assert isinstance(claude_cmd, list)
    assert claude_cmd[claude_cmd.index("--tools") + 1] == ""
    codex_cmd = codex_call["cmd"]
    assert isinstance(codex_cmd, list)
    assert codex_cmd[codex_cmd.index("--sandbox") + 1] == "read-only"
    for call in fake.calls:
        cmd = call["cmd"]
        assert isinstance(cmd, list)
        # A question that looks like a flag is passed after `--`.
        assert cmd[-2:] == ["--", "-rf / is that a flag?"]
        cwd = call["cwd"]
        assert isinstance(cwd, str)
        assert "agentshowdown-ask-" in cwd
        # Removed once the answer is in.
        assert not Path(cwd).exists()


def test_one_failing_contender_does_not_sink_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "run", _FakeCli({"claude": (CLAUDE_OK, 0)}))
    _mock_http(
        monkeypatch,
        lambda _req: httpx.Response(402, json={"error": {"message": "Insufficient credits"}}),
    )

    ask = _ask(
        [
            {"provider": "openrouter", "model": "nvidia/nemotron-3.5-lightning"},
            {"provider": "claude", "model": "claude-haiku-5-5"},
        ]
    )

    openrouter, claude = ask["answers"]
    assert openrouter["status"] == "error"
    assert openrouter["error"] == "HTTP 402: Insufficient credits"
    assert claude["status"] == "done"


def test_codex_api_error_is_unwrapped_to_its_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _FakeCli({"codex": (CODEX_UNSUPPORTED_MODEL, 1)}))

    (codex,) = _ask([{"provider": "codex", "model": "gpt-5.5"}])["answers"]

    assert codex["status"] == "error"
    assert codex["error"] == (
        "The 'gpt-5.5' model is not supported when using Codex with a ChatGPT account."
    )


def test_claude_login_error_is_reported_not_shown_as_an_answer() -> None:
    answer = parse_claude_output(CLAUDE_NOT_LOGGED_IN, "", 1)

    assert answer.status == "error"
    assert answer.error is not None
    assert "Not logged in" in answer.error
    assert answer.text == ""


def test_claude_output_that_is_not_json_falls_back_to_stderr() -> None:
    answer = parse_claude_output("", "error: unknown option '--tools'", 1)

    assert (answer.status, answer.error) == ("error", "error: unknown option '--tools'")


def test_codex_with_no_messages_and_a_bad_exit_is_an_error() -> None:
    answer = parse_codex_output("", "codex: not logged in", 1)

    assert (answer.status, answer.error) == ("error", "codex: not logged in")


def test_chat_completion_without_text_is_an_error() -> None:
    answer = parse_chat_completion({"choices": []})

    assert answer.status == "error"
    assert answer.input_tokens is None


def test_a_hung_cli_times_out_as_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout") or 0)

    monkeypatch.setattr(subprocess, "run", hang)

    (claude,) = _ask([{"provider": "claude", "model": "claude-haiku-5-5"}])["answers"]

    assert claude["status"] == "error"
    assert "no answer within" in claude["error"]


def test_ollama_is_called_on_the_host_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _chat_ok("Paris")

    _mock_http(monkeypatch, handler)

    (ollama,) = _ask([{"provider": "ollama", "model": "qwen2.5-coder:32b"}])["answers"]

    assert ollama["status"] == "done"
    assert str(seen[0].url) == "http://127.0.0.1:11434/v1/chat/completions"
    assert "authorization" not in seen[0].headers


@pytest.mark.parametrize(
    "body",
    [
        {"question": "", "contenders": [{"provider": "claude", "model": "m"}]},
        {"question": "   ", "contenders": [{"provider": "claude", "model": "m"}]},
        {"question": "hi", "contenders": []},
        {"question": "hi", "contenders": [{"provider": "gemini", "model": "m"}]},
        {"question": "hi", "contenders": [{"provider": "claude", "model": "a b"}]},
        {
            "question": "hi",
            "contenders": [
                {"provider": "claude", "model": "m"},
                {"provider": "claude", "model": "m"},
            ],
        },
    ],
    ids=["empty", "blank", "no-contenders", "unknown-provider", "bad-model", "duplicate"],
)
def test_invalid_asks_are_rejected(body: dict) -> None:
    response = client.post("/asks", json=body)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_an_unconfigured_provider_is_rejected_before_anything_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY")
    fake = _FakeCli({"claude": (CLAUDE_OK, 0)})
    monkeypatch.setattr(subprocess, "run", fake)

    response = client.post(
        "/asks",
        json={
            "question": "hi",
            "contenders": [
                {"provider": "claude", "model": "claude-haiku-5-5"},
                {"provider": "openrouter", "model": "x"},
            ],
        },
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "openrouter" in response.json()["detail"]
    assert fake.calls == []


def test_unknown_ask_is_404() -> None:
    assert client.get("/asks/nope").status_code == HTTPStatus.NOT_FOUND


def test_providers_report_what_this_host_can_use(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.setenv("OPEN_ROUTER_KEY", "legacy-name")
    monkeypatch.setattr(ask_module, "find_cli", lambda name: None if name == "codex" else "/x")

    providers = {p["provider"]: p for p in client.get("/ask/providers").json()["providers"]}

    assert providers["claude"]["configured"] is True
    assert providers["codex"]["configured"] is False
    # The key name `.env.example` already uses is honored too.
    assert providers["openrouter"]["configured"] is True
    assert providers["ollama"]["suggested_models"] == ["qwen2.5-coder:32b"]


def test_secrets_and_the_question_never_reach_the_logs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(subprocess, "run", _FakeCli({"claude": (CLAUDE_OK, 0)}))
    _mock_http(monkeypatch, lambda _req: httpx.Response(401, json={"error": "bad key"}))
    question = "my-private-question-text"

    _ask(
        [
            {"provider": "claude", "model": "claude-haiku-5-5"},
            {"provider": "openrouter", "model": "x"},
        ],
        question=question,
    )

    out = capsys.readouterr().out
    assert "subprocess_run" in out
    assert "ask_started" in out
    assert OPENROUTER_KEY not in out
    assert question not in out


def test_codex_models_come_from_saved_catalog():
    CatalogUpdates._save(
        home_dir(),
        {
            "codex": {
                "models": ["new-account-model"],
                "source": "cli",
                "updated_at": "2026-10-18T12:00:00Z",
            }
        },
    )
    providers = {p["provider"]: p for p in ask_module.list_providers()}
    assert providers["codex"]["suggested_models"] == ["new-account-model"]


def test_only_the_most_recent_asks_are_kept() -> None:
    registry = ask_module._AskRegistry()
    for i in range(MAX_TRACKED_ASKS + 1):
        registry.put(Ask(f"a{i}", "q", 0.0, [Answer("claude", "m")]))

    assert registry.get("a0") is None
    assert registry.get(f"a{MAX_TRACKED_ASKS}") is not None
