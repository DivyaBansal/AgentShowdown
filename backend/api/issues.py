"""Drafting features from GitHub issues.

Reads through the `gh` CLI rather than the REST API directly, so it reuses
whatever authentication the user already has (`gh auth login`) and this app
never handles a GitHub token.
"""

from __future__ import annotations

import json
import re
import shutil

from backend.logging import log
from backend.orchestrator.process import RunOptions, run

# GitHub task-list syntax: "- [ ] do the thing" / "- [x] done".
_TASK_LINE = re.compile(r"^\s*[-*]\s*\[[ xX]\]\s+(?P<text>.+?)\s*$")


class GithubUnavailableError(RuntimeError):
    """Raised when `gh` is missing or not authenticated."""


def gh_available() -> bool:
    """Returns whether the gh CLI is on PATH."""
    return shutil.which("gh") is not None


def acceptance_criteria_from_body(body: str) -> list[str]:
    """Lifts a GitHub task list out of an issue body.

    An issue's checklist is the closest thing it has to acceptance
    criteria, so it maps across directly. Issues without one simply get an
    empty list rather than a guess.

    Args:
        body: The raw issue body.

    Returns:
        One entry per task-list line, in order.
    """
    if not body:
        return []
    return [m.group("text") for line in body.splitlines() if (m := _TASK_LINE.match(line))]


def feature_id_for_issue(number: int) -> str:
    """Returns the deterministic feature id for an issue.

    Deterministic on purpose: re-importing an issue then updates the same
    feature rather than adding a near-duplicate. A slugified title would
    also drift whenever the issue is renamed, and could produce characters
    the feature id pattern rejects.
    """
    return f"issue-{number}"


def list_issues(
    repo: str,
    *,
    state: str = "open",
    limit: int = 30,
    labels: str | None = None,
) -> list[dict[str, object]]:
    """Lists issues for a repository.

    Args:
        repo: "owner/repo".
        state: open, closed or all.
        limit: Maximum issues to return.
        labels: Optional comma-separated label filter.

    Returns:
        The issues, newest first as gh returns them.

    Raises:
        GithubUnavailableError: If gh is missing, unauthenticated, or errors.
    """
    if not gh_available():
        raise GithubUnavailableError("gh is not installed or not on PATH")

    argv = [
        "gh",
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        state,
        "--limit",
        str(limit),
        "--json",
        "number,title,body,labels,state,url",
    ]
    if labels:
        argv += ["--label", labels]

    result = run(argv, options=RunOptions(check=False))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "gh issue list failed").strip()
        log.warning("github_issue_list_failed", repo=repo, detail=detail[-500:])
        raise GithubUnavailableError(detail[-500:])

    try:
        parsed = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise GithubUnavailableError(f"could not parse gh output: {exc}") from exc
    if not isinstance(parsed, list):
        raise GithubUnavailableError("unexpected gh output")
    return parsed
