"""The run/resume/respawn/reattach state machine and concurrent execution."""

from __future__ import annotations

import concurrent.futures
import json
import time
import traceback
from typing import TYPE_CHECKING

from backend.logging import log, set_job_context
from backend.orchestrator.agent_otel import exports_otel, policy_resource
from backend.orchestrator.config import (
    AgentSpec,
    Config,
    Feature,
    host_repo_path,
    resolve_dangerously_skip_permissions,
    resolve_env,
    resolve_kit_paths,
    resolve_model,
    resolve_provider,
    spec_to_json,
)
from backend.orchestrator.job.expand import (
    branch_for,
    record_queued,
    sandbox_name_for,
    spec_for_job,
)
from backend.orchestrator.job.finish import await_and_finalize, timed_step, utc_now
from backend.orchestrator.process import CommandError
from backend.orchestrator.prompt import build_continue_prompt, build_prompt, build_resume_prompt
from backend.orchestrator.sbx import (
    sbx_allow_network,
    sbx_create_detached,
    sbx_exists,
    sbx_launch_agent,
    sbx_restart_detached,
    sbx_rm,
)
from backend.orchestrator.state import StateStore

if TYPE_CHECKING:
    import threading

# Outcome metrics are only written once an attempt finishes. Every path that
# starts a new attempt clears them, otherwise a respawned or resumed job keeps
# showing the previous attempt's duration/tokens/diff, frozen, for as long as
# the new attempt runs.
_ATTEMPT_RESET: dict[str, object] = {
    "finished_at": None,
    "duration_seconds": None,
    "input_tokens": None,
    "output_tokens": None,
    "num_turns": None,
    "files_changed": None,
    "lines_added": None,
    "lines_removed": None,
    "tests_passed": None,
    "lint_passed": None,
    "agent_s": None,
    "verify_s": None,
    "push_s": None,
    "failure_category": None,
    "question_options": None,
}


def _allow_otel_export(config: Config, sandbox_name: str) -> None:
    """Lets a new sandbox reach the OTel receiver, which sbx denies by default.

    A refused rule is logged, not raised: the agent runs the same without
    telemetry, and its usage falls back to the run log.

    Args:
        config: Orchestrator config (supplies the endpoint).
        sandbox_name: The just-created sandbox.
    """
    if not config.agent_otel_endpoint:
        return
    try:
        resource = policy_resource(config.agent_otel_endpoint)
    except ValueError:
        log.warning("otel_network_rule_failed", reason="endpoint has no host")
        return
    if sbx_allow_network(sandbox_name, resource):
        log.info("otel_network_rule_added", resource=resource)
    else:
        log.warning("otel_network_rule_failed", resource=resource)


def run_job(config: Config, feature: Feature, spec: AgentSpec) -> None:
    """Runs (or reattaches to) one (feature, agent) job to completion.

    Reattach-aware: if a job with this sandbox name already exists,
    behavior depends on its recorded status -- `awaiting_input` and
    `succeeded` jobs are skipped (with guidance), `running`/`queued` jobs
    with a live sandbox are reattached rather than relaunched, and other
    terminal-failed jobs are removed and relaunched fresh.

    Args:
        config: Orchestrator config.
        feature: The feature to implement.
        spec: Which agent (and optional run_label) to run it through.
    """
    store = StateStore(config.state_db_path)
    branch = branch_for(feature.id, spec)
    sandbox_name = sandbox_name_for(feature.id, spec)
    agent_id = spec.agent_id

    existing = store.get(sandbox_name)
    sandbox_alive = sbx_exists(sandbox_name)

    if existing is not None:
        status = existing["status"]
        if status == "awaiting_input":
            log.info(
                "job_skipped_awaiting_input",
                hint=(f'python -m backend.orchestrator answer {sandbox_name} "<answer>"'),
            )
            return
        if status == "succeeded":
            log.info(
                "job_skipped_already_succeeded",
                pr_url=existing.get("pr_url"),
                hint="give this agent a distinct run_label in features.yaml to rerun",
            )
            return
        if status in ("queued", "running") and sandbox_alive:
            log.info("job_reattaching")
        elif sandbox_alive:
            log.info("job_relaunching", previous_status=status)
            sbx_rm(sandbox_name)
            sandbox_alive = False
    elif sandbox_alive:
        log.warning("orphan_sandbox_reattached")

    profile = config.agent_profiles.get(agent_id)
    resolved_model = resolve_model(spec, profile, config)
    store.upsert(
        sandbox_name,
        feature_id=feature.id,
        agent_id=agent_id,
        branch=branch,
        status="queued" if not sandbox_alive else "running",
        pr_url=None,
        detail=None,
        model=resolved_model,
        run_label=spec.run_label,
        repo=host_repo_path(config),
        cpus=config.cpus,
        memory_limit=config.memory,
    )
    store.set_spec(sandbox_name, spec_to_json(spec))

    if not sandbox_alive:
        prompt = build_prompt(feature, branch, config.status_file)
        resolved_env = resolve_env(spec, profile)
        log.info(
            "sandbox_launching",
            feature_id=feature.id,
            agent_id=agent_id,
            env_keys=sorted(resolved_env),
        )
        with timed_step(store, sandbox_name, "launch"):
            try:
                sbx_create_detached(
                    sandbox_name=sandbox_name,
                    agent=agent_id,
                    workspace=config.repo_path,
                    kit=resolve_kit_paths(spec, profile),
                    provider=resolve_provider(spec, profile, config),
                    model=resolved_model,
                    env=resolved_env,
                    cpus=config.cpus,
                    memory=config.memory,
                )
            except CommandError:
                # Likely a race with a concurrent `run` that created it first.
                if sbx_exists(sandbox_name):
                    log.info("sandbox_create_raced")
                else:
                    raise
            else:
                if exports_otel(agent_id, spec.command, config.agent_otel_endpoint):
                    _allow_otel_export(config, sandbox_name)
                store.set_agent_launched_at(sandbox_name, utc_now())
                sbx_launch_agent(
                    sandbox_name=sandbox_name,
                    agent_id=agent_id,
                    prompt=prompt,
                    config=config,
                    model_override=resolved_model,
                    skip_permissions_override=resolve_dangerously_skip_permissions(
                        spec, profile, config
                    ),
                    command_override=spec.command,
                    env=resolved_env,
                )

    started_monotonic = time.monotonic()
    store.upsert(sandbox_name, status="running", started_at=utc_now(), **_ATTEMPT_RESET)

    await_and_finalize(
        config,
        store,
        sandbox_name,
        started_monotonic,
        feature=feature,
        agent_id=agent_id,
        requested_branch=branch,
        command_override=spec.command,
    )


def option_text_for(job: dict, option_index: int) -> str:
    """Resolves a chosen option index to the option's text.

    Args:
        job: The job row, carrying its `question_options` column.
        option_index: Which offered option the human picked.

    Returns:
        The option's text, ready to be relayed as the answer.

    Raises:
        CommandError: If the job offered no options, or the index is out of
            range for the ones it offered.
    """
    raw = job.get("question_options")
    options = json.loads(raw) if isinstance(raw, str) else []
    if not isinstance(options, list) or not 0 <= option_index < len(options):
        raise CommandError(
            f"Job '{job['sandbox_name']}' has no option {option_index} -- "
            f"it offered {len(options) if isinstance(options, list) else 0}."
        )
    return str(options[option_index])


def plan_resume(
    config: Config, features: dict[str, Feature], sandbox_name: str
) -> tuple[dict, Feature, AgentSpec]:
    """Validates that a job can be answered right now.

    Kept separate from the relaunch so the HTTP layer can validate
    synchronously and then do the slow part in the background.

    Args:
        config: Orchestrator config.
        features: All loaded features, keyed by id (to look up the one
            this job belongs to).
        sandbox_name: The job to resume.

    Returns:
        (job row, feature, spec) to hand to `relaunch_and_await`.

    Raises:
        CommandError: If the job is unknown, isn't awaiting_input, or its
            sandbox no longer exists (in which case the job is also marked
            lost, since nothing can resume it).
    """
    with StateStore(config.state_db_path) as store:
        job = store.get(sandbox_name)
        if job is None:
            raise CommandError(
                f"No known job '{sandbox_name}'. Run "
                f"`python -m backend.orchestrator status` to list jobs."
            )
        if job["status"] != "awaiting_input":
            raise CommandError(
                f"Job '{sandbox_name}' is '{job['status']}', not 'awaiting_input' -- "
                f"nothing to answer."
            )
        if not sbx_exists(sandbox_name):
            store.upsert(
                sandbox_name,
                status="lost",
                detail="sandbox was removed while awaiting input; rerun the feature "
                "fresh with a new run_label instead.",
            )
            raise CommandError(
                f"Sandbox '{sandbox_name}' no longer exists -- can't resume it. "
                f"Rerun the feature fresh (with a new run_label)."
            )

        feature, spec = _feature_and_spec(config, store, features, job)
    return job, feature, spec


def _feature_and_spec(
    config: Config, store: StateStore, features: dict[str, Feature], job: dict
) -> tuple[Feature, AgentSpec]:
    """Looks up the feature a job row belongs to and the spec it ran under.

    Raises:
        CommandError: If the feature is gone from the features file.
    """
    feature = features.get(job["feature_id"])
    if feature is None:
        raise CommandError(f"Feature '{job['feature_id']}' not found in the loaded features file.")
    return feature, spec_for_job(config, store, feature, job["sandbox_name"])


def plan_followup(
    config: Config, features: dict[str, Feature], sandbox_name: str
) -> tuple[dict, Feature, AgentSpec]:
    """Validates that a job can take a follow-up instruction right now.

    Unlike `plan_resume`, this doesn't require the agent to have asked
    anything -- only that its sandbox is still there, idle, and able to run
    the agent's most recent session again.

    Args:
        config: Orchestrator config.
        features: Loaded features, keyed by id.
        sandbox_name: The job to follow up on.

    Returns:
        (job row, feature, spec) to hand to `relaunch_and_await`.

    Raises:
        CommandError: If the job is unknown, still running (a second
            launch would race the first), stopped (a stopped container
            can't exec anything until it's respawned), or its sandbox is
            gone (nothing left to continue).
    """
    with StateStore(config.state_db_path) as store:
        job = store.get(sandbox_name)
        if job is None:
            raise CommandError(f"No known job '{sandbox_name}'.")
        status = job["status"]
        if status in ("queued", "running"):
            raise CommandError(
                f"Job '{sandbox_name}' is still {status} -- wait for it to finish, or "
                f"stop it, before sending a follow-up."
            )
        if status == "stopped":
            raise CommandError(
                f"Job '{sandbox_name}' is stopped -- respawn it first; a stopped "
                f"sandbox can't run anything."
            )
        if not sbx_exists(sandbox_name):
            raise CommandError(
                f"Sandbox '{sandbox_name}' no longer exists, so there's no session to "
                f"follow up on. Respawn the job instead."
            )
        feature, spec = _feature_and_spec(config, store, features, job)
    return job, feature, spec


def relaunch_and_await(
    config: Config,
    job: dict,
    feature: Feature,
    spec: AgentSpec,
    prompt: str,
    *,
    event: str,
) -> None:
    """Relaunches a job's agent in resume mode with `prompt`, to completion.

    The shared second half of answering and following up: both continue the
    agent's most recent session inside the existing sandbox, then poll and
    finalize exactly as a first run does.

    Args:
        config: Orchestrator config.
        job: The job row, from `plan_resume` / `plan_followup`.
        feature: The feature being implemented.
        spec: The agent spec it runs under.
        prompt: What to tell the agent on this turn.
        event: Structured log event naming why it was relaunched.
    """
    sandbox_name = job["sandbox_name"]
    set_job_context(sandbox_name)
    store = StateStore(config.state_db_path)
    profile = config.agent_profiles.get(spec.agent_id)

    log.info(event)
    with timed_step(store, sandbox_name, "launch"):
        store.set_agent_launched_at(sandbox_name, utc_now())
        sbx_launch_agent(
            sandbox_name=sandbox_name,
            agent_id=spec.agent_id,
            prompt=prompt,
            config=config,
            model_override=resolve_model(spec, profile, config),
            skip_permissions_override=resolve_dangerously_skip_permissions(spec, profile, config),
            command_override=spec.command,
            resume=True,
            env=resolve_env(spec, profile),
        )
    started_monotonic = time.monotonic()
    store.upsert(
        sandbox_name, status="running", detail=None, started_at=utc_now(), **_ATTEMPT_RESET
    )

    await_and_finalize(
        config,
        store,
        sandbox_name,
        started_monotonic,
        feature=feature,
        agent_id=job["agent_id"],
        requested_branch=job["branch"],
        command_override=spec.command,
    )


def resume_job(
    config: Config, features: dict[str, Feature], sandbox_name: str, human_answer: str
) -> None:
    """Answers a job that is awaiting_input and resumes it to completion.

    Synchronous end to end -- the CLI's `answer` path. The HTTP layer runs
    the same two halves (`plan_resume`, `relaunch_and_await`) with the
    second in the background instead.

    Args:
        config: Orchestrator config.
        features: All loaded features, keyed by id.
        sandbox_name: The job to resume.
        human_answer: The human's answer to the agent's question.

    Raises:
        CommandError: See `plan_resume`.
    """
    set_job_context(sandbox_name)
    job, feature, spec = plan_resume(config, features, sandbox_name)
    prompt = build_resume_prompt(human_answer, config.status_file)
    relaunch_and_await(config, job, feature, spec, prompt, event="job_resuming")


# Respawning a job in this state would either race whatever already has it
# (queued/running), redo completed work (succeeded), or needs an answer
# rather than a bare restart (awaiting_input) -- handled by resume_job
# instead. Checked in plan_respawn, which decides against the DB status
# only when the sandbox itself is gone (a live sandbox gets its own check).
_NOT_RESPAWNABLE_WHEN_GONE = ("succeeded", "awaiting_input")


def plan_respawn(
    config: Config, features: dict[str, Feature], sandbox_name: str
) -> tuple[Feature, AgentSpec, bool]:
    """Validates a respawn request and decides in-place vs fresh relaunch.

    Kept separate from the actual respawn so a caller (the HTTP layer) can
    validate synchronously -- a bad request fails immediately with no side
    effect, before any background work is claimed or dispatched, the same
    way `expand_jobs` validates a run up front.

    Args:
        config: Orchestrator config.
        features: Loaded features, keyed by id.
        sandbox_name: The job to respawn.

    Returns:
        (feature, spec, in_place). `in_place` is True when the sandbox is
        alive and stopped, meaning the agent should be resumed inside it
        rather than relaunched from scratch.

    Raises:
        CommandError: If the job is unknown, its feature/spec can't be
            found, it has a live sandbox that isn't stopped, it already
            succeeded, or it's awaiting_input with no sandbox left to
            answer.
    """
    store = StateStore(config.state_db_path)
    job = store.get(sandbox_name)
    if job is None:
        raise CommandError(f"No known job '{sandbox_name}'.")
    feature = features.get(job["feature_id"])
    if feature is None:
        raise CommandError(f"Feature '{job['feature_id']}' not found in the loaded features file.")
    spec = spec_for_job(config, store, feature, sandbox_name)

    status = job["status"]
    if sbx_exists(sandbox_name):
        if status != "stopped":
            raise CommandError(
                f"Job '{sandbox_name}' is '{status}' with a live sandbox -- "
                f"only a stopped sandbox can be restarted in place. Stop it "
                f"first (or wait for it to reach a terminal state)."
            )
        return feature, spec, True

    if status in _NOT_RESPAWNABLE_WHEN_GONE:
        reason = (
            "it already succeeded"
            if status == "succeeded"
            else "there's no question left to answer -- its sandbox is gone"
        )
        raise CommandError(
            f"Job '{sandbox_name}' is '{status}' and {reason}. Give it a "
            f"new run_label in features.yaml to run it again instead."
        )
    return feature, spec, False


def _respawn_in_place(config: Config, feature: Feature, spec: AgentSpec, sandbox_name: str) -> None:
    """Restarts a stopped sandbox's container and resumes its agent session.

    The container's filesystem (git history, uncommitted work) survives a
    stop, so this continues the agent's most recent session rather than
    starting over -- the counterpart to `run_job`'s fresh-relaunch path.
    """
    store = StateStore(config.state_db_path)
    profile = config.agent_profiles.get(spec.agent_id)
    log.info("job_respawning_in_place")
    with timed_step(store, sandbox_name, "launch"):
        sbx_restart_detached(sandbox_name, spec.agent_id)
        store.set_agent_launched_at(sandbox_name, utc_now())
        sbx_launch_agent(
            sandbox_name=sandbox_name,
            agent_id=spec.agent_id,
            prompt=build_continue_prompt(config.status_file),
            config=config,
            model_override=resolve_model(spec, profile, config),
            skip_permissions_override=resolve_dangerously_skip_permissions(spec, profile, config),
            command_override=spec.command,
            resume=True,
            env=resolve_env(spec, profile),
        )
    started_monotonic = time.monotonic()
    store.upsert(
        sandbox_name, status="running", detail=None, started_at=utc_now(), **_ATTEMPT_RESET
    )

    await_and_finalize(
        config,
        store,
        sandbox_name,
        started_monotonic,
        feature=feature,
        agent_id=spec.agent_id,
        requested_branch=branch_for(feature.id, spec),
        command_override=spec.command,
    )


def respawn_job(
    config: Config, feature: Feature, spec: AgentSpec, sandbox_name: str, *, in_place: bool
) -> None:
    """Executes a respawn already validated by `plan_respawn`.

    Args:
        config: Orchestrator config.
        feature: The feature being implemented, from `plan_respawn`.
        spec: The agent spec to run it through, from `plan_respawn`.
        sandbox_name: The job to respawn.
        in_place: From `plan_respawn` -- True to restart the existing
            sandbox and resume its session, False to relaunch fresh via
            `run_job` (which already does this for a terminal-failed job
            whose sandbox is gone).
    """
    if in_place:
        _respawn_in_place(config, feature, spec, sandbox_name)
    else:
        run_job(config, feature, spec)


def reattach_job(config: Config, feature: Feature, spec: AgentSpec, sandbox_name: str) -> None:
    """Resumes watching a job whose sandbox survived a server restart.

    Unlike `run_job`, this never (re)launches the agent -- the sandbox
    already has one running, which is the whole premise here. It only
    re-enters the poll/finalize loop a now-dead thread was running before
    the process restarted.

    The elapsed-time clock restarts from now rather than the job's
    original `started_at`, so `duration_seconds` undercounts the time
    before the restart and the timeout deadline resets too. Accepted as a
    known limitation rather than solved here.

    Args:
        config: Orchestrator config.
        feature: The feature the job is implementing.
        spec: The agent spec it's running under.
        sandbox_name: The job's sandbox (must already exist).
    """
    set_job_context(sandbox_name)
    store = StateStore(config.state_db_path)
    branch = branch_for(feature.id, spec)
    log.info("job_reattaching_after_restart")
    started_monotonic = time.monotonic()
    store.upsert(sandbox_name, status="running")

    await_and_finalize(
        config,
        store,
        sandbox_name,
        started_monotonic,
        feature=feature,
        agent_id=spec.agent_id,
        requested_branch=branch,
        command_override=spec.command,
    )


def run_job_safe(config: Config, feature: Feature, spec: AgentSpec) -> None:
    """Runs run_job, catching any exception so it can't kill the worker
    pool or other jobs in flight.

    Args:
        config: Orchestrator config.
        feature: The feature to implement.
        spec: Which agent (and optional run_label) to run it through.
    """
    sandbox_name = sandbox_name_for(feature.id, spec)
    set_job_context(sandbox_name)
    try:
        run_job(config, feature, spec)
    except Exception:  # noqa: BLE001 -- must not let one job's failure kill the pool
        with StateStore(config.state_db_path) as store:
            store.upsert(sandbox_name, status="error", detail=traceback.format_exc()[-2000:])
        log.error("job_unexpected_error", exc_info=True)
    finally:
        set_job_context(None)


def run_all(
    config: Config,
    jobs: list[tuple[Feature, AgentSpec]],
    max_workers: int,
    slots: threading.Semaphore | None = None,
    run_id: str | None = None,
) -> None:
    """Runs a list of jobs concurrently, bounded by max_workers.

    Args:
        config: Orchestrator config.
        jobs: (Feature, AgentSpec) pairs, e.g. from expand_jobs.
        max_workers: Maximum number of jobs to run at once.
        slots: Optional cap shared with everything else running in this
            process, held for the length of each job. `max_workers` bounds
            this call alone, which is not enough when several runs go at
            once. The CLI passes nothing and so is unaffected.
        run_id: The run these jobs belong to, recorded on each job row.
    """
    print(f"Running {len(jobs)} job(s) with max_concurrency={max_workers}...")
    record_queued(config, jobs, run_id)

    def run_one(feature: Feature, spec: AgentSpec) -> None:
        if slots is None:
            run_job_safe(config, feature, spec)
            return
        with slots:
            run_job_safe(config, feature, spec)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(run_one, feature, spec) for feature, spec in jobs]
        concurrent.futures.wait(futures)
