"""Environment checks that decide whether live runs are possible at all.

Launching an agent needs a working sbx daemon and -- if PRs are being
opened -- an authenticated `gh`. None of that exists in CI, on a fresh
laptop, or in a container, so the UI asks here first and offers the seeded
demo dataset instead of failing at launch time with a subprocess error.

Health is probed through `sbx daemon status`, never `docker info`. Two
reasons: this project's rule is that sandbox operations go through sbx (its
isolation, credential proxying and network policy all live there), and
`docker info` is simply the wrong question -- sbx talks to its own
`sandboxd` over a user-owned socket, so on a host where the user is not in
the `docker` group `docker info` fails with a permission error while sbx
works perfectly. Verified on this machine.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from agentshowdown.orchestrator.process import run

DEMO_REPO_ENV = "AGENTSHOWDOWN_DEMO_REPO"
DEFAULT_DEMO_REPO = "../langlearn"


@dataclass
class Check:
    """One environment probe."""

    name: str
    ok: bool
    detail: str


@dataclass
class Preflight:
    """The full environment picture."""

    live_runs_possible: bool
    checks: list[Check] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        """Renders as JSON for the API."""
        return {
            "live_runs_possible": self.live_runs_possible,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks],
        }


def _binary(name: str) -> Check:
    path = shutil.which(name)
    return Check(
        name=name,
        ok=path is not None,
        detail=path or f"{name} not found on PATH",
    )


def _sandbox_daemon() -> Check:
    """Checks sbx's own daemon rather than the Docker socket."""
    if shutil.which("sbx") is None:
        return Check("sandboxd", False, "sbx not found on PATH")
    result = run(["sbx", "daemon", "status"], check=False)
    ok = result.returncode == 0 and "running" in result.stdout.lower()
    return Check(
        "sandboxd",
        ok,
        result.stdout.strip().splitlines()[0]
        if ok
        else "sbx daemon not running (`sbx daemon start`)",
    )


def _gh_authenticated() -> Check:
    if shutil.which("gh") is None:
        return Check("gh", False, "gh not found on PATH")
    # `gh auth status` writes to stderr and exits non-zero when logged out.
    result = run(["gh", "auth", "status"], check=False)
    ok = result.returncode == 0
    return Check(
        "gh",
        ok,
        "authenticated" if ok else "not authenticated (`gh auth login`)",
    )


def demo_repo_path() -> Path:
    """Returns the repo the demo preset points at."""
    return Path(os.environ.get(DEMO_REPO_ENV, DEFAULT_DEMO_REPO)).expanduser()


def _demo_repo() -> Check:
    path = demo_repo_path()
    ok = (path / ".git").is_dir()
    return Check(
        "demo_repo",
        ok,
        str(path.resolve()) if ok else f"no git repo at {path} (set {DEMO_REPO_ENV})",
    )


def preflight() -> Preflight:
    """Probes everything a live run depends on.

    Returns:
        The results. `live_runs_possible` reflects only what is strictly
        required to launch an agent -- `gh` and the demo repo are reported
        but not required, since PR opening is off by default and a user may
        point the app at any repo.
    """
    sbx = _binary("sbx")
    daemon = _sandbox_daemon()
    checks = [sbx, daemon, _gh_authenticated(), _demo_repo()]
    return Preflight(live_runs_possible=sbx.ok and daemon.ok, checks=checks)
