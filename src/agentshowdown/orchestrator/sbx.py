"""sbx wrapper -- thin, explicit subprocess calls. No hidden magic."""

from __future__ import annotations

import json
import shlex
import subprocess

from agentshowdown.orchestrator.config import Config
from agentshowdown.orchestrator.process import CommandError, run

AGENT_LOG_PATH = "/tmp/sbx-agent-run.log"


def sbx_create_detached(
    sandbox_name: str,
    agent: str,
    workspace: str,
    kit: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
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
    """
    cmd = ["sbx", "create", "--clone", agent, "--name", sandbox_name]
    for kit_ref in kit or []:
        cmd += ["--kit", kit_ref]
    if provider:
        cmd += ["--provider", provider]
        if model:
            cmd += ["--model", model]
    cmd.append(workspace)
    run(cmd, capture=False)


def build_claude_cmd(
    prompt: str,
    dangerously_skip_permissions: bool,
    model: str,
    max_turns: int | None,
    resume: bool = False,
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
    resume: bool = False,
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
    resume: bool = False,
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
    resume: bool = False,
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
    resume: bool = False,
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


def build_agent_cmd(
    agent_id: str,
    prompt: str,
    config: Config,
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
    )


def build_agent_invocation(
    agent_id: str,
    prompt: str,
    config: Config,
    model_override: str | None = None,
    skip_permissions_override: bool | None = None,
    command_override: str | None = None,
    resume: bool = False,
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


def build_agent_shell_command(cmd_str: str, status_file: str, log_path: str) -> str:
    """Builds the `bash -lc` script that runs the agent and guarantees a
    status file exists afterward.

    Args:
        cmd_str: The agent invocation (from build_agent_invocation). May
            span multiple lines (env exports followed by the command) --
            only the final line's exit code and output redirection matter,
            which is what the trailing "> log 2>&1" below attaches to.
        status_file: Workspace-relative path the agent is expected to write.
        log_path: Where to redirect the agent's stdout/stderr.

    Returns:
        A shell script: run the agent, capture its exit code, and -- only
        if status_file still doesn't exist -- write a "crashed" status via
        `python3 -c`, with the log tail and exit code passed as argv so no
        shell-escaping of log content is ever needed.
    """
    log_q = shlex.quote(log_path)
    status_q = shlex.quote(status_file)
    fallback_q = shlex.quote(STATUS_FALLBACK_PY)
    return (
        f"{cmd_str} > {log_q} 2>&1\n"
        f"EXIT_CODE=$?\n"
        f"if [ ! -f {status_q} ]; then\n"
        f'  python3 -c {fallback_q} {status_q} {log_q} "$EXIT_CODE"\n'
        f"fi\n"
        f'exit "$EXIT_CODE"\n'
    )


def sbx_launch_agent(
    sandbox_name: str,
    agent_id: str,
    prompt: str,
    config: Config,
    model_override: str | None = None,
    skip_permissions_override: bool | None = None,
    command_override: str | None = None,
    resume: bool = False,
) -> None:
    """Starts (or resumes) the agent inside an already-created sandbox,
    backgrounded via `sbx exec -d` so this call returns immediately.

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
    cmd_str = build_agent_invocation(
        agent_id,
        prompt,
        config,
        model_override=model_override,
        skip_permissions_override=skip_permissions_override,
        command_override=command_override,
        resume=resume,
    )
    shell_cmd = build_agent_shell_command(cmd_str, config.status_file, AGENT_LOG_PATH)
    # `sbx exec -d` does not actually return until the backgrounded command
    # exits (verified: `sbx exec -d <sandbox> sleep 30` blocks for ~30s), so
    # relying on it alone leaves this call -- and the calling job thread --
    # blocked for the agent's entire runtime instead of returning right
    # away. Self-daemonize instead: background the real script ourselves
    # with its own stdio fully detached from this exec session's pipes, so
    # `sbx exec` has nothing left to wait on and returns immediately no
    # matter what `-d` does.
    detached_cmd = f"({shell_cmd}) </dev/null >/dev/null 2>&1 &\ndisown 2>/dev/null || true\n"
    run(["sbx", "exec", "-d", sandbox_name, "bash", "-lc", detached_cmd], capture=False)


def sbx_exists(sandbox_name: str) -> bool:
    """Returns whether a sandbox with this name currently exists."""
    result = run(["sbx", "ls", "--quiet"], check=False)
    names = result.stdout.splitlines() if result.stdout else []
    return sandbox_name in names


def sbx_status(sandbox_name: str) -> str:
    """Returns sbx's reported status string (e.g. running, stopped, crashed)."""
    result = run(["sbx", "ls"], check=False)
    for line in result.stdout.splitlines():
        if line.startswith(sandbox_name + " ") or line.split()[:1] == [sandbox_name]:
            parts = line.split()
            if len(parts) >= 3:
                return parts[2]
    return "unknown"


def sbx_exec_capture(sandbox_name: str, shell_cmd: str) -> subprocess.CompletedProcess:
    """Runs a shell command inside a sandbox and captures its output."""
    return run(["sbx", "exec", sandbox_name, "bash", "-lc", shell_cmd], check=False)


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
        sandbox_name, f"cat {shlex.quote(status_file)} 2>/dev/null"
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


def sbx_current_branch(sandbox_name: str, workspace: str) -> str:
    """Returns the git branch currently checked out in the sandbox."""
    result = sbx_exec_capture(
        sandbox_name, f"cd {shlex.quote(workspace)} && git rev-parse --abbrev-ref HEAD"
    )
    return result.stdout.strip()


def sbx_rm(sandbox_name: str) -> None:
    """Removes a sandbox, ignoring errors (e.g. already removed).

    Passes --force: `sbx rm` otherwise prompts for interactive
    confirmation, and since our subprocess wrapper captures stdout (so
    the prompt is invisible) without feeding stdin, an unconfirmed call
    hangs the calling thread forever instead of failing.
    """
    run(["sbx", "rm", "--force", sandbox_name], check=False)
