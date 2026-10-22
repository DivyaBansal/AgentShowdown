"""Prompt construction for the agent run."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.orchestrator.config import Feature

# How many answer options an awaiting_input agent may offer. Stated in the
# prompt and enforced again when the status file is read (job.py), since an
# agent may not follow the instruction.
MAX_QUESTION_OPTIONS = 6


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
    criteria = "\n".join(f"- {c}" for c in feature.acceptance_criteria) or "(none specified)"
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
     exact question. If the question has a small set of discrete answers,
     also include them as "options": a list of short strings (at most
     {MAX_QUESTION_OPTIONS}), e.g. "options": ["Keep the old API", "Break it"].
     Then stop working immediately; do not guess and continue. You will be
     given the human's answer in a follow-up turn and should continue from
     there.
   - "stuck": you cannot complete the feature and further attempts won't
     help (a hard blocker, contradictory requirements, etc). Set
     "message" to why. Commit whatever partial progress you made first,
     with an explanatory commit message.
   - "done": you have completely finished (all acceptance criteria met,
     changes committed). Set "message" to a short summary.

   Write this file exactly once, as the last thing you do before ending
   your turn.
"""


def build_continue_prompt(status_file: str) -> str:
    """Builds the follow-up prompt used to restart a stopped job in place.

    Unlike `build_resume_prompt`, there is no human answer to relay -- the
    sandbox was simply paused (via `sbx stop`) and is being restarted to
    continue from exactly where it left off, with no new input.

    Args:
        status_file: Workspace-relative path the agent must write its
            status JSON to.

    Returns:
        The prompt string, to be passed alongside --continue/--resume.
    """
    return f"""Continue implementing the feature from exactly where you left off.

When you are next completely finished (or if you become permanently stuck),
update "{status_file}" following the same status-file contract as before
(overwrite it with a fresh {{"state": ..., "message": ...}} JSON object).
"""


def build_followup_prompt(instruction: str, status_file: str) -> str:
    """Builds the prompt for a follow-up a human sent to a job unprompted.

    Distinct from `build_resume_prompt`: the agent didn't ask anything, so
    telling it "a human answered your question" would be false and could
    send it looking for a question it never asked.

    Args:
        instruction: What the human wants done next.
        status_file: Workspace-relative path the agent must write its
            status JSON to.

    Returns:
        The prompt string, to be passed alongside --continue/--resume.
    """
    return f"""A human has a follow-up instruction for the work you did on this feature:

{instruction}

Carry it out on the same branch, committing as you go. When you are next
completely finished (or if you become permanently stuck, or need to ask
something), update "{status_file}" following the same status-file contract
as before (overwrite it with a fresh {{"state": ..., "message": ...}} JSON
object).
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
