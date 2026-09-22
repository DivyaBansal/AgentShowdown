"""Generic subprocess wrapper shared by the sbx and vcs modules."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.logging import log

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


class CommandError(RuntimeError):
    pass


class CommandTimeout(CommandError):
    """The command did not exit within its timeout.

    Subclasses CommandError so existing `except CommandError` call sites keep
    treating a timeout as just another failed command, while callers that
    care can catch this specifically to tell "failed" from "hung".
    """


def _redacted(cmd: list[str], redact: Sequence[str]) -> str:
    """Renders `cmd` for logging with every `redact` value masked.

    Args:
        cmd: The argv about to run.
        redact: Secret values to mask. Empty strings are ignored, since
            replacing "" would mangle every character boundary.

    Returns:
        A shell-quoted command string safe to log.
    """
    rendered = " ".join(shlex.quote(c) for c in cmd)
    for secret in redact:
        if secret:
            rendered = rendered.replace(secret, "***")
    return rendered


@dataclass(frozen=True, slots=True)
class RunOptions:
    """How to run a command, and what to do about a failure.

    One object rather than five parameters on `run`. That is partly so the
    signature stays under the argument-count limit, but mostly because these
    travel together: a wrapper that forwards "how to run it" can now pass a
    single value through instead of restating every knob.

    Attributes:
        check: Raise CommandError on a non-zero exit.
        capture: Capture stdout/stderr rather than inheriting them.
        env: Full environment for the child. Callers merge over
            `os.environ` themselves -- a bare dict here would wipe PATH.
        redact: Secret values to mask in the log line and in any
            CommandError message. Required whenever a secret reaches the
            command line, because both of those are otherwise verbatim
            copies of the argv.
        timeout: Seconds to wait before killing the child. None waits
            forever -- only safe for commands whose caller already bounds
            them another way (e.g. the orchestrator's own poll loop).
    """

    check: bool = True
    capture: bool = True
    env: Mapping[str, str] | None = None
    redact: Sequence[str] = ()
    timeout: float | None = None


# Frozen, so one shared instance is safe as a default argument -- and naming
# it keeps `run`'s signature free of a call expression (ruff B008).
DEFAULT_RUN_OPTIONS = RunOptions()


def run(
    cmd: list[str],
    *,
    options: RunOptions = DEFAULT_RUN_OPTIONS,
) -> subprocess.CompletedProcess:
    """Runs an argv list, logging it first.

    Args:
        cmd: The argv to run. Never a shell string.
        options: How to run it. See `RunOptions`; the default captures
            output and raises on a non-zero exit.

    Note:
        stdin is always /dev/null. Every caller here is a headless CLI
        invocation from a server process, and an inherited stdin is a trap:
        when the server runs in a terminal (`uvicorn --reload`), a child
        that decides to prompt -- `sbx` asking to confirm a daemon restart
        after a version bump, `git` asking for credentials -- reads from the
        developer's tty and blocks forever, pinning the threadpool worker
        that called it. Closing stdin turns that prompt into an immediate
        EOF, which every one of these tools handles by declining and
        exiting.

    Returns:
        The completed process.

    Raises:
        CommandError: If `options.check` and the command exited non-zero.
        CommandTimeout: If the command did not exit within `options.timeout`.
    """
    log.info("subprocess_run", command=_redacted(cmd, options.redact))
    # The only subprocess call in the package. `cmd` is always an argv list
    # built from literals plus validated config -- never a shell string, and
    # shell=False, so there is no shell to inject into.
    try:
        result = subprocess.run(  # noqa: S603
            cmd,
            capture_output=options.capture,
            text=True,
            check=False,
            env=dict(options.env) if options.env is not None else None,
            timeout=options.timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise CommandTimeout(
            f"Command timed out after {options.timeout}s: {_redacted(cmd, options.redact)}"
        ) from exc
    if options.check and result.returncode != 0:
        raise CommandError(
            f"Command failed ({result.returncode}): {_redacted(cmd, options.redact)}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result
