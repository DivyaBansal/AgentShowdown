"""OpenTelemetry export from the agent CLIs running inside sandboxes.

`claude` and `codex` can both export their own OTel log events -- one per
model request, tool call, prompt -- over OTLP/HTTP. This module turns that
on at launch and points it back at agentshowdown's own receiver
(`api/routes_otlp.py`), which stores the events against the job. That is
what makes "what happened inside the sandbox" visible without parsing a
CLI's stdout.

Correlation is by URL, not by resource attribute: each job exports to
`<endpoint>/otel/<sandbox_name>/v1/logs`. Codex reads none of the standard
`OTEL_*` variables (verified on codex-cli 0.147.0; it is configured only
through its `[otel]` table), so a path is the one tag both CLIs honor.

Only the logs signal is enabled. The events already carry what the metrics
signal would (tokens, cost, durations), and traces are beta in claude.

Token usage read from these events is preferred over `metrics.parse_usage`,
which stays as the fallback for runs that exported nothing -- a receiver
the sandbox could not reach, a CLI version without OTel, or any other agent.

Everything here is stdlib-only and side-effect free; the API layer owns the
receiver and the orchestrator owns the waiting.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote, urlsplit

from backend.orchestrator.metrics import Usage

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

# Agents whose CLI exports OTel events and whose launch is wired up here.
OTEL_AGENTS = frozenset({"claude", "codex"})

# Where a sandboxed CLI reaches the host. sbx forwards host.docker.internal
# to the host's loopback, so this works against a server bound to
# 127.0.0.1 -- the same route a host Ollama is reached by.
DEFAULT_AGENT_OTEL_ENDPOINT = "http://host.docker.internal:8000"

# claude batches log events every 5s by default. A short interval keeps the
# live view current; the final batch is flushed on exit either way.
_CLAUDE_LOGS_EXPORT_INTERVAL_MS = "1000"

# Event names carrying per-request token counts. claude reports them on
# every successful API request. For codex the documented carrier is the
# `response.completed` stream event; matched by attribute presence rather
# than by event kind, because codex has moved between SSE and websocket
# transports and names the event after the transport.
_CLAUDE_REQUEST_EVENT = "claude_code.api_request"
_CODEX_TOKEN_KEYS = ("input_token_count", "output_token_count")


def exports_otel(agent_id: str, command_override: str | None, endpoint: str | None) -> bool:
    """Whether a launch of this agent exports OTel events.

    A custom `command:` replaces the registered CLI builder entirely, so
    there is no invocation to add the exporter settings to.

    Args:
        agent_id: The sbx agent type.
        command_override: The spec's custom command, if any.
        endpoint: The configured receiver base URL; None or "" disables export.

    Returns:
        True when the launch will be configured to export.
    """
    return bool(endpoint) and agent_id in OTEL_AGENTS and not command_override


# Hostnames that reach the host's own loopback from a sandbox. sbx's policy
# names that destination "localhost" whichever of these the request used --
# observed on v0.48.0, where a request to host.docker.internal:4399 was
# denied as `net:domain:localhost:4399`.
_HOST_LOOPBACK_NAMES = frozenset({"host.docker.internal", "localhost", "127.0.0.1"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def policy_resource(endpoint: str) -> str:
    """The sbx network-policy resource that lets a sandbox reach `endpoint`.

    sbx denies the route to the host by default, so a job that exports
    telemetry gets an allow rule for exactly this resource, scoped to its
    own sandbox (see sbx.sbx_allow_network). sbx removes a scoped rule with
    the sandbox.

    Args:
        endpoint: Receiver base URL, e.g. "http://host.docker.internal:8000".

    Returns:
        "host:port", e.g. "localhost:8000".

    Raises:
        ValueError: If the URL has no host.
    """
    parts = urlsplit(endpoint)
    host = parts.hostname
    if not host:
        raise ValueError(f"no host in OTel endpoint {endpoint!r}")
    port = parts.port or _DEFAULT_PORTS.get(parts.scheme, 80)
    return f"{'localhost' if host in _HOST_LOOPBACK_NAMES else host}:{port}"


def logs_endpoint(endpoint: str, sandbox_name: str) -> str:
    """The full OTLP/HTTP logs URL a job exports to.

    Both CLIs are given the full `/v1/logs` URL rather than a base: codex's
    exporter posts to exactly the URL it is given, and claude's
    signal-specific variable is used as-is.

    Args:
        endpoint: Receiver base URL, e.g. "http://host.docker.internal:8000".
        sandbox_name: The job, which the receiver files the events under.

    Returns:
        The URL.
    """
    return f"{endpoint.rstrip('/')}/otel/{quote(sandbox_name, safe='')}/v1/logs"


def claude_otel_env(url: str) -> dict[str, str]:
    """Environment for `claude` to export log events to `url`.

    Tool details are on, so the timeline can say which command ran or which
    file was edited, not just "Bash". They land only in this app's local
    database. Prompts and responses stay at claude's default (redacted).

    Args:
        url: Full logs URL, from `logs_endpoint`.

    Returns:
        Env vars to set on the claude process.
    """
    return {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_METRICS_EXPORTER": "none",
        "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": url,
        "OTEL_LOGS_EXPORT_INTERVAL": _CLAUDE_LOGS_EXPORT_INTERVAL_MS,
        "OTEL_LOG_TOOL_DETAILS": "1",
    }


def codex_otel_overrides(url: str) -> list[str]:
    """`-c` overrides for `codex exec` to export log events to `url`.

    Passed on the command line because codex ignores `[otel]` in a
    project-local config and reads no OTEL_* variables. Syntax verified
    against codex-cli 0.147.0, which validates these keys at startup.

    Args:
        url: Full logs URL, from `logs_endpoint`.

    Returns:
        Arguments to insert after `codex exec`.
    """
    # A JSON string literal is a valid TOML basic string for any input.
    exporter = f'otel.exporter={{otlp-http={{endpoint={json.dumps(url)},protocol="json"}}}}'
    return ["-c", exporter, "-c", 'otel.environment="agentshowdown"']


def with_otel(agent_id: str, argv: list[str], url: str) -> list[str]:
    """Adds OTel export settings to an agent invocation.

    Args:
        agent_id: Which CLI `argv` runs; must be in OTEL_AGENTS.
        argv: The invocation from `sbx.build_agent_cmd`.
        url: Full logs URL, from `logs_endpoint`.

    Returns:
        A new argv. claude's settings are environment variables, applied via
        `env` so they reach only the agent process; codex's are `-c` flags
        placed right after `exec`, before any `resume` subcommand.

    Raises:
        ValueError: If `agent_id` has no OTel wiring.
    """
    if agent_id == "claude":
        env = claude_otel_env(url)
        return ["env", *(f"{k}={v}" for k, v in sorted(env.items())), *argv]
    if agent_id == "codex":
        return [*argv[:2], *codex_otel_overrides(url), *argv[2:]]
    raise ValueError(f"no OTel wiring for agent_id {agent_id!r}")


def _as_int(value: object) -> int | None:
    """Reads an OTel attribute as an int. OTLP/JSON encodes int64 as a
    string, and codex sends its counts as string attributes outright."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def _as_float(value: object) -> float | None:
    """Reads an OTel attribute as a float, accepting numeric strings."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _sum(values: Iterable[int | None]) -> int | None:
    """Sums the reported values; None when nothing was reported at all."""
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def usage_from_events(agent_id: str, events: Iterable[Mapping[str, object]]) -> Usage:
    """Totals token usage across one attempt's OTel events.

    claude's `input_tokens` excludes cache reads and writes, matching what
    its `--output-format json` result reports, so the two sources agree on
    what "input" means. Requests from claude's own auxiliary queries are
    included: they are billed like any other. `num_turns` has no OTel
    equivalent and is always None here.

    Args:
        agent_id: Which CLI produced the events.
        events: Stored events, each with "name" and "attributes".

    Returns:
        The totals; every field None when the events carry no token counts.
    """
    inputs: list[int | None] = []
    outputs: list[int | None] = []
    for event in events:
        attributes = event.get("attributes")
        if not isinstance(attributes, dict):
            continue
        if agent_id == "claude":
            if event.get("name") != _CLAUDE_REQUEST_EVENT:
                continue
            inputs.append(_as_int(attributes.get("input_tokens")))
            outputs.append(_as_int(attributes.get("output_tokens")))
        elif agent_id == "codex":
            if not any(key in attributes for key in _CODEX_TOKEN_KEYS):
                continue
            inputs.append(_as_int(attributes.get("input_token_count")))
            outputs.append(_as_int(attributes.get("output_token_count")))
    return Usage(input_tokens=_sum(inputs), output_tokens=_sum(outputs))


def prefer_otel_usage(otel: Usage, from_log: Usage) -> Usage:
    """Merges the two usage sources field by field, OTel first.

    Args:
        otel: From `usage_from_events`.
        from_log: From `metrics.parse_usage`, the fallback.

    Returns:
        Each field from OTel when it reported one, else from the log.
    """
    return Usage(
        input_tokens=otel.input_tokens if otel.input_tokens is not None else from_log.input_tokens,
        output_tokens=(
            otel.output_tokens if otel.output_tokens is not None else from_log.output_tokens
        ),
        num_turns=otel.num_turns if otel.num_turns is not None else from_log.num_turns,
    )


def summarize_events(events: Iterable[Mapping[str, object]]) -> dict[str, object]:
    """Rolls a job's events up into what a person scans first.

    Args:
        events: Stored events, each with "name" and "attributes".

    Returns:
        Counts per event name, total cost in USD (claude only; None when no
        event reported one), and per-tool call count, failures and total
        duration.
    """
    counts: dict[str, int] = {}
    cost: float | None = None
    tools: dict[str, dict[str, int]] = {}
    for event in events:
        name = str(event.get("name") or "")
        counts[name] = counts.get(name, 0) + 1
        attributes = event.get("attributes")
        if not isinstance(attributes, dict):
            continue
        event_cost = _as_float(attributes.get("cost_usd"))
        if event_cost is not None:
            cost = (cost or 0.0) + event_cost
        if name.endswith(".tool_result"):
            tool = str(attributes.get("tool_name") or attributes.get("tool") or "unknown")
            entry = tools.setdefault(tool, {"calls": 0, "failures": 0, "duration_ms": 0})
            entry["calls"] += 1
            if str(attributes.get("success")).lower() == "false":
                entry["failures"] += 1
            entry["duration_ms"] += _as_int(attributes.get("duration_ms")) or 0
    return {
        "event_counts": counts,
        "cost_usd": round(cost, 6) if cost is not None else None,
        "tools": tools,
    }


# Keys that name *what* a tool call acted on, most specific first. claude
# sends them inside `tool_input` / `tool_parameters` (JSON strings, with
# OTEL_LOG_TOOL_DETAILS on); codex sends `arguments`. Matched generically
# so a new tool still shows something useful.
_TOOL_TARGET_KEYS = (
    "command",
    "full_command",
    "bash_command",
    "cmd",
    "file_path",
    "path",
    "pattern",
    "url",
    "query",
    "description",
)
_TOOL_DETAIL_ATTRIBUTES = ("tool_input", "tool_parameters", "arguments", "input")

# Longest detail line shown; the full attribute is still in the raw event.
_MAX_DETAIL_CHARS = 300

TimelineKind = Literal["tool", "model", "prompt", "error", "other"]


@dataclass(frozen=True)
class TimelineEntry:
    """One row of a job's agent timeline.

    Attributes:
        at: When it happened (ISO-8601): the CLI's own time when it sent
            one, else when the receiver got it.
        kind: What sort of step this was, for filtering and styling.
        title: The tool, model or event, e.g. "Bash" or "claude-haiku-4-5".
        detail: What it acted on or produced, e.g. the command that ran, or
            token counts; None when the event says nothing more.
        duration_ms: How long it took, when reported.
        ok: Whether it succeeded, when reported.
        name: The raw event name, e.g. "claude_code.tool_result".
        seq: Position in the job's timeline, 0-based; stable because events
            are only ever appended.
    """

    at: str | None
    kind: TimelineKind
    title: str
    detail: str | None
    duration_ms: int | None
    ok: bool | None
    name: str
    seq: int = 0


def _short(text: str) -> str:
    """Collapses whitespace and truncates for a one-line detail."""
    flat = " ".join(text.split())
    return flat if len(flat) <= _MAX_DETAIL_CHARS else flat[: _MAX_DETAIL_CHARS - 1] + "…"


def _tool_target(attributes: Mapping[str, object]) -> str | None:
    """Finds what a tool call acted on, from whichever attribute carries it."""
    for attribute in _TOOL_DETAIL_ATTRIBUTES:
        raw = attributes.get(attribute)
        value: object = raw
        if isinstance(raw, str):
            try:
                value = json.loads(raw)
            except ValueError:
                return _short(raw) if raw.strip() else None
        if isinstance(value, dict):
            for key in _TOOL_TARGET_KEYS:
                target = value.get(key)
                if isinstance(target, list):
                    target = " ".join(str(part) for part in target)
                if isinstance(target, str) and target.strip():
                    return _short(target)
    return None


def _success(attributes: Mapping[str, object]) -> bool | None:
    """Reads a success flag sent as a bool or as "true"/"false"."""
    value = attributes.get("success")
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return None


def _model_detail(attributes: Mapping[str, object]) -> str | None:
    """Summarizes a model request's tokens and cost."""
    parts: list[str] = []
    input_tokens = _as_int(attributes.get("input_tokens", attributes.get("input_token_count")))
    output_tokens = _as_int(attributes.get("output_tokens", attributes.get("output_token_count")))
    if input_tokens is not None:
        parts.append(f"{input_tokens:,} in")
    if output_tokens is not None:
        parts.append(f"{output_tokens:,} out")
    cost = _as_float(attributes.get("cost_usd"))
    if cost is not None:
        parts.append(f"${cost:.4f}")
    return " · ".join(parts) or None


def _tool_entry(
    name: str, attributes: Mapping[str, object], at: str | None
) -> TimelineEntry | None:
    """A timeline row for a tool call or a denied one; None for other events."""
    suffix = name.rsplit(".", 1)[-1]
    tool = str(attributes.get("tool_name") or attributes.get("tool") or "tool")
    if suffix == "tool_result":
        error_type = attributes.get("error_type")
        detail = _tool_target(attributes) or (str(error_type) if error_type else None)
        duration = _as_int(attributes.get("duration_ms"))
        return TimelineEntry(at, "tool", tool, detail, duration, _success(attributes), name)
    if suffix == "tool_decision" and str(attributes.get("decision")).lower() == "reject":
        return TimelineEntry(at, "error", f"{tool} denied", None, None, False, name)
    return None


def _timeline_entry(name: str, attributes: Mapping[str, object], at: str | None) -> TimelineEntry:
    """Classifies one stored event into a timeline row."""
    tool = _tool_entry(name, attributes, at)
    if tool is not None:
        return tool
    suffix = name.rsplit(".", 1)[-1]
    duration = _as_int(attributes.get("duration_ms"))
    error = attributes.get("error.message") or attributes.get("error")
    model = str(attributes.get("model") or "model")

    if suffix in ("api_error", "api_retries_exhausted") or (suffix == "api_request" and error):
        return TimelineEntry(
            at, "error", model, _short(str(error)) if error else None, duration, False, name
        )
    if suffix == "api_request":
        return TimelineEntry(at, "model", model, _model_detail(attributes), duration, True, name)
    if suffix == "user_prompt":
        length = _as_int(attributes.get("prompt_length"))
        detail = f"{length:,} characters" if length is not None else None
        return TimelineEntry(at, "prompt", "Prompt", detail, None, None, name)
    if _model_detail(attributes) is not None:
        # codex reports a turn's tokens on a stream event, not on api_request.
        return TimelineEntry(at, "model", model, _model_detail(attributes), duration, None, name)
    return TimelineEntry(at, "other", suffix, None, duration, _success(attributes), name)


def timeline_from_events(events: Iterable[Mapping[str, object]]) -> list[TimelineEntry]:
    """Turns a job's stored events into its timeline, in the order received.

    Args:
        events: Stored events, each with "name", "attributes", "ts" and
            "received_at".

    Returns:
        One entry per event. Callers filter by `kind`; nothing is dropped
        here, so an event shape this code doesn't know still shows up.
    """
    entries: list[TimelineEntry] = []
    for seq, event in enumerate(events):
        attributes = event.get("attributes")
        at = event.get("ts") or event.get("received_at")
        entry = _timeline_entry(
            str(event.get("name") or "unknown"),
            attributes if isinstance(attributes, dict) else {},
            str(at) if at else None,
        )
        entries.append(dataclasses.replace(entry, seq=seq))
    return entries
