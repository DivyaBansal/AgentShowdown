"""Tests for agentshowdown.orchestrator.cli."""

from __future__ import annotations

import pytest

from agentshowdown.orchestrator.cli import build_arg_parser, print_status_table


def test_print_status_table_empty(capsys: pytest.CaptureFixture[str]) -> None:
    print_status_table([])
    out = capsys.readouterr().out
    assert "No jobs recorded yet." in out


def test_print_status_table_renders_columns_and_truncated_detail(
    capsys: pytest.CaptureFixture[str],
) -> None:
    jobs = [
        {
            "sandbox_name": "box-1",
            "feature_id": "f1",
            "agent_id": "claude",
            "status": "awaiting_input",
            "branch": "agent/f1/claude",
            "pr_url": None,
            "updated_at": "2026-08-24T00:00:00",
            "detail": "x" * 100,
        }
    ]

    print_status_table(jobs)
    out = capsys.readouterr().out

    assert "sandbox_name" in out
    assert "box-1" in out
    assert "awaiting_input" in out
    assert "x" * 60 in out
    assert "x" * 61 not in out


def test_run_requires_feature_id_or_all() -> None:
    parser = build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run"])


def test_run_feature_id_and_all_are_mutually_exclusive() -> None:
    parser = build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--feature-id", "f1", "--all"])


def test_run_parses_repeated_feature_ids() -> None:
    parser = build_arg_parser()
    args = parser.parse_args(["run", "--feature-id", "f1", "--feature-id", "f2"])
    assert args.feature_id == ["f1", "f2"]
    assert args.all is False


def test_run_parses_max_concurrency_override() -> None:
    parser = build_arg_parser()
    args = parser.parse_args(["run", "--all", "--max-concurrency", "5"])
    assert args.max_concurrency == 5


def test_status_watch_flag_defaults_false() -> None:
    parser = build_arg_parser()
    args = parser.parse_args(["status"])
    assert args.watch is False


def test_answer_parses_sandbox_name_and_text() -> None:
    parser = build_arg_parser()
    args = parser.parse_args(["answer", "box-1", "use option B"])
    assert args.sandbox_name == "box-1"
    assert args.answer_text == "use option B"


def test_answer_requires_both_positional_args() -> None:
    parser = build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["answer", "box-1"])
