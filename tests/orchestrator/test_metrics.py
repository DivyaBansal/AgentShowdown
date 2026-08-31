"""Tests for agentshowdown.orchestrator.metrics."""

from __future__ import annotations

import json

from agentshowdown.orchestrator.metrics import (
    Usage,
    parse_diff_numstat,
    parse_usage,
)

# Shape of `claude --print --output-format json`: one result object at the end.
CLAUDE_LOG = json.dumps(
    {
        "type": "result",
        "num_turns": 7,
        "usage": {"input_tokens": 1234, "output_tokens": 567},
    }
)

# Shape of `codex exec --json`: JSONL events, token counts accumulating.
CODEX_LOG = "\n".join(
    [
        '{"type": "thread.started", "thread_id": "t1"}',
        '{"type": "token_count", "info": {"total_token_usage": '
        '{"input_tokens": 100, "output_tokens": 20}}}',
        '{"type": "token_count", "info": {"total_token_usage": '
        '{"input_tokens": 900, "output_tokens": 80}}}',
        '{"type": "turn.completed"}',
    ]
)


def test_parses_claude_result_object() -> None:
    usage = parse_usage(CLAUDE_LOG, "claude")
    assert usage.input_tokens == 1234
    assert usage.output_tokens == 567
    assert usage.num_turns == 7


def test_parses_codex_jsonl_taking_the_final_cumulative_counts() -> None:
    usage = parse_usage(CODEX_LOG, "codex")
    assert usage.input_tokens == 900
    assert usage.output_tokens == 80


def test_claude_json_survives_surrounding_prose() -> None:
    """Wrapper scripts and shell warnings share the log with the JSON."""
    noisy = f"bash: warning: setlocale failed\n{CLAUDE_LOG}\n"
    assert parse_usage(noisy, "claude").input_tokens == 1234


def test_agents_without_structured_usage_report_nothing() -> None:
    for agent in ("cursor", "opencode", "copilot"):
        assert parse_usage(CLAUDE_LOG, agent) == Usage()
        assert parse_usage(CLAUDE_LOG, agent).is_empty


def test_unparseable_log_yields_empty_usage_rather_than_raising() -> None:
    for bad in ("", "not json at all", "{truncated", '{"usage": '):
        assert parse_usage(bad, "claude").is_empty


def test_missing_fields_stay_none_rather_than_zero() -> None:
    """A false zero would make an agent look free in a token comparison."""
    usage = parse_usage('{"type": "result", "num_turns": 2}', "claude")
    assert usage.num_turns == 2
    assert usage.input_tokens is None
    assert usage.output_tokens is None


def test_booleans_are_not_mistaken_for_counts() -> None:
    assert parse_usage('{"input_tokens": true}', "claude").input_tokens is None


def test_numstat_sums_files_and_lines() -> None:
    assert parse_diff_numstat("3\t1\ta.py\n10\t0\tb.py\n") == (2, 13, 1)


def test_numstat_counts_binary_files_without_line_totals() -> None:
    assert parse_diff_numstat("-\t-\timg.png\n5\t2\ta.py\n") == (2, 5, 2)


def test_numstat_ignores_malformed_lines() -> None:
    assert parse_diff_numstat("") == (0, 0, 0)
    assert parse_diff_numstat("garbage\n") == (0, 0, 0)
