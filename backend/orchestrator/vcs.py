"""Git / GitHub glue -- runs on the host."""

from __future__ import annotations

import pathlib
import shlex
import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import subprocess

from backend.orchestrator.process import RunOptions, run


def git(repo_path: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return run(["git", "-C", repo_path, *args], options=RunOptions(check=check))


def sandbox_ref(sandbox_name: str, branch: str) -> str:
    """Returns the host-side ref holding a sandbox's copy of `branch`."""
    return f"refs/remotes/sandbox-{sandbox_name}/{branch}"


def fetch_branch(repo_path: str, sandbox_name: str) -> None:
    """Fetches a sandbox's git history onto the host.

    sbx exposes each sandbox's history as a remote named `sandbox-<name>` on
    the host repo. Fetching is separate from pushing so the work can be
    verified on the host *before* anything reaches origin.

    Args:
        repo_path: Host repo to fetch into.
        sandbox_name: Sandbox whose remote to fetch.
    """
    git(repo_path, "fetch", f"sandbox-{sandbox_name}")


def push_branch(repo_path: str, sandbox_name: str, branch: str) -> None:
    """Pushes an already-fetched sandbox branch to origin.

    Args:
        repo_path: Host repo to push from.
        sandbox_name: Sandbox the branch came from.
        branch: Branch name to publish under.
    """
    git(
        repo_path,
        "push",
        "origin",
        f"{sandbox_ref(sandbox_name, branch)}:refs/heads/{branch}",
    )


def diff_numstat(repo_path: str, base_branch: str, ref: str) -> str:
    """Returns `git diff --numstat` between origin's base and a fetched ref.

    Runs entirely on the host against refs already fetched, so diff size is
    available for every agent and costs the sandbox nothing.

    Args:
        repo_path: Host repo holding both refs.
        base_branch: The base to compare against (e.g. "main").
        ref: The sandbox ref to measure.

    Returns:
        Raw numstat output, or "" if the refs can't be compared (e.g. no
        common ancestor yet) -- missing metrics must never fail a job.
    """
    result = git(
        repo_path,
        "diff",
        "--numstat",
        f"origin/{base_branch}...{ref}",
        check=False,
    )
    return result.stdout if result.returncode == 0 else ""


def run_in_worktree(repo_path: str, ref: str, command: str) -> subprocess.CompletedProcess:
    """Runs a shell command against `ref` in a throwaway worktree.

    This is what `verify_on: host` uses: the agent's branch is checked out
    into a temp worktree and the test/lint command runs there, so nothing
    extra executes inside the sandbox and the sandbox can be torn down
    first.

    Args:
        repo_path: Host repo to create the worktree from.
        ref: Ref to check out.
        command: Shell command to run inside the worktree.

    Returns:
        The completed process. The worktree is always removed afterward,
        even if the command fails.
    """
    with tempfile.TemporaryDirectory(prefix="agentshowdown-verify-") as tmp:
        worktree = str(pathlib.Path(tmp) / "wt")
        git(repo_path, "worktree", "add", "--detach", worktree, ref)
        try:
            return run(
                ["bash", "-c", f"cd {shlex.quote(worktree)} && {command}"],
                options=RunOptions(check=False),
            )
        finally:
            git(repo_path, "worktree", "remove", "--force", worktree, check=False)


def open_pr(
    repo_path: str,
    github_repo: str,
    branch: str,
    base: str,
    *,
    title: str,
    body: str,
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
    )
    return result.stdout.strip().splitlines()[-1]
