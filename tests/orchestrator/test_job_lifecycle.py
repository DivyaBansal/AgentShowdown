"""Tests for backend.orchestrator.job.lifecycle."""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest
from backend.orchestrator.config import AgentProfile, AgentSpec, Config, Feature
from backend.orchestrator.job import expand as expand_module
from backend.orchestrator.job import finish as finish_module
from backend.orchestrator.job import lifecycle as lifecycle_module
from backend.orchestrator.process import CommandError
from backend.orchestrator.state import StateStore


@pytest.fixture(autouse=True)
def _stub_otel_sbx_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stubs the sbx calls made for agents that export OTel.

    `run_job` adds a network rule for the receiver, and finalizing waits for
    the agent process to exit so its last telemetry batch has landed.
    """
    monkeypatch.setattr(lifecycle_module, "sbx_allow_network", lambda name, resource: True)
    monkeypatch.setattr(finish_module, "sbx_agent_exited", lambda name: True)


def _config(tmp_path: Path, **overrides) -> Config:
    defaults = {
        "repo_path": "/tmp/repo",
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
        "max_concurrency": 3,
        "test_command": "",
        "lint_command": "",
        "remove_sandbox_on_success": True,
        "remove_sandbox_on_failure": False,
        "state_db_path": str(tmp_path / "state.sqlite3"),
        "provider": None,
        "agent_profiles": {},
    }
    defaults.update(overrides)
    return Config(**defaults)


def _feature(agents: list[AgentSpec] | None = None) -> Feature:
    return Feature(
        id="f1",
        description="do the thing",
        acceptance_criteria=["works"],
        agents=agents or [],
    )


def _job(store: StateStore, sandbox_name: str) -> dict:
    job = store.get(sandbox_name)
    assert job is not None
    return job


def _refuse(*_a, **_k):
    raise AssertionError("this should not have been called")


def _failing_process() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="boom", stderr="")


def _ok_process(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _stub_finalize_host_calls(
    monkeypatch: pytest.MonkeyPatch, calls: list[str], *, verify_ok: bool = True
) -> None:
    """Stubs everything _finalize_success touches outside the sandbox."""
    monkeypatch.setattr(finish_module, "fetch_branch", lambda *a: calls.append("fetch"))
    monkeypatch.setattr(finish_module, "push_branch", lambda *a: calls.append("push"))
    monkeypatch.setattr(finish_module, "diff_numstat", lambda *a: "3\t1\ta.py\n")
    monkeypatch.setattr(
        finish_module,
        "run_in_worktree",
        lambda *a: (
            calls.append("verify_host") or (_ok_process() if verify_ok else _failing_process())
        ),
    )
    monkeypatch.setattr(lifecycle_module, "sbx_rm", lambda name: calls.append("rm"))
    monkeypatch.setattr(finish_module, "sbx_rm", lambda name: calls.append("rm"))


def _stub_teardown_read(monkeypatch: pytest.MonkeyPatch, calls: list[str], log: str = ""):
    """Stubs the single sandbox read, recording that it happened."""

    def fake_exec(name, cmd, **kw):
        calls.append("sbx_exec")
        if "rev-parse" in cmd:
            return _ok_process(
                "/work/repo\n---agentshowdown-dir---\n"
                f"agent/f1/claude\n---agentshowdown-log---\n{log}"
            )
        return _ok_process()

    monkeypatch.setattr(finish_module, "sbx_exec_capture", fake_exec)


# --- run_job -------------------------------------------------------------------


def test_run_job_skips_when_awaiting_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")
    sandbox_name = expand_module.sandbox_name_for(feature.id, spec)
    store.upsert(
        sandbox_name,
        feature_id=feature.id,
        agent_id="claude",
        branch="b",
        status="awaiting_input",
    )

    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: True)
    monkeypatch.setattr(lifecycle_module, "sbx_create_detached", _refuse)
    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", _refuse)
    monkeypatch.setattr(finish_module, "poll_until_signal", _refuse)

    lifecycle_module.run_job(config, feature, spec)  # should return early, no error

    assert _job(store, sandbox_name)["status"] == "awaiting_input"


def test_run_job_skips_already_succeeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")
    sandbox_name = expand_module.sandbox_name_for(feature.id, spec)
    store.upsert(
        sandbox_name,
        feature_id=feature.id,
        agent_id="claude",
        branch="b",
        status="succeeded",
        pr_url="https://example.com/pr/1",
    )

    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: False)
    monkeypatch.setattr(lifecycle_module, "sbx_create_detached", _refuse)
    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", _refuse)
    monkeypatch.setattr(finish_module, "poll_until_signal", _refuse)

    lifecycle_module.run_job(config, feature, spec)  # should return early

    assert _job(store, sandbox_name)["status"] == "succeeded"


def test_run_job_full_success_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")

    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: False)
    monkeypatch.setattr(lifecycle_module, "sbx_create_detached", lambda **kw: None)
    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", lambda **kw: None)
    monkeypatch.setattr(finish_module, "poll_until_signal", lambda *a: "done")
    finalize_calls = []
    monkeypatch.setattr(
        finish_module, "_finalize_success", lambda *a, **kw: finalize_calls.append(a)
    )

    lifecycle_module.run_job(config, feature, spec)

    assert len(finalize_calls) == 1


def test_run_job_stopped_status_does_not_remove_the_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deliberate stop must leave the sandbox in place for respawn,
    unlike every other non-"done" outcome."""
    config = _config(tmp_path, remove_sandbox_on_failure=True)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")

    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: False)
    monkeypatch.setattr(lifecycle_module, "sbx_create_detached", lambda **kw: None)
    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", lambda **kw: None)
    monkeypatch.setattr(finish_module, "poll_until_signal", lambda *a: "stopped")
    rm_calls: list[str] = []
    monkeypatch.setattr(lifecycle_module, "sbx_rm", rm_calls.append)
    monkeypatch.setattr(finish_module, "sbx_rm", rm_calls.append)

    lifecycle_module.run_job(config, feature, spec)

    assert rm_calls == []


def test_run_job_reattaches_to_running_sandbox_without_relaunching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")
    sandbox_name = expand_module.sandbox_name_for(feature.id, spec)
    store.upsert(
        sandbox_name,
        feature_id=feature.id,
        agent_id="claude",
        branch="b",
        status="running",
    )

    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: True)
    monkeypatch.setattr(lifecycle_module, "sbx_create_detached", _refuse)
    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", _refuse)
    monkeypatch.setattr(finish_module, "poll_until_signal", lambda *a: "timed_out")
    monkeypatch.setattr(lifecycle_module, "sbx_rm", lambda name: None)
    monkeypatch.setattr(finish_module, "sbx_rm", lambda name: None)

    lifecycle_module.run_job(config, feature, spec)  # should not raise, must not relaunch


def test_run_job_removes_and_relaunches_after_previous_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")
    sandbox_name = expand_module.sandbox_name_for(feature.id, spec)
    store.upsert(
        sandbox_name,
        feature_id=feature.id,
        agent_id="claude",
        branch="b",
        status="crashed",
    )

    calls: list[str] = []
    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: True)
    monkeypatch.setattr(lifecycle_module, "sbx_rm", lambda name: calls.append("rm"))
    monkeypatch.setattr(finish_module, "sbx_rm", lambda name: calls.append("rm"))
    monkeypatch.setattr(
        lifecycle_module, "sbx_create_detached", lambda **kw: calls.append("create")
    )
    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", lambda **kw: calls.append("launch"))
    monkeypatch.setattr(finish_module, "poll_until_signal", lambda *a: "timed_out")

    lifecycle_module.run_job(config, feature, spec)

    assert calls == ["rm", "create", "launch"]


def test_run_job_resolves_model_and_kit_from_agent_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(
        tmp_path,
        agent_profiles={"claude": AgentProfile(model="profile-model", kit=["./kits/base"])},
    )
    feature = _feature()
    spec = AgentSpec(agent_id="claude", kit=["./kits/job-specific"])

    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: False)
    create_calls = []
    launch_calls = []
    monkeypatch.setattr(
        lifecycle_module, "sbx_create_detached", lambda **kw: create_calls.append(kw)
    )
    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", lambda **kw: launch_calls.append(kw))
    monkeypatch.setattr(finish_module, "poll_until_signal", lambda *a: "timed_out")

    lifecycle_module.run_job(config, feature, spec)

    assert create_calls[0]["kit"] == ["./kits/base", "./kits/job-specific"]
    assert create_calls[0]["model"] == "profile-model"
    assert launch_calls[0]["model_override"] == "profile-model"


def test_run_job_spec_model_overrides_agent_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, agent_profiles={"claude": AgentProfile(model="profile-model")})
    feature = _feature()
    spec = AgentSpec(agent_id="claude", model="spec-model")

    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: False)
    monkeypatch.setattr(lifecycle_module, "sbx_create_detached", lambda **kw: None)
    launch_calls = []
    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", lambda **kw: launch_calls.append(kw))
    monkeypatch.setattr(finish_module, "poll_until_signal", lambda *a: "timed_out")

    lifecycle_module.run_job(config, feature, spec)

    assert launch_calls[0]["model_override"] == "spec-model"


def test_run_job_merges_and_passes_resolved_env_to_sbx_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(
        tmp_path,
        agent_profiles={
            "claude": AgentProfile(
                env={"ANTHROPIC_BASE_URL": "http://host.docker.internal:11434", "SHARED": "base"}
            )
        },
    )
    feature = _feature()
    spec = AgentSpec(
        agent_id="claude",
        model="qwen2.5-coder:32b",
        env={"ANTHROPIC_AUTH_TOKEN": "ollama", "SHARED": "override"},
    )

    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: False)
    create_calls: list[dict] = []
    monkeypatch.setattr(
        lifecycle_module, "sbx_create_detached", lambda **kw: create_calls.append(kw)
    )
    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", lambda **kw: None)
    monkeypatch.setattr(finish_module, "poll_until_signal", lambda *a: "timed_out")

    lifecycle_module.run_job(config, feature, spec)

    assert create_calls[0]["env"] == {
        "ANTHROPIC_BASE_URL": "http://host.docker.internal:11434",
        "ANTHROPIC_AUTH_TOKEN": "ollama",
        "SHARED": "override",
    }


def test_run_job_passes_and_records_sandbox_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, cpus=2, memory="4g")
    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: False)
    create_calls: list[dict] = []
    monkeypatch.setattr(
        lifecycle_module, "sbx_create_detached", lambda **kw: create_calls.append(kw)
    )
    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", lambda **kw: None)
    monkeypatch.setattr(finish_module, "poll_until_signal", lambda *a: "timed_out")

    lifecycle_module.run_job(config, _feature(), AgentSpec(agent_id="claude"))

    assert (create_calls[0]["cpus"], create_calls[0]["memory"]) == (2, "4g")
    row = StateStore(config.state_db_path).all_jobs()[0]
    assert (row["cpus"], row["memory_limit"]) == (2, "4g")


def test_run_job_resolves_provider_and_skip_permissions_from_agent_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(
        tmp_path,
        dangerously_skip_permissions=False,
        agent_profiles={
            "claude": AgentProfile(provider="ollama", dangerously_skip_permissions=True)
        },
    )
    feature = _feature()
    spec = AgentSpec(agent_id="claude")

    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: False)
    create_calls = []
    launch_calls = []
    monkeypatch.setattr(
        lifecycle_module, "sbx_create_detached", lambda **kw: create_calls.append(kw)
    )
    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", lambda **kw: launch_calls.append(kw))
    monkeypatch.setattr(finish_module, "poll_until_signal", lambda *a: "timed_out")

    lifecycle_module.run_job(config, feature, spec)

    assert create_calls[0]["provider"] == "ollama"
    assert launch_calls[0]["skip_permissions_override"] is True


# --- resume_job ------------------------------------------------------------


def test_resume_job_unknown_sandbox_raises(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with pytest.raises(CommandError, match="No known job"):
        lifecycle_module.resume_job(config, {}, "box-1", "answer")


def test_resume_job_not_awaiting_input_raises(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    store.upsert("box-1", feature_id="f1", agent_id="claude", branch="b", status="running")

    with pytest.raises(CommandError, match="not 'awaiting_input'"):
        lifecycle_module.resume_job(config, {}, "box-1", "answer")


def test_resume_job_sandbox_gone_marks_lost_and_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    store.upsert("box-1", feature_id="f1", agent_id="claude", branch="b", status="awaiting_input")
    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: False)

    with pytest.raises(CommandError, match="no longer exists"):
        lifecycle_module.resume_job(config, {"f1": _feature()}, "box-1", "answer")

    assert _job(store, "box-1")["status"] == "lost"


def test_resume_job_happy_path_finalizes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    feature = _feature()
    sandbox_name = expand_module.sandbox_name_for(feature.id, AgentSpec(agent_id="claude"))
    store.upsert(
        sandbox_name,
        feature_id=feature.id,
        agent_id="claude",
        branch="agent/f1/claude",
        status="awaiting_input",
    )

    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: True)
    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", lambda **kw: None)
    monkeypatch.setattr(finish_module, "poll_until_signal", lambda *a: "done")
    finalize_calls = []
    monkeypatch.setattr(
        finish_module, "_finalize_success", lambda *a, **kw: finalize_calls.append(a)
    )

    lifecycle_module.resume_job(config, {"f1": feature}, sandbox_name, "use option B")

    assert len(finalize_calls) == 1


def test_resume_job_resolves_model_and_skip_permissions_from_agent_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(
        tmp_path,
        dangerously_skip_permissions=False,
        agent_profiles={
            "claude": AgentProfile(model="profile-model", dangerously_skip_permissions=True)
        },
    )
    store = StateStore(config.state_db_path)
    feature = _feature()
    sandbox_name = expand_module.sandbox_name_for(feature.id, AgentSpec(agent_id="claude"))
    store.upsert(
        sandbox_name,
        feature_id=feature.id,
        agent_id="claude",
        branch="agent/f1/claude",
        status="awaiting_input",
    )

    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: True)
    launch_calls = []
    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", lambda **kw: launch_calls.append(kw))
    monkeypatch.setattr(finish_module, "poll_until_signal", lambda *a: "awaiting_input")

    lifecycle_module.resume_job(config, {"f1": feature}, sandbox_name, "use option B")

    assert launch_calls[0]["model_override"] == "profile-model"
    assert launch_calls[0]["skip_permissions_override"] is True


def test_resume_job_awaiting_input_again_does_not_finalize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    feature = _feature()
    sandbox_name = expand_module.sandbox_name_for(feature.id, AgentSpec(agent_id="claude"))
    store.upsert(
        sandbox_name,
        feature_id=feature.id,
        agent_id="claude",
        branch="agent/f1/claude",
        status="awaiting_input",
    )

    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: True)
    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", lambda **kw: None)
    monkeypatch.setattr(finish_module, "poll_until_signal", lambda *a: "awaiting_input")
    monkeypatch.setattr(finish_module, "_finalize_success", _refuse)

    lifecycle_module.resume_job(
        config, {"f1": feature}, sandbox_name, "use option B"
    )  # must not raise


def test_resume_clears_the_options_the_answered_question_offered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stale options would otherwise reappear as buttons on the next question."""
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    feature = _feature()
    sandbox_name = expand_module.sandbox_name_for(feature.id, AgentSpec(agent_id="claude"))
    store.upsert(
        sandbox_name,
        feature_id=feature.id,
        agent_id="claude",
        branch="agent/f1/claude",
        status="awaiting_input",
        question_options='["A", "B"]',
    )
    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: True)
    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", lambda **kw: None)
    monkeypatch.setattr(finish_module, "poll_until_signal", lambda *a: "awaiting_input")

    lifecycle_module.resume_job(config, {"f1": feature}, sandbox_name, "A")

    assert _job(store, sandbox_name)["question_options"] is None


# --- plan_followup -------------------------------------------------------------


def _seed(store: StateStore, sandbox_name: str, status: str) -> None:
    store.upsert(
        sandbox_name, feature_id="f1", agent_id="claude", branch="agent/f1/claude", status=status
    )


def test_plan_followup_unknown_job_raises(tmp_path: Path) -> None:
    with pytest.raises(CommandError, match="No known job"):
        lifecycle_module.plan_followup(_config(tmp_path), {}, "box-1")


@pytest.mark.parametrize("status", ["running", "queued"])
def test_plan_followup_refuses_a_live_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    """A second launch into a running sandbox would race the first."""
    config = _config(tmp_path)
    _seed(StateStore(config.state_db_path), "box-1", status)
    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: True)

    with pytest.raises(CommandError, match="still"):
        lifecycle_module.plan_followup(config, {"f1": _feature()}, "box-1")


def test_plan_followup_refuses_a_stopped_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    _seed(StateStore(config.state_db_path), "box-1", "stopped")
    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: True)

    with pytest.raises(CommandError, match="respawn it first"):
        lifecycle_module.plan_followup(config, {"f1": _feature()}, "box-1")


def test_plan_followup_refuses_when_the_sandbox_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    _seed(StateStore(config.state_db_path), "box-1", "succeeded")
    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: False)

    with pytest.raises(CommandError, match="no longer exists"):
        lifecycle_module.plan_followup(config, {"f1": _feature()}, "box-1")


def test_plan_followup_accepts_a_finished_job_with_its_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    feature = _feature()
    sandbox_name = expand_module.sandbox_name_for(feature.id, AgentSpec(agent_id="claude"))
    _seed(StateStore(config.state_db_path), sandbox_name, "tests_failed")
    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: True)

    job, feature_out, spec = lifecycle_module.plan_followup(config, {"f1": feature}, sandbox_name)

    assert job["sandbox_name"] == sandbox_name
    assert feature_out is feature
    assert spec.agent_id == "claude"


def test_relaunch_and_await_resumes_the_session_with_the_given_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")
    sandbox_name = expand_module.sandbox_name_for(feature.id, spec)
    _seed(store, sandbox_name, "tests_failed")
    launches: list[dict] = []
    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", lambda **kw: launches.append(kw))
    monkeypatch.setattr(finish_module, "poll_until_signal", lambda *a: "awaiting_input")

    lifecycle_module.relaunch_and_await(
        config, _job(store, sandbox_name), feature, spec, "fix the test", event="job_following_up"
    )

    assert launches[0]["prompt"] == "fix the test"
    assert launches[0]["resume"] is True


# --- plan_respawn / respawn_job ----------------------------------------------


def test_plan_respawn_unknown_job_raises(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with pytest.raises(CommandError, match="No known job"):
        lifecycle_module.plan_respawn(config, {}, "box-1")


def test_plan_respawn_unknown_feature_raises(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    store.upsert("box-1", feature_id="f1", agent_id="claude", branch="b", status="crashed")

    with pytest.raises(CommandError, match="not found in the loaded features"):
        lifecycle_module.plan_respawn(config, {}, "box-1")


def test_plan_respawn_live_sandbox_not_stopped_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    feature = _feature()
    sandbox_name = expand_module.sandbox_name_for(feature.id, AgentSpec(agent_id="claude"))
    store.upsert(sandbox_name, feature_id="f1", agent_id="claude", branch="b", status="running")
    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: True)

    with pytest.raises(CommandError, match="only a stopped sandbox"):
        lifecycle_module.plan_respawn(config, {"f1": feature}, sandbox_name)


def test_plan_respawn_live_stopped_sandbox_returns_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")
    sandbox_name = expand_module.sandbox_name_for(feature.id, spec)
    store.upsert(sandbox_name, feature_id="f1", agent_id="claude", branch="b", status="stopped")
    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: True)

    _feature_out, spec_out, in_place = lifecycle_module.plan_respawn(
        config, {"f1": feature}, sandbox_name
    )

    assert in_place is True
    assert spec_out == spec


def test_plan_respawn_gone_succeeded_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    feature = _feature()
    sandbox_name = expand_module.sandbox_name_for(feature.id, AgentSpec(agent_id="claude"))
    store.upsert(sandbox_name, feature_id="f1", agent_id="claude", branch="b", status="succeeded")
    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: False)

    with pytest.raises(CommandError, match="already succeeded"):
        lifecycle_module.plan_respawn(config, {"f1": feature}, sandbox_name)


def test_plan_respawn_gone_awaiting_input_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    feature = _feature()
    sandbox_name = expand_module.sandbox_name_for(feature.id, AgentSpec(agent_id="claude"))
    store.upsert(
        sandbox_name, feature_id="f1", agent_id="claude", branch="b", status="awaiting_input"
    )
    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: False)

    with pytest.raises(CommandError, match="no question left to answer"):
        lifecycle_module.plan_respawn(config, {"f1": feature}, sandbox_name)


def test_plan_respawn_gone_crashed_returns_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    feature = _feature()
    sandbox_name = expand_module.sandbox_name_for(feature.id, AgentSpec(agent_id="claude"))
    store.upsert(sandbox_name, feature_id="f1", agent_id="claude", branch="b", status="crashed")
    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: False)

    _f, _s, in_place = lifecycle_module.plan_respawn(config, {"f1": feature}, sandbox_name)

    assert in_place is False


def test_respawn_job_in_place_restarts_and_relaunches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")
    sandbox_name = expand_module.sandbox_name_for(feature.id, spec)
    StateStore(config.state_db_path).upsert(
        sandbox_name, feature_id=feature.id, agent_id="claude", branch="b", status="stopped"
    )

    restart_calls = []
    launch_calls = []
    monkeypatch.setattr(
        lifecycle_module,
        "sbx_restart_detached",
        lambda name, agent: restart_calls.append((name, agent)),
    )
    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", lambda **kw: launch_calls.append(kw))
    monkeypatch.setattr(finish_module, "poll_until_signal", lambda *a: "done")
    finalize_calls = []
    monkeypatch.setattr(
        finish_module, "_finalize_success", lambda *a, **kw: finalize_calls.append(a)
    )

    lifecycle_module.respawn_job(config, feature, spec, sandbox_name, in_place=True)

    assert restart_calls == [(sandbox_name, "claude")]
    assert launch_calls[0]["resume"] is True
    assert len(finalize_calls) == 1


def test_respawn_job_fresh_delegates_to_run_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")
    sandbox_name = expand_module.sandbox_name_for(feature.id, spec)

    run_job_calls = []
    monkeypatch.setattr(lifecycle_module, "run_job", lambda cfg, f, s: run_job_calls.append((f, s)))
    monkeypatch.setattr(lifecycle_module, "sbx_restart_detached", _refuse)

    lifecycle_module.respawn_job(config, feature, spec, sandbox_name, in_place=False)

    assert run_job_calls == [(feature, spec)]


_OLLAMA_ENV = {
    "ANTHROPIC_AUTH_TOKEN": "ollama",
    "ANTHROPIC_BASE_URL": "http://host.docker.internal:11434",
}


def test_fresh_respawn_keeps_env_the_run_was_launched_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run launcher sends its own copy of each agent, so a run's env may
    never reach features.yaml. Respawn used to rebuild the spec from the
    features file and relaunch without the Ollama env.
    """
    config = _config(tmp_path)
    # What features.yaml holds: the model, but not the run-only env.
    saved = AgentSpec(agent_id="claude", model="qwen3.5:122b")
    feature = _feature([saved])
    launched = AgentSpec(agent_id="claude", model="qwen3.5:122b", env=dict(_OLLAMA_ENV))
    sandbox_name = expand_module.sandbox_name_for(feature.id, launched)

    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: False)
    create_calls: list[dict] = []
    monkeypatch.setattr(
        lifecycle_module, "sbx_create_detached", lambda **kw: create_calls.append(kw)
    )
    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", lambda **kw: None)
    monkeypatch.setattr(finish_module, "poll_until_signal", lambda *a: "timed_out")

    lifecycle_module.run_job(config, feature, launched)
    StateStore(config.state_db_path).upsert(sandbox_name, status="crashed")

    _f, spec, in_place = lifecycle_module.plan_respawn(config, {"f1": feature}, sandbox_name)
    lifecycle_module.respawn_job(config, feature, spec, sandbox_name, in_place=in_place)

    assert in_place is False
    assert len(create_calls) == 2
    assert create_calls[1]["env"] == _OLLAMA_ENV


# --- reattach_job --------------------------------------------------------------


def test_reattach_job_does_not_relaunch_the_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")
    sandbox_name = expand_module.sandbox_name_for(feature.id, spec)
    StateStore(config.state_db_path).upsert(
        sandbox_name, feature_id=feature.id, agent_id="claude", branch="b", status="running"
    )

    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", _refuse)
    monkeypatch.setattr(lifecycle_module, "sbx_create_detached", _refuse)
    monkeypatch.setattr(finish_module, "poll_until_signal", lambda *a: "done")
    finalize_calls = []
    monkeypatch.setattr(
        finish_module, "_finalize_success", lambda *a, **kw: finalize_calls.append(a)
    )

    lifecycle_module.reattach_job(config, feature, spec, sandbox_name)

    assert len(finalize_calls) == 1


def test_reattach_job_removes_sandbox_on_a_failed_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves a reattached job rejoins the normal finalize/teardown path,
    not just the happy one."""
    config = _config(tmp_path, remove_sandbox_on_failure=True)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")
    sandbox_name = expand_module.sandbox_name_for(feature.id, spec)
    StateStore(config.state_db_path).upsert(
        sandbox_name, feature_id=feature.id, agent_id="claude", branch="b", status="running"
    )

    monkeypatch.setattr(finish_module, "poll_until_signal", lambda *a: "timed_out")
    rm_calls: list[str] = []
    monkeypatch.setattr(lifecycle_module, "sbx_rm", rm_calls.append)
    monkeypatch.setattr(finish_module, "sbx_rm", rm_calls.append)

    lifecycle_module.reattach_job(config, feature, spec, sandbox_name)

    assert rm_calls == [sandbox_name]


# --- run_job_safe / run_all --------------------------------------------------


def test_run_job_safe_records_error_status_on_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")
    sandbox_name = expand_module.sandbox_name_for(feature.id, spec)
    store.upsert(
        sandbox_name,
        feature_id=feature.id,
        agent_id="claude",
        branch="b",
        status="queued",
    )

    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(lifecycle_module, "run_job", boom)

    lifecycle_module.run_job_safe(config, feature, spec)  # must not raise

    job = _job(store, sandbox_name)
    assert job["status"] == "error"
    assert "kaboom" in job["detail"]


def test_run_all_respects_max_concurrency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    jobs = [(_feature(), AgentSpec(agent_id="claude", run_label=str(i))) for i in range(6)]
    lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_run_job_safe(cfg, feature, spec):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1

    monkeypatch.setattr(lifecycle_module, "run_job_safe", fake_run_job_safe)

    lifecycle_module.run_all(config, jobs, max_workers=2)

    assert max_active <= 2


def test_run_all_holds_a_shared_slot_for_each_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shared cap is what stops concurrent runs flooding the host.

    max_workers alone bounds one run, so without this two runs at
    max_concurrency 3 could put six sandboxes on the machine.
    """
    config = _config(tmp_path)
    jobs = [(_feature(), AgentSpec(agent_id="claude", run_label=str(i))) for i in range(4)]
    lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_run_job_safe(cfg, feature, spec):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1

    monkeypatch.setattr(lifecycle_module, "run_job_safe", fake_run_job_safe)

    lifecycle_module.run_all(config, jobs, max_workers=4, slots=threading.Semaphore(1))

    assert max_active == 1


# A previous attempt's outcome, as `_finalize_success` / `_await_and_finalize`
# would have left it.
_STALE_ATTEMPT = {
    "finished_at": "2026-01-01T00:00:00+00:00",
    "duration_seconds": 42.0,
    "input_tokens": 900,
    "output_tokens": 80,
    "num_turns": 4,
    "files_changed": 3,
    "lines_added": 40,
    "lines_removed": 2,
    "tests_passed": 0,
    "lint_passed": 1,
}


def _capture_row_while_polling(
    monkeypatch: pytest.MonkeyPatch, config: Config, sandbox_name: str
) -> dict:
    """Snapshots the job row at the moment polling starts, i.e. what the UI
    shows for the whole time the new attempt is running."""
    seen: dict = {}

    def poll(*_a: object) -> str:
        seen.update(_job(StateStore(config.state_db_path), sandbox_name))
        return "stopped"

    monkeypatch.setattr(finish_module, "poll_until_signal", poll)
    return seen


def _assert_previous_attempt_cleared(row: dict) -> None:
    assert row["status"] == "running"
    assert row["started_at"] is not None
    for column in _STALE_ATTEMPT:
        assert row[column] is None, column


def test_relaunch_clears_previous_attempts_metrics_while_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")
    sandbox_name = expand_module.sandbox_name_for(feature.id, spec)
    StateStore(config.state_db_path).upsert(
        sandbox_name, feature_id=feature.id, agent_id="claude", status="crashed", **_STALE_ATTEMPT
    )
    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: False)
    monkeypatch.setattr(lifecycle_module, "sbx_create_detached", lambda **kw: None)
    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", lambda **kw: None)
    seen = _capture_row_while_polling(monkeypatch, config, sandbox_name)

    lifecycle_module.run_job(config, feature, spec)

    _assert_previous_attempt_cleared(seen)


def test_resume_clears_previous_attempts_metrics_while_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    feature = _feature()
    sandbox_name = expand_module.sandbox_name_for(feature.id, AgentSpec(agent_id="claude"))
    StateStore(config.state_db_path).upsert(
        sandbox_name,
        feature_id=feature.id,
        agent_id="claude",
        branch="agent/f1/claude",
        status="awaiting_input",
        **_STALE_ATTEMPT,
    )
    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: True)
    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", lambda **kw: None)
    seen = _capture_row_while_polling(monkeypatch, config, sandbox_name)

    lifecycle_module.resume_job(config, {"f1": feature}, sandbox_name, "use option B")

    _assert_previous_attempt_cleared(seen)


def test_respawn_in_place_clears_previous_attempts_metrics_while_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")
    sandbox_name = expand_module.sandbox_name_for(feature.id, spec)
    StateStore(config.state_db_path).upsert(
        sandbox_name, feature_id=feature.id, agent_id="claude", status="stopped", **_STALE_ATTEMPT
    )
    monkeypatch.setattr(lifecycle_module, "sbx_restart_detached", lambda name, agent: None)
    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", lambda **kw: None)
    seen = _capture_row_while_polling(monkeypatch, config, sandbox_name)

    lifecycle_module.respawn_job(config, feature, spec, sandbox_name, in_place=True)

    _assert_previous_attempt_cleared(seen)


# --- per-step timing ---------------------------------------------------------

_STEP_COLUMNS = ("launch_s", "agent_s", "verify_s", "push_s")


def _stub_launch_and_poll(monkeypatch: pytest.MonkeyPatch, final_status: str) -> None:
    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: False)
    monkeypatch.setattr(lifecycle_module, "sbx_create_detached", lambda **kw: None)
    monkeypatch.setattr(lifecycle_module, "sbx_launch_agent", lambda **kw: None)
    monkeypatch.setattr(finish_module, "poll_until_signal", lambda *a: final_status)
    monkeypatch.setattr(lifecycle_module, "sbx_rm", lambda name: None)
    monkeypatch.setattr(finish_module, "sbx_rm", lambda name: None)


def test_run_job_success_records_every_step_and_no_failure_category(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, test_command="pytest")
    feature = _feature()
    spec = AgentSpec(agent_id="claude")
    sandbox_name = expand_module.sandbox_name_for(feature.id, spec)
    _stub_launch_and_poll(monkeypatch, "done")
    calls: list[str] = []
    _stub_teardown_read(monkeypatch, calls)
    _stub_finalize_host_calls(monkeypatch, calls)

    lifecycle_module.run_job(config, feature, spec)

    job = _job(StateStore(config.state_db_path), sandbox_name)
    assert job["status"] == "succeeded"
    for column in _STEP_COLUMNS:
        assert isinstance(job[column], float), column
        assert job[column] >= 0, column
    assert job["failure_category"] is None


@pytest.mark.parametrize(
    ("final_status", "category"),
    [("timed_out", "timeout"), ("crashed", "infra"), ("stuck", "agent_stuck")],
)
def test_run_job_agent_failure_records_category_and_agent_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, final_status: str, category: str
) -> None:
    config = _config(tmp_path)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")
    sandbox_name = expand_module.sandbox_name_for(feature.id, spec)
    store = StateStore(config.state_db_path)
    _stub_launch_and_poll(monkeypatch, final_status)

    def poll(_config, poll_store, name, _deadline) -> str:
        # The real poll loop is what writes the terminal status.
        poll_store.upsert(name, status=final_status)
        return final_status

    monkeypatch.setattr(finish_module, "poll_until_signal", poll)

    lifecycle_module.run_job(config, feature, spec)

    job = _job(store, sandbox_name)
    assert job["failure_category"] == category
    assert job["launch_s"] is not None
    assert job["agent_s"] is not None
    assert job["verify_s"] is None


def test_run_job_safe_unexpected_exception_is_categorised_internal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")
    sandbox_name = expand_module.sandbox_name_for(feature.id, spec)

    def boom(*_a, **_k) -> None:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: False)
    monkeypatch.setattr(lifecycle_module, "sbx_create_detached", boom)

    lifecycle_module.run_job_safe(config, feature, spec)

    job = _job(StateStore(config.state_db_path), sandbox_name)
    assert job["status"] == "error"
    assert job["failure_category"] == "internal"
    # The failed launch is still timed.
    assert job["launch_s"] is not None


def test_new_attempt_clears_the_previous_attempts_timings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relaunch must not show the last attempt's verify/push time while it runs."""
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")
    sandbox_name = expand_module.sandbox_name_for(feature.id, spec)
    store.upsert(
        sandbox_name,
        feature_id=feature.id,
        agent_id="claude",
        branch="b",
        status="tests_failed",
        verify_s=9.0,
        push_s=4.0,
    )
    _stub_launch_and_poll(monkeypatch, "awaiting_input")
    monkeypatch.setattr(lifecycle_module, "sbx_exists", lambda name: True)
    seen: dict[str, object] = {}

    def poll(_config, poll_store, name, _deadline) -> str:
        seen.update(poll_store.get(name) or {})
        return "awaiting_input"

    monkeypatch.setattr(finish_module, "poll_until_signal", poll)

    lifecycle_module.run_job(config, feature, spec)

    assert seen["verify_s"] is None
    assert seen["push_s"] is None
    assert seen["failure_category"] is None
    assert seen["launch_s"] is not None
