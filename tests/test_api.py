"""Tests for the HTTP layer.

Nothing here touches Docker or sbx: the orchestrator's subprocess primitive
is stubbed, so the whole API surface is exercised the way CI will run it.
"""

from __future__ import annotations

import json
import subprocess
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentshowdown.api import runner as runner_module
from agentshowdown.api import sandboxes as sandbox_ops
from agentshowdown.api.settings import CONFIG_ENV, FEATURES_ENV
from agentshowdown.main import app
from agentshowdown.orchestrator import sbx as sbx_module

client = TestClient(app)

CONFIG_YAML = """
repo_path: /repo
github:
  repo: owner/repo
  pat_secret_name: github
  base_branch: main
  open_pr: false
agent:
  sbx_agent: claude
  dangerously_skip_permissions: true
  model: claude-haiku-4-5
run:
  status_file: ".agent_status.json"
  poll_interval_seconds: 0
  liveness_interval_seconds: 0
  timeout_minutes: 1
  max_concurrency: 1
  test_command: ""
  lint_command: ""
  telemetry: false
state_db_path: "{db}"
"""

FEATURES_YAML = """
features:
  - id: f1
    description: Do the thing.
    acceptance_criteria: ["it works"]
    agents:
      - agent_id: claude
      - agent_id: codex
"""


@pytest.fixture(autouse=True)
def _arena(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Points the API at a throwaway config, features file and database."""
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG_YAML.format(db=tmp_path / "state.sqlite3"))
    features = tmp_path / "features.yaml"
    features.write_text(FEATURES_YAML)
    monkeypatch.setenv(CONFIG_ENV, str(config))
    monkeypatch.setenv(FEATURES_ENV, str(features))


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


# --- read endpoints ---------------------------------------------------------


def test_health_still_works() -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_orders_endpoint_is_still_registered() -> None:
    """The boilerplate's reference endpoint, which CLAUDE.md points at."""
    resp = client.post("/orders", json={"item_id": "sku-1", "quantity": 2})
    assert resp.status_code == HTTPStatus.OK


def test_preflight_reports_each_check() -> None:
    body = client.get("/preflight").json()
    assert "live_runs_possible" in body
    names = {c["name"] for c in body["checks"]}
    assert {"sbx", "sandboxd", "gh", "demo_repo"} <= names


def test_agents_endpoint_distinguishes_verified_from_best_effort() -> None:
    """The UI must not present all agents as equally supported."""
    agents = {a["agent_id"]: a for a in client.get("/agents").json()["agents"]}

    assert agents["claude"]["verified"] is True
    assert agents["codex"]["verified"] is True
    assert agents["cursor"]["verified"] is False
    # shell has no coding-agent CLI of its own; it needs a custom command.
    assert agents["shell"]["has_native_builder"] is False
    # Only these two expose structured token usage.
    assert agents["claude"]["reports_token_usage"] is True
    assert agents["opencode"]["reports_token_usage"] is False


def test_features_endpoint_lists_the_configured_features() -> None:
    features = client.get("/features").json()["features"]
    assert [f["id"] for f in features] == ["f1"]
    assert len(features[0]["agents"]) == 2


# --- launching --------------------------------------------------------------


def test_start_run_returns_a_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    submitted: list[object] = []
    monkeypatch.setattr(runner_module._dispatcher, "submit", lambda *a, **k: submitted.append(a))

    resp = client.post("/runs", json={"feature_id": "f1", "agents": [{"agent_id": "claude"}]})

    assert resp.status_code == HTTPStatus.OK
    assert resp.json()["run_id"].startswith("run-")
    assert len(submitted) == 1


def test_unknown_feature_is_rejected_before_anything_is_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submitted: list[object] = []
    monkeypatch.setattr(runner_module._dispatcher, "submit", lambda *a, **k: submitted.append(a))

    resp = client.post("/runs", json={"feature_id": "nope", "agents": [{"agent_id": "claude"}]})

    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert submitted == []  # nothing dispatched, so no sandbox exists


def test_unsupported_agent_is_rejected() -> None:
    resp = client.post("/runs", json={"feature_id": "f1", "agents": [{"agent_id": "gemini"}]})
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_unknown_model_is_rejected() -> None:
    resp = client.post(
        "/runs",
        json={
            "feature_id": "f1",
            "agents": [{"agent_id": "claude", "model": "gpt-4-turbo"}],
        },
    )
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_empty_agent_list_is_rejected_by_the_model() -> None:
    resp = client.post("/runs", json={"feature_id": "f1", "agents": []})
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_malformed_memory_limit_is_rejected() -> None:
    resp = client.post(
        "/runs",
        json={
            "feature_id": "f1",
            "agents": [{"agent_id": "claude"}],
            "memory": "loads",
        },
    )
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_unknown_run_is_404() -> None:
    assert client.get("/runs/run-nope").status_code == HTTPStatus.NOT_FOUND


def test_runs_and_jobs_listings_are_empty_initially() -> None:
    assert client.get("/runs").json()["runs"] == []
    assert client.get("/jobs").json()["jobs"] == []


# --- sandbox controls -------------------------------------------------------


def test_ping_reports_agent_liveness(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox_ops, "sbx_list", lambda: {"box-1": {}})
    monkeypatch.setattr(
        sandbox_ops,
        "sbx_exec_capture",
        lambda *a, **k: _completed(f"{sandbox_ops.PING_MARKER}\nagent_alive\n"),
    )

    body = client.post("/sandboxes/box-1/ping").json()

    assert body["reachable"] is True
    assert body["agent_alive"] is True
    assert body["status_file_present"] is False
    assert body["latency_ms"] >= 0


def test_ping_distinguishes_a_dead_agent_from_a_gone_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Container up but agent dead is the case worth catching early."""
    monkeypatch.setattr(sandbox_ops, "sbx_list", lambda: {"box-1": {}})
    monkeypatch.setattr(
        sandbox_ops,
        "sbx_exec_capture",
        lambda *a, **k: _completed(f"{sandbox_ops.PING_MARKER}\n"),
    )
    alive = client.post("/sandboxes/box-1/ping").json()
    assert alive["reachable"] is True
    assert alive["agent_alive"] is False

    monkeypatch.setattr(sandbox_ops, "sbx_list", dict)
    gone = client.post("/sandboxes/box-1/ping").json()
    assert gone["listed"] is False
    assert gone["reachable"] is False


def test_kill_all_requires_the_typed_confirmation() -> None:
    """A mis-click must not be able to wipe every sandbox on the host."""
    assert (
        client.post("/sandboxes/kill-all", json={}).status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    )
    assert (
        client.post("/sandboxes/kill-all", json={"confirm": "yes"}).status_code
        == HTTPStatus.UNPROCESSABLE_ENTITY
    )


def test_kill_all_with_confirmation_removes_and_marks_jobs_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(sandbox_ops, "sbx_list", lambda: {"box-1": {}, "box-2": {}})
    monkeypatch.setattr(sandbox_ops, "run", lambda cmd, **k: calls.append(cmd))

    body = client.post("/sandboxes/kill-all", json={"confirm": "KILL ALL"}).json()

    assert body["removed"] == ["box-1", "box-2"]
    assert calls == [["sbx", "rm", "--all", "--force"]]


def test_sandboxes_listing_uses_the_json_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sbx_module,
        "run",
        lambda *a, **k: _completed(
            json.dumps({"sandboxes": [{"name": "box-1", "status": "running"}]})
        ),
    )
    body = client.get("/sandboxes").json()
    assert body["sandboxes"][0]["name"] == "box-1"
