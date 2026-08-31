"""Job-scoped structured logging.

`run_all` runs each job on its own worker thread. Binding the sandbox name
into structlog's contextvars tags every event a job emits, so concurrent
jobs stay attributable without the per-line string prefix this module used
to build. Contextvars are per-thread, so a ThreadPoolExecutor worker's
binding never leaks into another job's events.

Re-exports the app-wide `log` from `agentshowdown.logging` so orchestrator
modules have one import site for both it and `set_job_context`.
"""

from __future__ import annotations

import structlog

from agentshowdown.logging import log

__all__ = ["log", "set_job_context"]


def set_job_context(sandbox_name: str | None) -> None:
    """Binds the sandbox name onto every event from this thread, or clears it.

    Args:
        sandbox_name: Sandbox to tag this thread's events with, or None to
            clear the tag when the job finishes.
    """
    if sandbox_name is None:
        structlog.contextvars.unbind_contextvars("sandbox_name")
    else:
        structlog.contextvars.bind_contextvars(sandbox_name=sandbox_name)
