"""Job expansion, the main poll loop, and concurrent execution."""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import shlex
import time
import traceback
from typing import TYPE_CHECKING

from agentshowdown.orchestrator.config import (
    AgentSpec,
    Config,
    Feature,
    resolve_dangerously_skip_permissions,
    resolve_kit_paths,
    resolve_model,
    resolve_provider,
    validate_model,
)
from agentshowdown.orchestrator.log import log, set_job_context
from agentshowdown.orchestrator.metrics import parse_diff_numstat, parse_usage
from agentshowdown.orchestrator.process import CommandError
from agentshowdown.orchestrator.prompt import build_prompt, build_resume_prompt
from agentshowdown.orchestrator.sbx import (
    AGENT_CLI_BUILDERS,
    AGENT_LOG_PATH,
    sbx_create_detached,
    sbx_exec_capture,
    sbx_exists,
    sbx_launch_agent,
    sbx_read_status,
    sbx_rm,
    sbx_status,
)
from agentshowdown.orchestrator.state import StateStore
from agentshowdown.orchestrator.vcs import (
    diff_numstat,
    fetch_branch,
    open_pr,
    push_branch,
    run_in_worktree,
    sandbox_ref,
)

if TYPE_CHECKING:
    import subprocess

TERMINAL_STATES = ("done", "stuck", "awaiting_input", "crashed")

# Container-level failure, as reported by `sbx ls` -- distinct from the
# agent's own status file, which may still hold a usable terminal state.
CONTAINER_FAILURE_STATES = ("crashed", "error", "failed")

# How many consecutive "not in the listing" observations before a job is
# declared lost. A sandbox can briefly not appear right after creation, so a
# single miss is not proof it is gone.
_MISSING_OBSERVATIONS_BEFORE_LOST = 3


def _utc_now() -> str:
    """Returns an ISO-8601 UTC timestamp for storage.

    Durations are measured with time.monotonic(), which is immune to clock
    changes; these timestamps are for display and ordering.
    """
    return dt.datetime.now(dt.UTC).isoformat()


def sandbox_name_for(feature_id: str, spec: AgentSpec) -> str:
    """Derives the sbx sandbox name for a (feature, agent) job."""
    base = f"arena-{feature_id}-{spec.agent_id}"
    if spec.run_label:
        base += f"-{spec.run_label}"
    return base[:60]


def branch_for(feature_id: str, spec: AgentSpec) -> str:
    """Derives the git branch name for a (feature, agent) job."""
    branch = f"agent/{feature_id}/{spec.agent_id}"
    if spec.run_label:
        branch += f"-{spec.run_label}"
    return branch


def expand_jobs(
    config: Config, features: dict[str, Feature], feature_ids: list[str] | None
) -> list[tuple[Feature, AgentSpec]]:
    """Flattens requested features into (Feature, AgentSpec) job pairs.

    Args:
        config: Orchestrator config (supplies the default agent).
        features: All loaded features, keyed by id.
        feature_ids: Which feature ids to include, or None for all of them.

    Returns:
        One (Feature, AgentSpec) pair per job. A feature with no `agents`
        entry expands to a single job using config.sbx_agent -- identical
        to Stage 1 behavior.

    Raises:
        CommandError: If an unknown feature id is requested, if an agent
            entry's resolved model isn't in KNOWN_MODELS for its agent_id,
            or if two agent entries for one feature would collide on
            sandbox name.
    """
    if feature_ids is None:
        selected = list(features.values())
    else:
        missing = [fid for fid in feature_ids if fid not in features]
        if missing:
            raise CommandError(
                f"Unknown feature id(s): {', '.join(missing)}. Available: {', '.join(features)}"
            )
        selected = [features[fid] for fid in feature_ids]

    jobs: list[tuple[Feature, AgentSpec]] = []
    seen_names: set[str] = set()
    for feature in selected:
        specs = feature.agents or [AgentSpec(agent_id=config.sbx_agent)]
        for spec in specs:
            if not spec.command and spec.agent_id not in AGENT_CLI_BUILDERS:
                raise CommandError(
                    f"Feature '{feature.id}': no CLI profile wired up for "
                    f"agent_id '{spec.agent_id}' yet (supported: "
                    f"{', '.join(AGENT_CLI_BUILDERS)}). Set `command:` on "
                    f"its entry in features.yaml to run it via a custom "
                    f"shell command instead (required for agent_id 'shell')."
                )
            if not spec.command:
                # command-based agents never receive --model (see
                # build_agent_invocation), so there's nothing to validate.
                profile = config.agent_profiles.get(spec.agent_id)
                try:
                    validate_model(spec.agent_id, resolve_model(spec, profile, config))
                except CommandError as e:
                    raise CommandError(f"Feature '{feature.id}': {e}") from e
            name = sandbox_name_for(feature.id, spec)
            if name in seen_names:
                raise CommandError(
                    f"Duplicate sandbox name '{name}' for feature '{feature.id}' -- "
                    f"give one of its agent entries a distinct run_label."
                )
            seen_names.add(name)
            jobs.append((feature, spec))
    return jobs


def find_spec_for_sandbox(config: Config, feature: Feature, sandbox_name: str) -> AgentSpec:
    """Reconstructs the AgentSpec a sandbox_name maps to.

    Used on resume, where the state DB only records agent_id (a plain
    string) but relaunching needs the full spec (model/command overrides
    too).

    Args:
        config: Orchestrator config (supplies the default agent).
        feature: The feature this sandbox belongs to.
        sandbox_name: Sandbox to find the originating spec for.

    Returns:
        The matching AgentSpec.

    Raises:
        CommandError: If no agent entry for this feature maps to
            sandbox_name (e.g. features.yaml was edited since the job
            was launched).
    """
    specs = feature.agents or [AgentSpec(agent_id=config.sbx_agent)]
    for spec in specs:
        if sandbox_name_for(feature.id, spec) == sandbox_name:
            return spec
    raise CommandError(
        f"No agent entry in feature '{feature.id}' maps to sandbox "
        f"'{sandbox_name}' -- was features.yaml edited since this job was "
        f"launched?"
    )


def poll_until_signal(config: Config, store: StateStore, sandbox_name: str, deadline: float) -> str:
    """Polls a sandbox until its agent reports a terminal status, the
    container itself crashes, or the deadline passes.

    Args:
        config: Orchestrator config (poll interval, status file path).
        store: State store to persist observed status into as it changes.
        sandbox_name: Sandbox to poll.
        deadline: time.monotonic() value to stop polling at.

    Returns:
        One of: "done", "stuck", "awaiting_input", "crashed", "timed_out".
    """
    next_status_read = 0.0
    consecutive_missing = 0

    while time.monotonic() < deadline:
        # --- cheap tier -------------------------------------------------
        # `sbx ls --json` runs on the host and spawns nothing inside the
        # sandbox, so this can run often without costing the agent anything.
        container_status = sbx_status(sandbox_name)

        if container_status in CONTAINER_FAILURE_STATES:
            # The container is gone/dying -- see if the agent (or the
            # fallback wrapper) managed to leave a status behind first.
            status = sbx_read_status(sandbox_name, config.status_file)
            if status and status.get("state") in TERMINAL_STATES:
                store.upsert(sandbox_name, status=status["state"], detail=status.get("message"))
                return status["state"]
            store.upsert(
                sandbox_name,
                status="crashed",
                detail=f"sbx reported container status: {container_status}",
            )
            return "crashed"

        if container_status == "unknown":
            # Not listed at all -- removed out from under us (a stray
            # `sbx rm`, a kill-all). Tolerate a few misses first, since a
            # freshly created sandbox can briefly not appear in the listing.
            consecutive_missing += 1
            if consecutive_missing >= _MISSING_OBSERVATIONS_BEFORE_LOST:
                store.upsert(
                    sandbox_name,
                    status="lost",
                    detail="sandbox disappeared from `sbx ls` while the job was running",
                )
                return "lost"
        else:
            consecutive_missing = 0

        # --- expensive tier ---------------------------------------------
        # Reading the status file spawns a process inside the sandbox, so it
        # runs on its own slower cadence rather than every liveness tick.
        now = time.monotonic()
        if now >= next_status_read:
            next_status_read = now + config.poll_interval_seconds
            status = sbx_read_status(sandbox_name, config.status_file)
            if status is not None:
                state = status.get("state")
                message = status.get("message")
                if state in TERMINAL_STATES:
                    store.upsert(sandbox_name, status=state, detail=message)
                    return state
                # "unknown"/malformed JSON -- keep polling rather than failing.
                store.upsert(sandbox_name, status="running", detail=message)
            log.info(
                "job_still_running",
                container_status=container_status,
                poll_interval_seconds=config.poll_interval_seconds,
            )

        time.sleep(config.liveness_interval_seconds)

    store.upsert(sandbox_name, status="timed_out")
    return "timed_out"


def _extract_before_teardown(
    config: Config, sandbox_name: str, requested_branch: str
) -> tuple[str, str]:
    """Pulls everything needed from the sandbox in a single exec.

    Once this returns, nothing else needs the sandbox alive, so it can be
    torn down immediately. Bundling the branch name and the run log into one
    `sbx exec` keeps teardown at one process inside the container rather
    than one per value.

    Args:
        config: Orchestrator config.
        sandbox_name: Sandbox to read from.
        requested_branch: Fallback if the branch can't be read.

    Returns:
        (actual_branch, run_log).
    """
    marker = "---agentshowdown-log---"
    result = sbx_exec_capture(
        sandbox_name,
        f"cd {shlex.quote(config.repo_path)} && git rev-parse --abbrev-ref HEAD; "
        f"echo {marker}; cat {shlex.quote(AGENT_LOG_PATH)} 2>/dev/null",
        login_shell=False,
    )
    head, _, log_text = result.stdout.partition(marker)
    branch = head.strip().splitlines()[-1].strip() if head.strip() else ""
    return branch or requested_branch, log_text.lstrip("\n")


def _verify(
    config: Config, sandbox_name: str, ref: str, command: str
) -> subprocess.CompletedProcess:
    """Runs a test or lint command, on the host or in the sandbox.

    Args:
        config: Orchestrator config (`verify_on` picks the location).
        sandbox_name: Sandbox to run in, when verifying there.
        ref: Fetched ref to check out, when verifying on the host.
        command: The command to run.

    Returns:
        The completed process.
    """
    if config.verify_on == "host":
        return run_in_worktree(config.repo_path, ref, command)
    return sbx_exec_capture(sandbox_name, f"cd {shlex.quote(config.repo_path)} && {command}")


def _finalize_success(
    config: Config,
    store: StateStore,
    sandbox_name: str,
    *,
    feature: Feature,
    agent_id: str,
    requested_branch: str,
) -> None:
    """Verifies the work, publishes the branch, and records metrics.

    Ordering is deliberate: everything the sandbox is needed for happens in
    one exec up front, then the branch is fetched to the host, and from that
    point on verification, diff measurement and publishing all run host-side
    so the sandbox can be released as early as possible.

    Args:
        config: Orchestrator config.
        store: State store to record the outcome into.
        sandbox_name: Sandbox that finished.
        feature: The feature that was implemented.
        agent_id: Which agent implemented it.
        requested_branch: The branch name the prompt asked for (the agent
            may have used a different one).
    """
    actual_branch, run_log = _extract_before_teardown(config, sandbox_name, requested_branch)
    if actual_branch != requested_branch:
        log.warning(
            "agent_used_unexpected_branch",
            actual_branch=actual_branch,
            requested_branch=requested_branch,
        )

    usage = parse_usage(run_log, agent_id)
    metrics: dict[str, object] = {
        "branch": actual_branch,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "num_turns": usage.num_turns,
    }

    # Fetch (not push) so the work can be verified on the host before any of
    # it reaches origin.
    fetch_branch(config.repo_path, sandbox_name)
    ref = sandbox_ref(sandbox_name, actual_branch)

    files_changed, lines_added, lines_removed = parse_diff_numstat(
        diff_numstat(config.repo_path, config.base_branch, ref)
    )
    metrics |= {
        "files_changed": files_changed,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
    }

    for command, label, failed_status in (
        (config.test_command, "tests", "tests_failed"),
        (config.lint_command, "lint", "lint_failed"),
    ):
        if not command:
            continue
        log.info(f"{label}_started", command=command, verify_on=config.verify_on)
        result = _verify(config, sandbox_name, ref, command)
        passed = result.returncode == 0
        metrics[f"{label}_passed"] = int(passed)
        if not passed:
            log.error(
                f"{label}_failed",
                command=command,
                stdout=result.stdout[-2000:],
                stderr=result.stderr[-2000:],
            )
            store.upsert(
                sandbox_name,
                status=failed_status,
                detail=result.stdout[-2000:],
                **metrics,
            )
            if config.remove_sandbox_on_failure:
                sbx_rm(sandbox_name)
            return

    log.info("branch_push_started", branch=actual_branch)
    push_branch(config.repo_path, sandbox_name, actual_branch)

    pr_url = None
    if config.open_pr:
        title = f"[{feature.id}] via {agent_id}"
        body = f"Automated implementation of feature `{feature.id}`.\n\n{feature.description}"
        pr_url = open_pr(
            config.repo_path,
            config.github_repo,
            actual_branch,
            config.base_branch,
            title=title,
            body=body,
        )
        log.info("pr_opened", pr_url=pr_url)
    else:
        # The branch is on origin either way; only the PR is gated, because
        # opening one is an outward-facing side effect on a real repo.
        log.info("pr_skipped", branch=actual_branch, reason="open_pr disabled")

    store.upsert(sandbox_name, status="succeeded", pr_url=pr_url, **metrics)

    if config.remove_sandbox_on_success:
        sbx_rm(sandbox_name)


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
                hint=(f'python -m agentshowdown.orchestrator answer {sandbox_name} "<answer>"'),
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
    )

    if not sandbox_alive:
        prompt = build_prompt(feature, branch, config.status_file)
        log.info("sandbox_launching", feature_id=feature.id, agent_id=agent_id)
        try:
            sbx_create_detached(
                sandbox_name=sandbox_name,
                agent=agent_id,
                workspace=config.repo_path,
                kit=resolve_kit_paths(spec, profile),
                provider=resolve_provider(spec, profile, config),
                model=resolved_model,
            )
        except CommandError:
            # Likely a race with a concurrent `run` that created it first.
            if sbx_exists(sandbox_name):
                log.info("sandbox_create_raced")
            else:
                raise
        else:
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
            )

    started_monotonic = time.monotonic()
    store.upsert(sandbox_name, status="running", started_at=_utc_now(), finished_at=None)

    deadline = started_monotonic + config.timeout_minutes * 60
    final_status = poll_until_signal(config, store, sandbox_name, deadline)

    store.upsert(
        sandbox_name,
        finished_at=_utc_now(),
        duration_seconds=round(time.monotonic() - started_monotonic, 3),
    )
    log.info("job_finished", status=final_status)

    if final_status == "awaiting_input":
        # Sandbox must survive so `answer` can resume it.
        return

    if final_status != "done":
        if config.remove_sandbox_on_failure:
            sbx_rm(sandbox_name)
        else:
            log.info("sandbox_left_for_inspection", hint=f"sbx exec {sandbox_name} bash")
        return

    _finalize_success(
        config,
        store,
        sandbox_name,
        feature=feature,
        agent_id=agent_id,
        requested_branch=branch,
    )


def resume_job(
    config: Config, features: dict[str, Feature], sandbox_name: str, human_answer: str
) -> None:
    """Answers a job that is awaiting_input and resumes it to completion.

    Args:
        config: Orchestrator config.
        features: All loaded features, keyed by id (to look up the one
            this job belongs to).
        sandbox_name: The job to resume.
        human_answer: The human's answer to the agent's question.

    Raises:
        CommandError: If the job is unknown, isn't awaiting_input, or its
            sandbox no longer exists.
    """
    set_job_context(sandbox_name)
    store = StateStore(config.state_db_path)
    job = store.get(sandbox_name)
    if job is None:
        raise CommandError(
            f"No known job '{sandbox_name}'. Run "
            f"`python -m agentshowdown.orchestrator status` to list jobs."
        )
    if job["status"] != "awaiting_input":
        raise CommandError(
            f"Job '{sandbox_name}' is '{job['status']}', not 'awaiting_input' -- nothing to answer."
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

    feature = features.get(job["feature_id"])
    if feature is None:
        raise CommandError(f"Feature '{job['feature_id']}' not found in the loaded features file.")
    spec = find_spec_for_sandbox(config, feature, sandbox_name)
    profile = config.agent_profiles.get(spec.agent_id)

    prompt = build_resume_prompt(human_answer, config.status_file)
    log.info("job_resuming")
    sbx_launch_agent(
        sandbox_name=sandbox_name,
        agent_id=spec.agent_id,
        prompt=prompt,
        config=config,
        model_override=resolve_model(spec, profile, config),
        skip_permissions_override=resolve_dangerously_skip_permissions(spec, profile, config),
        command_override=spec.command,
        resume=True,
    )
    started_monotonic = time.monotonic()
    store.upsert(sandbox_name, status="running", detail=None, started_at=_utc_now())

    deadline = started_monotonic + config.timeout_minutes * 60
    final_status = poll_until_signal(config, store, sandbox_name, deadline)

    store.upsert(
        sandbox_name,
        finished_at=_utc_now(),
        duration_seconds=round(time.monotonic() - started_monotonic, 3),
    )
    log.info("job_finished", status=final_status)

    if final_status == "awaiting_input":
        return

    if final_status != "done":
        if config.remove_sandbox_on_failure:
            sbx_rm(sandbox_name)
        return

    _finalize_success(
        config,
        store,
        sandbox_name,
        feature=feature,
        agent_id=job["agent_id"],
        requested_branch=job["branch"],
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


def run_all(config: Config, jobs: list[tuple[Feature, AgentSpec]], max_workers: int) -> None:
    """Runs a list of jobs concurrently, bounded by max_workers.

    Args:
        config: Orchestrator config.
        jobs: (Feature, AgentSpec) pairs, e.g. from expand_jobs.
        max_workers: Maximum number of jobs to run at once.
    """
    print(f"Running {len(jobs)} job(s) with max_concurrency={max_workers}...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(run_job_safe, config, feature, spec) for feature, spec in jobs]
        concurrent.futures.wait(futures)
