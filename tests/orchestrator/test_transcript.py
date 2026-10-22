"""Tests for backend.orchestrator.transcript."""

from __future__ import annotations

import json

from backend.orchestrator.transcript import parse_transcript

# Shape of a claude --output-format stream-json assistant turn.
CLAUDE_ASSISTANT_EVENT = json.dumps(
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Reading the file..."}],
        },
    }
)

# Shape of `claude --print --output-format json`: one result object.
CLAUDE_RESULT_EVENT = json.dumps({"type": "result", "result": "Added the missing import."})

# Verified codex `exec --json` shapes (see test_metrics.py) -- none of these
# carry recognizable text, which is the honest case this module has to
# handle: fall back to the raw JSON rather than inventing a summary.
CODEX_LOG = "\n".join(
    [
        '{"type": "thread.started", "thread_id": "t1"}',
        '{"type": "turn.completed"}',
    ]
)


def test_empty_log_yields_no_entries() -> None:
    assert parse_transcript("") == []


def test_non_json_log_yields_no_entries() -> None:
    assert parse_transcript("just some prose, no JSON here\n") == []


def test_extracts_text_from_a_claude_assistant_message() -> None:
    entries = parse_transcript(CLAUDE_ASSISTANT_EVENT)
    assert len(entries) == 1
    assert entries[0].kind == "assistant"
    assert entries[0].text == "Reading the file..."


def test_extracts_text_from_a_claude_result_event() -> None:
    entries = parse_transcript(CLAUDE_RESULT_EVENT)
    assert entries[0].kind == "result"
    assert entries[0].text == "Added the missing import."


def test_indexes_entries_in_order() -> None:
    log = "\n".join([CLAUDE_ASSISTANT_EVENT, CLAUDE_RESULT_EVENT])
    entries = parse_transcript(log)
    assert [e.index for e in entries] == [0, 1]
    assert entries[0].kind == "assistant"
    assert entries[1].kind == "result"


def test_falls_back_to_raw_json_when_no_text_field_is_recognized() -> None:
    """Codex's verified event shapes (thread.started, turn.completed) carry
    no text -- this must show something, not silently drop the event."""
    entries = parse_transcript(CODEX_LOG)
    assert len(entries) == 2
    assert entries[0].kind == "thread.started"
    assert "thread_id" in entries[0].text
    assert entries[1].kind == "turn.completed"


def test_event_with_no_type_field_has_no_kind() -> None:
    entries = parse_transcript(json.dumps({"result": "done"}))
    assert entries[0].kind is None
    assert entries[0].text == "done"


def test_survives_prose_mixed_with_json() -> None:
    noisy = f"bash: warning: setlocale failed\n{CLAUDE_RESULT_EVENT}\n"
    entries = parse_transcript(noisy)
    assert len(entries) == 1
    assert entries[0].text == "Added the missing import."


def test_truncates_a_very_long_raw_fallback() -> None:
    huge = json.dumps({"type": "huge", "data": "x" * 2000})
    entries = parse_transcript(huge)
    assert len(entries[0].text) <= 500
