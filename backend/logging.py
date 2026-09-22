"""Structured, trace-correlated logging.

JSON logs by default so they're machine-parseable by whatever aggregator
(Datadog, Grafana Loki, etc.) is on the other end. Each log line gets the
active OpenTelemetry trace/span ID attached, so a log line and a trace can
be pivoted between in the observability backend.

`set_job_context` binds a sandbox name into structlog's contextvars so every
event a job emits is tagged with it -- `run_all` runs each job on its own
worker thread, and contextvars are per-thread, so one worker's binding never
leaks into another job's events.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any

import structlog
from opentelemetry import trace

if TYPE_CHECKING:
    from collections.abc import MutableMapping


def _add_trace_context(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def configure_logging(*, json_logs: bool = True) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.INFO)

    renderer = structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _add_trace_context,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


log = structlog.get_logger()


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
