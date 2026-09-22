"""Tests for the shared subprocess wrapper."""

from __future__ import annotations

import os

import pytest
from backend.orchestrator.process import CommandError, CommandTimeout, RunOptions, run

# A fake value shaped like a real key. The whole point of these tests is that
# it must not survive into a log line or an error message.
TOKEN = "sk-secret-value-do-not-log"  # noqa: S105


def test_redacts_secret_values_from_the_log_line(capsys: pytest.CaptureFixture[str]) -> None:
    """A secret on the command line must never reach the log stream.

    Asserts against real emitted output rather than a captured processor
    chain: `configure_logging` caches bound loggers, so swapping processors
    in a fixture silently observes nothing -- and what actually lands on
    stdout is the property worth guaranteeing.
    """
    run(["echo", TOKEN], options=RunOptions(redact=[TOKEN]))

    out = capsys.readouterr().out
    assert TOKEN not in out
    assert "***" in out


def test_redacts_secret_values_from_the_error_message() -> None:
    """CommandError embeds the argv, and routes.py turns it into an HTTP detail."""
    with pytest.raises(CommandError) as excinfo:
        run(["false", TOKEN], options=RunOptions(redact=[TOKEN]))

    assert TOKEN not in str(excinfo.value)
    assert "***" in str(excinfo.value)


def test_logs_the_command_verbatim_when_nothing_is_redacted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The default path must stay readable -- redaction is opt-in."""
    run(["echo", "hello"])

    assert "echo hello" in capsys.readouterr().out


def test_empty_redact_value_does_not_mangle_the_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Replacing "" would insert *** between every character."""
    run(["echo", "hello"], options=RunOptions(redact=[""]))

    assert "echo hello" in capsys.readouterr().out


def test_env_replaces_the_child_environment() -> None:
    """`env` must reach the child, so the clone can set GIT_TERMINAL_PROMPT=0."""
    result = run(
        ["sh", "-c", "echo $AGENTSHOWDOWN_PROBE"],
        options=RunOptions(env={**os.environ, "AGENTSHOWDOWN_PROBE": "present"}),
    )

    assert result.stdout.strip() == "present"


def test_env_defaults_to_inheriting_the_parent_environment() -> None:
    """Omitting `env` must not blank out PATH."""
    result = run(["sh", "-c", 'test -n "$PATH" && echo inherited'])

    assert result.stdout.strip() == "inherited"


def test_run_raises_command_timeout_instead_of_hanging_forever() -> None:
    """A stuck child (e.g. git on a stalled mount) must not block the caller."""
    with pytest.raises(CommandTimeout):
        run(["sleep", "2"], options=RunOptions(timeout=0.1))


def test_command_timeout_is_a_command_error() -> None:
    """Existing `except CommandError` call sites must keep catching timeouts."""
    with pytest.raises(CommandError):
        run(["sleep", "2"], options=RunOptions(timeout=0.1))


def test_redacts_secret_values_from_the_timeout_message() -> None:
    # The token rides along as $0, so it is in the argv the message renders
    # without being parsed as part of the sleep interval.
    with pytest.raises(CommandTimeout) as excinfo:
        run(["sh", "-c", "sleep 2", TOKEN], options=RunOptions(timeout=0.1, redact=[TOKEN]))

    assert TOKEN not in str(excinfo.value)
    assert "***" in str(excinfo.value)


def test_child_stdin_is_closed_rather_than_inherited() -> None:
    """A prompting child must get EOF, not the server operator's terminal.

    Regression test for the dev server wedging: `sbx ls --json` decided to
    ask for confirmation after a client/server version bump, inherited the
    tty that `uvicorn --reload` was running on, and blocked in a terminal
    read forever -- pinning one FastAPI threadpool worker per call.

    A pipe stands in for that terminal here: it holds unread bytes and is
    never closed, so a child that inherits it reads the bytes and then
    blocks exactly as the real one did.
    """
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"operator terminal input\n")
    saved_stdin = os.dup(0)
    try:
        os.dup2(read_fd, 0)
        # `cat` on an inherited pipe echoes the bytes and then hangs waiting
        # for more; on a closed stdin it sees EOF and exits immediately.
        result = run(["cat"], options=RunOptions(timeout=5))
    finally:
        os.dup2(saved_stdin, 0)
        os.close(saved_stdin)
        os.close(read_fd)
        os.close(write_fd)

    assert result.returncode == 0
    assert result.stdout == ""
