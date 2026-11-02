"""Fixtures shared by every test.

The autouse fixture here is a safety net rather than a convenience: the app
keeps its workspace registry and shared jobs database under
`AGENTSHOWDOWN_HOME` (default `~/.agentshowdown`). Without redirecting that
per test, running the suite would write into the developer's real home
directory and tests would see each other's leftovers, making failures depend
on execution order.

The same goes for the `sbx` CLI: a developer machine has it installed and CI
does not, so a test that forgets to stub an sbx call passes locally (talking
to the real daemon) and fails in CI. `_no_real_sbx` makes every machine look
like CI.
"""

from __future__ import annotations

import errno
import shutil
import subprocess
from pathlib import PurePath
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Points AGENTSHOWDOWN_HOME at a per-test temp directory.

    Also clears the config/features overrides so a stray value in the
    developer's shell can't silently redirect a test to a real arena.
    """
    home = tmp_path / "agentshowdown-home"
    home.mkdir()
    monkeypatch.setenv("AGENTSHOWDOWN_HOME", str(home))
    monkeypatch.delenv("AGENTSHOWDOWN_CONFIG", raising=False)
    monkeypatch.delenv("AGENTSHOWDOWN_FEATURES", raising=False)


@pytest.fixture(autouse=True)
def _no_real_sbx(monkeypatch: pytest.MonkeyPatch) -> None:
    """Makes the sbx CLI unavailable to tests, as it is in CI.

    `shutil.which("sbx")` reports it missing, and spawning it raises the
    same FileNotFoundError CI sees, naming the stub the test is missing.
    Tests that stub sbx calls themselves are unaffected.
    """
    real_which = shutil.which

    def which(cmd: str, *args: Any, **kwargs: Any) -> str | None:
        return None if PurePath(cmd).name == "sbx" else real_which(cmd, *args, **kwargs)

    class GuardedPopen(subprocess.Popen):
        def __init__(self, args: Any, *rest: Any, **kwargs: Any) -> None:
            argv0 = args[0] if isinstance(args, (list, tuple)) and args else args
            if isinstance(argv0, (str, PurePath)) and PurePath(argv0).name == "sbx":
                raise FileNotFoundError(
                    errno.ENOENT, "tests must not run the real sbx CLI; stub the call", "sbx"
                )
            super().__init__(args, *rest, **kwargs)

    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(subprocess, "Popen", GuardedPopen)
