"""Manual catalog discovery, shared deadlines, and saved-only HTTP reads."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from backend import model_catalog as models
from backend.api import routes_models
from backend.api.paths import home_dir
from backend.api.workspace import select
from backend.main import app
from backend.orchestrator.config import validate_model
from backend.orchestrator.process import CommandError, CommandTimeout, run_bounded
from fastapi.testclient import TestClient

client = TestClient(app)


def entry(names=None, source="cli"):
    return {"models": names or ["new-model"], "source": source, "updated_at": models.now()}


def finish(manager, refresh_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        status = manager.status(refresh_id)
        if status["state"] == "finished":
            return status
        time.sleep(0.005)
    pytest.fail("Background update did not finish")


@pytest.mark.parametrize("payload", [None, [], {"codex": {"models": "bad"}}, "bad"])
def test_invalid_catalogs_fall_back(payload):
    (home_dir() / "models.json").write_text(json.dumps(payload))
    assert models.catalog("codex", ["builtin-model"])["models"] == ["builtin-model"]


def test_parse_codex_filters_hidden_and_deduplicates():
    assert models.parse_codex_models(
        {
            "models": [
                {"slug": "new-model", "visibility": "list"},
                {"slug": "hidden-model", "visibility": "hide"},
                {"slug": "new-model"},
                {"slug": ""},
                {"slug": 4},
                None,
            ]
        }
    ) == ["new-model"]
    with pytest.raises(ValueError, match="Unrecognized"):
        models.parse_codex_models({"models": "bad"})


@pytest.mark.parametrize(
    ("provider", "output", "expected"),
    [
        ("cursor", "Available models\n\x1b[32mgpt-new\x1b[0m - Fast\nauto\n", ["gpt-new", "auto"]),
        (
            "opencode",
            "Models\nopenai/gpt-new\ncustom/x\nopenai/gpt-new",
            ["openai/gpt-new", "custom/x"],
        ),
        ("ollama", "NAME ID SIZE MODIFIED\nqwen:32b abc 20GB now\n", ["qwen:32b"]),
        (
            "copilot",
            '--model MODEL\n  [choices: "gpt-new", "claude-new"]',
            ["gpt-new", "claude-new"],
        ),
    ],
)
def test_cli_parsers(provider, output, expected):
    assert models.parse_cli_models(provider, output) == expected


def test_copilot_without_choices_is_explicitly_unavailable():
    with pytest.raises(ValueError, match="does not advertise"):
        models.parse_cli_models("copilot", '--model MODEL\n--mode MODE [choices: "plan"]')


def test_codex_cache_is_only_used_during_explicit_discovery(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "models_cache.json").write_text(json.dumps({"models": [{"slug": "cache-model"}]}))

    def unavailable(*args):
        raise CommandError("old CLI")

    monkeypatch.setattr(models, "command", unavailable)
    assert models.catalog("codex")["models"] == []
    assert models.codex_models(time.monotonic() + 3) == (["cache-model"], "cache")


def test_claude_pagination_and_deadline(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calls = []

    def respond(request):
        calls.append(request)
        more = len(calls) == 1
        return httpx.Response(
            200,
            json={"data": [{"id": f"claude-{len(calls)}"}], "has_more": more, "last_id": "page-1"},
        )

    factory = httpx.Client
    monkeypatch.setattr(
        models.httpx, "Client", lambda: factory(transport=httpx.MockTransport(respond))
    )
    assert models.claude_models(time.monotonic() + 2) == ["claude-1", "claude-2"]
    assert calls[1].url.params["after_id"] == "page-1"
    with pytest.raises(CommandTimeout):
        models.remaining(time.monotonic() - 1)


def test_adapter_commands_and_missing_install(monkeypatch):
    calls = []
    monkeypatch.setattr(models, "find_cli", lambda name: None if name == "cursor-agent" else name)

    def execute(cmd, timeout):
        calls.append((cmd, timeout))
        return subprocess.CompletedProcess(cmd, 0, "gpt-new\n", "")

    monkeypatch.setattr(models, "run_bounded", execute)
    assert models.discover("cursor", time.monotonic() + 3)["models"] == ["gpt-new"]
    assert calls[0][0] == ["agent", "models"]
    assert 0 < calls[0][1] <= 3
    monkeypatch.setattr(models, "find_cli", lambda name: None)
    with pytest.raises(CommandError, match="not installed"):
        models.command("opencode", ["models"], time.monotonic() + 2)


def test_partial_refresh_persists_success_and_retains_failure(monkeypatch):
    models.CatalogUpdates._save(home_dir(), {"claude": entry(["old-claude"])})
    manager = models.CatalogUpdates()
    deadlines = []

    def discover(provider, deadline):
        deadlines.append(deadline)
        if provider == "claude":
            raise ValueError("secret credentials must never be exposed")
        return entry([f"new-{provider}"])

    monkeypatch.setattr(models, "discover", discover)
    status = finish(manager, manager.start()["refresh_id"])
    assert len(set(deadlines)) == 1
    assert status["results"]["claude"]["status"] == "failed"
    assert "secret credentials" not in json.dumps(status)
    assert models.catalog("claude")["models"] == ["old-claude"]
    assert models.catalog("codex")["models"] == ["new-codex"]
    assert models.CatalogUpdates().status() is None  # saved catalogs survive manager restarts
    assert models.catalog("codex")["source"] == "cli"


def test_duplicate_requests_share_one_background_update(monkeypatch):
    manager = models.CatalogUpdates()
    release = threading.Event()

    def discover(provider, deadline):
        release.wait(2)
        return entry()

    monkeypatch.setattr(models, "discover", discover)
    first = manager.start()
    try:
        assert first["state"] == "running"
        assert manager.start()["refresh_id"] == first["refresh_id"]
    finally:
        release.set()
    finish(manager, first["refresh_id"])


def test_shared_deadline_keeps_partial_success(monkeypatch):
    manager = models.CatalogUpdates()
    job = models.Refresh("deadline-test", models.now())
    # Controllable clock: every worker sees the same deadline, without a ten-minute wait.
    monkeypatch.setattr(models.time, "monotonic", lambda: 100)

    def discover(provider, deadline):
        if provider == "codex":
            return entry()
        raise CommandTimeout("deadline")

    monkeypatch.setattr(models, "discover", discover)
    manager._execute(job, home_dir(), 101)
    assert job.state == "finished"
    assert job.results["codex"]["status"] == "updated"
    assert job.results["claude"]["status"] == "timed_out"
    assert models.catalog("codex")["models"] == ["new-model"]


def test_supervisor_stops_waiting_at_deadline(monkeypatch):
    release = threading.Event()
    manager = models.CatalogUpdates()
    job = models.Refresh("timeout", models.now())

    def stalled(provider, deadline):
        release.wait(2)
        return entry()

    monkeypatch.setattr(models, "discover", stalled)
    try:
        manager._execute(job, home_dir(), time.monotonic() + 0.05)
        assert job.state == "finished"
        assert all(result["status"] == "timed_out" for result in job.results.values())
        assert not (home_dir() / "models.json").exists()
    finally:
        release.set()


def test_deadline_kills_child_processes(tmp_path):
    pidfile = tmp_path / "child.pid"
    script = (
        "import os,time,pathlib; child=os.fork(); "
        f"pathlib.Path({str(pidfile)!r}).write_text(str(child)) if child else None; "
        "time.sleep(60)"
    )
    started = time.monotonic()
    with pytest.raises(CommandTimeout):
        run_bounded([sys.executable, "-c", script], 0.5)
    assert time.monotonic() - started < 3
    child = int(pidfile.read_text())
    statefile = Path(f"/proc/{child}/stat")
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            if statefile.read_text().split()[2] == "Z":
                return
        except (FileNotFoundError, ProcessLookupError):
            return  # The child was already reaped, including during this read.
        time.sleep(0.005)
    pytest.fail("Discovery child survived its process-group timeout")


def test_bounded_command_failure():
    with pytest.raises(CommandError):
        run_bounded([sys.executable, "-c", "raise SystemExit(1)"], 2)


def test_http_reads_never_discover_and_use_shared_saved_models(monkeypatch):
    def forbidden(*args):
        pytest.fail("Model discovery must be explicit")

    monkeypatch.setattr(models, "discover", forbidden)
    monkeypatch.setattr(models, "command", forbidden)
    models.CatalogUpdates._save(home_dir(), {"codex": entry(["gpt-new"])})
    agents = client.get("/agents").json()["agents"]
    assert next(a for a in agents if a["agent_id"] == "codex")["known_models"] == ["gpt-new"]
    assert client.get("/models").json()["catalogs"]["codex"]["models"] == ["gpt-new"]
    assert client.get("/ollama/models").status_code == 200
    client.get("/ask/providers")
    validate_model("codex", "gpt-new")
    validate_model("codex", "manually-entered-model")


def test_refresh_api_requires_editable_workspace_and_returns_progress(tmp_path, monkeypatch):
    manager = models.CatalogUpdates()
    monkeypatch.setattr(routes_models, "updates", manager)
    monkeypatch.setattr(models, "discover", lambda provider, deadline: entry())
    assert client.post("/models/refresh").status_code == 409
    repo = tmp_path / "repo"
    repo.mkdir()
    select(repo)
    monkeypatch.setenv("AGENTSHOWDOWN_CONFIG", "demo/langlearn/config.yaml")
    assert client.post("/models/refresh").status_code == 409
    monkeypatch.delenv("AGENTSHOWDOWN_CONFIG")
    response = client.post("/models/refresh")
    assert response.status_code == 202
    refresh_id = response.json()["refresh_id"]
    finish(manager, refresh_id)
    assert client.get(f"/models/refresh/{refresh_id}").json()["state"] == "finished"
    assert client.get("/models/refresh/unknown").status_code == 404


def test_catalog_save_failure_is_reported_and_retains_previous_file(monkeypatch):
    manager = models.CatalogUpdates()
    models.CatalogUpdates._save(home_dir(), {"codex": entry(["old-model"])})
    monkeypatch.setattr(models, "discover", lambda provider, deadline: entry())

    def failed_save(*args):
        raise OSError("read-only directory")

    monkeypatch.setattr(manager, "_save", failed_save)
    status = finish(manager, manager.start()["refresh_id"])
    assert "Could not save" in status["error"]
    assert models.catalog("codex")["models"] == ["old-model"]
