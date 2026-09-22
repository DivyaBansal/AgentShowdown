"""Tests for the HTTP layer.

Nothing here touches Docker or sbx: the orchestrator's subprocess primitive
is stubbed, so the whole API surface is exercised the way CI will run it.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import threading
from http import HTTPStatus
from pathlib import Path

import pytest
from backend.api import runner as runner_module
from backend.api import sandboxes as sandbox_ops
from backend.api.settings import CONFIG_ENV, FEATURES_ENV, load_config
from backend.main import app
from backend.orchestrator import sbx as sbx_module
from backend.orchestrator.state import StateStore
from fastapi.testclient import TestClient

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
  - id: f2
    description: Do the other thing.
    acceptance_criteria: ["it also works"]
    agents:
      - agent_id: claude
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
    # Claims outlive a request by design (they are released when the run
    # ends), and tests that stub the dispatcher never let a run end.
    runner_module._claims.clear()


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


# --- read endpoints ---------------------------------------------------------


def test_health_still_works() -> None:
    assert client.get("/health").json() == {"status": "ok"}


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
    assert agents["shell"]["requires_command"] is True
    # A command-based job never receives --model, so no model is offered.
    assert agents["shell"]["accepts_model"] is False
    assert agents["claude"]["requires_command"] is False
    # opencode takes the flag in the spec but its CLI ignores it.
    assert agents["opencode"]["supports_skip_permissions"] is False
    assert agents["claude"]["supports_skip_permissions"] is True
    # Only these two expose structured token usage.
    assert agents["claude"]["reports_token_usage"] is True
    assert agents["opencode"]["reports_token_usage"] is False


def test_features_endpoint_lists_the_configured_features() -> None:
    features = client.get("/features").json()["features"]
    assert [f["id"] for f in features] == ["f1", "f2"]
    assert len(features[0]["agents"]) == 2


# --- launching --------------------------------------------------------------


def _entry(feature_id: str = "f1", **agent: object) -> dict:
    """One request entry: a feature and a single agent on it."""
    return {"feature_id": feature_id, "agents": [{"agent_id": "claude", **agent}]}


def test_start_run_returns_a_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    submitted: list[object] = []
    monkeypatch.setattr(runner_module._dispatcher, "submit", lambda *a, **k: submitted.append(a))

    resp = client.post("/runs", json={"entries": [_entry()]})

    assert resp.status_code == HTTPStatus.OK
    assert [rid.startswith("run-") for rid in resp.json()["run_ids"]] == [True]
    assert len(submitted) == 1


def test_a_batch_starts_one_run_per_feature(monkeypatch: pytest.MonkeyPatch) -> None:
    """The point of the batch: several features, each with its own agents."""
    submitted: list[tuple] = []
    monkeypatch.setattr(runner_module._dispatcher, "submit", lambda *a, **k: submitted.append(a))

    resp = client.post(
        "/runs",
        json={
            "entries": [
                {"feature_id": "f1", "agents": [{"agent_id": "claude"}, {"agent_id": "codex"}]},
                {"feature_id": "f2", "agents": [{"agent_id": "claude"}]},
            ]
        },
    )

    assert resp.status_code == HTTPStatus.OK
    run_ids = resp.json()["run_ids"]
    assert len(run_ids) == 2

    runs = {r["run_id"]: r["feature_id"] for r in client.get("/runs").json()["runs"]}
    assert [runs[rid] for rid in run_ids] == ["f1", "f2"]
    # Each run was dispatched with only its own feature's jobs.
    assert [len(call[2]) for call in submitted] == [2, 1]


def test_a_batch_that_fails_validation_starts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bad row must not leave the good rows half-launched."""
    submitted: list[object] = []
    monkeypatch.setattr(runner_module._dispatcher, "submit", lambda *a, **k: submitted.append(a))

    resp = client.post(
        "/runs",
        json={"entries": [_entry("f1"), _entry("f2", model="gpt-4-turbo")]},
    )

    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert submitted == []
    assert client.get("/runs").json()["runs"] == []


def test_the_same_feature_twice_in_one_batch_is_rejected() -> None:
    """Two runs on one feature would race for the same sandboxes."""
    resp = client.post("/runs", json={"entries": [_entry("f1"), _entry("f1", run_label="b")]})

    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "only once" in resp.text


def test_relaunching_a_job_a_live_run_owns_is_a_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both runs would otherwise finalize and push the same sandbox."""
    monkeypatch.setattr(runner_module._dispatcher, "submit", lambda *a, **k: None)
    assert client.post("/runs", json={"entries": [_entry()]}).status_code == HTTPStatus.OK

    resp = client.post("/runs", json={"entries": [_entry()]})

    assert resp.status_code == HTTPStatus.CONFLICT
    assert "arena-f1-claude" in resp.json()["detail"]
    # Only the first run was ever recorded.
    assert len(client.get("/runs").json()["runs"]) == 1


def test_a_conflict_on_one_entry_starts_none_of_the_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clash on the last row must not leave the earlier rows running."""
    monkeypatch.setattr(runner_module._dispatcher, "submit", lambda *a, **k: None)
    assert client.post("/runs", json={"entries": [_entry("f2")]}).status_code == HTTPStatus.OK

    resp = client.post("/runs", json={"entries": [_entry("f1"), _entry("f2")]})

    assert resp.status_code == HTTPStatus.CONFLICT
    # Only the original run exists; f1 was never started.
    assert [r["feature_id"] for r in client.get("/runs").json()["runs"]] == ["f2"]


def test_a_run_label_runs_the_same_agent_alongside_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner_module._dispatcher, "submit", lambda *a, **k: None)
    assert client.post("/runs", json={"entries": [_entry()]}).status_code == HTTPStatus.OK

    resp = client.post("/runs", json={"entries": [_entry(run_label="b")]})

    assert resp.status_code == HTTPStatus.OK


def test_a_finished_run_releases_its_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    finished = threading.Event()
    monkeypatch.setattr(runner_module, "run_all", lambda *a, **k: None)
    monkeypatch.setattr(
        runner_module.bus,
        "publish",
        lambda event, **kw: finished.set() if event == "run_finished" else None,
    )
    assert client.post("/runs", json={"entries": [_entry()]}).status_code == HTTPStatus.OK
    assert finished.wait(timeout=10)

    # That run is over, so the same comparison can be launched again.
    assert client.post("/runs", json={"entries": [_entry()]}).status_code == HTTPStatus.OK


def test_a_second_run_does_not_wait_for_the_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs execute side by side; a queue of one would deadlock this test."""
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()

    def fake_run_all(config: object, jobs: list, *a: object, **k: object) -> None:
        if jobs[0][0].id == "f1":
            first_started.set()
            release_first.wait(timeout=10)
        else:
            second_started.set()

    monkeypatch.setattr(runner_module, "run_all", fake_run_all)
    try:
        assert client.post("/runs", json={"entries": [_entry("f1")]}).status_code == HTTPStatus.OK
        assert first_started.wait(timeout=10)

        assert client.post("/runs", json={"entries": [_entry("f2")]}).status_code == HTTPStatus.OK

        assert second_started.wait(timeout=10), "second run waited for the first to finish"
    finally:
        release_first.set()


def test_unsupported_agent_is_rejected() -> None:
    resp = client.post("/runs", json={"entries": [_entry(agent_id="gemini")]})
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_an_agent_without_a_launcher_needs_a_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """shell has no CLI of its own, so a command is the only way to run it."""
    monkeypatch.setattr(runner_module._dispatcher, "submit", lambda *a, **k: None)
    resp = client.post("/runs", json={"entries": [_entry(agent_id="shell")]})
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    resp = client.post(
        "/runs",
        json={"entries": [_entry(agent_id="shell", command="echo hi")]},
    )
    assert resp.status_code == HTTPStatus.OK


def test_unknown_feature_is_rejected_before_anything_is_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submitted: list[object] = []
    monkeypatch.setattr(runner_module._dispatcher, "submit", lambda *a, **k: submitted.append(a))

    resp = client.post("/runs", json={"entries": [_entry("nope")]})

    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert submitted == []  # nothing dispatched, so no sandbox exists


def test_unknown_model_is_rejected() -> None:
    resp = client.post("/runs", json={"entries": [_entry(model="gpt-4-turbo")]})
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_empty_agent_list_is_rejected_by_the_model() -> None:
    resp = client.post("/runs", json={"entries": [{"feature_id": "f1", "agents": []}]})
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_a_batch_with_no_entries_is_rejected() -> None:
    assert client.post("/runs", json={"entries": []}).status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_malformed_memory_limit_is_rejected() -> None:
    resp = client.post("/runs", json={"entries": [_entry()], "memory": "loads"})
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


def test_the_sampler_serves_every_concurrent_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """One run finishing must not stop sampling for the runs still going."""
    started: list[object] = []
    stopped: list[object] = []

    class FakeSampler:
        def __init__(self, config: object, on_sample: object = None) -> None:
            pass

        def start(self) -> None:
            started.append(self)

        def stop(self) -> None:
            stopped.append(self)

    monkeypatch.setattr(runner_module, "TelemetrySampler", FakeSampler)
    config = load_config()
    sampling = dataclasses.replace(config, telemetry=True)

    assert runner_module.start_sampler(sampling) is True
    assert runner_module.start_sampler(sampling) is True
    assert len(started) == 1  # one sampler serves both runs

    runner_module.stop_sampler()
    assert stopped == []  # the second run is still going

    runner_module.stop_sampler()
    assert len(stopped) == 1


def test_telemetry_off_means_no_sampler_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_module, "TelemetrySampler", _refuse_sampler)

    assert runner_module.start_sampler(load_config()) is False


def _refuse_sampler(*_a: object, **_k: object) -> None:
    raise AssertionError("no sampler should be created when telemetry is off")


def test_list_jobs_filters_by_repo() -> None:
    """The board narrows to one repo now that one database holds them all."""
    config = load_config()
    with StateStore(config.state_db_path) as store:
        common = {"feature_id": "f1", "agent_id": "claude", "status": "succeeded"}
        store.upsert("box-one", **common, repo="/repos/one")
        store.upsert("box-two", **common, repo="/repos/two")

    everything = client.get("/jobs").json()["jobs"]
    filtered = client.get("/jobs", params={"repo": "/repos/one"}).json()["jobs"]

    assert {j["sandbox_name"] for j in everything} >= {"box-one", "box-two"}
    assert [j["sandbox_name"] for j in filtered] == ["box-one"]


def test_list_jobs_rejects_an_over_long_repo_filter() -> None:
    """The filter is validated at the boundary like every other input."""
    response = client.get("/jobs", params={"repo": "x" * 5000})

    assert response.status_code == 422


def test_start_run_carries_the_full_agent_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    """env/command/kit must survive the API, or the UI cannot express them.

    Dropping `env` would silently disable custom model endpoints (a local
    Ollama, a gateway) for every run launched from the web UI.
    """
    captured: list = []
    monkeypatch.setattr(
        runner_module,
        "expand_jobs",
        lambda config, features, ids: captured.append(features) or [],
    )
    monkeypatch.setattr(runner_module._dispatcher, "submit", lambda *a, **kw: None)

    response = client.post(
        "/runs",
        json={
            "entries": [
                {
                    "feature_id": "f1",
                    "agents": [
                        {
                            "agent_id": "claude",
                            "run_label": "a",
                            "model": "qwen2.5-coder:32b",
                            "kit": ["./kits/auth"],
                            "provider": "ollama",
                            "env": {"ANTHROPIC_BASE_URL": "http://host.docker.internal:11434"},
                        }
                    ],
                }
            ]
        },
    )

    assert response.status_code == HTTPStatus.OK
    spec = captured[0]["f1"].agents[0]
    assert spec.env == {"ANTHROPIC_BASE_URL": "http://host.docker.internal:11434"}
    assert spec.kit == ["./kits/auth"]
    assert spec.provider == "ollama"
    assert spec.run_label == "a"
