"""Prompt construction for the agent run."""

from __future__ import annotations

from agentshowdown.orchestrator.config import Feature


def build_prompt(feature: Feature, branch: str, status_file: str) -> str:
    """Builds the initial prompt for a fresh agent run.

    Args:
        feature: The feature to implement.
        branch: Git branch the agent must create and work on.
        status_file: Workspace-relative path the agent must write its
            status JSON to.

    Returns:
        The prompt string.
    """
    criteria = (
        "\n".join(f"- {c}" for c in feature.acceptance_criteria) or "(none specified)"
    )
    return f"""You are implementing a single feature in this repository.

Feature: {feature.description}

Acceptance criteria:
{criteria}

Instructions:
1. Create and check out a new git branch named exactly: {branch}
2. Implement the feature on that branch, with commits as you go.
3. Communicate your status by writing a single JSON object to the file
   "{status_file}" in the repository root, OVERWRITING it (not appending)
   whenever your state changes. Shape:

     {{"state": "<state>", "message": "<short human-readable text>"}}

   Allowed "state" values:
   - "awaiting_input": you are blocked on a decision only a human can
     make (an ambiguous requirement, a missing credential, a genuine
     design choice) -- NOT for problems you could resolve yourself by
     reading more code or trying another approach. Set "message" to the
     exact question. Then stop working immediately; do not guess and
     continue. You will be given the human's answer in a follow-up turn
     and should continue from there.
   - "stuck": you cannot complete the feature and further attempts won't
     help (a hard blocker, contradictory requirements, etc). Set
     "message" to why. Commit whatever partial progress you made first,
     with an explanatory commit message.
   - "done": you have completely finished (all acceptance criteria met,
     changes committed). Set "message" to a short summary.

   Write this file exactly once, as the last thing you do before ending
   your turn.
"""


def build_resume_prompt(human_answer: str, status_file: str) -> str:
    """Builds the follow-up prompt used to resume a stuck agent.

    Args:
        human_answer: The human's answer to the agent's question.
        status_file: Workspace-relative path the agent must write its
            status JSON to.

    Returns:
        The prompt string, to be passed alongside --continue.
    """
    return f"""A human has answered the question you asked earlier:

{human_answer}

Continue the feature implementation using this answer. When you are next
completely finished (or if you become permanently stuck again), update
"{status_file}" following the same status-file contract as before
(overwrite it with a fresh {{"state": ..., "message": ...}} JSON object).
"""
