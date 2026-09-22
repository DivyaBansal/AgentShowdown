"""Fixtures shared by every test.

The autouse fixture here is a safety net rather than a convenience: the app
keeps its workspace registry and shared jobs database under
`AGENTSHOWDOWN_HOME` (default `~/.agentshowdown`). Without redirecting that
per test, running the suite would write into the developer's real home
directory and tests would see each other's leftovers, making failures depend
on execution order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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
