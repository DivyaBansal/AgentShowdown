"""Tests for backend.orchestrator.vcs."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from backend.orchestrator import vcs

if TYPE_CHECKING:
    import pytest


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_unpushed_commit_count_returns_the_count(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **_k: object) -> subprocess.CompletedProcess:
        if "fetch" in cmd:
            return _completed()
        if "rev-list" in cmd:
            return _completed("3\n")
        return _completed()

    monkeypatch.setattr(vcs, "run", fake_run)

    assert vcs.unpushed_commit_count("/repo", "main", "agent/f1/claude", "box-1") == 3


def test_unpushed_commit_count_compares_against_origin_base_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], **_k: object) -> subprocess.CompletedProcess:
        seen.append(cmd)
        return _completed("0\n")

    monkeypatch.setattr(vcs, "run", fake_run)
    vcs.unpushed_commit_count("/repo", "main", "agent/f1/claude", "box-1")

    rev_list_cmd = next(c for c in seen if "rev-list" in c)
    assert rev_list_cmd[-1] == "origin/main..refs/remotes/sandbox-box-1/agent/f1/claude"


def test_unpushed_commit_count_zero_when_nothing_ahead(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vcs, "run", lambda cmd, **k: _completed("0\n"))
    assert vcs.unpushed_commit_count("/repo", "main", "agent/f1/claude", "box-1") == 0


def test_unpushed_commit_count_fetch_failure_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """A gone sandbox or network hiccup must not block a delete."""

    def fake_run(cmd: list[str], **_k: object) -> subprocess.CompletedProcess:
        if "fetch" in cmd:
            return _completed(returncode=1)
        return _completed("99\n")

    monkeypatch.setattr(vcs, "run", fake_run)

    assert vcs.unpushed_commit_count("/repo", "main", "agent/f1/claude", "box-1") == 0


def test_unpushed_commit_count_rev_list_failure_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd: list[str], **_k: object) -> subprocess.CompletedProcess:
        if "rev-list" in cmd:
            return _completed(returncode=1)
        return _completed()

    monkeypatch.setattr(vcs, "run", fake_run)

    assert vcs.unpushed_commit_count("/repo", "main", "agent/f1/claude", "box-1") == 0


def test_unpushed_commit_count_non_numeric_output_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd: list[str], **_k: object) -> subprocess.CompletedProcess:
        if "rev-list" in cmd:
            return _completed("not a number\n")
        return _completed()

    monkeypatch.setattr(vcs, "run", fake_run)

    assert vcs.unpushed_commit_count("/repo", "main", "agent/f1/claude", "box-1") == 0
