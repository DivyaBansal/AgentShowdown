"""Per-job token usage, parsed back out of an agent's run log.

Token counts are the only metric that has to come *out of* the sandbox --
timing, diff size and test/lint outcome are all measured on the host, so they
work for every agent and cost the sandbox nothing.

Coverage is deliberately partial, and callers must not paper over that:

* ``claude`` -- ``--print --output-format json`` emits a final JSON object
  carrying ``usage`` and ``num_turns``. Flag confirmed against the CLI inside
  a real sandbox.
* ``codex`` -- ``exec --json`` emits JSONL events that carry token counts.
  Flag confirmed in ``codex exec --help`` on the installed binary.
* ``cursor``, ``opencode``, ``copilot`` -- expose no structured usage, so
  every field stays ``None``.

``None`` means "not reported" and must render as "no data", never as ``0``: a
false zero makes an agent look free in a cost or token comparison.

The parser deliberately searches for token fields wherever they appear rather
than matching a fixed event shape. These CLIs change their JSON between
releases, and a metrics gap is acceptable while a crashed job is not -- so
every function here is total and returns empty usage rather than raising.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# Agents whose CLI can emit machine-readable usage, and the flags that ask
# for it. Anything absent here simply reports no usage.
USAGE_CAPABLE_AGENTS = ("claude", "codex")

_INPUT_KEYS = ("input_tokens", "prompt_tokens", "input")
_OUTPUT_KEYS = ("output_tokens", "completion_tokens", "output")
_TURN_KEYS = ("num_turns", "turns")

# `git diff --numstat` emits: added \t removed \t path
_NUMSTAT_FIELDS = 3


@dataclass(frozen=True)
class Usage:
    """Token usage for one agent run. ``None`` means the agent didn't report it."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    num_turns: int | None = None

    @property
    def is_empty(self) -> bool:
        """Whether nothing at all was reported."""
        return self.input_tokens is None and self.output_tokens is None and self.num_turns is None


def _iter_json_objects(log_text: str) -> list[dict]:
    """Extracts every JSON object in a log, whether JSONL or one final blob.

    Args:
        log_text: Raw agent stdout/stderr.

    Returns:
        Each successfully parsed top-level JSON object, in the order it
        appeared. Non-JSON lines (an agent's prose, a stack trace, a shell
        warning) are skipped rather than treated as an error.
    """
    found: list[dict] = []
    for line in log_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            found.append(parsed)
    if found:
        return found
    # Not JSONL -- fall back to the last balanced {...} span, which is where
    # a single pretty-printed result object ends up.
    start = log_text.find("{")
    while start != -1:
        try:
            parsed = json.loads(log_text[start:])
        except json.JSONDecodeError:
            start = log_text.find("{", start + 1)
            continue
        return [parsed] if isinstance(parsed, dict) else []
    return []


def _collect_ints(obj: object, keys: tuple[str, ...], into: list[int]) -> None:
    """Walks a decoded JSON value, collecting int values under any of `keys`."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in keys and isinstance(value, int) and not isinstance(value, bool):
                into.append(value)
            else:
                _collect_ints(value, keys, into)
    elif isinstance(obj, list):
        for item in obj:
            _collect_ints(item, keys, into)


def _last_int(objects: list[dict], keys: tuple[str, ...]) -> int | None:
    """Returns the last int found under any of `keys`, or None.

    The last occurrence wins because these CLIs report running totals -- the
    final event carries the cumulative count for the whole run.
    """
    found: list[int] = []
    for obj in objects:
        _collect_ints(obj, keys, found)
    return found[-1] if found else None


def parse_usage(log_text: str, agent_id: str) -> Usage:
    """Parses token usage out of an agent's run log.

    Args:
        log_text: The captured contents of the agent's log.
        agent_id: Which agent produced it. Agents outside
            `USAGE_CAPABLE_AGENTS` always yield empty usage, so a caller
            never mistakes "this CLI can't report" for "this run used none".

    Returns:
        The parsed usage. Every field is None when nothing could be read;
        this function never raises.
    """
    if agent_id not in USAGE_CAPABLE_AGENTS or not log_text:
        return Usage()
    objects = _iter_json_objects(log_text)
    if not objects:
        return Usage()
    return Usage(
        input_tokens=_last_int(objects, _INPUT_KEYS),
        output_tokens=_last_int(objects, _OUTPUT_KEYS),
        num_turns=_last_int(objects, _TURN_KEYS),
    )


def parse_diff_numstat(numstat_output: str) -> tuple[int, int, int]:
    """Parses `git diff --numstat` into (files_changed, added, removed).

    Runs on the host against the fetched branch, so it works for every agent
    and costs the sandbox nothing. Binary files report "-" for both counts
    and contribute to the file count only.

    Args:
        numstat_output: Raw `git diff --numstat` stdout.

    Returns:
        (files_changed, lines_added, lines_removed).
    """
    files = added = removed = 0
    for line in numstat_output.splitlines():
        parts = line.split("\t")
        if len(parts) < _NUMSTAT_FIELDS:
            continue
        files += 1
        if parts[0].isdigit():
            added += int(parts[0])
        if parts[1].isdigit():
            removed += int(parts[1])
    return files, added, removed
