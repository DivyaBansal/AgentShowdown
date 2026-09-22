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
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from backend.logging import log
from backend.orchestrator.config import AgentSpec
from backend.orchestrator.events import bus
from backend.orchestrator.job import expand_jobs, run_all, sandbox_name_for
from backend.orchestrator.process import CommandError
from backend.orchestrator.sbx import sbx_list
from backend.orchestrator.state import StateStore
from backend.orchestrator.telemetry import TelemetrySampler

if TYPE_CHECKING:
    from backend.api.models import RunEntryIn, StartRunRequest
    from backend.orchestrator.config import Config, Feature
    from backend.orchestrator.telemetry import Sample

# Runs execute concurrently, so a second showdown starts immediately instead
# of waiting for the first to finish. The cap matches StartRunRequest's
# entry limit, so one batch can never queue behind itself. What protects the
# host from the resulting sandbox count is `_job_slots`, not this pool.
MAX_ACTIVE_RUNS = 16
_dispatcher = ThreadPoolExecutor(max_workers=MAX_ACTIVE_RUNS, thread_name_prefix="run-dispatch")


class RunConflictError(CommandError):
    """A requested job is already owned by a run that is still going.

    Two runs sharing a sandbox name would both poll and finalize the same
    sandbox -- fetching, verifying and pushing its branch twice -- because
    `run_job` reattaches to a live sandbox rather than refusing it.
    """


class _SamplerHolder:
    """Owns the single process-wide sampler, so `global` isn't needed.

    One sampler serves every concurrent run: it already samples every live
    sandbox rather than one run's, so the only thing needed is a count of
    how many runs still want it, and a lock so two runs starting at once
    can't both create one.
    """

    current: TelemetrySampler | None = None
    users: int = 0
    lock = threading.Lock()


_holder = _SamplerHolder()


class _SlotHolder:
    """Owns the process-wide cap on concurrently running jobs.

    `config.max_concurrency` bounds one run's own pool, so without this N
    concurrent runs could put N * max_concurrency sandboxes on the host at
    once. Created on first use, because its size comes from the config.
    """

    current: threading.BoundedSemaphore | None = None
    lock = threading.Lock()


_slots = _SlotHolder()

# Sandbox names owned by runs that are still going, mapped to the run id
# holding them. Guarded by `_claims_lock`; a run releases its own when it
# ends.
_claims: dict[str, str] = {}
_claims_lock = threading.Lock()


def _job_slots(config: Config) -> threading.BoundedSemaphore:
    """Returns the process-wide job-slot semaphore, creating it once."""
    with _slots.lock:
        if _slots.current is None:
            _slots.current = threading.BoundedSemaphore(config.max_concurrency)
        return _slots.current


def _claim(owners: dict[str, str]) -> None:
    """Claims every sandbox name in the batch, or claims none of them.

    All-or-nothing across the whole batch, so a conflict on the last entry
    can't leave the earlier ones running.

    Args:
        owners: Sandbox name to the run id that intends to create it.

    Raises:
        RunConflictError: If any name is already held. Nothing is claimed.
    """
    with _claims_lock:
        taken = [(name, _claims[name]) for name in owners if name in _claims]
        if taken:
            name, holder = taken[0]
            raise RunConflictError(
                f"'{name}' is already running as part of {holder}. Wait for it "
                f"to finish, or give this agent a run label to run it alongside."
            )
        _claims.update(owners)


def _release(sandbox_names: list[str]) -> None:
    """Releases claims held by a finished run."""
    with _claims_lock:
        for name in sandbox_names:
            _claims.pop(name, None)


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


def _selected_feature(features: dict[str, Feature], entry: RunEntryIn) -> Feature:
    """Returns `entry`'s feature carrying the agents the request asked for.

    The request's agent list overrides whatever features.yaml says, so the
    UI can compose a comparison without editing the file. Every AgentSpec
    field is carried across, not just the scalars: dropping `env` would
    silently disable custom model endpoints, and dropping `command` would
    make agent kits without a built-in launcher (e.g. "shell") impossible
    to start from the UI at all.

    Args:
        features: Loaded features, keyed by id.
        entry: One feature and its agents, from the request.

    Returns:
        A copy of the feature whose `agents` are the request's.

    Raises:
        CommandError: If the feature id is unknown.
    """
    feature = features.get(entry.feature_id)
    if feature is None:
        raise CommandError(
            f"Unknown feature id: {entry.feature_id}. Available: {', '.join(features)}"
        )
    specs = [
        AgentSpec(
            agent_id=a.agent_id,
            run_label=a.run_label,
            model=a.model,
            command=a.command,
            dangerously_skip_permissions=a.dangerously_skip_permissions,
            kit=list(a.kit),
            provider=a.provider,
            env=dict(a.env),
        )
        for a in entry.agents
    ]
    return dataclasses.replace(feature, agents=specs)


def start_run(config: Config, features: dict[str, Feature], request: StartRunRequest) -> list[str]:
    """Validates and launches one run per requested feature, in the background.

    Validation covers the whole batch before anything is created: the
    single `expand_jobs` call rejects an unknown feature, an unsupported
    agent or a bad model, and also catches two entries that would collide
    on one sandbox name. So a rejected batch leaves no half-started runs
    behind.

    Args:
        config: Base config (request overrides are applied here).
        features: Loaded features, keyed by id.
        request: What to run.

    Returns:
        One run id per entry, in request order.

    Raises:
        CommandError: If the request doesn't describe a runnable set of
            jobs. Nothing has been created when this raises.
        RunConflictError: If a job is already owned by a live run.
    """
    effective = apply_overrides(config, request)
    selected = [_selected_feature(features, entry) for entry in request.entries]
    # One call, so a name used by two different entries is caught too.
    expand_jobs(effective, {f.id: f for f in selected}, [f.id for f in selected])

    planned = [
        (feature, expand_jobs(effective, {feature.id: feature}, [feature.id]))
        for feature in selected
    ]
    names_by_run = {
        f"run-{uuid.uuid4().hex[:12]}": [sandbox_name_for(feature.id, spec) for _, spec in jobs]
        for feature, jobs in planned
    }
    # Claimed in one go, before anything is recorded or dispatched, so a
    # conflict on the last entry leaves no half-started batch behind.
    _claim({name: run_id for run_id, names in names_by_run.items() for name in names})

    # The global cap comes from the configured default, not from a request
    # that happens to be the first one after startup.
    slots = _job_slots(config)

    run_ids = list(names_by_run)
    for run_id, (feature, jobs) in zip(run_ids, planned, strict=True):
        names = names_by_run[run_id]
        with StateStore(effective.state_db_path) as store:
            store.add_run(
                run_id,
                feature_id=feature.id,
                created_at=_now(),
                mode="live",
                status="running",
            )

        bus.publish(
            "run_started",
            run_id=run_id,
            feature_id=feature.id,
            job_count=len(jobs),
        )
        log.info(
            "run_started",
            run_id=run_id,
            feature_id=feature.id,
            job_count=len(jobs),
            telemetry=effective.telemetry,
            verify_on=effective.verify_on,
        )
        _dispatcher.submit(_execute_run, effective, jobs, run_id, names, slots)

    log.info(
        "batch_started",
        run_ids=run_ids,
        feature_ids=[f.id for f in selected],
    )
    return run_ids


def _execute_run(
    config: Config,
    jobs: list,
    run_id: str,
    sandbox_names: list[str],
    slots: threading.BoundedSemaphore,
) -> None:
    """Runs every job in a run, then marks the run finished."""
    sampling = start_sampler(config)
    try:
        run_all(config, jobs, config.max_concurrency, slots=slots, run_id=run_id)
    finally:
        if sampling:
            stop_sampler()
        _release(sandbox_names)
        with StateStore(config.state_db_path) as store:
            store.finish_run(run_id, finished_at=_now(), status="finished")
        bus.publish("run_finished", run_id=run_id)


def start_sampler(config: Config) -> bool:
    """Starts telemetry sampling for the duration of a run, if enabled.

    Samples are persisted and republished so a chart can backfill from the
    database on load and stay live afterwards. When telemetry is off this
    does nothing at all -- no thread is created.

    Concurrent runs share one sampler, counted here so that the first run
    to finish doesn't stop sampling for the others.

    Returns:
        Whether this caller counted as a user of the sampler, and so owes
        a matching `stop_sampler`.
    """
    if not config.telemetry:
        return False

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

    with _holder.lock:
        _holder.users += 1
        if _holder.current is None:
            _holder.current = TelemetrySampler(config, on_sample=on_sample)
            _holder.current.start()
    return True


def stop_sampler() -> None:
    """Drops one user of the sampler, stopping it once the last one goes."""
    with _holder.lock:
        _holder.users = max(0, _holder.users - 1)
        if _holder.users == 0 and _holder.current is not None:
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
