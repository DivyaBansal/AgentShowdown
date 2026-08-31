"""What a job is allowed to cost the sandbox it runs in.

These tests exist to make "lightweight" checkable rather than aspirational.
They drive a whole job through the real code path with only the single
subprocess primitive stubbed, then assert on the exact `sbx` commands that
were issued.

The distinction they enforce:

* ``sbx ls --json`` is host-side -- it spawns nothing inside the sandbox, so
  liveness may be polled as often as we like.
* ``sbx exec`` runs a process *inside* the container. Every one of these
  costs the agent something, so the count is pinned here and any new call
  has to be justified by changing this test on purpose.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agentshowdown.orchestrator import job as job_module
from agentshowdown.orchestrator import sbx as sbx_module
from agentshowdown.orchestrator import vcs as vcs_module
from agentshowdown.orchestrator.config import AgentSpec, Config, Feature
from agentshowdown.orchestrator.state import StateStore

LS_JSON = json.dumps(
    {"sandboxes": [{"name": "arena-f1-claude", "agent": "claude", "status": "running"}]}
)


def _config(tmp_path: Path, **overrides: object) -> Config:
    defaults: dict[str, object] = {
        "repo_path": "/repo",
        "github_repo": "owner/repo",
        "github_pat_secret_name": "github",
        "base_branch": "main",
        "sbx_agent": "claude",
        "model": "claude-haiku-4-5",
        "dangerously_skip_permissions": True,
        "max_turns": None,
        "status_file": ".agent_status.json",
        "poll_interval_seconds": 0,
        "liveness_interval_seconds": 0,
        "timeout_minutes": 60,
        "max_concurrency": 1,
        "test_command": "pytest",
        "lint_command": "",
        "remove_sandbox_on_success": True,
        "remove_sandbox_on_failure": True,
        "state_db_path": str(tmp_path / "state.sqlite3"),
    }
    return Config(**{**defaults, **overrides})  # type: ignore[arg-type]


def _ok(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


@pytest.fixture
def sbx_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Records every sbx command and returns plausible canned output."""
    calls: list[list[str]] = []
    created = False

    def fake_run(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess:
        nonlocal created
        calls.append(cmd)
        joined = " ".join(cmd)
        if len(cmd) > 1 and cmd[1] == "create":
            created = True
            return _ok()
        if "ls" in cmd and "--json" in cmd:
            # The sandbox only appears once it has actually been created,
            # so run_job takes the launch path rather than reattaching.
            return _ok(LS_JSON if created else '{"sandboxes": []}')
        if ".agent_status.json" in joined and "cat" in joined:
            return _ok('{"state": "done", "message": "finished"}')
        if "rev-parse" in joined:
            return _ok("agent/f1/claude\n---agentshowdown-log---\n")
        return _ok()

    monkeypatch.setattr(sbx_module, "run", fake_run)
    # Host-side git/gh, recorded separately so it can't be confused with
    # anything running in the sandbox.
    monkeypatch.setattr(vcs_module, "run", lambda cmd, **kw: _ok("3\t1\ta.py\n"))
    return calls


def _run_one_job(config: Config) -> None:
    feature = Feature(id="f1", description="d", acceptance_criteria=[], agents=[])
    job_module.run_job(config, feature, AgentSpec(agent_id="claude"))


def _execs(calls: list[list[str]]) -> list[list[str]]:
    return [c for c in calls if len(c) > 1 and c[0] == "sbx" and c[1] == "exec"]


def test_default_job_makes_exactly_three_sandbox_execs(
    tmp_path: Path, sbx_calls: list[list[str]]
) -> None:
    """Launch the agent, read its status once, extract once at teardown.

    Anything beyond these three is new cost inside the container.
    """
    _run_one_job(_config(tmp_path))

    execs = _execs(sbx_calls)
    assert len(execs) == 3, [" ".join(c) for c in execs]

    joined = [" ".join(c) for c in execs]
    # The launch command also mentions the status file (its fallback wrapper
    # writes one if the agent dies), so match the actual read, not the path.
    assert sum("-d" in c.split() for c in joined) == 1  # launching the agent
    assert sum("cat .agent_status.json" in c for c in joined) == 1  # one status read
    assert sum("rev-parse" in c for c in joined) == 1  # one bundled teardown read


def test_verification_does_not_run_in_the_sandbox_by_default(
    tmp_path: Path, sbx_calls: list[list[str]]
) -> None:
    """verify_on defaults to host, so the test command must never appear
    in an sbx exec."""
    _run_one_job(_config(tmp_path, test_command="pytest -q"))

    assert not any("pytest" in " ".join(c) for c in _execs(sbx_calls))


def test_verify_on_sandbox_costs_one_extra_exec(tmp_path: Path, sbx_calls: list[list[str]]) -> None:
    """The opt-in path is allowed to cost more -- and visibly does."""
    _run_one_job(_config(tmp_path, verify_on="sandbox", test_command="pytest -q"))

    execs = _execs(sbx_calls)
    assert len(execs) == 4
    assert any("pytest" in " ".join(c) for c in execs)


def test_liveness_polling_uses_the_host_side_listing(
    tmp_path: Path, sbx_calls: list[list[str]]
) -> None:
    _run_one_job(_config(tmp_path))

    ls_calls = [c for c in sbx_calls if len(c) > 1 and c[1] == "ls"]
    assert ls_calls, "liveness should be polled"
    # Never the tabular form, which was parsed by column position.
    assert all("--json" in c for c in ls_calls)


def test_status_read_avoids_the_login_shell(tmp_path: Path, sbx_calls: list[list[str]]) -> None:
    """A login shell sources the whole profile; the polled read must not."""
    _run_one_job(_config(tmp_path))

    for call in _execs(sbx_calls):
        joined = " ".join(call)
        # The agent launch legitimately needs a login shell for PATH; the
        # polled reads do not.
        if "cat .agent_status.json" in joined or "rev-parse" in joined:
            assert "-lc" not in call, joined
            assert "-c" in call


def test_sandbox_is_removed_once_the_job_is_done(
    tmp_path: Path, sbx_calls: list[list[str]]
) -> None:
    _run_one_job(_config(tmp_path))

    rm_calls = [c for c in sbx_calls if len(c) > 1 and c[1] == "rm"]
    assert rm_calls, "sandbox should be torn down"
    # --force is mandatory: the confirmation prompt is invisible under
    # captured stdout and would hang the worker thread forever.
    assert all("--force" in c for c in rm_calls)


def test_no_pr_is_opened_by_default(tmp_path: Path, sbx_calls: list[list[str]]) -> None:
    """open_pr defaults off, so a UI-triggered run can't touch a real repo."""
    gh_calls: list[list[str]] = []
    config = _config(tmp_path)
    _run_one_job(config)

    with StateStore(config.state_db_path) as store:
        job = store.get("arena-f1-claude")
    assert job is not None
    assert job["status"] == "succeeded"
    assert job["pr_url"] is None
    assert gh_calls == []
