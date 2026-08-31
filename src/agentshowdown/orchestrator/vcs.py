"""Git / GitHub glue -- runs on the host."""

from __future__ import annotations

import subprocess

from agentshowdown.orchestrator.process import run


def git(repo_path: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return run(["git", "-C", repo_path, *args], check=check)


def fetch_and_push_branch(repo_path: str, sandbox_name: str, branch: str) -> None:
    """
    sbx exposes each sandbox's git history as a remote named
    'sandbox-<name>' on the host repo. We fetch it, then push that ref to
    origin under the same branch name.
    """
    remote = f"sandbox-{sandbox_name}"
    git(repo_path, "fetch", remote)
    git(
        repo_path,
        "push",
        "origin",
        f"refs/remotes/{remote}/{branch}:refs/heads/{branch}",
    )


def open_pr(
    repo_path: str, github_repo: str, branch: str, base: str, title: str, body: str
) -> str:
    """
    Requires the `gh` CLI to be authenticated (e.g. via GH_TOKEN env var set
    from the same PAT stored in sbx secrets). Returns the PR URL.
    """
    result = run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            github_repo,
            "--base",
            base,
            "--head",
            branch,
            "--title",
            title,
            "--body",
            body,
        ],
        capture=True,
    )
    return result.stdout.strip().splitlines()[-1]
