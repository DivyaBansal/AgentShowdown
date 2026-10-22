"""Tests for backend.orchestrator.sbx."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from backend.orchestrator import sbx
from backend.orchestrator.config import Config
from backend.orchestrator.process import CommandError, CommandTimeout, RunOptions, run


def _config(**overrides) -> Config:
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
        "poll_interval_seconds": 15,
        "timeout_minutes": 60,
        "max_concurrency": 3,
        "test_command": "",
        "lint_command": "",
        "remove_sandbox_on_success": True,
        "remove_sandbox_on_failure": False,
        "state_db_path": ":memory:",
        "provider": None,
        "agent_profiles": {},
    }
    defaults.update(overrides)
    return Config(**defaults)


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_build_claude_cmd_default_flags() -> None:
    cmd = sbx.build_claude_cmd("do the thing", True, "claude-haiku-4-5", None)
    assert cmd == [
        "claude",
        "--dangerously-skip-permissions",
        "--model",
        "claude-haiku-4-5",
        "--print",
        "do the thing",
    ]


def test_build_claude_cmd_without_skip_permissions_or_model() -> None:
    cmd = sbx.build_claude_cmd("do the thing", False, "", None)
    assert cmd == ["claude", "--print", "do the thing"]


def test_build_claude_cmd_with_max_turns() -> None:
    cmd = sbx.build_claude_cmd("do the thing", False, "", 5)
    assert "--max-turns" in cmd
    assert cmd[cmd.index("--max-turns") + 1] == "5"


def test_build_claude_cmd_resume_appends_continue() -> None:
    cmd = sbx.build_claude_cmd("follow up", False, "", None, resume=True)
    assert "--continue" in cmd
    # --continue must come before the trailing prompt argument.
    assert cmd.index("--continue") < len(cmd) - 1
    assert cmd[-1] == "follow up"


def test_build_codex_cmd_default_flags() -> None:
    cmd = sbx.build_codex_cmd("do the thing", True, "gpt-5-codex", None)
    assert cmd == [
        "codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--model",
        "gpt-5-codex",
        "do the thing",
    ]


def test_build_codex_cmd_resume_inserts_resume_last_before_flags() -> None:
    cmd = sbx.build_codex_cmd("follow up", False, "", None, resume=True)
    assert cmd[:4] == ["codex", "exec", "resume", "--last"]
    assert cmd[-1] == "follow up"


def test_build_cursor_cmd_default_flags() -> None:
    cmd = sbx.build_cursor_cmd("do the thing", True, "sonnet-4.5", None)
    assert cmd == [
        "cursor-agent",
        "--print",
        "--force",
        "--model",
        "sonnet-4.5",
        "do the thing",
    ]


def test_build_cursor_cmd_resume_appends_resume_flag() -> None:
    cmd = sbx.build_cursor_cmd("follow up", False, "", None, resume=True)
    assert "--resume" in cmd
    assert cmd[-1] == "follow up"


def test_build_opencode_cmd_default_flags() -> None:
    cmd = sbx.build_opencode_cmd("do the thing", True, "anthropic/claude", None)
    assert cmd == ["opencode", "run", "--model", "anthropic/claude", "do the thing"]


def test_build_opencode_cmd_resume_appends_continue() -> None:
    cmd = sbx.build_opencode_cmd("follow up", False, "", None, resume=True)
    assert "--continue" in cmd
    assert cmd[-1] == "follow up"


def test_build_agent_cmd_known_agent_delegates_to_builder() -> None:
    cmd = sbx.build_agent_cmd("claude", "do the thing", _config())
    assert cmd[0] == "claude"
    assert cmd[-1] == "do the thing"


def test_build_agent_cmd_model_override_wins_over_config_model() -> None:
    cmd = sbx.build_agent_cmd("claude", "do the thing", _config(), model_override="haiku-override")
    assert "haiku-override" in cmd
    assert "claude-haiku-4-5" not in cmd


def test_build_agent_cmd_unknown_agent_raises() -> None:
    with pytest.raises(CommandError, match="gemini"):
        sbx.build_agent_cmd("gemini", "do the thing", _config())


def test_build_agent_invocation_known_agent_returns_shlex_joined_argv() -> None:
    cmd_str = sbx.build_agent_invocation("claude", "do the thing", _config(capture_usage=False))
    assert (
        cmd_str
        == "claude --dangerously-skip-permissions --model claude-haiku-4-5 --print 'do the thing'"
    )


def test_capture_usage_adds_the_claude_json_output_flag() -> None:
    cmd = sbx.build_agent_cmd("claude", "hi", _config(capture_usage=True))
    assert "--output-format" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "json"


def test_capture_usage_adds_the_codex_json_flag() -> None:
    cmd = sbx.build_agent_cmd("codex", "hi", _config(capture_usage=True))
    assert "--json" in cmd


def test_capture_usage_off_leaves_the_agent_output_human_readable() -> None:
    assert "--output-format" not in sbx.build_agent_cmd(
        "claude", "hi", _config(capture_usage=False)
    )
    assert "--json" not in sbx.build_agent_cmd("codex", "hi", _config(capture_usage=False))


def test_capture_usage_is_accepted_and_ignored_by_agents_without_usage() -> None:
    """The flag is passed blind; CLIs with no usage output must not break."""
    for agent in ("cursor", "opencode", "copilot"):
        cmd = sbx.build_agent_cmd(agent, "hi", _config(capture_usage=True))
        assert "--json" not in cmd
        assert "--output-format" not in cmd


def test_build_agent_invocation_command_override_exports_env_and_runs_verbatim() -> None:
    cmd_str = sbx.build_agent_invocation(
        "shell",
        "do the thing",
        _config(),
        command_override="echo $ORCH_PROMPT > out.txt",
    )
    assert "export ORCH_PROMPT='do the thing'\n" in cmd_str
    assert "export ORCH_STATUS_FILE=.agent_status.json\n" in cmd_str
    assert "export ORCH_RESUME=0\n" in cmd_str
    assert cmd_str.endswith("echo $ORCH_PROMPT > out.txt")


def test_build_agent_invocation_command_override_resume_sets_orch_resume_1() -> None:
    cmd_str = sbx.build_agent_invocation(
        "shell", "follow up", _config(), command_override="my-script.sh", resume=True
    )
    assert "export ORCH_RESUME=1\n" in cmd_str


def test_build_agent_shell_command_captures_exit_code_and_falls_back() -> None:
    shell_cmd = sbx.build_agent_shell_command(
        "claude --print hi", ".agent_status.json", "/tmp/sbx-agent-run.log"
    )
    assert "EXIT_CODE=$?" in shell_cmd
    assert "if [ ! -f .agent_status.json ]; then" in shell_cmd
    assert "python3 -c" in shell_cmd
    assert 'exit "$EXIT_CODE"' in shell_cmd


def test_build_agent_shell_command_redirects_only_final_line() -> None:
    cmd_str = "export FOO=bar\nmy-command --flag"
    shell_cmd = sbx.build_agent_shell_command(
        cmd_str, ".agent_status.json", "/tmp/sbx-agent-run.log"
    )
    lines = shell_cmd.splitlines()
    assert lines[0] == "export FOO=bar"
    assert lines[1] == "my-command --flag > /tmp/sbx-agent-run.log 2>&1"


_LS_JSON = json.dumps(
    {
        "sandboxes": [
            {
                "name": "box-1",
                "id": "96f315ef-5da2-46eb-b001-b86801bc2b73",
                "agent": "claude",
                "status": "running",
                "ports": [],
                "workspaces": ["/home/u/repo"],
            },
            {"name": "box-2", "id": "b2", "agent": "codex", "status": "stopped"},
        ]
    }
)


def test_sbx_launch_agent_does_not_pass_detach_to_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sbx v0.48.0 fails `sbx exec -d` with "--detach is not supported for
    exec", so the agent never starts. The launch must self-daemonize inside
    a plain `sbx exec` instead.
    """
    calls = []
    monkeypatch.setattr(sbx, "run", lambda cmd, **k: calls.append(cmd) or _completed())

    sbx.sbx_launch_agent("box-1", "claude", "do the thing", _config())

    assert len(calls) == 1
    argv = calls[0]
    assert argv[:5] == ["sbx", "exec", "box-1", "bash", "-lc"]
    assert "-d" not in argv
    assert "--detach" not in argv
    # The script still backgrounds itself so the exec returns immediately.
    assert "&\ndisown" in argv[5]


def test_sbx_launch_agent_clears_the_previous_turns_status_before_returning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: a resumed job's first poll read the old `awaiting_input`
    status file and ended the turn before the resumed agent had started.

    Runs the real launch script in a local shell, standing in for the
    sandbox, with a stale status file present.
    """
    stale = tmp_path / ".agent_status.json"
    stale.write_text('{"state": "awaiting_input", "message": "old question"}')
    scripts: list[str] = []
    monkeypatch.setattr(sbx, "run", lambda cmd, **k: scripts.append(cmd[-1]) or _completed())
    monkeypatch.setattr(sbx, "AGENT_LOG_PATH", str(tmp_path / "run.log"))

    sbx.sbx_launch_agent(
        "box-1", "shell", "answer", _config(), command_override="sleep 5", resume=True
    )
    run(["bash", "-c", scripts[0]], options=RunOptions(cwd=str(tmp_path), timeout=10))

    # Checked as soon as the launch returns, as the first poll would be.
    assert not stale.exists()


def test_sbx_launch_agent_keeps_the_status_file_out_of_the_agents_commits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: an agent that ran `git add -A` committed the orchestrator's
    status file onto its feature branch, so it showed up in the diff.

    Runs the real launch script twice (a fresh turn, then a resume) in a
    local git repo standing in for the sandbox's clone.
    """
    run(["git", "init", "-q", str(tmp_path)])
    scripts: list[str] = []
    monkeypatch.setattr(sbx, "run", lambda cmd, **k: scripts.append(cmd[-1]) or _completed())
    monkeypatch.setattr(sbx, "AGENT_LOG_PATH", str(tmp_path / "run.log"))
    monkeypatch.setattr(sbx, "AGENT_EXIT_PATH", str(tmp_path / "exit"))

    sbx.sbx_launch_agent("box-1", "shell", "go", _config(), command_override="true")
    sbx.sbx_launch_agent("box-1", "shell", "go", _config(), command_override="true", resume=True)
    for script in scripts:
        run(["bash", "-c", script], options=RunOptions(cwd=str(tmp_path), timeout=10))
    (tmp_path / ".agent_status.json").write_text('{"state": "done", "message": "ok"}')

    status = run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        options=RunOptions(cwd=str(tmp_path)),
    ).stdout
    assert ".agent_status.json" not in status
    # Idempotent across turns: one exclude line, not one per launch.
    exclude = (tmp_path / ".git" / "info" / "exclude").read_text()
    assert exclude.splitlines().count("/.agent_status.json") == 1


def test_sbx_list_uses_the_host_side_json_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_run(cmd, **k):
        calls.append(cmd)
        return _completed(stdout=_LS_JSON)

    monkeypatch.setattr(sbx, "run", fake_run)

    listing = sbx.sbx_list()

    # `sbx ls` is host-side; it must never shell into a sandbox.
    assert calls == [["sbx", "ls", "--json"]]
    assert set(listing) == {"box-1", "box-2"}
    assert listing["box-1"]["status"] == "running"


def test_sbx_list_reads_the_bare_array_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    """sbx v0.48.0 dropped the {"sandboxes": [...]} wrapper. Reading the bare
    array as empty made a stopped sandbox look gone, so respawn tried to
    `sbx create` over the existing name and failed.
    """
    bare = json.dumps([{"name": "box-1", "agent": "claude", "status": "stopped"}])
    monkeypatch.setattr(sbx, "run", lambda *a, **k: _completed(stdout=bare))

    assert sbx.sbx_status("box-1") == "stopped"
    assert sbx.sbx_exists("box-1") is True


def test_sbx_exists_true_when_name_in_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sbx, "run", lambda *a, **k: _completed(stdout=_LS_JSON))
    assert sbx.sbx_exists("box-1") is True
    assert sbx.sbx_exists("box-3") is False


def test_sbx_status_reads_the_status_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sbx, "run", lambda *a, **k: _completed(stdout=_LS_JSON))
    assert sbx.sbx_status("box-1") == "running"
    assert sbx.sbx_status("box-2") == "stopped"


def test_sbx_status_unknown_when_not_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sbx, "run", lambda *a, **k: _completed(stdout=_LS_JSON))
    assert sbx.sbx_status("box-3") == "unknown"


def test_sbx_list_empty_when_sbx_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sbx, "run", lambda *a, **k: _completed(stdout="", returncode=1))
    assert sbx.sbx_list() == {}
    assert sbx.sbx_exists("box-1") is False


def test_sbx_list_empty_on_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sbx, "run", lambda *a, **k: _completed(stdout="not json"))
    assert sbx.sbx_list() == {}


def test_sbx_list_empty_when_payload_shape_is_unexpected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sbx, "run", lambda *a, **k: _completed(stdout='{"sandboxes": "nope"}'))
    assert sbx.sbx_list() == {}


def test_status_read_skips_the_login_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """The polled status read must not source the profile on every tick."""
    calls = []

    def fake_run(cmd, **k):
        calls.append(cmd)
        return _completed(stdout='{"state": "done"}')

    monkeypatch.setattr(sbx, "run", fake_run)
    sbx.sbx_read_status("box-1", ".agent_status.json")

    assert calls[0][:5] == ["sbx", "exec", "box-1", "bash", "-c"]


def test_sbx_read_status_missing_file_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sbx, "sbx_exec_capture", lambda *a, **k: _completed(stdout="", returncode=1)
    )
    assert sbx.sbx_read_status("box-1", ".agent_status.json") is None


def test_sbx_read_status_valid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sbx,
        "sbx_exec_capture",
        lambda *a, **k: _completed(stdout='{"state": "done", "message": "all set"}'),
    )
    status = sbx.sbx_read_status("box-1", ".agent_status.json")
    assert status == {"state": "done", "message": "all set"}


def test_sbx_read_status_malformed_json_returns_unknown_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sbx, "sbx_exec_capture", lambda *a, **k: _completed(stdout="not json"))
    status = sbx.sbx_read_status("box-1", ".agent_status.json")
    assert status is not None
    assert status["state"] == "unknown"


# --- sbx_read_status_and_tail / sbx_tail_log -----------------------------------


def test_read_status_and_tail_combines_status_and_log_in_one_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_exec(name, cmd, **kw):
        calls.append(cmd)
        return _completed(
            stdout='{"state": "done", "message": "yay"}\n'
            "---agentshowdown-tail---\nbuilding...\ndone.\n"
        )

    monkeypatch.setattr(sbx, "sbx_exec_capture", fake_exec)

    status, tail = sbx.sbx_read_status_and_tail(
        "box-1", ".agent_status.json", "/tmp/sbx-agent-run.log", 200
    )

    assert len(calls) == 1  # one exec covers both reads
    assert status == {"state": "done", "message": "yay"}
    assert tail == "building...\ndone.\n"  # tail's own trailing newline is kept


def test_read_status_and_tail_missing_status_file_returns_none_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sbx,
        "sbx_exec_capture",
        lambda *a, **k: _completed(stdout="---agentshowdown-tail---\nsome output\n"),
    )

    status, tail = sbx.sbx_read_status_and_tail(
        "box-1", ".agent_status.json", "/tmp/sbx-agent-run.log", 200
    )

    assert status is None  # still running, from the caller's point of view
    assert tail == "some output\n"


def test_read_status_and_tail_malformed_status_returns_unknown_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sbx,
        "sbx_exec_capture",
        lambda *a, **k: _completed(stdout="not json\n---agentshowdown-tail---\n"),
    )

    status, _tail = sbx.sbx_read_status_and_tail(
        "box-1", ".agent_status.json", "/tmp/sbx-agent-run.log", 200
    )

    assert status is not None
    assert status["state"] == "unknown"


def test_read_status_and_tail_missing_log_returns_empty_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sbx,
        "sbx_exec_capture",
        lambda *a, **k: _completed(stdout='{"state": "done"}\n---agentshowdown-tail---\n'),
    )

    _status, tail = sbx.sbx_read_status_and_tail(
        "box-1", ".agent_status.json", "/tmp/sbx-agent-run.log", 200
    )

    assert tail == ""


# --- sbx_stop / sbx_restart_detached -------------------------------------------


def test_sbx_stop_calls_sbx_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(sbx, "run", lambda cmd, **k: calls.append(cmd) or _completed())
    sbx.sbx_stop("box-1")
    assert calls == [["sbx", "stop", "box-1"]]


def test_sbx_restart_detached_calls_sbx_run_with_name_and_detached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(sbx, "run", lambda cmd, **k: calls.append(cmd) or _completed())
    sbx.sbx_restart_detached("box-1", "claude")
    assert calls == [["sbx", "run", "claude", "--name", "box-1", "--detached"]]


def test_sbx_tail_log_passes_through_sandbox_output(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def fake_exec(name, cmd, **kw):
        seen["name"], seen["cmd"] = name, cmd
        return _completed(stdout="line one\nline two\n")

    monkeypatch.setattr(sbx, "sbx_exec_capture", fake_exec)

    tail = sbx.sbx_tail_log("box-1", "/tmp/sbx-agent-run.log", 50)

    assert tail == "line one\nline two\n"
    assert seen["name"] == "box-1"
    assert "tail -n 50" in seen["cmd"]


# --- build_copilot_cmd ---------------------------------------------------------


def test_build_copilot_cmd_default_flags() -> None:
    cmd = sbx.build_copilot_cmd("do the thing", True, "gpt-5", None)
    assert cmd == ["copilot", "-p", "do the thing", "--allow-all", "--model", "gpt-5"]


def test_build_copilot_cmd_without_skip_permissions_or_model() -> None:
    cmd = sbx.build_copilot_cmd("do the thing", False, "", None)
    assert cmd == ["copilot", "-p", "do the thing"]


def test_build_copilot_cmd_resume_raises_command_error() -> None:
    with pytest.raises(CommandError, match="resume"):
        sbx.build_copilot_cmd("follow up", False, "", None, resume=True)


def test_copilot_in_agent_cli_builders() -> None:
    assert "copilot" in sbx.AGENT_CLI_BUILDERS


# --- build_agent_cmd skip_permissions_override ----------------------------------


def test_build_agent_cmd_skip_permissions_override_false_beats_config_true() -> None:
    cmd = sbx.build_agent_cmd(
        "claude",
        "do the thing",
        _config(dangerously_skip_permissions=True),
        skip_permissions_override=False,
    )
    assert "--dangerously-skip-permissions" not in cmd


def test_build_agent_cmd_skip_permissions_override_none_falls_back_to_config() -> None:
    cmd = sbx.build_agent_cmd(
        "claude",
        "do the thing",
        _config(dangerously_skip_permissions=True),
        skip_permissions_override=None,
    )
    assert "--dangerously-skip-permissions" in cmd


# --- sbx_create_detached --kit / --provider passthrough -------------------------


def test_sbx_create_detached_no_kit_or_provider_matches_today(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(sbx, "run", lambda cmd, **k: calls.append(cmd))
    sbx.sbx_create_detached("box-1", "claude", "/repo")
    assert calls == [["sbx", "create", "--clone", "claude", "--name", "box-1", "/repo"]]


def test_sbx_create_detached_passes_repeated_kit_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(sbx, "run", lambda cmd, **k: calls.append(cmd))
    sbx.sbx_create_detached("box-1", "claude", "/repo", kit=["./kits/a", "./kits/b"])
    assert calls == [
        [
            "sbx",
            "create",
            "--clone",
            "claude",
            "--name",
            "box-1",
            "--kit",
            "./kits/a",
            "--kit",
            "./kits/b",
            "/repo",
        ]
    ]


def test_sbx_create_detached_passes_provider_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(sbx, "run", lambda cmd, **k: calls.append(cmd))
    sbx.sbx_create_detached("box-1", "claude", "/repo", provider="ollama", model="gemma3:e4b")
    assert calls == [
        [
            "sbx",
            "create",
            "--clone",
            "claude",
            "--name",
            "box-1",
            "--provider",
            "ollama",
            "--model",
            "gemma3:e4b",
            "/repo",
        ]
    ]


def test_sbx_create_detached_emits_sorted_env_flags_before_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(sbx, "run", lambda cmd, **k: calls.append(cmd))
    sbx.sbx_create_detached(
        "box-1",
        "claude",
        "/repo",
        env={
            "ANTHROPIC_BASE_URL": "http://host.docker.internal:11434",
            "ANTHROPIC_AUTH_TOKEN": "ollama",
        },
    )
    assert calls == [
        [
            "sbx",
            "create",
            "--clone",
            "claude",
            "--name",
            "box-1",
            "-e",
            "ANTHROPIC_AUTH_TOKEN=ollama",
            "-e",
            "ANTHROPIC_BASE_URL=http://host.docker.internal:11434",
            "/repo",
        ]
    ]


def test_sbx_create_detached_empty_env_adds_no_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(sbx, "run", lambda cmd, **k: calls.append(cmd))
    sbx.sbx_create_detached("box-1", "claude", "/repo", env={})
    assert calls == [["sbx", "create", "--clone", "claude", "--name", "box-1", "/repo"]]


def test_sbx_create_detached_passes_resource_limits_before_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(sbx, "run", lambda cmd, **k: calls.append(cmd))
    sbx.sbx_create_detached("box-1", "claude", "/repo", cpus=2, memory="4g")
    assert calls == [
        [
            "sbx",
            "create",
            "--clone",
            "claude",
            "--name",
            "box-1",
            "--cpus",
            "2",
            "--memory",
            "4g",
            "/repo",
        ]
    ]


def test_exec_capture_emits_workdir_flag_only_when_set(monkeypatch) -> None:
    """`-w` targets a container path; omitting it keeps the sandbox default."""
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sbx, "run", fake_run)

    sbx.sbx_exec_capture("box-1", "echo hi")
    sbx.sbx_exec_capture("box-1", "echo hi", workdir="/work/repo")

    assert "-w" not in seen[0]
    assert seen[1][:4] == ["sbx", "exec", "-w", "/work/repo"]


def test_sbx_list_bounds_how_long_the_listing_may_take(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`sbx ls` runs from HTTP handlers, so it must not be able to hang one."""
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return _completed(stdout=_LS_JSON)

    monkeypatch.setattr(sbx, "run", fake_run)

    sbx.sbx_list()

    assert seen["options"].timeout > 0


def test_sbx_list_empty_when_the_command_hangs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A wedged `sbx` reads as "no sandboxes", not as an exception.

    Regression test: this used to propagate out of the poll loop and the
    /sandboxes handler, because sbx_list passed no timeout at all.
    """

    def fake_run(*_args, **_kwargs):
        raise CommandTimeout("sbx ls hung")

    monkeypatch.setattr(sbx, "run", fake_run)

    assert sbx.sbx_list() == {}
    assert sbx.sbx_exists("box-1") is False


def test_sbx_launch_agent_exports_claude_telemetry_to_the_jobs_receiver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts: list[str] = []
    monkeypatch.setattr(sbx, "run", lambda cmd, **k: scripts.append(cmd[-1]) or _completed())

    sbx.sbx_launch_agent(
        "box-1", "claude", "do the thing", _config(agent_otel_endpoint="http://h:8000")
    )

    assert "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=http://h:8000/otel/box-1/v1/logs" in scripts[0]


def test_sbx_launch_agent_leaves_a_custom_command_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts: list[str] = []
    monkeypatch.setattr(sbx, "run", lambda cmd, **k: scripts.append(cmd[-1]) or _completed())

    sbx.sbx_launch_agent(
        "box-1",
        "claude",
        "do the thing",
        _config(agent_otel_endpoint="http://h:8000"),
        command_override="my-script.sh",
    )

    assert "OTEL_" not in scripts[0]


def test_sbx_launch_agent_without_an_endpoint_exports_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts: list[str] = []
    monkeypatch.setattr(sbx, "run", lambda cmd, **k: scripts.append(cmd[-1]) or _completed())

    sbx.sbx_launch_agent("box-1", "codex", "do the thing", _config(agent_otel_endpoint=None))

    assert "otel." not in scripts[0]


def test_the_launch_wrapper_records_the_exit_code_after_the_agent_exits(
    tmp_path: Path,
) -> None:
    """Runs the real wrapper locally, standing in for the sandbox."""
    exit_file = tmp_path / "exit"
    script = sbx.build_agent_shell_command(
        "sh -c 'exit 3'", ".agent_status.json", str(tmp_path / "run.log"), str(exit_file)
    )

    result = run(
        ["bash", "-c", script],
        options=RunOptions(cwd=str(tmp_path), timeout=10, check=False),
    )

    assert result.returncode == 3
    assert exit_file.read_text().strip() == "3"
    # Written after the fallback status, so it marks the end of the turn.
    assert (tmp_path / ".agent_status.json").exists()


def test_sbx_launch_agent_clears_the_previous_turns_exit_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts: list[str] = []
    monkeypatch.setattr(sbx, "run", lambda cmd, **k: scripts.append(cmd[-1]) or _completed())

    sbx.sbx_launch_agent("box-1", "claude", "hi", _config())

    first_line = scripts[0].splitlines()[0]
    assert first_line.startswith("rm -f ")
    assert sbx.AGENT_EXIT_PATH in first_line


@pytest.mark.parametrize(("returncode", "exited"), [(0, True), (1, False)])
def test_sbx_agent_exited_checks_for_the_exit_marker(
    monkeypatch: pytest.MonkeyPatch, returncode: int, exited: bool
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        sbx, "run", lambda cmd, **k: calls.append(cmd) or _completed(returncode=returncode)
    )

    assert sbx.sbx_agent_exited("box-1") is exited
    assert calls[0][-1] == f"test -f {sbx.AGENT_EXIT_PATH}"
