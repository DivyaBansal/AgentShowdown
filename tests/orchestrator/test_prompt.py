"""Tests for backend.orchestrator.prompt."""

from __future__ import annotations

from backend.orchestrator.config import Feature
from backend.orchestrator.prompt import build_continue_prompt, build_prompt, build_resume_prompt


def test_build_prompt_includes_branch_and_status_file() -> None:
    feature = Feature(id="f1", description="Do the thing", acceptance_criteria=["works"], agents=[])

    prompt = build_prompt(feature, "agent/f1/claude", ".agent_status.json")

    assert "agent/f1/claude" in prompt
    assert ".agent_status.json" in prompt
    assert "Do the thing" in prompt
    assert "- works" in prompt
    assert "awaiting_input" in prompt
    assert "stuck" in prompt
    assert '"done"' in prompt


def test_build_prompt_no_acceptance_criteria_shows_placeholder() -> None:
    feature = Feature(id="f1", description="Do the thing", acceptance_criteria=[], agents=[])

    prompt = build_prompt(feature, "agent/f1/claude", ".agent_status.json")

    assert "(none specified)" in prompt


def test_build_resume_prompt_includes_answer_and_status_file() -> None:
    prompt = build_resume_prompt("use option B", ".agent_status.json")

    assert "use option B" in prompt
    assert ".agent_status.json" in prompt


def test_build_continue_prompt_includes_status_file_and_no_human_framing() -> None:
    prompt = build_continue_prompt(".agent_status.json")

    assert ".agent_status.json" in prompt
    assert "continue" in prompt.lower()
    # Unlike build_resume_prompt, there's no answer to relay.
    assert "human" not in prompt.lower()
    assert "answered" not in prompt.lower()
