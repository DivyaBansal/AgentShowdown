"""Job expansion, the main poll loop, and concurrent execution."""

from __future__ import annotations

import concurrent.futures
import contextlib
import datetime as dt
import json
import shlex
import time
import traceback
from typing import TYPE_CHECKING, Literal

from backend.logging import log, set_job_context
from backend.orchestrator.agent_otel import (
    exports_otel,
    policy_resource,
    prefer_otel_usage,
    usage_from_events,
)
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
    spec_from_json,
    spec_to_json,
    uses_custom_model_endpoint,
    validate_model,
)
from backend.orchestrator.events import bus
from backend.orchestrator.metrics import parse_diff_numstat, parse_usage
from backend.orchestrator.process import CommandError
from backend.orchestrator.prompt import (
    MAX_QUESTION_OPTIONS,
    build_continue_prompt,
    build_prompt,
    build_resume_prompt,
)
from backend.orchestrator.sbx import (
    AGENT_CLI_BUILDERS,
    AGENT_LOG_PATH,
    sbx_agent_exited,
    sbx_allow_network,
    sbx_create_detached,
    sbx_exec_capture,
    sbx_exists,
    sbx_launch_agent,
    sbx_read_status,
    sbx_read_status_and_tail,
    sbx_restart_detached,
    sbx_rm,
    sbx_status,
)
from backend.orchestrator.state import StateStore, failure_category_for
from backend.orchestrator.vcs import (
    diff_numstat,
    fetch_branch,
    open_pr,
    push_branch,
    run_in_worktree,
    sandbox_ref,
)

if TYPE_CHECKING:
    import subprocess
    import threading
    from collections.abc import Iterator

TERMINAL_STATES = ("done", "stuck", "awaiting_input", "crashed")

# Container-level failure, as reported by `sbx ls` -- distinct from the
# agent's own status file, which may still hold a usable terminal state.
CONTAINER_FAILURE_STATES = ("crashed", "error", "failed")

# How many consecutive "not in the listing" observations before a job is
# declared lost. A sandbox can briefly not appear right after creation, so a
# single miss is not proof it is gone.
_MISSING_OBSERVATIONS_BEFORE_LOST = 3

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

# The phases of a job attempt, each timed into its own column so a slow run
# shows where the time went. `launch_s` is deliberately not in
# `_ATTEMPT_RESET`: launch is timed *before* the reset is written, and a
# reattach launches nothing, so it keeps the value its launch recorded.
JobStep = Literal["launch", "agent", "verify", "push"]
_STEP_COLUMNS: dict[JobStep, str] = {
    "launch": "launch_s",
    "agent": "agent_s",
    "verify": "verify_s",
    "push": "push_s",
}


# An agent reports its status before its process exits: it may still make a
# final model request, and its CLI flushes OTel events on the way out.
# Teardown waits for the exit so those last batches reach the receiver,
# but never longer than this -- a wedged CLI must not hold a job open.
AGENT_EXIT_TIMEOUT_SECONDS = 30.0
_AGENT_EXIT_POLL_SECONDS = 1.0

# Statuses after which the agent process is finishing on its own. Others
# either leave it running on purpose (awaiting_input, stopped), mean it is
# still running (timed_out), or mean the sandbox is gone (lost).
_EXITING_STATUSES = ("done", "stuck", "crashed")


@contextlib.contextmanager
def _timed_step(store: StateStore, sandbox_name: str, step: JobStep) -> Iterator[None]:
    """Times one phase of a job into its `<step>_s` column and logs it.

    The duration is recorded even when the step raises, so a launch that
    fails after a minute of retrying still shows that minute.

    Args:
        store: State store to record the duration into.
        sandbox_name: The job being timed.
        step: Which phase this is.
    """
    started = time.monotonic()
    outcome = "error"
    try:
        yield
        outcome = "ok"
    finally:
        elapsed = time.monotonic() - started
        store.upsert(sandbox_name, **{_STEP_COLUMNS[step]: round(elapsed, 3)})
        log.info(
            "job_step_finished",
            step=step,
            duration_ms=round(elapsed * 1000),
            outcome=outcome,
        )


def _utc_now() -> str:
    """Returns an ISO-8601 UTC timestamp for storage.

    Durations are measured with time.monotonic(), which is immune to clock
    changes; these timestamps are for display and ordering.
    """
    return dt.datetime.now(dt.UTC).isoformat()


# An option is a button label in the UI, not a paragraph.
_MAX_OPTION_CHARS = 200


def question_options_from(status: dict) -> str | None:
    """Extracts the answer options an awaiting_input agent offered, if any.

    The status file is written by the agent, so this is external input:
    anything that isn't a list of non-empty strings is ignored rather than
    trusted, and the list is capped in length and per-item size.

    Args:
        status: The parsed status file.

    Returns:
        The options as a JSON-encoded list, or None when the state isn't
        awaiting_input or no usable options were given -- so a terminal
        state without options also clears any options a previous question
        left behind.
    """
    if status.get("state") != "awaiting_input":
        return None
    raw = status.get("options")
    if not isinstance(raw, list):
        return None
    options = [
        item.strip()[:_MAX_OPTION_CHARS] for item in raw if isinstance(item, str) and item.strip()
    ][:MAX_QUESTION_OPTIONS]
    return json.dumps(options) if options else None


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
        CommandError: If an unknown feature id or unsupported agent is
            requested, or if two agent entries for one feature would
            collide on sandbox name.
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
                # A custom model endpoint (Ollama, a gateway) serves its own
                # model names, which KNOWN_MODELS can't know -- skip the check.
                if not uses_custom_model_endpoint(resolve_env(spec, profile)):
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


def spec_for_job(
    config: Config, store: StateStore, feature: Feature, sandbox_name: str
) -> AgentSpec:
    """Returns the AgentSpec a job was actually launched with.

    A run can carry agent settings that were never saved to features.yaml
    (the run launcher sends its own copy of each agent), so rebuilding the
    spec from the features file would silently drop them -- e.g. the env
    that points claude at a local Ollama. Prefer the spec `run_job`
    recorded, and fall back to the features file only for jobs recorded
    before specs were stored.

    Args:
        config: Orchestrator config.
        store: The state store holding the job.
        feature: The feature this sandbox belongs to.
        sandbox_name: The job's sandbox.

    Returns:
        The recorded AgentSpec, else the one from find_spec_for_sandbox.

    Raises:
        CommandError: If nothing is recorded and no features.yaml entry
            maps to sandbox_name.
    """
    raw = store.get_spec(sandbox_name)
    spec = spec_from_json(raw) if raw is not None else None
    if spec is not None:
        return spec
    if raw is not None:
        log.warning("job_spec_unreadable", sandbox_name=sandbox_name)
    return find_spec_for_sandbox(config, feature, sandbox_name)


def poll_until_signal(config: Config, store: StateStore, sandbox_name: str, deadline: float) -> str:
    """Polls a sandbox until its agent reports a terminal status, the
    container itself crashes, or the deadline passes.

    Args:
        config: Orchestrator config (poll interval, status file path).
        store: State store to persist observed status into as it changes.
        sandbox_name: Sandbox to poll.
        deadline: time.monotonic() value to stop polling at.

    Returns:
        One of: "done", "stuck", "awaiting_input", "crashed", "timed_out",
        "stopped".
    """
    next_status_read = 0.0
    consecutive_missing = 0

    while time.monotonic() < deadline:
        # --- cheap tier -------------------------------------------------
        # `sbx ls --json` runs on the host and spawns nothing inside the
        # sandbox, so this can run often without costing the agent anything.
        container_status = sbx_status(sandbox_name)

        if container_status == "stopped":
            # A deliberate `sbx stop` (see sandboxes.stop), not a crash --
            # exec-ing into a stopped container would just fail, so this is
            # detected and returned immediately rather than falling through
            # to the status-file read below.
            store.upsert(sandbox_name, status="stopped", detail="sandbox stopped")
            return "stopped"

        if container_status in CONTAINER_FAILURE_STATES:
            # The container is gone/dying -- see if the agent (or the
            # fallback wrapper) managed to leave a status behind first.
            status = sbx_read_status(sandbox_name, config.status_file)
            if status and status.get("state") in TERMINAL_STATES:
                store.upsert(
                    sandbox_name,
                    status=status["state"],
                    detail=status.get("message"),
                    question_options=question_options_from(status),
                )
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
        # The log tail rides along in the same exec (see
        # sbx_read_status_and_tail) so this tier still costs one process per
        # tick, not two.
        now = time.monotonic()
        if now >= next_status_read:
            next_status_read = now + config.poll_interval_seconds
            status, tail = sbx_read_status_and_tail(
                sandbox_name, config.status_file, AGENT_LOG_PATH, config.log_tail_lines
            )
            # Written separately from the status/detail upsert below (and
            # without publishing job_changed): the tail refreshes every
            # tick regardless of whether the job's status actually moved,
            # and reconciling the whole board that often would defeat the
            # point of a live tail. Subscribers get it via the dedicated
            # job_log event published right after.
            store.update_log_tail(sandbox_name, tail)
            bus.publish("job_log", sandbox_name=sandbox_name, tail=tail)
            if status is not None:
                state = status.get("state")
                message = status.get("message")
                if state in TERMINAL_STATES:
                    store.upsert(
                        sandbox_name,
                        status=state,
                        detail=message,
                        question_options=question_options_from(status),
                    )
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


def _await_agent_exit(sandbox_name: str) -> bool:
    """Waits, bounded, for this turn's agent process to exit.

    Once it has exited, every OTel batch it flushed has been received: the
    receiver commits each batch before acknowledging it, and the CLI's exit
    waits on that acknowledgement.

    Args:
        sandbox_name: The job's sandbox. Gives up after
            AGENT_EXIT_TIMEOUT_SECONDS.

    Returns:
        True if the exit was observed, False on timeout.
    """
    started = time.monotonic()
    while True:
        if sbx_agent_exited(sandbox_name):
            log.info("agent_exit_observed", waited_ms=round((time.monotonic() - started) * 1000))
            return True
        if time.monotonic() - started >= AGENT_EXIT_TIMEOUT_SECONDS:
            log.warning("agent_exit_wait_timed_out", timeout_seconds=AGENT_EXIT_TIMEOUT_SECONDS)
            return False
        time.sleep(_AGENT_EXIT_POLL_SECONDS)


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


def _extract_before_teardown(
    config: Config, sandbox_name: str, requested_branch: str
) -> tuple[str, str, str]:
    """Pulls everything needed from the sandbox in a single exec.

    Once this returns, nothing else needs the sandbox alive, so it can be
    torn down immediately. Bundling the branch name, the run log and the
    container's repo directory into one `sbx exec` keeps teardown at one
    process inside the container rather than one per value.

    No `cd` is issued. `config.repo_path` is a *host* path and generally
    does not exist inside the container; a `--clone` sandbox already starts
    in its own copy of the repo, which is the same assumption
    `sbx_read_status` relies on with its bare `cat`. `login_shell=False`
    keeps that true by not sourcing a profile that could `cd` elsewhere.

    Args:
        config: Orchestrator config.
        sandbox_name: Sandbox to read from.
        requested_branch: Fallback if the branch can't be read.

    Returns:
        (actual_branch, run_log, container_repo_dir). The last is "" if the
        container did not report one, meaning callers should fall back to
        the sandbox's default working directory.
    """
    dir_marker = "---agentshowdown-dir---"
    marker = "---agentshowdown-log---"
    result = sbx_exec_capture(
        sandbox_name,
        f"pwd; echo {dir_marker}; "
        f"git rev-parse --abbrev-ref HEAD; "
        f"echo {marker}; cat {shlex.quote(AGENT_LOG_PATH)} 2>/dev/null",
        login_shell=False,
    )
    # A missing dir marker must not swallow the log: `partition` returns the
    # whole string as the head when the separator is absent, so branch on
    # presence rather than trusting the split.
    if dir_marker in result.stdout:
        dir_text, _, rest = result.stdout.partition(dir_marker)
        container_dir = dir_text.strip().splitlines()[-1].strip() if dir_text.strip() else ""
    else:
        rest, container_dir = result.stdout, ""
    head, _, log_text = rest.partition(marker)
    branch = head.strip().splitlines()[-1].strip() if head.strip() else ""
    return branch or requested_branch, log_text.lstrip("\n"), container_dir


def _verify(
    config: Config,
    sandbox_name: str,
    ref: str,
    command: str,
    container_dir: str = "",
) -> subprocess.CompletedProcess:
    """Runs a test or lint command, on the host or in the sandbox.

    Args:
        config: Orchestrator config (`verify_on` picks the location).
        sandbox_name: Sandbox to run in, when verifying there.
        ref: Fetched ref to check out, when verifying on the host.
        command: The command to run.
        container_dir: The container's own repo directory, as reported by
            `_extract_before_teardown`. Passed to sbx as `-w` rather than
            `cd`-ed to, because `config.repo_path` is a host path. Empty
            means "use the sandbox's default working directory".

    Returns:
        The completed process.
    """
    if config.verify_on == "host":
        return run_in_worktree(config.repo_path, ref, command)
    return sbx_exec_capture(sandbox_name, command, workdir=container_dir or None)


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
    # The verify step is everything between the agent finishing and the
    # publish: extract, fetch, diff, then the test and lint commands.
    failed: tuple[str, subprocess.CompletedProcess] | None = None
    with _timed_step(store, sandbox_name, "verify"):
        actual_branch, run_log, container_dir = _extract_before_teardown(
            config, sandbox_name, requested_branch
        )
        if actual_branch != requested_branch:
            log.warning(
                "agent_used_unexpected_branch",
                actual_branch=actual_branch,
                requested_branch=requested_branch,
            )

        # OTel events first, the run log as the fallback: the events cover
        # every request in the turn, while the log only has what the CLI
        # printed -- and nothing at all if it died before printing it.
        otel_usage = usage_from_events(
            agent_id,
            store.get_agent_events(sandbox_name, since=store.get_agent_launched_at(sandbox_name)),
        )
        usage = prefer_otel_usage(otel_usage, parse_usage(run_log, agent_id))
        log.info(
            "usage_recorded",
            source="otel" if not otel_usage.is_empty else "log",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
        metrics: dict[str, object] = {
            "branch": actual_branch,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "num_turns": usage.num_turns,
        }

        # Fetch (not push) so the work can be verified on the host before any
        # of it reaches origin.
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
            result = _verify(config, sandbox_name, ref, command, container_dir)
            passed = result.returncode == 0
            metrics[f"{label}_passed"] = int(passed)
            if not passed:
                log.error(
                    f"{label}_failed",
                    command=command,
                    stdout=result.stdout[-2000:],
                    stderr=result.stderr[-2000:],
                )
                failed = (failed_status, result)
                break

    if failed is not None:
        failed_status, result = failed
        store.upsert(
            sandbox_name,
            status=failed_status,
            detail=result.stdout[-2000:],
            **metrics,
        )
        if config.remove_sandbox_on_failure:
            sbx_rm(sandbox_name)
        return

    pr_url = None
    with _timed_step(store, sandbox_name, "push"):
        log.info("branch_push_started", branch=actual_branch)
        push_branch(config.repo_path, sandbox_name, actual_branch)

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
            # The branch is on origin either way; only the PR is gated,
            # because opening one is an outward-facing side effect on a real
            # repo.
            log.info("pr_skipped", branch=actual_branch, reason="open_pr disabled")

    store.upsert(sandbox_name, status="succeeded", pr_url=pr_url, **metrics)

    if config.remove_sandbox_on_success:
        sbx_rm(sandbox_name)


def _await_and_finalize(
    config: Config,
    store: StateStore,
    sandbox_name: str,
    started_monotonic: float,
    *,
    feature: Feature,
    agent_id: str,
    requested_branch: str,
    command_override: str | None = None,
) -> None:
    """Polls a launched agent to a terminal state, records timing, then either
    finalizes the successful run or tears the sandbox down.

    Shared by `run_job` and `resume_job`: everything after the agent is
    (re)launched is identical between them.

    Args:
        config: Orchestrator config.
        store: State store to record into.
        sandbox_name: The job's sandbox.
        started_monotonic: `time.monotonic()` captured at launch, for the
            timeout deadline and the duration metric. After a reattach this
            is the reattach time, so both `duration_seconds` and `agent_s`
            undercount the time before the restart.
        feature: The feature being implemented.
        agent_id: Which agent implemented it.
        requested_branch: The branch the prompt asked for.
        command_override: The spec's custom command, if any -- a custom
            command exports no OTel, so there is nothing to wait for.
    """
    deadline = started_monotonic + config.timeout_minutes * 60
    with _timed_step(store, sandbox_name, "agent"):
        final_status = poll_until_signal(config, store, sandbox_name, deadline)

    # poll_until_signal already wrote the status, and with it the category.
    store.upsert(
        sandbox_name,
        finished_at=_utc_now(),
        duration_seconds=round(time.monotonic() - started_monotonic, 3),
    )
    log.info(
        "job_finished",
        status=final_status,
        failure_category=failure_category_for(final_status),
    )

    if final_status in ("awaiting_input", "stopped"):
        # Sandbox must survive: `answer` resumes an awaiting_input job,
        # `respawn` restarts a stopped one -- either way there's nothing to
        # finalize or tear down yet.
        return

    if final_status in _EXITING_STATUSES and exports_otel(
        agent_id, command_override, config.agent_otel_endpoint
    ):
        _await_agent_exit(sandbox_name)

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
        requested_branch=requested_branch,
    )


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
        with _timed_step(store, sandbox_name, "launch"):
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
                store.set_agent_launched_at(sandbox_name, _utc_now())
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
    store.upsert(sandbox_name, status="running", started_at=_utc_now(), **_ATTEMPT_RESET)

    _await_and_finalize(
        config,
        store,
        sandbox_name,
        started_monotonic,
        feature=feature,
        agent_id=agent_id,
        requested_branch=branch,
        command_override=spec.command,
    )


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
    with _timed_step(store, sandbox_name, "launch"):
        store.set_agent_launched_at(sandbox_name, _utc_now())
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
    store.upsert(
        sandbox_name, status="running", detail=None, started_at=_utc_now(), **_ATTEMPT_RESET
    )

    _await_and_finalize(
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
    with _timed_step(store, sandbox_name, "launch"):
        sbx_restart_detached(sandbox_name, spec.agent_id)
        store.set_agent_launched_at(sandbox_name, _utc_now())
        sbx_launch_agent(
            sandbox_name=sandbox_name,
            agent_id=spec.agent_id,
            prompt=build_continue_prompt(config.status_file),
            config=config,
            model_override=resolve_model(spec, profile, config),
            skip_permissions_override=resolve_dangerously_skip_permissions(spec, profile, config),
            command_override=spec.command,
            resume=True,
        )
    started_monotonic = time.monotonic()
    store.upsert(
        sandbox_name, status="running", detail=None, started_at=_utc_now(), **_ATTEMPT_RESET
    )

    _await_and_finalize(
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

    _await_and_finalize(
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


def record_queued(
    config: Config, jobs: list[tuple[Feature, AgentSpec]], run_id: str | None
) -> None:
    """Gives every job a row up front, so waiting jobs are visible.

    Without this a job has no row until a worker picks it up, so five jobs
    at max_concurrency 3 show as three on the board and the rest appear
    from nowhere later.

    A job that already has a row keeps its status: `run_job` decides what
    to do from it -- skipping work that already succeeded, relaunching what
    failed -- and overwriting that with "queued" would lose that history.
    Such a row still gets `run_id`, so it belongs to this run either way.

    Args:
        config: Orchestrator config.
        jobs: (Feature, AgentSpec) pairs, e.g. from expand_jobs.
        run_id: The run these jobs belong to, if any (the CLI has none).
    """
    with StateStore(config.state_db_path) as store:
        for feature, spec in jobs:
            sandbox_name = sandbox_name_for(feature.id, spec)
            if store.get(sandbox_name) is not None:
                store.upsert(sandbox_name, run_id=run_id)
                continue
            store.upsert(
                sandbox_name,
                feature_id=feature.id,
                agent_id=spec.agent_id,
                branch=branch_for(feature.id, spec),
                status="queued",
                run_id=run_id,
                model=resolve_model(spec, config.agent_profiles.get(spec.agent_id), config),
                run_label=spec.run_label,
                repo=host_repo_path(config),
            )


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
