"""Job expansion, the main poll loop, and concurrent execution."""

from __future__ import annotations

import concurrent.futures
import shlex
import sys
import time
import traceback

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
from agentshowdown.orchestrator.process import CommandError
from agentshowdown.orchestrator.prompt import build_prompt, build_resume_prompt
from agentshowdown.orchestrator.sbx import (
    AGENT_CLI_BUILDERS,
    sbx_create_detached,
    sbx_current_branch,
    sbx_exec_capture,
    sbx_exists,
    sbx_launch_agent,
    sbx_read_status,
    sbx_rm,
    sbx_status,
)
from agentshowdown.orchestrator.state import StateStore
from agentshowdown.orchestrator.vcs import fetch_and_push_branch, open_pr

TERMINAL_STATES = ("done", "stuck", "awaiting_input", "crashed")


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
                f"Unknown feature id(s): {', '.join(missing)}. "
                f"Available: {', '.join(features)}"
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


def find_spec_for_sandbox(
    config: Config, feature: Feature, sandbox_name: str
) -> AgentSpec:
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


def poll_until_signal(
    config: Config, store: StateStore, sandbox_name: str, deadline: float
) -> str:
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
    while time.monotonic() < deadline:
        container_status = sbx_status(sandbox_name)
        if container_status in ("crashed", "error", "failed"):
            # The container is gone/dying -- see if the agent (or the
            # fallback wrapper) managed to leave a status behind first.
            status = sbx_read_status(sandbox_name, config.status_file)
            if status and status.get("state") in TERMINAL_STATES:
                store.upsert(
                    sandbox_name, status=status["state"], detail=status.get("message")
                )
                return status["state"]
            store.upsert(
                sandbox_name,
                status="crashed",
                detail=f"sbx reported container status: {container_status}",
            )
            return "crashed"

        status = sbx_read_status(sandbox_name, config.status_file)
        if status is not None:
            state = status.get("state")
            message = status.get("message")
            if state in TERMINAL_STATES:
                store.upsert(sandbox_name, status=state, detail=message)
                return state
            # "unknown"/malformed JSON -- keep polling rather than failing.
            store.upsert(sandbox_name, status="running", detail=message)

        log(
            f"  ...still running (sbx status: {container_status}), "
            f"checking again in {config.poll_interval_seconds}s"
        )
        time.sleep(config.poll_interval_seconds)

    store.upsert(sandbox_name, status="timed_out")
    return "timed_out"


def _finalize_success(
    config: Config,
    store: StateStore,
    sandbox_name: str,
    feature: Feature,
    agent_id: str,
    requested_branch: str,
) -> None:
    """Runs test/lint, pushes the branch, and opens a PR for a done job.

    Args:
        config: Orchestrator config.
        store: State store to record the outcome into.
        sandbox_name: Sandbox that finished.
        feature: The feature that was implemented.
        agent_id: Which agent implemented it.
        requested_branch: The branch name the prompt asked for (the agent
            may have used a different one).
    """
    actual_branch = (
        sbx_current_branch(sandbox_name, config.repo_path) or requested_branch
    )
    if actual_branch != requested_branch:
        log(
            f"NOTE: agent used branch '{actual_branch}' instead of requested "
            f"'{requested_branch}'. Using the actual branch."
        )

    if config.test_command:
        log(f"Running tests inside sandbox: {config.test_command}")
        test_result = sbx_exec_capture(
            sandbox_name, f"cd {shlex.quote(config.repo_path)} && {config.test_command}"
        )
        if test_result.returncode != 0:
            log("Tests FAILED. Not opening a PR.")
            log(test_result.stdout)
            log(test_result.stderr)
            store.upsert(
                sandbox_name,
                status="tests_failed",
                branch=actual_branch,
                detail=test_result.stdout[-2000:],
            )
            if config.remove_sandbox_on_failure:
                sbx_rm(sandbox_name)
            return

    if config.lint_command:
        log(f"Running lint inside sandbox: {config.lint_command}")
        lint_result = sbx_exec_capture(
            sandbox_name, f"cd {shlex.quote(config.repo_path)} && {config.lint_command}"
        )
        if lint_result.returncode != 0:
            log("Lint FAILED. Not opening a PR.")
            store.upsert(
                sandbox_name,
                status="lint_failed",
                branch=actual_branch,
                detail=lint_result.stdout[-2000:],
            )
            if config.remove_sandbox_on_failure:
                sbx_rm(sandbox_name)
            return

    log(f"Fetching branch '{actual_branch}' from sandbox and pushing to origin...")
    fetch_and_push_branch(config.repo_path, sandbox_name, actual_branch)

    title = f"[{feature.id}] via {agent_id}"
    body = (
        f"Automated implementation of feature `{feature.id}`.\n\n{feature.description}"
    )
    pr_url = open_pr(
        config.repo_path,
        config.github_repo,
        actual_branch,
        config.base_branch,
        title,
        body,
    )
    log(f"\nOpened PR: {pr_url}\n")

    store.upsert(sandbox_name, status="succeeded", branch=actual_branch, pr_url=pr_url)

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
            log(
                f"'{sandbox_name}' is awaiting_input -- use "
                f'`python -m agentshowdown.orchestrator answer {sandbox_name} "..."` to continue it. '
                f"Skipping."
            )
            return
        if status == "succeeded":
            log(
                f"'{sandbox_name}' already succeeded (PR: {existing.get('pr_url')}). "
                f"Give this agent a distinct run_label in features.yaml to rerun. Skipping."
            )
            return
        if status in ("queued", "running") and sandbox_alive:
            log(f"Reattaching to already-running sandbox '{sandbox_name}'...")
        elif sandbox_alive:
            log(
                f"'{sandbox_name}' previously ended as '{status}' -- removing and relaunching."
            )
            sbx_rm(sandbox_name)
            sandbox_alive = False
    elif sandbox_alive:
        log(
            f"Sandbox '{sandbox_name}' exists with no job record -- reattaching "
            f"rather than risking a second agent process in it."
        )

    store.upsert(
        sandbox_name,
        feature_id=feature.id,
        agent_id=agent_id,
        branch=branch,
        status="queued" if not sandbox_alive else "running",
        pr_url=None,
        detail=None,
    )

    if not sandbox_alive:
        profile = config.agent_profiles.get(agent_id)
        resolved_model = resolve_model(spec, profile, config)
        prompt = build_prompt(feature, branch, config.status_file)
        log(
            f"\n=== Launching sandbox '{sandbox_name}' for feature '{feature.id}' "
            f"(agent: {agent_id}) ===\n"
        )
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
                log(
                    f"'{sandbox_name}' already exists -- reattaching instead of relaunching."
                )
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

    store.upsert(sandbox_name, status="running")

    deadline = time.monotonic() + config.timeout_minutes * 60
    final_status = poll_until_signal(config, store, sandbox_name, deadline)

    log(f"\n=== Job '{sandbox_name}' finished with status: {final_status} ===\n")

    if final_status == "awaiting_input":
        # Sandbox must survive so `answer` can resume it.
        return

    if final_status != "done":
        if config.remove_sandbox_on_failure:
            sbx_rm(sandbox_name)
        else:
            log(f"Sandbox left running for inspection: sbx exec {sandbox_name} bash")
        return

    _finalize_success(config, store, sandbox_name, feature, agent_id, branch)


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
    store = StateStore(config.state_db_path)
    job = store.get(sandbox_name)
    if job is None:
        raise CommandError(
            f"No known job '{sandbox_name}'. Run `python -m agentshowdown.orchestrator status` to list jobs."
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
        raise CommandError(
            f"Feature '{job['feature_id']}' not found in the loaded features file."
        )
    spec = find_spec_for_sandbox(config, feature, sandbox_name)
    profile = config.agent_profiles.get(spec.agent_id)

    prompt = build_resume_prompt(human_answer, config.status_file)
    log(f"\n=== Resuming sandbox '{sandbox_name}' with human answer ===\n")
    sbx_launch_agent(
        sandbox_name=sandbox_name,
        agent_id=spec.agent_id,
        prompt=prompt,
        config=config,
        model_override=resolve_model(spec, profile, config),
        skip_permissions_override=resolve_dangerously_skip_permissions(
            spec, profile, config
        ),
        command_override=spec.command,
        resume=True,
    )
    store.upsert(sandbox_name, status="running", detail=None)

    deadline = time.monotonic() + config.timeout_minutes * 60
    final_status = poll_until_signal(config, store, sandbox_name, deadline)

    log(f"\n=== Job '{sandbox_name}' finished with status: {final_status} ===\n")

    if final_status == "awaiting_input":
        return

    if final_status != "done":
        if config.remove_sandbox_on_failure:
            sbx_rm(sandbox_name)
        return

    _finalize_success(
        config, store, sandbox_name, feature, job["agent_id"], job["branch"]
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
        store = StateStore(config.state_db_path)
        store.upsert(
            sandbox_name, status="error", detail=traceback.format_exc()[-2000:]
        )
        log(
            f"\n=== Job '{sandbox_name}' raised an unexpected error ===\n",
            file=sys.stderr,
        )
        traceback.print_exc()
    finally:
        set_job_context(None)


def run_all(
    config: Config, jobs: list[tuple[Feature, AgentSpec]], max_workers: int
) -> None:
    """Runs a list of jobs concurrently, bounded by max_workers.

    Args:
        config: Orchestrator config.
        jobs: (Feature, AgentSpec) pairs, e.g. from expand_jobs.
        max_workers: Maximum number of jobs to run at once.
    """
    print(f"Running {len(jobs)} job(s) with max_concurrency={max_workers}...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(run_job_safe, config, feature, spec) for feature, spec in jobs
        ]
        concurrent.futures.wait(futures)
