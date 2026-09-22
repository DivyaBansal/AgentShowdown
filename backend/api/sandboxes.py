"""Direct sandbox controls: ping, remove, and the host-wide wipe."""

from __future__ import annotations

import shlex
import time

from backend.logging import log
from backend.orchestrator.process import RunOptions, run
from backend.orchestrator.sbx import sbx_exec_capture, sbx_list, sbx_rm
from backend.orchestrator.state import StateStore

PING_MARKER = "agentshowdown-ping-ok"


def ping(sandbox_name: str, status_file: str) -> dict[str, object]:
    """Probes a sandbox and the agent inside it.

    Answers the question the CLI never could: "is the container up but the
    agent dead?" A sandbox that responds instantly while its agent process
    is gone is a job that will poll until it times out, and knowing that
    early is the difference between a minute of debugging and an hour.

    Args:
        sandbox_name: Sandbox to probe.
        status_file: Workspace-relative status file to check for.

    Returns:
        Reachability, round-trip latency, whether an agent process is still
        running, and whether the status file exists yet.
    """
    listed = sandbox_name in sbx_list()
    if not listed:
        return {
            "sandbox_name": sandbox_name,
            "listed": False,
            "reachable": False,
            "latency_ms": None,
            "agent_alive": None,
            "status_file_present": None,
        }

    started = time.monotonic()
    probe = sbx_exec_capture(
        sandbox_name,
        # One round trip for all three answers.
        f"echo {PING_MARKER}; "
        f"pgrep -f 'claude|codex|cursor-agent|opencode|copilot' >/dev/null "
        f"&& echo agent_alive; "
        f"test -f {shlex.quote(status_file)} && echo status_file",
        login_shell=False,
    )
    latency_ms = round((time.monotonic() - started) * 1000, 1)
    stdout = probe.stdout

    return {
        "sandbox_name": sandbox_name,
        "listed": True,
        "reachable": PING_MARKER in stdout,
        "latency_ms": latency_ms,
        "agent_alive": "agent_alive" in stdout,
        "status_file_present": "status_file" in stdout,
    }


def remove(sandbox_name: str, state_db_path: str) -> dict[str, object]:
    """Removes one sandbox and marks its job lost.

    Args:
        sandbox_name: Sandbox to remove.
        state_db_path: Where job state lives.

    Returns:
        What happened, for the API response.
    """
    sbx_rm(sandbox_name)
    with StateStore(state_db_path) as store:
        if store.get(sandbox_name) is not None:
            store.upsert(
                sandbox_name,
                status="lost",
                detail="sandbox removed from the UI",
            )
    log.warning("sandbox_removed", sandbox=sandbox_name)
    return {"removed": [sandbox_name]}


def kill_all(state_db_path: str) -> dict[str, object]:
    """Removes every sandbox on the host.

    This is `sbx rm --all --force`, and its blast radius is the whole
    machine: sandboxes this app never created go too, and any in-flight job
    whose branch has not been fetched yet loses that work. The API gates it
    behind a typed confirmation for exactly that reason.

    Args:
        state_db_path: Where job state lives.

    Returns:
        Which sandboxes were present, and how many jobs were marked lost.
    """
    doomed = sorted(sbx_list())
    log.warning("all_sandboxes_killed", count=len(doomed), sandboxes=doomed)
    run(["sbx", "rm", "--all", "--force"], options=RunOptions(check=False))

    marked = 0
    with StateStore(state_db_path) as store:
        for job in store.all_jobs():
            if job["status"] in ("queued", "running"):
                store.upsert(
                    job["sandbox_name"],
                    status="lost",
                    detail="sandbox removed by kill-all",
                )
                marked += 1
    return {"removed": doomed, "jobs_marked_lost": marked}
