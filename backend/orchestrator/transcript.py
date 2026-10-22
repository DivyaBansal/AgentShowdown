"""Best-effort structured view of an agent's JSON output.

This makes no attempt to fully model any agent CLI's event schema -- unlike
``metrics.parse_usage``, which only ever extracts a few known int fields and
so is robust to a shape it has never seen, a transcript has to show
*something* for events this code doesn't recognize. The design is: every
JSON object found in the log becomes one entry, tagged with whatever its own
``type`` field says (passed through verbatim, not mapped onto an invented
taxonomy), with a best-effort human-readable summary that falls back to the
raw JSON rather than silently dropping an event.

Agents that emit no JSON at all (``cursor``, ``opencode``, ``copilot``, or a
custom ``command:``) simply yield an empty list -- callers fall back to the
raw log for those, the same way ``metrics.py`` falls back to empty usage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from backend.orchestrator.metrics import iter_json_objects

# Keys checked, in order, for a short human-readable summary of one event.
# Claude's own shape nests text under "message": {"content": [...]}; the
# rest are common enough across these CLIs to be worth checking generically.
_TEXT_KEYS = ("message", "text", "content", "result", "summary")

# How much of the raw JSON to fall back to when no recognizable text field
# is found -- enough to be useful, not enough to flood the UI with one giant
# unparsed event.
_RAW_FALLBACK_CHARS = 500


@dataclass(frozen=True)
class TranscriptEntry:
    """One event from an agent's structured run output.

    Attributes:
        index: Position in the log, 0-based -- stable even though nothing
            else about an event is guaranteed.
        kind: Whatever the event's own "type" field said (e.g. claude's
            "assistant"/"result", codex's event type), or None if it had
            none. Never translated into a different vocabulary.
        text: The best human-readable summary found, or a truncated raw
            JSON rendering when nothing recognizable was found.
    """

    index: int
    kind: str | None
    text: str


def _text_from_content(content: object) -> str | None:
    """Extracts text from a "content" value shaped like Claude's own.

    Args:
        content: Either a plain string, or a list of content blocks
            (``{"type": "text", "text": "..."}`` and others this ignores).

    Returns:
        The joined text, or None if nothing usable was found.
    """
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        texts = (block.get("text") for block in content if isinstance(block, dict))
        parts = [text.strip() for text in texts if isinstance(text, str) and text.strip()]
        if parts:
            return " ".join(parts)
    return None


def _extract_text(obj: dict) -> str:
    """Best-effort human-readable summary of one parsed JSON event.

    Args:
        obj: One decoded JSON object from the log.

    Returns:
        The first recognizable text found under `_TEXT_KEYS`, checking one
        level of nesting for a Claude-style "content" shape. Falls back to
        a truncated raw JSON rendering so nothing is ever silently dropped.
    """
    for key in _TEXT_KEYS:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = _text_from_content(value.get("content"))
            if nested is not None:
                return nested
    return json.dumps(obj)[:_RAW_FALLBACK_CHARS]


def parse_transcript(log_text: str) -> list[TranscriptEntry]:
    """Parses an agent's run log into a best-effort sequence of events.

    Args:
        log_text: Raw agent stdout/stderr -- the same text `metrics.py`
            reads, and the same text a job's live tail is drawn from, so
            this works equally on a finished run's full log or a running
            job's in-progress tail.

    Returns:
        One entry per JSON object found, in the order it appeared. Empty
        when the log has no JSON in it at all.
    """
    return [
        TranscriptEntry(
            index=index,
            kind=obj.get("type") if isinstance(obj.get("type"), str) else None,
            text=_extract_text(obj),
        )
        for index, obj in enumerate(iter_json_objects(log_text))
    ]
