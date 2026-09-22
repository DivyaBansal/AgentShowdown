"""Validated scalar types for the inputs this API newly accepts.

Pointing the app at a repository means taking a filesystem path and a git
URL from an HTTP client and acting on both on the host. These types are the
boundary, so the checks live here rather than as ad hoc `if`s in handlers.

The git URL rules are the sharp end. `git clone` treats several URL forms as
instructions to execute a program:

- `ext::sh -c '<command>'` runs that command (a remote helper by design).
- A URL beginning with `-` is parsed by git as a *flag*, so
  `--upload-pack=/bin/sh` becomes argument injection.
- `file://` and local paths reach the filesystem rather than the network.

The call site also passes `--` before the URL and disables the ext/file
protocols, so these validators are the readable error rather than the only
defense.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import AfterValidator

from backend.api.paths import WORKSPACE_ROOT_ENV

ALLOWED_CLONE_HOSTS_ENV = "AGENTSHOWDOWN_ALLOWED_CLONE_HOSTS"
DEFAULT_CLONE_HOSTS = ("github.com",)

# Directories that are never a source checkout. Refusing them turns a
# fat-fingered path into a clear 422 instead of a confusing failure later.
_FORBIDDEN_ROOTS = ("/proc", "/sys", "/dev", "/boot", "/run", "/etc")

# owner/repo, optionally with a .git suffix. Anything else in the path is
# either a subpath (not clonable) or an attempt to smuggle something.
# Anything below U+0020, plus DEL, is a control character.
_FIRST_PRINTABLE = 0x20
_DELETE = 0x7F

_REPO_PATH_RE = re.compile(r"^/[\w.-]+/[\w.-]+?(\.git)?$")
_SCP_LIKE_RE = re.compile(
    r"^(?P<user>[\w.-]+)@(?P<host>[\w.-]+):(?P<path>[\w.-]+/[\w.-]+?)(\.git)?$"
)


def _reject_control_characters(value: str, label: str) -> None:
    """Raises if `value` holds a NUL or other control character.

    A NUL becomes an opaque ValueError deep inside `os`; other control
    characters corrupt log lines and terminal output.
    """
    if "\x00" in value:
        raise ValueError(f"{label} must not contain a null byte")
    if any(ord(ch) < _FIRST_PRINTABLE or ord(ch) == _DELETE for ch in value):
        raise ValueError(f"{label} must not contain control characters")


def validate_local_repo_path(value: str) -> str:
    """Canonicalizes a host directory path, rejecting obviously wrong ones.

    Returns the *resolved* path so `..` is dissolved rather than
    pattern-matched, and so everything downstream -- the registry, the
    recorded `repo` column, `sbx create` -- sees one canonical spelling.

    Existence is deliberately not checked here: a path that does not exist
    is a 404 from the handler, which is state rather than malformed input.

    Args:
        value: The submitted path.

    Returns:
        The resolved absolute path.

    Raises:
        ValueError: If the path is empty, malformed, or somewhere no source
            checkout belongs.
    """
    if not value.strip():
        raise ValueError("path must not be empty")
    _reject_control_characters(value, "path")

    resolved = Path(value).expanduser().resolve()

    for forbidden in _FORBIDDEN_ROOTS:
        root = Path(forbidden)
        if resolved == root or root in resolved.parents:
            raise ValueError(f"{forbidden} is not a valid workspace location")

    # Optional confinement for anything not a single-user localhost tool.
    confine = os.environ.get(WORKSPACE_ROOT_ENV)
    if confine:
        allowed = Path(confine).expanduser().resolve()
        if not resolved.is_relative_to(allowed):
            raise ValueError(f"path must be inside {allowed}")

    return str(resolved)


def _allowed_clone_hosts() -> tuple[str, ...]:
    """Returns the hosts a repository may be cloned from."""
    configured = os.environ.get(ALLOWED_CLONE_HOSTS_ENV)
    if not configured:
        return DEFAULT_CLONE_HOSTS
    return tuple(h.strip().lower() for h in configured.split(",") if h.strip())


def validate_git_clone_url(value: str) -> str:
    """Accepts only an https or scp-like SSH URL on an allowed host.

    Args:
        value: The submitted clone URL.

    Returns:
        The stripped URL.

    Raises:
        ValueError: For anything that is not a plain remote repository URL
            on an allowed host.
    """
    url = value.strip()
    if not url:
        raise ValueError("url must not be empty")
    _reject_control_characters(url, "url")
    if any(ch.isspace() for ch in url):
        raise ValueError("url must not contain whitespace")

    # git parses a leading "-" as a flag, e.g. --upload-pack=/bin/sh.
    if url.startswith("-"):
        raise ValueError("url must not start with '-'")

    hosts = _allowed_clone_hosts()

    scp = _SCP_LIKE_RE.match(url)
    if scp:
        if scp.group("host").lower() not in hosts:
            raise ValueError(f"url host must be one of: {', '.join(hosts)}")
        return url

    if "://" not in url:
        raise ValueError("url must be an https:// or git@host:owner/repo URL")

    parts = urlsplit(url)
    if parts.scheme != "https":
        # Names the dangerous ones explicitly so the error teaches.
        raise ValueError(
            f"unsupported url scheme '{parts.scheme}'. "
            "Only https:// (or git@host:owner/repo) is allowed"
        )
    if parts.password is not None or parts.username is not None:
        raise ValueError("url must not embed credentials")
    if (parts.hostname or "").lower() not in hosts:
        raise ValueError(f"url host must be one of: {', '.join(hosts)}")
    if not _REPO_PATH_RE.match(parts.path):
        raise ValueError("url path must be /owner/repo")
    if parts.query or parts.fragment:
        raise ValueError("url must not carry a query string or fragment")

    return url


def validate_directory_name(value: str) -> str:
    """Validates a single path component used as a clone directory name."""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", value):
        raise ValueError("directory_name must be 1-64 chars of [A-Za-z0-9._-]")
    if value in {".", ".."}:
        raise ValueError("directory_name must be a real name")
    return value


LocalRepoPath = Annotated[str, AfterValidator(validate_local_repo_path)]
GitCloneUrl = Annotated[str, AfterValidator(validate_git_clone_url)]
DirectoryName = Annotated[str, AfterValidator(validate_directory_name)]
