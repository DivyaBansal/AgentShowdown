"""Watching a launched agent to its outcome, then verifying and publishing it."""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import shlex
import time
from typing import TYPE_CHECKING, Literal

from backend.logging import log
from backend.orchestrator.agent_otel import exports_otel, prefer_otel_usage, usage_from_events
from backend.orchestrator.events import bus
from backend.orchestrator.metrics import parse_diff_numstat, parse_usage
from backend.orchestrator.prompt import MAX_QUESTION_OPTIONS
from backend.orchestrator.sbx import (
    AGENT_LOG_PATH,
    sbx_agent_exited,
    sbx_exec_capture,
    sbx_read_status,
    sbx_read_status_and_tail,
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
    from collections.abc import Iterator

    from backend.orchestrator.config import Config, Feature

TERMINAL_STATES = ("done", "stuck", "awaiting_input", "crashed")

# Container-level failure, as reported by `sbx ls` -- distinct from the
# agent's own status file, which may still hold a usable terminal state.
CONTAINER_FAILURE_STATES = ("crashed", "error", "failed")

# How many consecutive "not in the listing" observations before a job is
# declared lost. A sandbox can briefly not appear right after creation, so a
# single miss is not proof it is gone.
_MISSING_OBSERVATIONS_BEFORE_LOST = 3


# The phases of a job attempt, each timed into its own column so a slow run
# shows where the time went. `launch_s` is deliberately not in
# `lifecycle._ATTEMPT_RESET`: launch is timed *before* the reset is written, and a
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
def timed_step(store: StateStore, sandbox_name: str, step: JobStep) -> Iterator[None]:
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


def utc_now() -> str:
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
    with timed_step(store, sandbox_name, "verify"):
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
    with timed_step(store, sandbox_name, "push"):
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


def await_and_finalize(
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
    with timed_step(store, sandbox_name, "agent"):
        final_status = poll_until_signal(config, store, sandbox_name, deadline)

    # poll_until_signal already wrote the status, and with it the category.
    store.upsert(
        sandbox_name,
        finished_at=utc_now(),
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
