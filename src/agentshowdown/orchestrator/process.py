"""Generic subprocess wrapper shared by the sbx and vcs modules."""

from __future__ import annotations

import shlex
import subprocess

from agentshowdown.orchestrator.log import log


class CommandError(RuntimeError):
    pass


def run(
    cmd: list[str], check: bool = True, capture: bool = True
) -> subprocess.CompletedProcess:
    log(f"$ {' '.join(shlex.quote(c) for c in cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise CommandError(
            f"Command failed ({result.returncode}): {' '.join(cmd)}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result
