"""sbx wrapper functions for sandbox: create, launch, poll, teardown."""

from __future__ import annotations

import json
import shlex
from typing import TYPE_CHECKING

from backend.logging import log
from backend.orchestrator.agent_otel import exports_otel, logs_endpoint, with_otel
from backend.orchestrator.process import CommandError, CommandTimeout, RunOptions, run

if TYPE_CHECKING:
    import subprocess

    from backend.orchestrator.config import Config

# Path *inside* the sandbox container, not on the host -- S108 is about
# host-side temp files, which this is not.
AGENT_LOG_PATH = "/tmp/sbx-agent-run.log"  # noqa: S108
# Written by the launch wrapper once the agent process has exited, holding
# its exit code. Removed at every launch, so its presence means *this*
# turn's process is gone -- and with it, any telemetry it flushed on exit.
AGENT_EXIT_PATH = "/tmp/sbx-agent-exit"  # noqa: S108


def sbx_create_detached(
    sandbox_name: str,
    agent: str,
    workspace: str,
    *,
    kit: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
    env: dict[str, str] | None = None,
    cpus: int | None = None,
    memory: str | None = None,
) -> None:
    """
    Create a sandbox (in --clone mode) without attaching or launching the
    agent. Equivalent to: sbx create --clone claude --name X <workspace>

    NOTE: `sbx run <agent> --detached -- ...` looks like it launches the
    agent headlessly and returns once it's running, but in practice (as of
    sbx v0.38.0) it creates the sandbox and prints "Starting claude agent"
    without ever actually starting the claude process inside it -- the
    sandbox then just sits idle forever with no agent running, and no error.
    Verified via `ps aux` inside the sandbox after `sbx run --detached`
    across several flag combinations. Splitting create + launch (below) and
    starting the agent ourselves via `sbx exec -d` avoids that dead end.

    Args:
        sandbox_name: Name for the new sandbox.
        agent: The sbx agent kit to clone (e.g. "claude").
        workspace: Host repo path to clone into the sandbox.
        kit: sbx `--kit` refs to attach (dir/zip/OCI, repeatable), from
            config.resolve_kit_paths. Auth wrappers or skill/mixin kits.
        provider: sbx `--provider` (e.g. "ollama", sbx >=0.39.0), from
            config.resolve_provider. Routes the agent to an alternative
            model backend at sandbox-creation time.
        model: Paired with `provider` -- passed as `--model` alongside
            `--provider` when provider is set. Unused otherwise (the
            in-sandbox CLI invocation carries its own --model flag).
        env: Environment variables to bake into the sandbox, emitted as
            repeated `-e KEY=VALUE` flags (sorted for a stable command).
            From config.resolve_env -- e.g. `ANTHROPIC_BASE_URL` /
            `ANTHROPIC_AUTH_TOKEN` to route the agent CLI at a local
            Ollama instead of the proxy-managed cloud credential.
        cpus: `--cpus`, CPUs for the sandbox's VM. None keeps sbx's
            default (every host CPU).
        memory: `--memory`, e.g. "2g". None keeps sbx's default (half the
            host's memory). Both are hard limits: verified on v0.48.0 that
            `nproc` and `free` inside the sandbox report them, and that
            allocating past the memory limit fails.
    """
    cmd = ["sbx", "create", "--clone", agent, "--name", sandbox_name]
    for kit_ref in kit or []:
        cmd += ["--kit", kit_ref]
    for key, value in sorted((env or {}).items()):
        cmd += ["-e", f"{key}={value}"]
    if cpus is not None:
        cmd += ["--cpus", str(cpus)]
    if memory is not None:
        cmd += ["--memory", memory]
    if provider:
        cmd += ["--provider", provider]
        if model:
            cmd += ["--model", model]
    cmd.append(workspace)
    run(cmd, options=RunOptions(capture=False))


def build_claude_cmd(
    prompt: str,
    dangerously_skip_permissions: bool,
    model: str,
    max_turns: int | None,
    *,
    resume: bool = False,
    capture_usage: bool = False,
) -> list[str]:
    """Builds the `claude` CLI invocation for a headless run or resume.

    Args:
        prompt: The prompt (or, when resume=True, the follow-up prompt).
        dangerously_skip_permissions: Whether to pass
            --dangerously-skip-permissions.
        model: Model name, or "" to use claude's default.
        max_turns: Optional turn cap.
        resume: When True, appends --continue to resume the most recent
            session in the sandbox's workspace (confirmed via sbx's own
            `sbx run claude -- --continue` usage example) instead of
            starting a fresh conversation.

    Returns:
        The argv list to run inside the sandbox.
    """
    cmd = ["claude"]
    if dangerously_skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    if model:
        cmd += ["--model", model]
    cmd.append("--print")
    if capture_usage:
        # Emits a final result object carrying usage.* and num_turns, which
        # metrics.parse_usage reads back out of the run log. Verified against
        # the claude CLI inside a real sandbox.
        cmd += ["--output-format", "json"]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    if resume:
        cmd.append("--continue")
    cmd.append(prompt)
    return cmd


def build_codex_cmd(
    prompt: str,
    dangerously_skip_permissions: bool,
    model: str,
    max_turns: int | None,
    *,
    resume: bool = False,
    capture_usage: bool = False,
) -> list[str]:
    """Builds the `codex` CLI invocation for a headless run or resume.

    Best-effort: codex's non-interactive flags move around across
    releases. If this drifts from what's actually installed in the
    sandbox image, either fix this builder or set `command:` on the
    AgentSpec to bypass it entirely (see AgentSpec.command).

    Args:
        prompt: The prompt (or, when resume=True, the follow-up prompt).
        dangerously_skip_permissions: Whether to run fully unattended
            (approvals and sandboxing both bypassed).
        model: Model name, or "" to use codex's default.
        max_turns: Unused -- codex's `exec` mode has no turn-cap flag.
        resume: When True, continues the sandbox's most recent codex
            session instead of starting fresh.

    Returns:
        The argv list to run inside the sandbox.
    """
    cmd = ["codex", "exec"]
    if capture_usage:
        # Prints events to stdout as JSONL, including token counts.
        cmd.append("--json")
    if resume:
        cmd += ["resume", "--last"]
    if dangerously_skip_permissions:
        cmd.append("--dangerously-bypass-approvals-and-sandbox")
    if model:
        cmd += ["--model", model]
    cmd.append(prompt)
    return cmd


def build_cursor_cmd(
    prompt: str,
    dangerously_skip_permissions: bool,
    model: str,
    max_turns: int | None,
    *,
    resume: bool = False,
    capture_usage: bool = False,
) -> list[str]:
    """Builds the `cursor-agent` CLI invocation for a headless run or resume.

    Best-effort: see the caveat on build_codex_cmd -- verify against
    the installed cursor-agent version, or override via `command:`.

    Args:
        prompt: The prompt (or, when resume=True, the follow-up prompt).
        dangerously_skip_permissions: Whether to pass --force (skip
            confirmation prompts for file edits/commands).
        model: Model name, or "" to use cursor-agent's default.
        max_turns: Unused -- cursor-agent's print mode has no turn-cap flag.
        resume: When True, resumes the sandbox's most recent cursor-agent
            session instead of starting fresh.

    Returns:
        The argv list to run inside the sandbox.
    """
    cmd = ["cursor-agent", "--print"]
    if dangerously_skip_permissions:
        cmd.append("--force")
    if model:
        cmd += ["--model", model]
    if resume:
        cmd.append("--resume")
    cmd.append(prompt)
    return cmd


def build_opencode_cmd(
    prompt: str,
    dangerously_skip_permissions: bool,
    model: str,
    max_turns: int | None,
    *,
    resume: bool = False,
    capture_usage: bool = False,
) -> list[str]:
    """Builds the `opencode` CLI invocation for a headless run or resume.

    Best-effort: see the caveat on build_codex_cmd -- verify against
    the installed opencode version, or override via `command:`.

    Args:
        prompt: The prompt (or, when resume=True, the follow-up prompt).
        dangerously_skip_permissions: Unused -- opencode's `run` mode
            has no separate approval gate to bypass.
        model: `provider/model` string, or "" to use opencode's default.
        max_turns: Unused -- opencode's `run` mode has no turn-cap flag.
        resume: When True, continues the sandbox's most recent opencode
            session instead of starting fresh.

    Returns:
        The argv list to run inside the sandbox.
    """
    cmd = ["opencode", "run"]
    if model:
        cmd += ["--model", model]
    if resume:
        cmd.append("--continue")
    cmd.append(prompt)
    return cmd


def build_copilot_cmd(
    prompt: str,
    dangerously_skip_permissions: bool,
    model: str,
    max_turns: int | None,
    *,
    resume: bool = False,
    capture_usage: bool = False,
) -> list[str]:
    """Builds the `copilot` CLI invocation for a headless run.

    Best-effort: copilot isn't installed on this host (unlike codex), so
    unlike build_codex_cmd this couldn't be checked against a real
    binary. Flags are sourced from GitHub's own docs (Using GitHub
    Copilot CLI / CLI programmatic reference, fetched 2026-08-25):
    `-p PROMPT` for non-interactive mode, `--allow-all` (alias `--yolo`)
    to bypass tool/path/URL approval entirely, `--model=MODEL`. Verify
    against the actual sandboxed CLI version (`sbx create copilot . &&
    sbx exec -it <sandbox> copilot --help`) and fix this builder if it's
    drifted.

    Args:
        prompt: The prompt for this turn.
        dangerously_skip_permissions: Whether to pass --allow-all.
        model: Model name, or "" to use copilot's default.
        max_turns: Unused -- copilot's non-interactive mode has no
            turn-cap flag.
        resume: Unsupported. Copilot's --resume needs a session id
            printed in a prior run's own output (not "continue most
            recent" like the other agents), and a bare --resume opens an
            interactive picker that would hang a headless sandbox
            forever -- so this raises rather than risk that. Use
            `command:` on the AgentSpec for a custom resume script, or
            resume interactively via `sbx exec -it <sandbox> copilot
            --resume`.

    Returns:
        The argv list to run inside the sandbox.

    Raises:
        CommandError: If resume=True.
    """
    if resume:
        raise CommandError(
            "copilot resume isn't wired up yet (its --resume needs a "
            "session id from the prior run's own output, unlike the "
            'other agents\' "continue most recent" flags). Set '
            "`command:` on its AgentSpec for a custom resume script, or "
            "resume interactively via `sbx exec -it <sandbox> copilot "
            "--resume`."
        )
    cmd = ["copilot", "-p", prompt]
    if dangerously_skip_permissions:
        cmd.append("--allow-all")
    if model:
        cmd += ["--model", model]
    return cmd


# Per-agent-CLI argument builders. sbx itself also supports other agent
# kits (gemini, droid, kiro, docker-agent, ...) but their unattended-run
# and resume flags aren't wired up here -- either add an entry, or run
# them via AgentSpec.command instead. There's deliberately no entry for
# "shell": sbx's bare shell kit has no coding-agent CLI of its own, so it
# only ever runs via AgentSpec.command.
AGENT_CLI_BUILDERS = {
    "claude": build_claude_cmd,
    "codex": build_codex_cmd,
    "cursor": build_cursor_cmd,
    "opencode": build_opencode_cmd,
    "copilot": build_copilot_cmd,
}

# Builders that take `dangerously_skip_permissions` but have no flag to map
# it onto, so setting it does nothing. `opencode run` is already
# non-interactive (see build_opencode_cmd). Offering the toggle for these
# would promise a change that never happens.
SKIP_PERMISSIONS_IGNORED = frozenset({"opencode"})


def build_agent_cmd(
    agent_id: str,
    prompt: str,
    config: Config,
    *,
    model_override: str | None = None,
    skip_permissions_override: bool | None = None,
    resume: bool = False,
) -> list[str]:
    """Looks up and invokes the CLI-arg builder for an agent_id.

    Args:
        agent_id: The sbx agent type (e.g. "claude").
        prompt: The prompt to run.
        config: Orchestrator config (supplies model/flags).
        model_override: Per-job model, from AgentSpec.model (already
            resolved against AgentProfile, if any). Falls back to
            config.model when None.
        skip_permissions_override: Resolved dangerously_skip_permissions
            (from config.resolve_dangerously_skip_permissions). Falls
            back to config.dangerously_skip_permissions when None.
        resume: Whether this is a resume (follow-up) turn.

    Returns:
        The argv list to run inside the sandbox.

    Raises:
        CommandError: If no builder is registered for agent_id.
    """
    builder = AGENT_CLI_BUILDERS.get(agent_id)
    if builder is None:
        raise CommandError(
            f"No CLI profile wired up for agent_id '{agent_id}' yet "
            f"(supported: {', '.join(AGENT_CLI_BUILDERS)}). Set `command:` "
            f"on its AgentSpec in features.yaml to run it via a custom "
            f"shell command instead."
        )
    return builder(
        prompt,
        skip_permissions_override
        if skip_permissions_override is not None
        else config.dangerously_skip_permissions,
        model_override if model_override is not None else config.model,
        config.max_turns,
        resume=resume,
        capture_usage=config.capture_usage,
    )


def build_agent_invocation(
    agent_id: str,
    prompt: str,
    config: Config,
    *,
    model_override: str | None = None,
    skip_permissions_override: bool | None = None,
    command_override: str | None = None,
    resume: bool = False,
    otel_url: str | None = None,
) -> str:
    """Builds the shell command string that runs one agent turn.

    Args:
        agent_id: The sbx agent type (e.g. "claude", "shell").
        prompt: The prompt (or, when resume=True, the follow-up prompt).
        config: Orchestrator config (supplies model/flags/status_file).
        model_override: Per-job model, from AgentSpec.model.
        skip_permissions_override: Resolved dangerously_skip_permissions,
            from config.resolve_dangerously_skip_permissions.
        command_override: Per-job custom command, from AgentSpec.command.
            When set, it is run verbatim (via `bash -lc`, so shell
            syntax like `&&` and pipes works) instead of consulting
            AGENT_CLI_BUILDERS, with ORCH_PROMPT, ORCH_STATUS_FILE and
            ORCH_RESUME exported so the command can read the prompt and
            report status however it likes. Required for agent_id
            "shell"; optional escape hatch for any other agent_id.
        resume: Whether this is a resume (follow-up) turn.
        otel_url: OTLP/HTTP logs URL to export the CLI's telemetry to (see
            agent_otel). Ignored with command_override, which replaces the
            registered CLI invocation the settings attach to.

    Returns:
        A shell command string (may be multiple lines when
        command_override is set, since env exports precede it).
    """
    if command_override:
        env = {
            "ORCH_PROMPT": prompt,
            "ORCH_STATUS_FILE": config.status_file,
            "ORCH_RESUME": "1" if resume else "0",
        }
        exports = "".join(f"export {k}={shlex.quote(v)}\n" for k, v in env.items())
        return exports + command_override
    argv = build_agent_cmd(
        agent_id,
        prompt,
        config,
        model_override=model_override,
        skip_permissions_override=skip_permissions_override,
        resume=resume,
    )
    if otel_url:
        argv = with_otel(agent_id, argv, otel_url)
    return shlex.join(argv)


# Fallback status writer, run inside the sandbox only if the agent exits
# without ever writing config.status_file itself (crash, OOM, killed,
# hang-then-timeout-kill). Paths and the exit code are passed as argv, not
# interpolated into this source, so log content can never break the script
# regardless of what it contains.
STATUS_FALLBACK_PY = r"""
import json, pathlib, sys, datetime
status_path = pathlib.Path(sys.argv[1])
log_path = pathlib.Path(sys.argv[2])
exit_code = sys.argv[3]
tail = ""
if log_path.exists():
    tail = log_path.read_text(errors="replace")[-4000:]
status_path.write_text(json.dumps({
    "state": "crashed",
    "message": f"agent process exited with code {exit_code} without writing a status file",
    "detail": tail,
    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
}))
"""


def build_agent_shell_command(
    cmd_str: str, status_file: str, log_path: str, exit_path: str = AGENT_EXIT_PATH
) -> str:
    """Builds the `bash -lc` script that runs the agent and guarantees a
    status file exists afterward.

    Args:
        cmd_str: The agent invocation (from build_agent_invocation). May
            span multiple lines (env exports followed by the command) --
            only the final line's exit code and output redirection matter,
            which is what the trailing "> log 2>&1" below attaches to.
        status_file: Workspace-relative path the agent is expected to write.
        log_path: Where to redirect the agent's stdout/stderr.
        exit_path: Where to record the exit code once the agent has exited.
            Written last, after the fallback status, so it marks the end of
            everything this turn does.

    Returns:
        A shell script: run the agent, capture its exit code, and -- only
        if status_file still doesn't exist -- write a "crashed" status via
        `python3 -c`, with the log tail and exit code passed as argv so no
        shell-escaping of log content is ever needed.
    """
    exit_q = shlex.quote(exit_path)
    log_q = shlex.quote(log_path)
    status_q = shlex.quote(status_file)
    fallback_q = shlex.quote(STATUS_FALLBACK_PY)
    return (
        f"{cmd_str} > {log_q} 2>&1\n"
        f"EXIT_CODE=$?\n"
        f"if [ ! -f {status_q} ]; then\n"
        f'  python3 -c {fallback_q} {status_q} {log_q} "$EXIT_CODE"\n'
        f"fi\n"
        f'echo "$EXIT_CODE" > {exit_q}\n'
        f'exit "$EXIT_CODE"\n'
    )


def sbx_launch_agent(
    sandbox_name: str,
    agent_id: str,
    prompt: str,
    config: Config,
    *,
    model_override: str | None = None,
    skip_permissions_override: bool | None = None,
    command_override: str | None = None,
    resume: bool = False,
) -> None:
    """Starts (or resumes) the agent inside an already-created sandbox,
    self-daemonized inside a plain `sbx exec` so this call returns immediately.

    Args:
        sandbox_name: Target sandbox.
        agent_id: The sbx agent type (e.g. "claude").
        prompt: Prompt for this turn.
        config: Orchestrator config.
        model_override: Per-job model, from AgentSpec.model.
        skip_permissions_override: Resolved dangerously_skip_permissions,
            from config.resolve_dangerously_skip_permissions.
        command_override: Per-job custom command, from AgentSpec.command
            (see build_agent_invocation).
        resume: Whether to resume the most recent session instead of
            starting fresh.
    """
    endpoint = config.agent_otel_endpoint
    otel_url = (
        logs_endpoint(endpoint, sandbox_name)
        if endpoint and exports_otel(agent_id, command_override, endpoint)
        else None
    )
    cmd_str = build_agent_invocation(
        agent_id,
        prompt,
        config,
        model_override=model_override,
        skip_permissions_override=skip_permissions_override,
        command_override=command_override,
        resume=resume,
        otel_url=otel_url,
    )
    shell_cmd = build_agent_shell_command(cmd_str, config.status_file, AGENT_LOG_PATH)
    # No `-d`: sbx v0.48.0 rejects it outright ("--detach is not supported
    # for exec"), and on earlier versions it didn't return until the
    # backgrounded command exited anyway. Self-daemonize instead: background
    # the real script ourselves with its own stdio fully detached from this
    # exec session's pipes, so `sbx exec` has nothing left to wait on and
    # returns immediately. Verified on v0.48.0 that the child outlives the
    # exec session.
    #
    # The previous turn's status file is removed first, in the foreground, so
    # it is gone before this call returns. Otherwise a resumed job's first
    # poll reads the old `awaiting_input` and ends the turn before the agent
    # has started -- verified live on v0.48.0, where an answered job bounced
    # straight back to its old question while the agent ran on unwatched.
    #
    # The status file lives in the repo root, so an agent that runs
    # `git add -A` would commit it onto its feature branch. Excluding it in
    # the clone's own info/exclude keeps it out of every diff without
    # touching a tracked .gitignore. Idempotent, since this runs every turn.
    # The launch runs at the clone root, where `sbx create --clone` leaves a
    # real .git directory; anywhere else this is a harmless no-op.
    status_q = shlex.quote(config.status_file)
    exclude_q = shlex.quote("/" + config.status_file.lstrip("/"))
    detached_cmd = (
        f"rm -f {status_q} {shlex.quote(AGENT_EXIT_PATH)}\n"
        "if [ -d .git ]; then\n"
        "  mkdir -p .git/info\n"
        f"  grep -qxF {exclude_q} .git/info/exclude 2>/dev/null"
        f" || echo {exclude_q} >> .git/info/exclude\n"
        "fi\n"
        f"({shell_cmd}) </dev/null >/dev/null 2>&1 &\ndisown 2>/dev/null || true\n"
    )
    run(
        ["sbx", "exec", sandbox_name, "bash", "-lc", detached_cmd],
        options=RunOptions(capture=False),
    )


# `sbx ls` is a host-side listing that should return in well under a second.
# Bounding it matters because this runs from HTTP handlers and from the
# telemetry poll loop: an `sbx` that stalls -- a wedged daemon, or a client
# that wants to say something about a version mismatch -- would otherwise
# hold a threadpool worker indefinitely, and enough of those wedge the API.
_LIST_TIMEOUT_SECONDS = 15.0


def sbx_list() -> dict[str, dict]:
    """Lists every sandbox, keyed by name.

    This is the cheap tier of polling: `sbx ls` runs entirely on the host and
    spawns no process inside any sandbox, so liveness can be checked often
    without costing the agent anything. Reading the status file (see
    `sbx_read_status`) is the expensive tier.

    Returns:
        A mapping of sandbox name to its `sbx ls --json` entry (keys include
        `status`, `agent`, `id`, `workspaces`). Empty if sbx fails, hangs, or
        emits anything but the expected JSON shape -- callers read an absent
        name as "not running", which is the safe reading either way.
    """
    try:
        result = run(
            ["sbx", "ls", "--json"],
            options=RunOptions(check=False, timeout=_LIST_TIMEOUT_SECONDS),
        )
    except CommandTimeout:
        log.warning("sbx_list_timed_out", timeout_seconds=_LIST_TIMEOUT_SECONDS)
        return {}
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    # sbx v0.48.0 prints a bare array; earlier versions wrapped it as
    # {"sandboxes": [...]}. Misreading either as "no sandboxes" makes every
    # live sandbox look gone, so accept both.
    sandboxes = payload.get("sandboxes") if isinstance(payload, dict) else payload
    if not isinstance(sandboxes, list):
        return {}
    return {
        entry["name"]: entry for entry in sandboxes if isinstance(entry, dict) and "name" in entry
    }


def sbx_exists(sandbox_name: str) -> bool:
    """Returns whether a sandbox with this name currently exists."""
    return sandbox_name in sbx_list()


def sbx_status(sandbox_name: str) -> str:
    """Returns sbx's reported status string (e.g. running, stopped, crashed).

    Reads the `status` field from `sbx ls --json` rather than splitting the
    human-readable table on whitespace, which broke as soon as a column moved
    or a value contained a space.
    """
    entry = sbx_list().get(sandbox_name)
    if entry is None:
        return "unknown"
    status = entry.get("status")
    return status if isinstance(status, str) else "unknown"


def sbx_exec_capture(
    sandbox_name: str,
    shell_cmd: str,
    *,
    login_shell: bool = True,
    workdir: str | None = None,
) -> subprocess.CompletedProcess:
    """Runs a shell command inside a sandbox and captures its output.

    Args:
        sandbox_name: Sandbox to run in.
        shell_cmd: Command to run.
        login_shell: Whether to use `bash -lc`. A login shell sources the
            whole profile on every call, which the agent launch needs for
            PATH but a polled `cat` does not -- pass False on hot paths.
        workdir: Directory *inside the container* to run in, applied by sbx
            before the shell starts (so it is safe even with a login
            shell). Never pass a host path -- `config.repo_path` names a
            directory on the host, which generally does not exist in the
            container.

    Returns:
        The completed process, with stdout and stderr captured.
    """
    argv = ["sbx", "exec"]
    if workdir:
        argv += ["-w", workdir]
    argv += [sandbox_name, "bash", "-lc" if login_shell else "-c", shell_cmd]
    return run(argv, options=RunOptions(check=False))


def sbx_agent_exited(sandbox_name: str) -> bool:
    """Whether this turn's agent process has exited.

    An agent reports its status *before* it exits -- it writes the status
    file, then may make one more model request and flush its telemetry --
    so a terminal status alone does not mean the process is done.

    Args:
        sandbox_name: Sandbox to check.

    Returns:
        True once the launch wrapper has written AGENT_EXIT_PATH. False
        while the agent runs, and also when the sandbox can't be reached.
    """
    result = sbx_exec_capture(
        sandbox_name, f"test -f {shlex.quote(AGENT_EXIT_PATH)}", login_shell=False
    )
    return result.returncode == 0


def sbx_read_status(sandbox_name: str, status_file: str) -> dict | None:
    """Reads and parses the agent's JSON status file.

    Args:
        sandbox_name: Sandbox to read from.
        status_file: Workspace-relative path to the status file.

    Returns:
        None if the file doesn't exist yet (still running, from our point
        of view). A dict with `state`/`message` on success. A dict with
        `state: "unknown"` if the file exists but isn't valid JSON --
        callers should treat that as "keep polling", not a hard failure.
    """
    result = sbx_exec_capture(
        sandbox_name, f"cat {shlex.quote(status_file)} 2>/dev/null", login_shell=False
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "state": "unknown",
            "message": f"invalid JSON in status file: {result.stdout[:500]!r}",
        }


def sbx_read_status_and_tail(
    sandbox_name: str, status_file: str, log_path: str, tail_lines: int
) -> tuple[dict | None, str]:
    """Reads the agent's status file and a tail of its run log in one exec.

    Used by the periodic polling tier in `job.poll_until_signal`, which
    reads both on the same cadence -- combining them keeps that tier at one
    `sbx exec` per tick instead of two, the same reasoning
    `_extract_before_teardown` uses for the teardown read.

    Args:
        sandbox_name: Sandbox to read from.
        status_file: Workspace-relative path to the status file.
        log_path: Path *inside the sandbox* to the agent's run log.
        tail_lines: How many trailing lines of the log to read.

    Returns:
        (status, tail). `status` has the same shape as `sbx_read_status`'s
        return value: None if the status file doesn't exist yet, a dict
        with `state`/`message` on valid JSON, or `state: "unknown"` on
        malformed content. `tail` is "" if the log doesn't exist yet.
    """
    marker = "---agentshowdown-tail---"
    result = sbx_exec_capture(
        sandbox_name,
        f"cat {shlex.quote(status_file)} 2>/dev/null; echo {marker}; "
        f"tail -n {tail_lines} {shlex.quote(log_path)} 2>/dev/null",
        login_shell=False,
    )
    status_text, _, tail_text = result.stdout.partition(marker)
    status_text = status_text.strip()
    status: dict | None = None
    if status_text:
        try:
            status = json.loads(status_text)
        except json.JSONDecodeError:
            status = {
                "state": "unknown",
                "message": f"invalid JSON in status file: {status_text[:500]!r}",
            }
    return status, tail_text.lstrip("\n")


def sbx_tail_log(sandbox_name: str, log_path: str, tail_lines: int) -> str:
    """Reads the trailing lines of a sandbox's run log on demand.

    Standalone counterpart to the tail half of `sbx_read_status_and_tail`,
    for a caller that wants a deeper on-demand look (e.g. the `/log`
    endpoint) without also reading the status file.

    Args:
        sandbox_name: Sandbox to read from.
        log_path: Path *inside the sandbox* to the agent's run log.
        tail_lines: How many trailing lines to read.

    Returns:
        The tail text, or "" if the log doesn't exist (yet, or the sandbox
        is gone) -- never raises.
    """
    result = sbx_exec_capture(
        sandbox_name, f"tail -n {tail_lines} {shlex.quote(log_path)} 2>/dev/null", login_shell=False
    )
    return result.stdout


def sbx_allow_network(sandbox_name: str, resource: str) -> bool:
    """Adds a network allow rule scoped to one sandbox.

    Scoped rules live in sbx's local policy and are removed along with the
    sandbox (verified on v0.48.0), so nothing outlives the job.

    Args:
        sandbox_name: The sandbox the rule applies to.
        resource: A "host:port" resource, e.g. "localhost:8000".

    Returns:
        Whether sbx accepted the rule.
    """
    result = run(
        ["sbx", "policy", "allow", "network", "--sandbox", sandbox_name, resource],
        options=RunOptions(check=False),
    )
    return result.returncode == 0


def sbx_rm(sandbox_name: str) -> None:
    """Removes a sandbox, ignoring errors (e.g. already removed).

    Passes --force: `sbx rm` otherwise prompts for interactive
    confirmation, and since our subprocess wrapper captures stdout (so
    the prompt is invisible) without feeding stdin, an unconfirmed call
    hangs the calling thread forever instead of failing.
    """
    run(["sbx", "rm", "--force", sandbox_name], options=RunOptions(check=False))


def sbx_stop(sandbox_name: str) -> None:
    """Stops a sandbox's container without removing it.

    Per `sbx stop --help`: "Stopped sandboxes retain their state and can be
    restarted with sbx run" -- the filesystem, git history and any
    uncommitted work survive. `sbx_restart_detached` is the other half of
    this pair.
    """
    run(["sbx", "stop", sandbox_name], options=RunOptions(check=False))


def sbx_restart_detached(sandbox_name: str, agent: str) -> None:
    """Restarts a stopped local sandbox's container, without launching the agent.

    Equivalent to: sbx run <agent> --name <sandbox_name> --detached

    Mirrors `sbx_create_detached`'s split-then-launch structure, and for
    the same reason documented there: relying on `sbx run --detached` to
    actually start the agent process itself is not reliable (verified
    against sbx v0.38.0 for the fresh-create case; untested for restart,
    so this plays it safe the same way). This call only brings the
    container back to "running" -- `sbx_launch_agent` does the real work
    afterward, exactly as it does after a fresh `sbx_create_detached`.

    Args:
        sandbox_name: The stopped sandbox to restart (must already exist).
        agent: The sbx agent kit it was created with. `sbx run`'s agent
            positional is optional for a named sandbox that already exists
            (sbx reads it from the sandbox's own spec), but passing it
            explicitly avoids relying on that inference.
    """
    run(
        ["sbx", "run", agent, "--name", sandbox_name, "--detached"],
        options=RunOptions(capture=False),
    )
