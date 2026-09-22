"""The set of repositories agentshowdown knows about, and which one is active.

A *workspace* is a git repository on the host, optionally carrying its own
`.agentshowdown/config.yaml` and `features.yaml`. The pointer to the active
one cannot live inside a workspace (nothing would say which to read), so it
lives in a small JSON registry under `AGENTSHOWDOWN_HOME`.

The registry is parsed through Pydantic models: it is a file a user can hand
edit, which makes it external input.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, ValidationError

from backend.api.paths import registry_path, workspace_config_dir
from backend.logging import log
from backend.orchestrator.process import CommandError, CommandTimeout, RunOptions, run

if TYPE_CHECKING:
    from pathlib import Path


class WorkspaceEntry(BaseModel):
    """One repository agentshowdown has been pointed at."""

    path: str = Field(min_length=1, max_length=4096)
    origin_url: str | None = Field(default=None, max_length=2048)
    github_repo: str | None = Field(default=None, max_length=256)
    cloned: bool = False


class WorkspaceRegistry(BaseModel):
    """Every known workspace, plus which one is selected."""

    active: str | None = Field(default=None, max_length=4096)
    workspaces: list[WorkspaceEntry] = Field(default_factory=list, max_length=256)


class WorkspaceInfo(BaseModel):
    """What a directory turns out to be, without committing to using it."""

    path: str
    exists: bool
    is_git_repo: bool
    origin_url: str | None = None
    github_repo: str | None = None
    default_branch: str | None = None
    has_config: bool = False
    has_features: bool = False


def load_registry() -> WorkspaceRegistry:
    """Reads the registry, tolerating a missing or damaged file.

    A corrupt registry returns an empty one rather than raising: the UI has
    to come up in order to show that something is wrong, and refusing to
    start would leave no way to fix it.
    """
    path = registry_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return WorkspaceRegistry()
    except OSError as exc:
        log.warning("workspace_registry_unreadable", path=str(path), error=str(exc))
        return WorkspaceRegistry()
    try:
        return WorkspaceRegistry.model_validate_json(raw)
    except ValidationError as exc:
        log.warning("workspace_registry_invalid", path=str(path), errors=exc.error_count())
        return WorkspaceRegistry()


def save_registry(registry: WorkspaceRegistry) -> None:
    """Writes the registry atomically.

    A half-written registry would lose every known workspace, so the new
    content is staged beside the target and renamed into place.
    """
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(registry.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, path)


def active_workspace() -> WorkspaceEntry | None:
    """Returns the selected workspace, or None when none is selected."""
    registry = load_registry()
    if registry.active is None:
        return None
    for entry in registry.workspaces:
        if entry.path == registry.active:
            return entry
    return None


# These are read-only plumbing commands against a local repo -- they should
# return in milliseconds. Bounding them keeps `inspect()` (called from an
# HTTP handler while the UI shows a spinner) from hanging forever if the
# repo sits on a stalled network mount or git is stuck on a credential
# prompt it can't display headlessly.
_GIT_TIMEOUT_SECONDS = 10.0


def _git_stdout(repo_path: Path, *args: str) -> str | None:
    """Runs a read-only git command, returning None when it fails or hangs."""
    try:
        result = run(
            ["git", "-C", str(repo_path), *args],
            options=RunOptions(check=False, timeout=_GIT_TIMEOUT_SECONDS),
        )
    except CommandTimeout:
        log.warning("git_command_timed_out", repo_path=str(repo_path), args=args)
        return None
    except (OSError, CommandError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


# A GitHub repo reference is exactly "owner/repo" -- two path segments.
_GITHUB_REPO_SEGMENTS = 2


def parse_github_repo(origin_url: str) -> str | None:
    """Extracts `owner/repo` from a GitHub remote URL.

    Handles every form git itself writes: https, scp-like SSH
    (`git@github.com:owner/repo`), and full ssh:// URLs.

    Args:
        origin_url: A remote URL.

    Returns:
        "owner/repo", or None if the URL is not a recognizable GitHub one.
    """
    url = origin_url.strip()
    if url.endswith(".git"):
        url = url[: -len(".git")]

    # Strip any scheme, then any userinfo ("git@"), leaving host[:/]path.
    _, _, remainder = url.rpartition("://")
    remainder = remainder or url
    remainder = remainder.split("@", 1)[-1]

    if not remainder.startswith("github.com"):
        return None
    # The scp-like form separates host from path with ":", the URL form "/".
    path = remainder[len("github.com") :].lstrip(":/")

    parts = [p for p in path.split("/") if p]
    if len(parts) != _GITHUB_REPO_SEGMENTS:
        return None
    return f"{parts[0]}/{parts[1]}"


def inspect(path: Path) -> WorkspaceInfo:
    """Reports what a directory is, without registering or selecting it.

    Looking at a folder is a separate step from using it, so this never
    mutates the registry. It is what lets the UI prefill its forms and
    decide whether to offer GitHub issue import.

    Args:
        path: Directory to examine.

    Returns:
        What was found. Missing pieces are reported, not raised.
    """
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        return WorkspaceInfo(path=str(resolved), exists=False, is_git_repo=False)

    is_git = (resolved / ".git").exists()
    origin = _git_stdout(resolved, "remote", "get-url", "origin") if is_git else None
    head = (
        _git_stdout(resolved, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
        if is_git
        else None
    )
    default_branch = head.split("/", 1)[-1] if head else None
    config_dir = workspace_config_dir(resolved)

    return WorkspaceInfo(
        path=str(resolved),
        exists=True,
        is_git_repo=is_git,
        origin_url=origin,
        github_repo=parse_github_repo(origin) if origin else None,
        default_branch=default_branch,
        has_config=(config_dir / "config.yaml").is_file(),
        has_features=(config_dir / "features.yaml").is_file(),
    )


def register(info: WorkspaceInfo, *, cloned: bool = False, select: bool = True) -> WorkspaceEntry:
    """Adds a workspace to the registry, optionally making it active.

    Re-registering a known path updates it in place rather than adding a
    duplicate, so repeatedly picking the same repo stays idempotent.

    Args:
        info: The inspected directory to record.
        cloned: Whether agentshowdown created this checkout itself.
        select: Whether to make it the active workspace.

    Returns:
        The stored entry.
    """
    registry = load_registry()
    entry = WorkspaceEntry(
        path=info.path,
        origin_url=info.origin_url,
        github_repo=info.github_repo,
        cloned=cloned,
    )
    registry.workspaces = [w for w in registry.workspaces if w.path != info.path]
    registry.workspaces.insert(0, entry)
    if select:
        registry.active = info.path
    save_registry(registry)
    log.info(
        "workspace_registered",
        path=info.path,
        is_git_repo=info.is_git_repo,
        github_repo=info.github_repo,
        cloned=cloned,
        selected=select,
    )
    return entry


def select(path: Path) -> WorkspaceEntry:
    """Makes `path` the active workspace, registering it if needed.

    Args:
        path: Repository to select.

    Returns:
        The stored entry.

    Raises:
        FileNotFoundError: If the directory does not exist.
    """
    info = inspect(path)
    if not info.exists:
        raise FileNotFoundError(f"No such directory: {info.path}")
    entry = register(info, select=True)
    log.info("workspace_selected", path=info.path, is_git_repo=info.is_git_repo)
    return entry
