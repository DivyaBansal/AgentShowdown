"""Tests for the workspace registry and config resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
from backend.api import settings, workspace
from backend.api.paths import jobs_db_path, registry_path
from backend.api.workspace import (
    WorkspaceRegistry,
    inspect,
    load_registry,
    parse_github_repo,
    save_registry,
    select,
)
from backend.orchestrator.process import CommandTimeout, run


def _git_repo(path: Path, *, origin: str | None = None) -> Path:
    """Creates a real git repo, since inspect() shells out to git."""
    path.mkdir(parents=True, exist_ok=True)
    run(["git", "-C", str(path), "init", "-q"])
    if origin is not None:
        run(["git", "-C", str(path), "remote", "add", "origin", origin])
    return path


# --- registry ---------------------------------------------------------------


def test_registry_round_trips(tmp_path: Path) -> None:
    save_registry(
        WorkspaceRegistry(
            active="/repos/one",
            workspaces=[workspace.WorkspaceEntry(path="/repos/one", github_repo="o/one")],
        )
    )

    loaded = load_registry()

    assert loaded.active == "/repos/one"
    assert loaded.workspaces[0].github_repo == "o/one"


def test_saving_the_registry_leaves_no_temp_file() -> None:
    save_registry(WorkspaceRegistry(active=None))

    assert not registry_path().with_name("workspaces.json.tmp").exists()


def test_a_missing_registry_reads_as_empty() -> None:
    assert load_registry().workspaces == []


def test_a_corrupt_registry_reads_as_empty_rather_than_raising() -> None:
    """The UI must come up in order to show that something is wrong."""
    registry_path().parent.mkdir(parents=True, exist_ok=True)
    registry_path().write_text("{not json at all")

    assert load_registry().workspaces == []


def test_re_registering_the_same_path_does_not_duplicate_it(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")

    select(repo)
    select(repo)

    assert len(load_registry().workspaces) == 1


# --- inspection -------------------------------------------------------------


def test_inspect_reports_a_missing_directory(tmp_path: Path) -> None:
    info = inspect(tmp_path / "nope")

    assert not info.exists
    assert not info.is_git_repo


def test_inspect_reports_a_plain_directory_as_not_a_repo(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    info = inspect(plain)

    assert info.exists
    assert not info.is_git_repo


def test_inspect_finds_the_origin_and_github_repo(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo", origin="https://github.com/owner/app.git")

    info = inspect(repo)

    assert info.is_git_repo
    assert info.origin_url == "https://github.com/owner/app.git"
    assert info.github_repo == "owner/app"


def test_inspect_detects_existing_agentshowdown_files(tmp_path: Path) -> None:
    """This is what lets the UI prefill instead of starting blank."""
    repo = _git_repo(tmp_path / "repo")
    config_dir = repo / ".agentshowdown"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("repo_path: .")

    info = inspect(repo)

    assert info.has_config
    assert not info.has_features


def test_inspect_treats_a_hung_git_command_as_unavailable_info(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stuck git process must not hang the HTTP handler this feeds."""
    repo = _git_repo(tmp_path / "repo", origin="https://github.com/owner/app.git")

    def _timed_out(*_args: object, **_kwargs: object) -> None:
        raise CommandTimeout("Command timed out after 10.0s: git ...")

    monkeypatch.setattr(workspace, "run", _timed_out)

    info = inspect(repo)

    assert info.exists
    assert info.is_git_repo
    assert info.origin_url is None
    assert info.default_branch is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/owner/repo", "owner/repo"),
        ("https://github.com/owner/repo.git", "owner/repo"),
        ("git@github.com:owner/repo.git", "owner/repo"),
        ("ssh://git@github.com/owner/repo.git", "owner/repo"),
        ("https://gitlab.com/owner/repo.git", None),
        ("not a url", None),
    ],
)
def test_parse_github_repo(url: str, expected: str | None) -> None:
    assert parse_github_repo(url) == expected


# --- resolution order -------------------------------------------------------


def test_config_path_prefers_the_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit override always wins, which is what pins tests and deploys."""
    repo = _git_repo(tmp_path / "repo")
    select(repo)
    monkeypatch.setenv(settings.CONFIG_ENV, "/explicit/config.yaml")

    assert settings.config_path() == Path("/explicit/config.yaml")
    assert settings.config_source() == "env"


def test_config_path_follows_the_active_workspace(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    select(repo)

    assert settings.config_path() == repo / ".agentshowdown" / "config.yaml"
    assert settings.features_path() == repo / ".agentshowdown" / "features.yaml"
    assert settings.config_source() == "workspace"


def test_config_path_falls_back_to_the_bundled_demo() -> None:
    assert settings.config_path() == Path(settings.DEFAULT_CONFIG)
    assert settings.config_source() == "demo"


def test_load_config_pins_the_shared_jobs_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One database across every workspace, whatever a workspace's file says."""
    config = tmp_path / "config.yaml"
    config.write_text(
        "repo_path: /tmp/repo\n"
        "github: {repo: o/r, pat_secret_name: github, base_branch: main}\n"
        "agent: {sbx_agent: claude, model: claude-haiku-4-5,"
        " dangerously_skip_permissions: true}\n"
        "run: {poll_interval_seconds: 5, timeout_minutes: 60}\n"
        'state_db_path: "/somewhere/else/ignored.sqlite3"\n'
    )
    monkeypatch.setenv(settings.CONFIG_ENV, str(config))

    assert settings.load_config().state_db_path == str(jobs_db_path())
