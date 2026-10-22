"""How a job uses its agent's OTel events: the exit wait and token usage.

Drives whole jobs through the real code path with only the subprocess
primitive stubbed, like test_sandbox_cost. The stubbed exit check stands in
for the agent process, and is where its final telemetry "arrives".
"""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest
from backend.orchestrator import job as job_module
from backend.orchestrator import sbx as sbx_module
from backend.orchestrator import vcs as vcs_module
from backend.orchestrator.config import AgentSpec, Config, Feature
from backend.orchestrator.state import AgentEventRow, StateStore

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

SANDBOX = "arena-f1-claude"
LS_JSON = json.dumps({"sandboxes": [{"name": SANDBOX, "agent": "claude", "status": "running"}]})
# What `claude --output-format json` prints: the fallback source.
LOG_USAGE = json.dumps({"usage": {"input_tokens": 7, "output_tokens": 3}, "num_turns": 2})


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
        "test_command": "",
        "lint_command": "",
        "remove_sandbox_on_success": True,
        "remove_sandbox_on_failure": True,
        "state_db_path": str(tmp_path / "state.sqlite3"),
        "agent_otel_endpoint": "http://h:8000",
    }
    return Config(**{**defaults, **overrides})  # type: ignore[arg-type]


def _ok(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class FakeSandbox:
    """Answers sbx commands for one job whose agent reports done at once.

    Attributes:
        exit_checks: How many exit checks have been made.
        exits_after: The exit check that first finds the process gone;
            None never finds it.
        on_exit: Called when the process is first seen gone -- the moment
            its flushed telemetry has been received.
        order: The sbx steps issued, to check their ordering.
        policy_returncode: What `sbx policy allow` exits with.
    """

    def __init__(self) -> None:
        self.exit_checks = 0
        self.exits_after: int | None = 1
        self.on_exit: Callable[[], None] = lambda: None
        self.order: list[str] = []
        self.policy_returncode = 0
        self._created = False

    def _check_exit(self) -> subprocess.CompletedProcess:
        self.exit_checks += 1
        self.order.append("exit_check")
        if self.exits_after is None or self.exit_checks < self.exits_after:
            return _ok(returncode=1)
        if self.exit_checks == self.exits_after:
            self.on_exit()
        return _ok()

    def run(self, cmd: list[str], **_kw: object) -> subprocess.CompletedProcess:
        verb = cmd[1]
        if verb == "create":
            self._created = True
        elif verb == "policy":
            self.order.append(" ".join(cmd[1:]))
            return _ok(returncode=self.policy_returncode)
        elif verb == "ls":
            return _ok(LS_JSON if self._created else '{"sandboxes": []}')
        elif verb == "rm":
            self.order.append("rm")
        elif verb == "exec":
            return self._exec(cmd[-1])
        return _ok()

    def _exec(self, script: str) -> subprocess.CompletedProcess:
        if script == f"test -f {sbx_module.AGENT_EXIT_PATH}":
            return self._check_exit()
        if "disown" in script:
            self.order.append("launch")
        elif "cat .agent_status.json" in script:
            return _ok('{"state": "done", "message": "finished"}')
        elif "rev-parse" in script:
            self.order.append("extract")
            return _ok(
                f"/work\n---agentshowdown-dir---\nagent/f1/claude\n"
                f"---agentshowdown-log---\n{LOG_USAGE}\n"
            )
        return _ok()


@pytest.fixture
def sandbox(monkeypatch: pytest.MonkeyPatch) -> FakeSandbox:
    fake = FakeSandbox()
    monkeypatch.setattr(sbx_module, "run", fake.run)
    monkeypatch.setattr(vcs_module, "run", lambda cmd, **kw: _ok("1\t1\ta.py\n"))
    monkeypatch.setattr(job_module, "_AGENT_EXIT_POLL_SECONDS", 0)
    return fake


def _run(config: Config, agent_id: str = "claude", command: str | None = None) -> dict:
    feature = Feature(id="f1", description="d", acceptance_criteria=[], agents=[])
    job_module.run_job(config, feature, AgentSpec(agent_id=agent_id, command=command))
    with StateStore(config.state_db_path) as store:
        row = store.get(SANDBOX if agent_id == "claude" else f"arena-f1-{agent_id}")
    assert row is not None
    return row


def _deliver(config: Config, *events: AgentEventRow) -> Callable[[], None]:
    def deliver() -> None:
        with StateStore(config.state_db_path) as store:
            store.add_agent_events(SANDBOX, list(events))

    return deliver


def _request(input_tokens: int, output_tokens: int) -> AgentEventRow:
    return AgentEventRow(
        name="claude_code.api_request",
        ts=None,
        attributes={"input_tokens": str(input_tokens), "output_tokens": str(output_tokens)},
    )


def test_teardown_waits_for_the_agent_to_exit(tmp_path: Path, sandbox: FakeSandbox) -> None:
    """The status file is written before the process exits; extracting and
    removing the sandbox in between would lose its last telemetry batch."""
    sandbox.exits_after = 3

    _run(_config(tmp_path))

    after_launch = sandbox.order[sandbox.order.index("launch") + 1 :]
    assert after_launch == ["exit_check"] * 3 + ["extract", "rm"]


def test_usage_comes_from_the_events_flushed_on_exit(tmp_path: Path, sandbox: FakeSandbox) -> None:
    config = _config(tmp_path)
    sandbox.exits_after = 2
    sandbox.on_exit = _deliver(config, _request(100, 20), _request(50, 5))

    row = _run(config)

    assert (row["input_tokens"], row["output_tokens"]) == (150, 25)
    # OTel has no turn count, so that one still comes from the log.
    assert row["num_turns"] == 2


def test_usage_falls_back_to_the_run_log_without_events(
    tmp_path: Path, sandbox: FakeSandbox
) -> None:
    row = _run(_config(tmp_path))

    assert (row["input_tokens"], row["output_tokens"], row["num_turns"]) == (7, 3, 2)


def test_events_from_an_earlier_turn_are_not_counted(tmp_path: Path, sandbox: FakeSandbox) -> None:
    """Usage covers the current turn, as the run log -- overwritten at every
    launch -- does for the fallback."""
    config = _config(tmp_path)
    with StateStore(config.state_db_path) as store:
        store.upsert(SANDBOX, feature_id="f1", agent_id="claude", status="crashed")
        store.add_agent_events(SANDBOX, [_request(9000, 9000)])
    sandbox.on_exit = _deliver(config, _request(10, 1))

    row = _run(config)

    assert (row["input_tokens"], row["output_tokens"]) == (10, 1)


def test_an_agent_that_never_exits_holds_teardown_only_until_the_timeout(
    tmp_path: Path, sandbox: FakeSandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox.exits_after = None
    monkeypatch.setattr(job_module, "AGENT_EXIT_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(job_module, "_AGENT_EXIT_POLL_SECONDS", 0.01)

    row = _run(_config(tmp_path))

    assert row["status"] == "succeeded"
    assert sandbox.order[-2:] == ["extract", "rm"]
    assert sandbox.exit_checks >= 2


def test_a_custom_command_is_not_waited_on(tmp_path: Path, sandbox: FakeSandbox) -> None:
    """A custom command exports nothing, so there is nothing to flush."""
    _run(_config(tmp_path), command="my-agent.sh")

    assert sandbox.exit_checks == 0


def _policy_calls(sandbox: FakeSandbox) -> list[str]:
    return [c for c in sandbox.order if c.startswith("policy")]


def test_a_new_sandbox_is_allowed_to_reach_the_receiver(
    tmp_path: Path, sandbox: FakeSandbox
) -> None:
    """sbx denies the route to the host by default; without the rule every
    export is refused and the timeline stays empty."""
    _run(_config(tmp_path, agent_otel_endpoint="http://host.docker.internal:8000"))

    assert _policy_calls(sandbox) == [f"policy allow network --sandbox {SANDBOX} localhost:8000"]
    # Added before the agent starts, so its first batch is not refused.
    assert sandbox.order.index(_policy_calls(sandbox)[0]) < sandbox.order.index("launch")


@pytest.mark.parametrize(
    ("overrides", "command"),
    [({"agent_otel_endpoint": None}, None), ({}, "my-agent.sh")],
)
def test_no_rule_is_added_when_nothing_is_exported(
    tmp_path: Path, sandbox: FakeSandbox, overrides: dict, command: str | None
) -> None:
    _run(_config(tmp_path, **overrides), command=command)

    assert _policy_calls(sandbox) == []


def test_a_refused_rule_does_not_fail_the_job(tmp_path: Path, sandbox: FakeSandbox) -> None:
    sandbox.policy_returncode = 1

    row = _run(_config(tmp_path))

    assert row["status"] == "succeeded"
