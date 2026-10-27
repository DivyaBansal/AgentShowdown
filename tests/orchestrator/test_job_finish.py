"""Tests for backend.orchestrator.job.finish."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest
import structlog.testing
from backend.orchestrator.config import AgentSpec, Config, Feature
from backend.orchestrator.events import bus as event_bus
from backend.orchestrator.job import finish as finish_module
from backend.orchestrator.job import lifecycle as lifecycle_module
from backend.orchestrator.process import CommandError
from backend.orchestrator.prompt import MAX_QUESTION_OPTIONS
from backend.orchestrator.state import StateStore


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


# --- poll_until_signal -------------------------------------------------------


def test_poll_until_signal_returns_done_when_status_file_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    store.upsert("box-1", feature_id="f1", agent_id="claude", branch="b", status="running")
    monkeypatch.setattr(finish_module, "sbx_status", lambda name: "running")
    monkeypatch.setattr(
        finish_module,
        "sbx_read_status_and_tail",
        lambda name, status_file, log_path, tail_lines: (
            {"state": "done", "message": "yay"},
            "building...\ndone.",
        ),
    )

    result = finish_module.poll_until_signal(config, store, "box-1", time.monotonic() + 10)

    assert result == "done"
    assert _job(store, "box-1")["status"] == "done"
    assert _job(store, "box-1")["detail"] == "yay"
    # The tail is written on the same tick, via the dedicated log_tail path.
    assert _job(store, "box-1")["log_tail"] == "building...\ndone."


def test_poll_until_signal_returns_awaiting_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    store.upsert("box-1", feature_id="f1", agent_id="claude", branch="b", status="running")
    monkeypatch.setattr(finish_module, "sbx_status", lambda name: "running")
    monkeypatch.setattr(
        finish_module,
        "sbx_read_status_and_tail",
        lambda name, status_file, log_path, tail_lines: (
            {"state": "awaiting_input", "message": "which option?"},
            "",
        ),
    )

    result = finish_module.poll_until_signal(config, store, "box-1", time.monotonic() + 10)

    assert result == "awaiting_input"
    assert _job(store, "box-1")["detail"] == "which option?"


def test_poll_until_signal_publishes_job_log_on_each_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live tail is broadcast on its own event, not folded into
    job_changed -- see StateStore.update_log_tail."""
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    store.upsert("box-1", feature_id="f1", agent_id="claude", branch="b", status="running")
    monkeypatch.setattr(finish_module, "sbx_status", lambda name: "running")
    monkeypatch.setattr(
        finish_module,
        "sbx_read_status_and_tail",
        lambda name, status_file, log_path, tail_lines: (
            {"state": "done", "message": "yay"},
            "line one\nline two",
        ),
    )
    received = []
    unsubscribe = event_bus.subscribe(lambda e: received.append(e) if e.type == "job_log" else None)
    try:
        finish_module.poll_until_signal(config, store, "box-1", time.monotonic() + 10)
    finally:
        unsubscribe()

    assert len(received) == 1
    assert received[0].data == {"sandbox_name": "box-1", "tail": "line one\nline two"}


def test_poll_until_signal_container_crash_with_no_status_returns_crashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    store.upsert("box-1", feature_id="f1", agent_id="claude", branch="b", status="running")
    monkeypatch.setattr(finish_module, "sbx_status", lambda name: "crashed")
    monkeypatch.setattr(finish_module, "sbx_read_status", lambda name, path: None)

    result = finish_module.poll_until_signal(config, store, "box-1", time.monotonic() + 10)

    assert result == "crashed"


def test_poll_until_signal_container_crash_but_status_file_says_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    store.upsert("box-1", feature_id="f1", agent_id="claude", branch="b", status="running")
    monkeypatch.setattr(finish_module, "sbx_status", lambda name: "crashed")
    monkeypatch.setattr(
        finish_module,
        "sbx_read_status",
        lambda name, path: {"state": "done", "message": "finished first"},
    )

    result = finish_module.poll_until_signal(config, store, "box-1", time.monotonic() + 10)

    assert result == "done"


def test_poll_until_signal_times_out_when_deadline_already_passed(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    store.upsert("box-1", feature_id="f1", agent_id="claude", branch="b", status="running")

    result = finish_module.poll_until_signal(config, store, "box-1", time.monotonic() - 1)

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
            ({"state": "unknown", "message": "bad json"}, ""),
            ({"state": "done", "message": "ok"}, ""),
        ]
    )
    monkeypatch.setattr(finish_module, "sbx_status", lambda name: "running")
    monkeypatch.setattr(
        finish_module,
        "sbx_read_status_and_tail",
        lambda name, status_file, log_path, tail_lines: next(responses),
    )

    result = finish_module.poll_until_signal(config, store, "box-1", time.monotonic() + 10)

    assert result == "done"


def test_poll_until_signal_returns_stopped_without_reading_status_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deliberate `sbx stop` must be detected directly, not via a status
    read that would fail against a stopped (non-exec-able) container."""
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    store.upsert("box-1", feature_id="f1", agent_id="claude", branch="b", status="running")
    monkeypatch.setattr(finish_module, "sbx_status", lambda name: "stopped")
    monkeypatch.setattr(finish_module, "sbx_read_status_and_tail", _refuse)

    result = finish_module.poll_until_signal(config, store, "box-1", time.monotonic() + 10)

    assert result == "stopped"
    assert _job(store, "box-1")["status"] == "stopped"


# --- _finalize_success --------------------------------------------------------


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


def _finalize(config, store, feature) -> None:
    finish_module._finalize_success(
        config,
        store,
        "box-1",
        feature=feature,
        agent_id="claude",
        requested_branch="agent/f1/claude",
    )


def _seed_done(store) -> None:
    store.upsert(
        "box-1",
        feature_id="f1",
        agent_id="claude",
        branch="agent/f1/claude",
        status="done",
    )


def test_finalize_success_pushes_branch_and_records_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, test_command="pytest", open_pr=True)
    store = StateStore(config.state_db_path)
    _seed_done(store)
    calls: list[str] = []
    claude_log = '{"type":"result","num_turns":7,"usage":{"input_tokens":1234,"output_tokens":567}}'
    _stub_teardown_read(monkeypatch, calls, log=claude_log)
    _stub_finalize_host_calls(monkeypatch, calls)
    monkeypatch.setattr(
        finish_module, "open_pr", lambda *a, **kw: "https://github.com/owner/repo/pull/1"
    )

    _finalize(config, store, _feature())

    job = _job(store, "box-1")
    assert job["status"] == "succeeded"
    assert job["pr_url"] == "https://github.com/owner/repo/pull/1"
    assert "push" in calls
    assert "rm" in calls
    # Metrics measured on the host, plus tokens parsed from the run log.
    assert job["files_changed"] == 1
    assert job["lines_added"] == 3
    assert job["input_tokens"] == 1234
    assert job["num_turns"] == 7
    assert job["tests_passed"] == 1


def test_finalize_success_skips_the_pr_when_open_pr_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """open_pr defaults off: the branch still reaches origin, the PR does not."""
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    _seed_done(store)
    calls: list[str] = []
    _stub_teardown_read(monkeypatch, calls)
    _stub_finalize_host_calls(monkeypatch, calls)
    monkeypatch.setattr(finish_module, "open_pr", _refuse)

    _finalize(config, store, _feature())

    job = _job(store, "box-1")
    assert job["status"] == "succeeded"
    assert job["pr_url"] is None
    assert "push" in calls


def test_finalize_success_verifies_on_the_host_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """verify_on=host must not run the test command inside the sandbox."""
    config = _config(tmp_path, test_command="pytest", lint_command="ruff check")
    store = StateStore(config.state_db_path)
    _seed_done(store)
    calls: list[str] = []
    _stub_teardown_read(monkeypatch, calls)
    _stub_finalize_host_calls(monkeypatch, calls)

    _finalize(config, store, _feature())

    assert calls.count("verify_host") == 2  # tests + lint, both host-side
    # Exactly one sandbox exec: the bundled teardown read.
    assert calls.count("sbx_exec") == 1


def test_finalize_success_verify_on_sandbox_uses_the_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, test_command="pytest", verify_on="sandbox")
    store = StateStore(config.state_db_path)
    _seed_done(store)
    calls: list[str] = []
    _stub_teardown_read(monkeypatch, calls)
    _stub_finalize_host_calls(monkeypatch, calls)

    _finalize(config, store, _feature())

    assert "verify_host" not in calls
    # Teardown read plus the in-sandbox test run.
    assert calls.count("sbx_exec") == 2
    assert _job(store, "box-1")["status"] == "succeeded"


def test_finalize_success_test_failure_skips_push_and_removes_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, test_command="pytest", remove_sandbox_on_failure=True)
    store = StateStore(config.state_db_path)
    _seed_done(store)
    calls: list[str] = []
    _stub_teardown_read(monkeypatch, calls)
    _stub_finalize_host_calls(monkeypatch, calls, verify_ok=False)
    monkeypatch.setattr(finish_module, "open_pr", _refuse)

    _finalize(config, store, _feature())

    job = _job(store, "box-1")
    assert job["status"] == "tests_failed"
    assert job["tests_passed"] == 0
    assert "push" not in calls  # nothing reaches origin when tests fail
    assert "rm" in calls


# --- question options --------------------------------------------------------


def test_question_options_from_reads_offered_options() -> None:
    status = {"state": "awaiting_input", "message": "which?", "options": ["Keep it", "Drop it"]}
    assert finish_module.question_options_from(status) == '["Keep it", "Drop it"]'


def test_question_options_from_ignores_options_outside_awaiting_input() -> None:
    assert finish_module.question_options_from({"state": "done", "options": ["A"]}) is None


def test_question_options_from_rejects_a_non_list() -> None:
    assert finish_module.question_options_from({"state": "awaiting_input", "options": "A"}) is None


def test_question_options_from_drops_non_strings_and_blanks() -> None:
    status = {"state": "awaiting_input", "options": ["A", 3, "", "  ", None, "B"]}
    assert finish_module.question_options_from(status) == '["A", "B"]'


def test_question_options_from_caps_count_and_length() -> None:
    """The status file is written by the agent -- external input, bounded."""
    status = {"state": "awaiting_input", "options": ["x" * 500] + [str(i) for i in range(20)]}

    options = json.loads(finish_module.question_options_from(status) or "[]")

    assert len(options) == MAX_QUESTION_OPTIONS
    assert len(options[0]) == 200


def test_option_text_for_resolves_an_index() -> None:
    job = {"sandbox_name": "box-1", "question_options": '["Keep it", "Drop it"]'}
    assert lifecycle_module.option_text_for(job, 1) == "Drop it"


def test_option_text_for_out_of_range_raises() -> None:
    job = {"sandbox_name": "box-1", "question_options": '["Keep it"]'}
    with pytest.raises(CommandError, match="no option 3"):
        lifecycle_module.option_text_for(job, 3)


def test_option_text_for_without_options_raises() -> None:
    with pytest.raises(CommandError, match="offered 0"):
        lifecycle_module.option_text_for({"sandbox_name": "box-1", "question_options": None}, 0)


def test_poll_until_signal_records_the_options_with_the_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = StateStore(config.state_db_path)
    store.upsert("box-1", feature_id="f1", agent_id="claude", branch="b", status="running")
    monkeypatch.setattr(finish_module, "sbx_status", lambda name: "running")
    monkeypatch.setattr(
        finish_module,
        "sbx_read_status_and_tail",
        lambda *a: ({"state": "awaiting_input", "message": "which?", "options": ["A", "B"]}, ""),
    )

    finish_module.poll_until_signal(config, store, "box-1", time.monotonic() + 10)

    job = _job(store, "box-1")
    assert job["detail"] == "which?"
    assert json.loads(job["question_options"]) == ["A", "B"]


# --- host path vs container path -------------------------------------------
#
# `config.repo_path` is a *host* path (it is what `sbx create` clones and what
# every vcs.py call operates on). It was also being pasted into `sbx exec` as
# if it were a path *inside* the container. That only worked because the demo
# preset uses the relative "../langlearn" and the sandbox's working directory
# is already the clone dir of that name, so `cd ../langlearn` returned to where
# it started. An absolute host path -- what pointing the UI at a local repo
# produces -- does not exist inside the container, so the `cd` fails, `&&`
# short-circuits, and the run log (and with it every token metric) is lost.


def _container_without_host_paths(host_repo_path: str, stdout: str):
    """An sbx_exec_capture stub for a container that lacks the host's paths."""

    def fake_exec(_sandbox_name, shell_cmd, **kwargs):
        if f"cd {host_repo_path}" in shell_cmd:
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="No such file or directory"
            )
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")

    return fake_exec


def test_teardown_read_survives_an_absolute_host_repo_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run log must survive when repo_path is an absolute host path."""
    host_path = "/home/someone/code/langlearn"
    config = _config(tmp_path, repo_path=host_path)
    stdout = (
        "/work/langlearn\n"
        "---agentshowdown-dir---\n"
        "agent/f1/claude\n"
        "---agentshowdown-log---\n"
        '{"type":"result","num_turns":7,'
        '"usage":{"input_tokens":1234,"output_tokens":567}}\n'
    )
    seen: list[str] = []

    def recording_exec(sandbox_name, shell_cmd, **kwargs):
        seen.append(shell_cmd)
        return _container_without_host_paths(host_path, stdout)(sandbox_name, shell_cmd, **kwargs)

    monkeypatch.setattr(finish_module, "sbx_exec_capture", recording_exec)

    branch, run_log, container_dir = finish_module._extract_before_teardown(
        config, "box-1", "agent/f1/claude"
    )

    assert f"cd {host_path}" not in seen[0], "host path must not be cd'd to in the container"
    assert run_log != "", "run log lost -- token metrics would be silently dropped"
    assert branch == "agent/f1/claude"
    assert container_dir == "/work/langlearn"


def test_verify_on_sandbox_uses_the_discovered_container_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In-sandbox verification must target the container's own repo dir."""
    host_path = "/home/someone/code/langlearn"
    config = _config(tmp_path, repo_path=host_path, verify_on="sandbox")
    seen: dict[str, object] = {}

    def fake_exec(_sandbox_name, shell_cmd, **kwargs):
        seen["cmd"] = shell_cmd
        seen["workdir"] = kwargs.get("workdir")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(finish_module, "sbx_exec_capture", fake_exec)

    finish_module._verify(config, "box-1", "some-ref", "pytest", "/work/langlearn")

    assert seen["workdir"] == "/work/langlearn"
    assert host_path not in str(seen["cmd"])


def test_teardown_read_without_a_dir_marker_still_returns_the_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A container that reports no directory must not cost us the run log."""
    config = _config(tmp_path)
    monkeypatch.setattr(
        finish_module,
        "sbx_exec_capture",
        lambda *a, **kw: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='agent/f1/claude\n---agentshowdown-log---\n{"num_turns":3}\n',
            stderr="",
        ),
    )

    branch, run_log, container_dir = finish_module._extract_before_teardown(
        config, "box-1", "agent/f1/claude"
    )

    assert branch == "agent/f1/claude"
    assert "num_turns" in run_log
    assert container_dir == ""


def test_verify_on_sandbox_falls_back_to_the_default_workdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty container_dir means "wherever the sandbox already is"."""
    config = _config(tmp_path, verify_on="sandbox")
    seen: dict[str, object] = {}

    def fake_exec(_name, cmd, **kwargs):
        seen["workdir"] = kwargs.get("workdir")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(finish_module, "sbx_exec_capture", fake_exec)

    finish_module._verify(config, "box-1", "ref", "pytest", "")

    assert seen["workdir"] is None


# --- per-step timing ---------------------------------------------------------


def test_finalize_success_test_failure_records_verify_but_not_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, test_command="pytest")
    store = StateStore(config.state_db_path)
    _seed_done(store)
    calls: list[str] = []
    _stub_teardown_read(monkeypatch, calls)
    _stub_finalize_host_calls(monkeypatch, calls, verify_ok=False)

    _finalize(config, store, _feature())

    job = _job(store, "box-1")
    assert job["failure_category"] == "tests"
    assert job["verify_s"] is not None
    assert job["push_s"] is None


def test_finalize_success_lint_failure_is_categorised_as_lint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, lint_command="ruff check")
    store = StateStore(config.state_db_path)
    _seed_done(store)
    calls: list[str] = []
    _stub_teardown_read(monkeypatch, calls)
    _stub_finalize_host_calls(monkeypatch, calls, verify_ok=False)

    _finalize(config, store, _feature())

    job = _job(store, "box-1")
    assert job["status"] == "lint_failed"
    assert job["failure_category"] == "lint"


# --- timed_step --------------------------------------------------------------


def test_timed_step_logs_the_step_with_its_duration(tmp_path: Path) -> None:
    store = StateStore(str(tmp_path / "state.sqlite3"))
    store.upsert("box-1", feature_id="f1", agent_id="claude", status="running")

    with structlog.testing.capture_logs() as logs, finish_module.timed_step(store, "box-1", "push"):
        pass

    events = [e for e in logs if e["event"] == "job_step_finished"]
    assert len(events) == 1
    assert events[0]["step"] == "push"
    assert events[0]["outcome"] == "ok"
    assert isinstance(events[0]["duration_ms"], int)
    assert _job(store, "box-1")["push_s"] is not None


def test_timed_step_records_the_duration_when_the_step_raises(tmp_path: Path) -> None:
    store = StateStore(str(tmp_path / "state.sqlite3"))
    store.upsert("box-1", feature_id="f1", agent_id="claude", status="running")

    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(RuntimeError),
        finish_module.timed_step(store, "box-1", "launch"),
    ):
        raise RuntimeError("sbx create failed")

    events = [e for e in logs if e["event"] == "job_step_finished"]
    assert events[0]["outcome"] == "error"
    assert _job(store, "box-1")["launch_s"] is not None
