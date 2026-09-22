"""Cloning a GitHub repository into a local workspace.

Runs on a background thread rather than in the request. A real clone takes
minutes, and a synchronous handler would pin one of FastAPI's threadpool
workers for the duration while the browser waits on a request that may time
out with no way to recover the result. `runner.py` rejects that same shape
for runs, and `stream.py`'s handler is async for the same reason.

Progress is published on the existing event bus, so the SPA learns the
outcome over the SSE stream it already holds open -- including replay after
a reconnect. `GET /workspaces/clone/{id}` is the fallback for a browser that
missed the terminal event entirely.
"""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from backend.api.paths import clones_dir
from backend.api.workspace import inspect, parse_github_repo, register
from backend.logging import log
from backend.orchestrator.events import bus
from backend.orchestrator.process import RunOptions, run

# One at a time: clones are network- and disk-heavy, and a queue is easier to
# reason about than several competing for bandwidth.
_clone_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clone")

CloneState = Literal["running", "finished", "failed"]


@dataclass
class CloneJob:
    """One clone, tracked so a reconnecting browser can ask how it went."""

    clone_id: str
    url: str
    target_path: str
    state: CloneState = "running"
    detail: str = ""
    github_repo: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Renders as JSON for the API."""
        return {
            "clone_id": self.clone_id,
            "url": self.url,
            "target_path": self.target_path,
            "state": self.state,
            "detail": self.detail,
            "github_repo": self.github_repo,
        }


@dataclass
class _CloneRegistry:
    """In-memory record of clones this process started."""

    jobs: dict[str, CloneJob] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def put(self, job: CloneJob) -> None:
        with self.lock:
            self.jobs[job.clone_id] = job

    def get(self, clone_id: str) -> CloneJob | None:
        with self.lock:
            return self.jobs.get(clone_id)


_registry = _CloneRegistry()


def get_clone(clone_id: str) -> CloneJob | None:
    """Returns a tracked clone, or None if this process never started it."""
    return _registry.get(clone_id)


def _target_for(url: str, directory_name: str | None) -> Path:
    """Chooses a destination inside the clones directory.

    The destination is always computed here, never taken from the request,
    so no input can redirect a clone elsewhere on the filesystem.
    """
    if directory_name:
        base = directory_name
    else:
        repo = parse_github_repo(url)
        base = repo.replace("/", "-") if repo else "repo"

    root = clones_dir()
    candidate = root / base
    suffix = 2
    while candidate.exists() and any(candidate.iterdir()):
        candidate = root / f"{base}-{suffix}"
        suffix += 1

    resolved = candidate.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("clone destination escaped the clones directory")
    return candidate


def _do_clone(job: CloneJob) -> None:
    """Performs the clone and records the outcome.

    Separated from the pool so it can be tested directly with `run` stubbed.
    """
    dest = Path(job.target_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = run(
        [
            "git",
            # ext:: and file:: remote helpers can execute commands; neither is
            # ever needed to clone from a hosted forge.
            "-c",
            "protocol.ext.allow=never",
            "-c",
            "protocol.file.allow=never",
            # Full history: `git diff base...ref` needs a merge base, so a
            # shallow clone would silently zero every diff metric.
            "clone",
            "--no-tags",
            # Everything after this cannot be read as a flag.
            "--",
            job.url,
            str(dest),
        ],
        options=RunOptions(
            check=False,
            env={
                **os.environ,
                # Without these a private URL hangs the thread on a credential
                # prompt forever instead of failing.
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": "/bin/true",
            },
        ),
    )

    if result.returncode != 0:
        job.state = "failed"
        job.detail = (result.stderr or result.stdout or "git clone failed").strip()[-2000:]
        log.warning("repo_clone_failed", clone_id=job.clone_id, detail=job.detail)
        bus.publish("clone_failed", clone_id=job.clone_id, detail=job.detail)
        return

    info = inspect(dest)
    register(info, cloned=True, select=True)
    job.state = "finished"
    job.github_repo = info.github_repo
    job.detail = ""
    log.info("repo_clone_finished", clone_id=job.clone_id, target_path=job.target_path)
    bus.publish(
        "clone_finished",
        clone_id=job.clone_id,
        target_path=job.target_path,
        github_repo=info.github_repo,
    )
    bus.publish("workspace_changed", path=job.target_path)


def start_clone(url: str, directory_name: str | None = None) -> CloneJob:
    """Starts a clone on a background thread and returns immediately.

    Args:
        url: An already-validated clone URL.
        directory_name: Optional single path component for the destination.

    Returns:
        The tracked job, in state "running".
    """
    target = _target_for(url, directory_name)
    job = CloneJob(
        clone_id=f"clone-{uuid.uuid4().hex[:12]}",
        url=url,
        target_path=str(target),
    )
    _registry.put(job)
    log.info(
        "repo_clone_requested",
        clone_id=job.clone_id,
        github_repo=parse_github_repo(url),
        target_path=job.target_path,
    )
    bus.publish(
        "clone_started",
        clone_id=job.clone_id,
        url=url,
        target_path=job.target_path,
    )
    _clone_pool.submit(_do_clone, job)
    return job
