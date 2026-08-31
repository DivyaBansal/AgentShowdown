"""Tests for agentshowdown.orchestrator.job."""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest

from agentshowdown.orchestrator import job as job_module
from agentshowdown.orchestrator.config import AgentProfile, AgentSpec, Config, Feature
from agentshowdown.orchestrator.process import CommandError
from agentshowdown.orchestrator.state import StateStore


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


def _ok_process() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def _failing_process() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="boom", stderr="")


# --- sandbox_name_for / branch_for -----------------------------------------


def test_sandbox_name_for_without_run_label() -> None:
    assert job_module.sandbox_name_for("f1", AgentSpec(agent_id="claude")) == "arena-f1-claude"


def test_sandbox_name_for_with_run_label() -> None:
    assert (
        job_module.sandbox_name_for("f1", AgentSpec(agent_id="claude", run_label="b"))
        == "arena-f1-claude-b"
    )


def test_sandbox_name_for_truncates_to_60_chars() -> None:
    long_id = "x" * 100
    name = job_module.sandbox_name_for(long_id, AgentSpec(agent_id="claude"))
    assert len(name) == 60


def test_branch_for_without_run_label() -> None:
    assert job_module.branch_for("f1", AgentSpec(agent_id="claude")) == "agent/f1/claude"


def test_branch_for_with_run_label() -> None:
    assert (
        job_module.branch_for("f1", AgentSpec(agent_id="claude", run_label="b"))
        == "agent/f1/claude-b"
    )


# --- expand_jobs -------------------------------------------------------------


def test_expand_jobs_no_agents_key_defaults_to_config_agent(tmp_path: Path) -> None:
    config = _config(tmp_path)
    features = {"f1": _feature()}
    jobs = job_module.expand_jobs(config, features, None)
    assert jobs == [(features["f1"], AgentSpec(agent_id="claude"))]


def test_expand_jobs_multiple_agents(tmp_path: Path) -> None:
    config = _config(tmp_path)
    specs = [AgentSpec(agent_id="claude"), AgentSpec(agent_id="claude", run_label="b")]
    features = {"f1": _feature(agents=specs)}
    jobs = job_module.expand_jobs(config, features, None)
    assert [spec for _, spec in jobs] == specs


def test_expand_jobs_unknown_feature_id_raises(tmp_path: Path) -> None:
    config = _config(tmp_path)
    features = {"f1": _feature()}
    with pytest.raises(CommandError, match="Unknown feature"):
        job_module.expand_jobs(config, features, ["nope"])


def test_expand_jobs_duplicate_sandbox_name_raises(tmp_path: Path) -> None:
    config = _config(tmp_path)
    specs = [AgentSpec(agent_id="claude"), AgentSpec(agent_id="claude")]
    features = {"f1": _feature(agents=specs)}
    with pytest.raises(CommandError, match="Duplicate sandbox name"):
        job_module.expand_jobs(config, features, None)


def test_expand_jobs_unsupported_agent_without_command_raises(tmp_path: Path) -> None:
    config = _config(tmp_path)
    features = {"f1": _feature(agents=[AgentSpec(agent_id="shell")])}
    with pytest.raises(CommandError, match="no CLI profile wired up"):
        job_module.expand_jobs(config, features, None)


def test_expand_jobs_unsupported_agent_with_command_is_allowed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    spec = AgentSpec(agent_id="shell", command="./run-my-agent.sh")
    features = {"f1": _feature(agents=[spec])}
    jobs = job_module.expand_jobs(config, features, None)
    assert jobs == [(features["f1"], spec)]


def test_expand_jobs_accepts_copilot_agent_id(tmp_path: Path) -> None:
    config = _config(tmp_path)
    features = {"f1": _feature(agents=[AgentSpec(agent_id="copilot")])}
    jobs = job_module.expand_jobs(config, features, None)
    assert jobs == [(features["f1"], AgentSpec(agent_id="copilot"))]


def test_expand_jobs_unknown_model_raises(tmp_path: Path) -> None:
    config = _config(tmp_path)
    spec = AgentSpec(agent_id="claude", model="not-a-real-model")
    features = {"f1": _feature(agents=[spec])}
    with pytest.raises(CommandError, match="Unknown model 'not-a-real-model'"):
        job_module.expand_jobs(config, features, None)


def test_expand_jobs_unknown_model_error_names_the_feature(tmp_path: Path) -> None:
    config = _config(tmp_path)
    spec = AgentSpec(agent_id="claude", model="not-a-real-model")
    features = {"f1": _feature(agents=[spec])}
    with pytest.raises(CommandError, match="Feature 'f1'"):
        job_module.expand_jobs(config, features, None)


def test_expand_jobs_unvalidated_agent_model_is_allowed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    spec = AgentSpec(agent_id="cursor", model="literally-anything")
    features = {"f1": _feature(agents=[spec])}
    jobs = job_module.expand_jobs(config, features, None)
    assert jobs == [(features["f1"], spec)]


def test_expand_jobs_command_override_skips_model_validation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    spec = AgentSpec(agent_id="claude", model="not-a-real-model", command="./run-my-agent.sh")
    features = {"f1": _feature(agents=[spec])}
    jobs = job_module.expand_jobs(config, features, None)
    assert jobs == [(features["f1"], spec)]


def test_expand_jobs_selects_only_requested_features(tmp_path: Path) -> None:
    config = _config(tmp_path)
    features = {
        "f1": _feature(),
        "f2": Feature(id="f2", description="d2", acceptance_criteria=[], agents=[]),
    }
    jobs = job_module.expand_jobs(config, features, ["f2"])
    assert [f.id for f, _ in jobs] == ["f2"]


# --- find_spec_for_sandbox ----------------------------------------------------


def test_find_spec_for_sandbox_matches_by_derived_name(tmp_path: Path) -> None:
    config = _config(tmp_path)
    specs = [AgentSpec(agent_id="claude"), AgentSpec(agent_id="claude", run_label="b")]
    feature = _feature(agents=specs)
    sandbox_name = job_module.sandbox_name_for("f1", specs[1])
    assert job_module.find_spec_for_sandbox(config, feature, sandbox_name) == specs[1]


def test_find_spec_for_sandbox_no_agents_key_uses_config_default(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    feature = _feature()
    sandbox_name = job_module.sandbox_name_for("f1", AgentSpec(agent_id="claude"))
    spec = job_module.find_spec_for_sandbox(config, feature, sandbox_name)
    assert spec == AgentSpec(agent_id="claude")


def test_find_spec_for_sandbox_no_match_raises(tmp_path: Path) -> None:
    config = _config(tmp_path)
    feature = _feature(agents=[AgentSpec(agent_id="claude")])
    with pytest.raises(CommandError, match="No agent entry"):
        job_module.find_spec_for_sandbox(config, feature, "not-a-real-sandbox")


# --- poll_until_signal -------------------------------------------------------


def test_poll_until_signal_returns_done_when_status_file_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    store.upsert("box-1", feature_id="f1", agent_id="claude", branch="b", status="running")
    monkeypatch.setattr(job_module, "sbx_status", lambda name: "running")
    monkeypatch.setattr(
        job_module,
        "sbx_read_status",
        lambda name, path: {"state": "done", "message": "yay"},
    )

    result = job_module.poll_until_signal(config, store, "box-1", time.monotonic() + 10)

    assert result == "done"
    assert _job(store, "box-1")["status"] == "done"
    assert _job(store, "box-1")["detail"] == "yay"


def test_poll_until_signal_returns_awaiting_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    store.upsert("box-1", feature_id="f1", agent_id="claude", branch="b", status="running")
    monkeypatch.setattr(job_module, "sbx_status", lambda name: "running")
    monkeypatch.setattr(
        job_module,
        "sbx_read_status",
        lambda name, path: {"state": "awaiting_input", "message": "which option?"},
    )

    result = job_module.poll_until_signal(config, store, "box-1", time.monotonic() + 10)

    assert result == "awaiting_input"
    assert _job(store, "box-1")["detail"] == "which option?"


def test_poll_until_signal_container_crash_with_no_status_returns_crashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    store.upsert("box-1", feature_id="f1", agent_id="claude", branch="b", status="running")
    monkeypatch.setattr(job_module, "sbx_status", lambda name: "crashed")
    monkeypatch.setattr(job_module, "sbx_read_status", lambda name, path: None)

    result = job_module.poll_until_signal(config, store, "box-1", time.monotonic() + 10)

    assert result == "crashed"


def test_poll_until_signal_container_crash_but_status_file_says_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    store.upsert("box-1", feature_id="f1", agent_id="claude", branch="b", status="running")
    monkeypatch.setattr(job_module, "sbx_status", lambda name: "crashed")
    monkeypatch.setattr(
        job_module,
        "sbx_read_status",
        lambda name, path: {"state": "done", "message": "finished first"},
    )

    result = job_module.poll_until_signal(config, store, "box-1", time.monotonic() + 10)

    assert result == "done"


def test_poll_until_signal_times_out_when_deadline_already_passed(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    store.upsert("box-1", feature_id="f1", agent_id="claude", branch="b", status="running")

    result = job_module.poll_until_signal(config, store, "box-1", time.monotonic() - 1)

    assert result == "timed_out"
    assert _job(store, "box-1")["status"] == "timed_out"


def test_poll_until_signal_unknown_state_keeps_polling_until_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    store.upsert("box-1", feature_id="f1", agent_id="claude", branch="b", status="running")
    responses = iter(
        [
            {"state": "unknown", "message": "bad json"},
            {"state": "done", "message": "ok"},
        ]
    )
    monkeypatch.setattr(job_module, "sbx_status", lambda name: "running")
    monkeypatch.setattr(job_module, "sbx_read_status", lambda name, path: next(responses))

    result = job_module.poll_until_signal(config, store, "box-1", time.monotonic() + 10)

    assert result == "done"


# --- _finalize_success --------------------------------------------------------


def test_finalize_success_opens_pr_when_tests_and_lint_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, test_command="pytest", lint_command="ruff check .")
    store = StateStore(config.state_db_path)
    store.upsert(
        "box-1",
        feature_id="f1",
        agent_id="claude",
        branch="agent/f1/claude",
        status="done",
    )
    feature = _feature()
    calls: list[str] = []

    monkeypatch.setattr(job_module, "sbx_current_branch", lambda name, repo: "agent/f1/claude")
    monkeypatch.setattr(job_module, "sbx_exec_capture", lambda name, cmd: _ok_process())
    monkeypatch.setattr(
        job_module, "fetch_and_push_branch", lambda *a: calls.append("fetch_and_push")
    )
    monkeypatch.setattr(
        job_module, "open_pr", lambda *a, **kw: "https://github.com/owner/repo/pull/1"
    )
    monkeypatch.setattr(job_module, "sbx_rm", lambda name: calls.append("rm"))

    job_module._finalize_success(
        config,
        store,
        "box-1",
        feature=feature,
        agent_id="claude",
        requested_branch="agent/f1/claude",
    )

    job = _job(store, "box-1")
    assert job["status"] == "succeeded"
    assert job["pr_url"] == "https://github.com/owner/repo/pull/1"
    assert "fetch_and_push" in calls
    assert "rm" in calls  # remove_sandbox_on_success defaults True


def test_finalize_success_test_failure_skips_pr_and_respects_remove_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, test_command="pytest", remove_sandbox_on_failure=True)
    store = StateStore(config.state_db_path)
    store.upsert(
        "box-1",
        feature_id="f1",
        agent_id="claude",
        branch="agent/f1/claude",
        status="done",
    )
    feature = _feature()
    calls: list[str] = []

    monkeypatch.setattr(job_module, "sbx_current_branch", lambda name, repo: "agent/f1/claude")
    monkeypatch.setattr(job_module, "sbx_exec_capture", lambda name, cmd: _failing_process())
    monkeypatch.setattr(job_module, "sbx_rm", lambda name: calls.append("rm"))
    monkeypatch.setattr(job_module, "open_pr", _refuse)

    job_module._finalize_success(
        config,
        store,
        "box-1",
        feature=feature,
        agent_id="claude",
        requested_branch="agent/f1/claude",
    )

    assert _job(store, "box-1")["status"] == "tests_failed"
    assert "rm" in calls  # opportunistic fix: remove_sandbox_on_failure now honored here too


# --- run_job -------------------------------------------------------------------


def test_run_job_skips_when_awaiting_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")
    sandbox_name = job_module.sandbox_name_for(feature.id, spec)
    store.upsert(
        sandbox_name,
        feature_id=feature.id,
        agent_id="claude",
        branch="b",
        status="awaiting_input",
    )

    monkeypatch.setattr(job_module, "sbx_exists", lambda name: True)
    monkeypatch.setattr(job_module, "sbx_create_detached", _refuse)
    monkeypatch.setattr(job_module, "sbx_launch_agent", _refuse)
    monkeypatch.setattr(job_module, "poll_until_signal", _refuse)

    job_module.run_job(config, feature, spec)  # should return early, no error

    assert _job(store, sandbox_name)["status"] == "awaiting_input"


def test_run_job_skips_already_succeeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")
    sandbox_name = job_module.sandbox_name_for(feature.id, spec)
    store.upsert(
        sandbox_name,
        feature_id=feature.id,
        agent_id="claude",
        branch="b",
        status="succeeded",
        pr_url="https://example.com/pr/1",
    )

    monkeypatch.setattr(job_module, "sbx_exists", lambda name: False)
    monkeypatch.setattr(job_module, "sbx_create_detached", _refuse)
    monkeypatch.setattr(job_module, "sbx_launch_agent", _refuse)
    monkeypatch.setattr(job_module, "poll_until_signal", _refuse)

    job_module.run_job(config, feature, spec)  # should return early

    assert _job(store, sandbox_name)["status"] == "succeeded"


def test_run_job_full_success_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")

    monkeypatch.setattr(job_module, "sbx_exists", lambda name: False)
    monkeypatch.setattr(job_module, "sbx_create_detached", lambda **kw: None)
    monkeypatch.setattr(job_module, "sbx_launch_agent", lambda **kw: None)
    monkeypatch.setattr(job_module, "poll_until_signal", lambda *a: "done")
    finalize_calls = []
    monkeypatch.setattr(job_module, "_finalize_success", lambda *a, **kw: finalize_calls.append(a))

    job_module.run_job(config, feature, spec)

    assert len(finalize_calls) == 1


def test_run_job_reattaches_to_running_sandbox_without_relaunching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")
    sandbox_name = job_module.sandbox_name_for(feature.id, spec)
    store.upsert(
        sandbox_name,
        feature_id=feature.id,
        agent_id="claude",
        branch="b",
        status="running",
    )

    monkeypatch.setattr(job_module, "sbx_exists", lambda name: True)
    monkeypatch.setattr(job_module, "sbx_create_detached", _refuse)
    monkeypatch.setattr(job_module, "sbx_launch_agent", _refuse)
    monkeypatch.setattr(job_module, "poll_until_signal", lambda *a: "timed_out")
    monkeypatch.setattr(job_module, "sbx_rm", lambda name: None)

    job_module.run_job(config, feature, spec)  # should not raise, must not relaunch


def test_run_job_removes_and_relaunches_after_previous_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")
    sandbox_name = job_module.sandbox_name_for(feature.id, spec)
    store.upsert(
        sandbox_name,
        feature_id=feature.id,
        agent_id="claude",
        branch="b",
        status="crashed",
    )

    calls: list[str] = []
    monkeypatch.setattr(job_module, "sbx_exists", lambda name: True)
    monkeypatch.setattr(job_module, "sbx_rm", lambda name: calls.append("rm"))
    monkeypatch.setattr(job_module, "sbx_create_detached", lambda **kw: calls.append("create"))
    monkeypatch.setattr(job_module, "sbx_launch_agent", lambda **kw: calls.append("launch"))
    monkeypatch.setattr(job_module, "poll_until_signal", lambda *a: "timed_out")

    job_module.run_job(config, feature, spec)

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

    monkeypatch.setattr(job_module, "sbx_exists", lambda name: False)
    create_calls = []
    launch_calls = []
    monkeypatch.setattr(job_module, "sbx_create_detached", lambda **kw: create_calls.append(kw))
    monkeypatch.setattr(job_module, "sbx_launch_agent", lambda **kw: launch_calls.append(kw))
    monkeypatch.setattr(job_module, "poll_until_signal", lambda *a: "timed_out")

    job_module.run_job(config, feature, spec)

    assert create_calls[0]["kit"] == ["./kits/base", "./kits/job-specific"]
    assert create_calls[0]["model"] == "profile-model"
    assert launch_calls[0]["model_override"] == "profile-model"


def test_run_job_spec_model_overrides_agent_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, agent_profiles={"claude": AgentProfile(model="profile-model")})
    feature = _feature()
    spec = AgentSpec(agent_id="claude", model="spec-model")

    monkeypatch.setattr(job_module, "sbx_exists", lambda name: False)
    monkeypatch.setattr(job_module, "sbx_create_detached", lambda **kw: None)
    launch_calls = []
    monkeypatch.setattr(job_module, "sbx_launch_agent", lambda **kw: launch_calls.append(kw))
    monkeypatch.setattr(job_module, "poll_until_signal", lambda *a: "timed_out")

    job_module.run_job(config, feature, spec)

    assert launch_calls[0]["model_override"] == "spec-model"


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

    monkeypatch.setattr(job_module, "sbx_exists", lambda name: False)
    create_calls = []
    launch_calls = []
    monkeypatch.setattr(job_module, "sbx_create_detached", lambda **kw: create_calls.append(kw))
    monkeypatch.setattr(job_module, "sbx_launch_agent", lambda **kw: launch_calls.append(kw))
    monkeypatch.setattr(job_module, "poll_until_signal", lambda *a: "timed_out")

    job_module.run_job(config, feature, spec)

    assert create_calls[0]["provider"] == "ollama"
    assert launch_calls[0]["skip_permissions_override"] is True


# --- resume_job ------------------------------------------------------------


def test_resume_job_unknown_sandbox_raises(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with pytest.raises(CommandError, match="No known job"):
        job_module.resume_job(config, {}, "box-1", "answer")


def test_resume_job_not_awaiting_input_raises(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    store.upsert("box-1", feature_id="f1", agent_id="claude", branch="b", status="running")

    with pytest.raises(CommandError, match="not 'awaiting_input'"):
        job_module.resume_job(config, {}, "box-1", "answer")


def test_resume_job_sandbox_gone_marks_lost_and_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    store.upsert("box-1", feature_id="f1", agent_id="claude", branch="b", status="awaiting_input")
    monkeypatch.setattr(job_module, "sbx_exists", lambda name: False)

    with pytest.raises(CommandError, match="no longer exists"):
        job_module.resume_job(config, {"f1": _feature()}, "box-1", "answer")

    assert _job(store, "box-1")["status"] == "lost"


def test_resume_job_happy_path_finalizes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    feature = _feature()
    sandbox_name = job_module.sandbox_name_for(feature.id, AgentSpec(agent_id="claude"))
    store.upsert(
        sandbox_name,
        feature_id=feature.id,
        agent_id="claude",
        branch="agent/f1/claude",
        status="awaiting_input",
    )

    monkeypatch.setattr(job_module, "sbx_exists", lambda name: True)
    monkeypatch.setattr(job_module, "sbx_launch_agent", lambda **kw: None)
    monkeypatch.setattr(job_module, "poll_until_signal", lambda *a: "done")
    finalize_calls = []
    monkeypatch.setattr(job_module, "_finalize_success", lambda *a, **kw: finalize_calls.append(a))

    job_module.resume_job(config, {"f1": feature}, sandbox_name, "use option B")

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
    sandbox_name = job_module.sandbox_name_for(feature.id, AgentSpec(agent_id="claude"))
    store.upsert(
        sandbox_name,
        feature_id=feature.id,
        agent_id="claude",
        branch="agent/f1/claude",
        status="awaiting_input",
    )

    monkeypatch.setattr(job_module, "sbx_exists", lambda name: True)
    launch_calls = []
    monkeypatch.setattr(job_module, "sbx_launch_agent", lambda **kw: launch_calls.append(kw))
    monkeypatch.setattr(job_module, "poll_until_signal", lambda *a: "awaiting_input")

    job_module.resume_job(config, {"f1": feature}, sandbox_name, "use option B")

    assert launch_calls[0]["model_override"] == "profile-model"
    assert launch_calls[0]["skip_permissions_override"] is True


def test_resume_job_awaiting_input_again_does_not_finalize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    feature = _feature()
    sandbox_name = job_module.sandbox_name_for(feature.id, AgentSpec(agent_id="claude"))
    store.upsert(
        sandbox_name,
        feature_id=feature.id,
        agent_id="claude",
        branch="agent/f1/claude",
        status="awaiting_input",
    )

    monkeypatch.setattr(job_module, "sbx_exists", lambda name: True)
    monkeypatch.setattr(job_module, "sbx_launch_agent", lambda **kw: None)
    monkeypatch.setattr(job_module, "poll_until_signal", lambda *a: "awaiting_input")
    monkeypatch.setattr(job_module, "_finalize_success", _refuse)

    job_module.resume_job(config, {"f1": feature}, sandbox_name, "use option B")  # must not raise


# --- run_job_safe / run_all --------------------------------------------------


def test_run_job_safe_records_error_status_on_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    feature = _feature()
    spec = AgentSpec(agent_id="claude")
    sandbox_name = job_module.sandbox_name_for(feature.id, spec)
    store.upsert(
        sandbox_name,
        feature_id=feature.id,
        agent_id="claude",
        branch="b",
        status="queued",
    )

    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(job_module, "run_job", boom)

    job_module.run_job_safe(config, feature, spec)  # must not raise

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

    monkeypatch.setattr(job_module, "run_job_safe", fake_run_job_safe)

    job_module.run_all(config, jobs, max_workers=2)

    assert max_active <= 2
