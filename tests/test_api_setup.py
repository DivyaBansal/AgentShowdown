"""Tests for the repository picker and the config/features editors."""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING

from backend.api import settings
from backend.api.workspace import select
from backend.main import app
from backend.orchestrator.config import Config, Feature
from backend.orchestrator.process import run
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    import pytest

client = TestClient(app)


def _git_repo(path: Path, *, origin: str | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    run(["git", "-C", str(path), "init", "-q"])
    if origin is not None:
        run(["git", "-C", str(path), "remote", "add", "origin", origin])
    return path


# --- workspaces -------------------------------------------------------------


def test_inspect_reports_a_repo(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "app", origin="https://github.com/owner/app.git")

    body = client.post("/workspaces/inspect", json={"path": str(repo)}).json()

    assert body["is_git_repo"]
    assert body["github_repo"] == "owner/app"
    assert not body["has_config"]


def test_inspect_does_not_select_the_workspace(tmp_path: Path) -> None:
    """Looking at a folder must not change which one is in use."""
    repo = _git_repo(tmp_path / "app")

    client.post("/workspaces/inspect", json={"path": str(repo)})

    assert client.get("/workspaces").json()["active"] is None


def test_inspect_404s_on_a_missing_directory(tmp_path: Path) -> None:
    response = client.post("/workspaces/inspect", json={"path": str(tmp_path / "nope")})

    assert response.status_code == HTTPStatus.NOT_FOUND


def test_inspect_rejects_a_dangerous_path() -> None:
    response = client.post("/workspaces/inspect", json={"path": "/proc/self"})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT


def test_select_then_features_follow_the_workspace(tmp_path: Path) -> None:
    """Selecting a repo repoints the whole app at its own files."""
    repo = _git_repo(tmp_path / "app")
    config_dir = repo / ".agentshowdown"
    config_dir.mkdir()
    (config_dir / "features.yaml").write_text(
        "features:\n  - id: from-workspace\n    description: hi\n"
    )

    client.post("/workspaces/select", json={"path": str(repo)})
    ids = [f["id"] for f in client.get("/features").json()["features"]]

    assert ids == ["from-workspace"]


# --- config -----------------------------------------------------------------


def test_get_config_returns_a_draft_when_none_is_saved(tmp_path: Path) -> None:
    """A freshly picked repo has no config -- that is when the form opens."""
    repo = _git_repo(tmp_path / "app", origin="https://github.com/owner/app.git")
    select(repo)

    body = client.get("/config").json()

    assert body["exists"] is False
    assert body["config"]["github"]["repo"] == "owner/app"
    assert body["repo_path"] == str(repo)


def test_put_config_round_trips_through_the_workspace_file(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "app")
    select(repo)
    payload = {
        "github": {"repo": "owner/app", "pat_secret_name": "github", "base_branch": "main"},
        "agent": {"sbx_agent": "claude", "model": "claude-haiku-4-5"},
        "run": {"test_command": "pytest", "verify_on": "sandbox"},
        "agents": {"claude": {"model": "claude-sonnet-5", "env": {"A": "b"}}},
    }

    assert client.put("/config", json=payload).status_code == HTTPStatus.OK

    saved = Config.load(str(repo / ".agentshowdown" / "config.yaml"))
    assert saved.github_repo == "owner/app"
    assert saved.test_command == "pytest"
    assert saved.agent_profiles["claude"].env == {"A": "b"}
    # repo_path is stamped server-side, never taken from the request.
    assert saved.repo_path == str(repo)


def test_put_config_without_a_workspace_is_a_conflict() -> None:
    payload = {
        "github": {"repo": "o/r"},
        "agent": {"sbx_agent": "claude", "model": "claude-haiku-4-5"},
    }

    assert client.put("/config", json=payload).status_code == HTTPStatus.CONFLICT


def test_put_config_rejects_an_unknown_verify_on(tmp_path: Path) -> None:
    select(_git_repo(tmp_path / "app"))
    payload = {
        "github": {"repo": "o/r"},
        "agent": {"sbx_agent": "claude", "model": "claude-haiku-4-5"},
        "run": {"verify_on": "moon"},
    }

    assert client.put("/config", json=payload).status_code == HTTPStatus.UNPROCESSABLE_CONTENT


def test_the_bundled_demo_preset_is_never_written_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It is checked-in documentation; saving over it dirties a fresh clone."""
    select(_git_repo(tmp_path / "app"))
    demo = settings.package_root() / "demo" / "langlearn" / "config.yaml"
    before = demo.read_bytes()
    monkeypatch.setenv(settings.CONFIG_ENV, str(demo))

    response = client.put(
        "/config",
        json={
            "github": {"repo": "o/r"},
            "agent": {"sbx_agent": "claude", "model": "claude-haiku-4-5"},
        },
    )

    assert response.status_code == HTTPStatus.CONFLICT
    assert demo.read_bytes() == before


# --- features ---------------------------------------------------------------


def test_put_features_round_trips_a_full_agent_spec(tmp_path: Path) -> None:
    """The whole point of widening AgentSpecIn: env and command must persist."""
    repo = _git_repo(tmp_path / "app")
    select(repo)
    payload = {
        "features": [
            {
                "id": "add-verbs",
                "description": "Add verb practice.",
                "acceptance_criteria": ["it works"],
                "agents": [
                    {
                        "agent_id": "claude",
                        "run_label": "a",
                        "model": "qwen2.5-coder:32b",
                        "kit": ["./kit"],
                        "provider": "ollama",
                        "env": {"ANTHROPIC_BASE_URL": "http://x:11434"},
                    }
                ],
            }
        ]
    }

    assert client.put("/features", json=payload).status_code == HTTPStatus.OK

    saved = Feature.load_all(str(repo / ".agentshowdown" / "features.yaml"))
    spec = saved["add-verbs"].agents[0]
    assert spec.env == {"ANTHROPIC_BASE_URL": "http://x:11434"}
    assert spec.kit == ["./kit"]
    assert spec.provider == "ollama"


def test_put_features_rejects_duplicate_ids(tmp_path: Path) -> None:
    """Duplicates would silently vanish, since features are keyed by id."""
    select(_git_repo(tmp_path / "app"))
    payload = {
        "features": [
            {"id": "same", "description": "one"},
            {"id": "same", "description": "two"},
        ]
    }

    assert client.put("/features", json=payload).status_code == HTTPStatus.UNPROCESSABLE_CONTENT


def test_put_features_rejects_an_id_with_spaces(tmp_path: Path) -> None:
    select(_git_repo(tmp_path / "app"))
    payload = {"features": [{"id": "has spaces", "description": "x"}]}

    assert client.put("/features", json=payload).status_code == HTTPStatus.UNPROCESSABLE_CONTENT


def test_get_features_returns_the_full_agent_spec(tmp_path: Path) -> None:
    """The setup form prefills from this payload.

    A narrower one would round-trip a feature's env/kit/command into oblivion
    the first time anyone saved from the UI.
    """
    repo = _git_repo(tmp_path / "app")
    select(repo)
    config_dir = repo / ".agentshowdown"
    config_dir.mkdir()
    (config_dir / "features.yaml").write_text(
        "features:\n"
        "  - id: f1\n"
        "    description: hi\n"
        "    agents:\n"
        "      - agent_id: claude\n"
        "        kit: ['./kit']\n"
        "        provider: ollama\n"
        "        command: run-it\n"
        "        env:\n"
        "          ANTHROPIC_BASE_URL: http://x:11434\n"
    )

    agent = client.get("/features").json()["features"][0]["agents"][0]

    assert agent["env"] == {"ANTHROPIC_BASE_URL": "http://x:11434"}
    assert agent["kit"] == ["./kit"]
    assert agent["provider"] == "ollama"
    assert agent["command"] == "run-it"
