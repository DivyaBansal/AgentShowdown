"""Tests for backend.orchestrator.agent_otel."""

from __future__ import annotations

import tomllib

import pytest
from backend.orchestrator import agent_otel
from backend.orchestrator.agent_otel import (
    codex_otel_overrides,
    exports_otel,
    logs_endpoint,
    prefer_otel_usage,
    summarize_events,
    usage_from_events,
    with_otel,
)
from backend.orchestrator.metrics import Usage
from backend.orchestrator.sbx import build_codex_cmd

URL = "http://host.docker.internal:8000/otel/arena-f1-claude/v1/logs"


@pytest.mark.parametrize(
    ("agent_id", "command", "endpoint", "expected"),
    [
        ("claude", None, "http://h:8000", True),
        ("codex", None, "http://h:8000", True),
        ("cursor", None, "http://h:8000", False),
        ("claude", "my-script.sh", "http://h:8000", False),
        ("claude", None, None, False),
        ("claude", None, "", False),
    ],
)
def test_only_wired_clis_with_an_endpoint_and_no_custom_command_export(
    agent_id: str, command: str | None, endpoint: str | None, expected: bool
) -> None:
    assert exports_otel(agent_id, command, endpoint) is expected


def test_logs_endpoint_files_events_under_the_job() -> None:
    assert logs_endpoint("http://h:8000/", "arena-f1-claude") == (
        "http://h:8000/otel/arena-f1-claude/v1/logs"
    )


def test_logs_endpoint_keeps_the_sandbox_name_one_path_segment() -> None:
    assert logs_endpoint("http://h", "a/b c") == "http://h/otel/a%2Fb%20c/v1/logs"


def test_claude_gets_json_logs_export_through_its_environment() -> None:
    argv = with_otel("claude", ["claude", "--print", "hi"], URL)

    assert argv[0] == "env"
    assert argv[-3:] == ["claude", "--print", "hi"]
    env = dict(a.split("=", 1) for a in argv[1:-3])
    assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
    assert env["OTEL_LOGS_EXPORTER"] == "otlp"
    assert env["OTEL_EXPORTER_OTLP_LOGS_PROTOCOL"] == "http/json"
    assert env["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"] == URL
    # The events carry everything metrics would, so only logs are sent.
    assert env["OTEL_METRICS_EXPORTER"] == "none"


def test_codex_overrides_go_before_a_resume_subcommand() -> None:
    """`-c` belongs to `exec`; after `resume` it would be the wrong parser's."""
    argv = with_otel("codex", build_codex_cmd("hi", False, "", None, resume=True), URL)

    assert argv[:3] == ["codex", "exec", "-c"]
    assert argv.index("resume") > max(i for i, a in enumerate(argv) if a == "-c")


def test_codex_exporter_override_is_valid_toml_with_the_json_protocol() -> None:
    """codex parses each `-c` value as TOML, so the exporter table must be."""
    overrides = codex_otel_overrides('http://h/otel/x"y/v1/logs')
    exporter = next(o for o in overrides if o.startswith("otel.exporter="))

    parsed = tomllib.loads(exporter.replace("otel.exporter=", "exporter = ", 1))

    assert parsed["exporter"] == {
        "otlp-http": {"endpoint": 'http://h/otel/x"y/v1/logs', "protocol": "json"}
    }


def test_with_otel_refuses_an_unwired_cli() -> None:
    with pytest.raises(ValueError, match="cursor"):
        with_otel("cursor", ["cursor-agent"], URL)


def _event(name: str, **attributes: object) -> dict[str, object]:
    return {"name": name, "attributes": attributes}


def _counts(input_count: str, output_count: str) -> dict[str, object]:
    """codex's token attributes, as sent: numbers in strings."""
    return {"input_token_count": input_count, "output_token_count": output_count}


def test_claude_usage_sums_every_api_request_in_the_turn() -> None:
    events = [
        _event("claude_code.api_request", input_tokens="100", output_tokens="20"),
        _event("claude_code.tool_result", tool_name="Bash", success="true"),
        _event("claude_code.api_request", input_tokens=50, output_tokens=5),
    ]

    usage = usage_from_events("claude", events)

    assert usage == Usage(input_tokens=150, output_tokens=25, num_turns=None)


def test_codex_usage_reads_token_counts_wherever_they_are_reported() -> None:
    events = [
        _event("codex.api_request", duration_ms="250"),
        _event("codex.sse_event", **_counts("300", "40")),
        _event("codex.websocket_event", **_counts("10", "1")),
    ]

    assert usage_from_events("codex", events) == Usage(input_tokens=310, output_tokens=41)


def test_no_token_events_is_no_data_rather_than_zero() -> None:
    """A false zero would make an agent look free in a comparison."""
    events = [_event("codex.user_prompt", prompt_length="6")]

    assert usage_from_events("codex", events).is_empty
    assert usage_from_events("claude", []).is_empty


def test_otel_usage_wins_field_by_field_and_the_log_fills_gaps() -> None:
    otel = Usage(input_tokens=150, output_tokens=None)
    log = Usage(input_tokens=90, output_tokens=12, num_turns=4)

    assert prefer_otel_usage(otel, log) == Usage(input_tokens=150, output_tokens=12, num_turns=4)


def test_summary_counts_events_totals_cost_and_rolls_up_tools() -> None:
    events = [
        _event("claude_code.api_request", cost_usd="0.25"),
        _event("claude_code.api_request", cost_usd=0.5),
        _event("claude_code.tool_result", tool_name="Bash", success="false", duration_ms="30"),
        _event("claude_code.tool_result", tool_name="Bash", success="true", duration_ms=70),
        _event("claude_code.tool_result", tool_name="Edit", success="true", duration_ms="5"),
    ]

    summary = summarize_events(events)

    assert summary["event_counts"] == {
        "claude_code.api_request": 2,
        "claude_code.tool_result": 3,
    }
    assert summary["cost_usd"] == 0.75
    assert summary["tools"] == {
        "Bash": {"calls": 2, "failures": 1, "duration_ms": 100},
        "Edit": {"calls": 1, "failures": 0, "duration_ms": 5},
    }


def test_summary_cost_is_none_when_no_event_reports_one() -> None:
    assert summarize_events([_event("codex.api_request")])["cost_usd"] is None


def test_default_endpoint_is_the_host_as_seen_from_a_sandbox() -> None:
    assert agent_otel.DEFAULT_AGENT_OTEL_ENDPOINT.startswith("http://host.docker.internal:")


@pytest.mark.parametrize(
    ("endpoint", "resource"),
    [
        # sbx files a request to the host under "localhost", whatever name it used.
        ("http://host.docker.internal:8000", "localhost:8000"),
        ("http://127.0.0.1:9000/", "localhost:9000"),
        ("http://collector.internal:4318", "collector.internal:4318"),
        ("https://otel.example.com", "otel.example.com:443"),
        ("http://otel.example.com", "otel.example.com:80"),
    ],
)
def test_policy_resource_names_what_sbx_checks(endpoint: str, resource: str) -> None:
    assert agent_otel.policy_resource(endpoint) == resource


def test_policy_resource_needs_a_host() -> None:
    with pytest.raises(ValueError, match="no host"):
        agent_otel.policy_resource("localhost:8000")


def test_claude_sends_tool_details_so_the_timeline_can_name_them() -> None:
    assert agent_otel.claude_otel_env(URL)["OTEL_LOG_TOOL_DETAILS"] == "1"


def _stored(name: str, ts: str | None = "2026-10-22T10:00:00Z", **attributes: object) -> dict:
    return {"name": name, "ts": ts, "received_at": "2026-10-22T10:00:09Z", "attributes": attributes}


def test_a_claude_bash_call_shows_the_command_that_ran() -> None:
    (entry,) = agent_otel.timeline_from_events(
        [
            _stored(
                "claude_code.tool_result",
                tool_name="Bash",
                success="true",
                duration_ms="1200",
                tool_input='{"command": "pytest -q\\n  tests/"}',
            )
        ]
    )

    assert (entry.kind, entry.title, entry.detail) == ("tool", "Bash", "pytest -q tests/")
    assert (entry.ok, entry.duration_ms, entry.at) == (True, 1200, "2026-10-22T10:00:00Z")


def test_a_file_tool_shows_its_path_from_the_tool_parameters() -> None:
    (entry,) = agent_otel.timeline_from_events(
        [
            _stored(
                "claude_code.tool_result",
                tool_name="Edit",
                success="false",
                error_type="Error:ENOENT",
                tool_parameters='{"file_path": "src/app.py"}',
            )
        ]
    )

    assert (entry.title, entry.detail, entry.ok) == ("Edit", "src/app.py", False)


def test_a_tool_without_details_falls_back_to_its_error_type() -> None:
    (entry,) = agent_otel.timeline_from_events(
        [_stored("claude_code.tool_result", tool_name="Read", error_type="ShellError")]
    )

    assert entry.detail == "ShellError"


def test_a_codex_command_given_as_an_argv_list_is_joined() -> None:
    (entry,) = agent_otel.timeline_from_events(
        [
            _stored(
                "codex.tool_result",
                tool_name="shell",
                arguments='{"command": ["bash", "-lc", "ls -la"]}',
                success="true",
            )
        ]
    )

    assert entry.detail == "bash -lc ls -la"


def test_a_long_detail_is_truncated_to_one_line() -> None:
    (entry,) = agent_otel.timeline_from_events(
        [_stored("claude_code.tool_result", tool_name="Bash", tool_input="x" * 1000)]
    )

    assert entry.detail is not None
    assert len(entry.detail) == 300
    assert entry.detail.endswith("…")


def test_model_requests_summarize_tokens_and_cost() -> None:
    (entry,) = agent_otel.timeline_from_events(
        [
            _stored(
                "claude_code.api_request",
                model="claude-haiku-4-5",
                input_tokens="1200",
                output_tokens=80,
                cost_usd="0.00123",
                duration_ms="900",
            )
        ]
    )

    assert (entry.kind, entry.title) == ("model", "claude-haiku-4-5")
    assert entry.detail == "1,200 in · 80 out · $0.0012"


def test_failed_requests_and_denied_tools_are_errors() -> None:
    entries = agent_otel.timeline_from_events(
        [
            _stored("codex.api_request", model="gpt-5.6-sol", **{"error.message": "http 401"}),
            _stored("claude_code.api_error", model="m", error="overloaded"),
            _stored("claude_code.tool_decision", tool_name="Bash", decision="reject"),
        ]
    )

    assert [(e.kind, e.title, e.detail, e.ok) for e in entries] == [
        ("error", "gpt-5.6-sol", "http 401", False),
        ("error", "m", "overloaded", False),
        ("error", "Bash denied", None, False),
    ]


def test_prompts_and_unknown_events_still_appear() -> None:
    entries = agent_otel.timeline_from_events(
        [
            _stored("codex.user_prompt", prompt_length="1234"),
            _stored("codex.startup_phase", ts=None, duration_ms="37"),
        ]
    )

    assert (entries[0].kind, entries[0].detail) == ("prompt", "1,234 characters")
    assert (entries[1].kind, entries[1].title) == ("other", "startup_phase")
    # No CLI time: falls back to when the receiver got it.
    assert entries[1].at == "2026-10-22T10:00:09Z"
