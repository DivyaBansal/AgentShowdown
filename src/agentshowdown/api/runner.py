"""Background execution of runs, and recovery of runs across restarts.

A run outlives the request that started it: `POST /runs` validates, records,
hands the work to a background executor and returns a run id immediately.
Closing the browser -- or losing the connection -- cannot stop a run,
because nothing about its progress depends on anyone watching. State lives
in SQLite and the orchestrator's own worker pool does the rest.

Restarts are handled by `recover_in_flight_jobs`: on startup, any job still
marked running is reconciled against the live sandbox listing, so a job
whose sandbox is gone is marked lost instead of sitting "running" forever.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from agentshowdown.orchestrator.config import AgentSpec
from agentshowdown.orchestrator.events import bus
from agentshowdown.orchestrator.job import expand_jobs, run_all
from agentshowdown.orchestrator.log import log
from agentshowdown.orchestrator.process import CommandError
from agentshowdown.orchestrator.sbx import sbx_list
from agentshowdown.orchestrator.state import StateStore
from agentshowdown.orchestrator.telemetry import TelemetrySampler

if TYPE_CHECKING:
    from agentshowdown.api.models import StartRunRequest
    from agentshowdown.orchestrator.config import Config, Feature
    from agentshowdown.orchestrator.telemetry import Sample

# Runs are dispatched one at a time; the orchestrator's own pool provides
# per-job concurrency inside a run (config.max_concurrency).
_dispatcher = ThreadPoolExecutor(max_workers=1, thread_name_prefix="run-dispatch")


class _SamplerHolder:
    """Owns the single process-wide sampler, so `global` isn't needed."""

    current: TelemetrySampler | None = None


_holder = _SamplerHolder()


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def apply_overrides(config: Config, request: StartRunRequest) -> Config:
    """Returns a copy of `config` with the request's overrides applied.

    Only fields the request actually set are changed, so omitting a control
    means "use the configured default" rather than "reset it".

    Args:
        config: The loaded base config.
        request: The launch request.

    Returns:
        A new Config; the original is left untouched.
    """
    overrides = {
        field: value
        for field, value in (
            ("poll_interval_seconds", request.poll_interval_seconds),
            ("liveness_interval_seconds", request.liveness_interval_seconds),
            ("telemetry", request.telemetry),
            ("verify_on", request.verify_on),
            ("open_pr", request.open_pr),
            ("max_concurrency", request.max_concurrency),
        )
        if value is not None
    }
    return dataclasses.replace(config, **overrides)


def start_run(config: Config, features: dict[str, Feature], request: StartRunRequest) -> str:
    """Validates and launches a run in the background.

    Validation happens up front, via the orchestrator's own `expand_jobs`,
    so an unknown feature, an unsupported agent or a bad model is a 422
    before any sandbox exists -- rather than a half-created run with two of
    three sandboxes up.

    Args:
        config: Base config (request overrides are applied here).
        features: Loaded features, keyed by id.
        request: What to run.

    Returns:
        The new run id.

    Raises:
        CommandError: If the request doesn't describe a runnable set of
            jobs. Nothing has been created when this raises.
    """
    effective = apply_overrides(config, request)
    feature = features.get(request.feature_id)
    if feature is None:
        raise CommandError(
            f"Unknown feature id: {request.feature_id}. Available: {', '.join(features)}"
        )

    # The request's agent list overrides whatever features.yaml says, so the
    # UI can compose a comparison without editing the file.
    specs = [
        AgentSpec(agent_id=a.agent_id, run_label=a.run_label, model=a.model) for a in request.agents
    ]
    selected = dataclasses.replace(feature, agents=specs)
    jobs = expand_jobs(effective, {selected.id: selected}, [selected.id])

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    with StateStore(effective.state_db_path) as store:
        store.add_run(
            run_id,
            feature_id=selected.id,
            created_at=_now(),
            mode="live",
            status="running",
        )

    bus.publish(
        "run_started",
        run_id=run_id,
        feature_id=selected.id,
        job_count=len(jobs),
    )
    log.info(
        "run_started",
        run_id=run_id,
        feature_id=selected.id,
        job_count=len(jobs),
        telemetry=effective.telemetry,
        verify_on=effective.verify_on,
    )
    _dispatcher.submit(_execute_run, effective, jobs, run_id)
    return run_id


def _execute_run(config: Config, jobs: list, run_id: str) -> None:
    """Runs every job in a run, then marks the run finished."""
    start_sampler(config)
    try:
        run_all(config, jobs, config.max_concurrency)
    finally:
        stop_sampler()
        with StateStore(config.state_db_path) as store:
            store.finish_run(run_id, finished_at=_now(), status="finished")
        bus.publish("run_finished", run_id=run_id)


def start_sampler(config: Config) -> None:
    """Starts telemetry sampling for the duration of a run, if enabled.

    Samples are persisted and republished so a chart can backfill from the
    database on load and stay live afterwards. When telemetry is off this
    does nothing at all -- no thread is created.
    """
    if not config.telemetry or _holder.current is not None:
        return

    def on_sample(sample: Sample) -> None:
        with StateStore(config.state_db_path) as store:
            store.add_sample(sample)
        bus.publish(
            "sample",
            sandbox_name=sample.sandbox_name,
            ts=sample.ts,
            cpu_cores=sample.cpu_cores,
            mem_bytes=sample.mem_bytes,
            mem_limit_bytes=sample.mem_limit_bytes,
            pids=sample.pids,
        )

    _holder.current = TelemetrySampler(config, on_sample=on_sample)
    _holder.current.start()


def stop_sampler() -> None:
    """Stops telemetry sampling, if it was running."""
    if _holder.current is not None:
        _holder.current.stop()
        _holder.current = None


def recover_in_flight_jobs(config: Config) -> int:
    """Reconciles jobs left mid-flight by a previous process.

    A job recorded as running whose sandbox no longer exists cannot make
    progress and would otherwise sit "running" forever in the UI. Its
    sandbox being alive is the only evidence the work survived, so that is
    what gets checked. `uvicorn --reload` restarts on every code change, so
    this runs constantly in development.

    Args:
        config: Orchestrator config.

    Returns:
        How many jobs were reconciled to "lost".
    """
    live = set(sbx_list())
    reconciled = 0
    with StateStore(config.state_db_path) as store:
        for job in store.all_jobs():
            if job["status"] not in ("queued", "running"):
                continue
            if job["sandbox_name"] in live:
                continue
            store.upsert(
                job["sandbox_name"],
                status="lost",
                detail="sandbox was gone when the server restarted",
            )
            reconciled += 1
    if reconciled:
        log.warning("in_flight_jobs_reconciled", count=reconciled)
    return reconciled
