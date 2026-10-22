"""The OTLP/HTTP receiver for agent CLI telemetry, and its read endpoint.

Sandboxed `claude` and `codex` processes post OTel log events to
`/otel/<sandbox_name>/v1/logs` (see orchestrator/agent_otel). The receiver
speaks OTLP/HTTP with JSON encoding only -- both CLIs are configured for
it -- and files each batch under the job named in the path.

These routes are hit by the sandboxes directly, not through the frontend's
`/api` proxy, which is why the path has no prefix to strip.
"""

# No `from __future__ import annotations`: FastAPI reads the query model's
# annotation at runtime to build the `Depends()` parameters.
import dataclasses
import zlib
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from backend.api.models import AgentEventsQuery
from backend.api.otlp import ExportLogsServiceRequest, to_event_rows
from backend.api.settings import load_config
from backend.logging import log
from backend.orchestrator.agent_otel import summarize_events, timeline_from_events
from backend.orchestrator.state import AgentEventRow, StateStore

# Largest export body accepted, after decompression. A batch is a second
# or so of events; this is far above that and well below harm.
MAX_EXPORT_BYTES = 4 * 1024 * 1024


def _read_body(raw: bytes, content_encoding: str) -> bytes:
    """Decodes the request body, bounding its decompressed size.

    Raises:
        HTTPException: 415 for an encoding other than gzip or none, 413 when
            the body is too large, 400 when it is not valid gzip.
    """
    encoding = content_encoding.strip().lower()
    if encoding in ("", "identity"):
        body = raw
    elif encoding == "gzip":
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            body = decompressor.decompress(raw, MAX_EXPORT_BYTES + 1)
        except zlib.error as exc:
            raise HTTPException(status_code=400, detail="Body is not valid gzip") from exc
    else:
        raise HTTPException(status_code=415, detail=f"Unsupported encoding: {encoding}")
    if len(body) > MAX_EXPORT_BYTES:
        raise HTTPException(status_code=413, detail="Export batch too large")
    return body


def _state_db_path() -> str:
    """The jobs database, or a 503 when no config exists yet."""
    try:
        return load_config().state_db_path
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"No orchestrator config found: {exc}") from exc


def _store_batch(sandbox_name: str, rows: list[AgentEventRow]) -> None:
    """Files a batch under its job. Blocking; run off the event loop.

    Raises:
        HTTPException: 404 when no such job is recorded.
    """
    with StateStore(_state_db_path()) as store:
        if store.get(sandbox_name) is None:
            # A sandbox outliving its job row, or a CLI run started by hand
            # with this endpoint. 404 is not retried by exporters.
            log.warning("otlp_unknown_job", sandbox_name=sandbox_name)
            raise HTTPException(status_code=404, detail=f"Unknown job: {sandbox_name}")
        if rows:
            store.add_agent_events(sandbox_name, rows)


def register_otlp_routes(app: FastAPI) -> None:
    """Registers the OTLP receiver and the agent-events read endpoint on `app`."""

    @app.post("/otel/{sandbox_name}/v1/logs")
    async def receive_logs(sandbox_name: str, request: Request) -> dict[str, object]:
        """Stores one OTLP/HTTP JSON logs export from a job's agent.

        Validated through `ExportLogsServiceRequest` rather than a route
        parameter because the body has to be size-checked and possibly
        gunzipped first. Answers with an empty ExportLogsServiceResponse.
        """
        content_type = request.headers.get("content-type", "").split(";")[0].strip()
        if content_type != "application/json":
            # protobuf is the OTLP default; reaching here means a CLI was
            # not given the JSON protocol setting.
            log.warning("otlp_rejected", reason="content_type", content_type=content_type)
            raise HTTPException(status_code=415, detail="Only OTLP/HTTP JSON is accepted")
        raw = await request.body()
        if len(raw) > MAX_EXPORT_BYTES:
            log.warning("otlp_rejected", reason="too_large", bytes=len(raw))
            raise HTTPException(status_code=413, detail="Export batch too large")
        body = _read_body(raw, request.headers.get("content-encoding", ""))
        try:
            export = ExportLogsServiceRequest.model_validate_json(body)
        except ValidationError as exc:
            log.warning("otlp_rejected", reason="invalid", errors=exc.error_count())
            raise HTTPException(status_code=400, detail="Not an OTLP logs export") from exc

        rows = to_event_rows(export)
        await run_in_threadpool(_store_batch, sandbox_name, rows)
        log.debug("otlp_batch_stored", sandbox_name=sandbox_name, events=len(rows))
        return {}

    @app.get("/jobs/{sandbox_name}/agent-events")
    def get_agent_events(
        sandbox_name: str, query: Annotated[AgentEventsQuery, Depends()]
    ) -> dict[str, object]:
        """A job's agent telemetry events, oldest first, with a rollup and
        the same events as a readable timeline of tool calls and requests.

        Empty rather than 404 for a job with no events, matching
        `/samples`: an agent that exports nothing is a normal case.
        """
        with StateStore(_state_db_path()) as store:
            since = store.get_agent_launched_at(sandbox_name) if query.latest_turn else None
            events = store.get_agent_events(sandbox_name, since=since, limit=query.limit)
        return {
            "events": events,
            "summary": summarize_events(events),
            "timeline": [dataclasses.asdict(e) for e in timeline_from_events(events)],
        }
