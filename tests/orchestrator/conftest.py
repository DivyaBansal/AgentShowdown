"""Shared fixtures for the orchestrator tests."""

from __future__ import annotations

import contextlib
import sqlite3
from typing import TYPE_CHECKING

import pytest

from agentshowdown.orchestrator.state import StateStore

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _close_state_stores(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Closes every StateStore a test opens.

    Tests construct stores directly and let them fall out of scope, which
    raises ResourceWarning per leaked connection. Left alone that is ~40
    lines of noise per run -- enough to hide a genuine leak from production
    code, which is the thing worth noticing.
    """
    opened: list[StateStore] = []
    real_init = StateStore.__init__

    def tracking_init(self: StateStore, db_path: str) -> None:
        real_init(self, db_path)
        opened.append(self)

    monkeypatch.setattr(StateStore, "__init__", tracking_init)
    yield
    for store in opened:
        with contextlib.suppress(sqlite3.Error):
            store.close()
