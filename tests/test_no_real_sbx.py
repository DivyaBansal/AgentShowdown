"""Tests for the suite-wide guard against running the real sbx CLI."""

from __future__ import annotations

import shutil
import subprocess
import sys

import pytest


def test_sbx_is_not_on_path_for_tests() -> None:
    assert shutil.which("sbx") is None
    assert shutil.which("/usr/bin/sbx") is None


def test_spawning_sbx_raises_like_a_machine_without_it() -> None:
    with pytest.raises(FileNotFoundError, match="stub the call"):
        subprocess.run(["/usr/bin/sbx", "ls"], capture_output=True, check=False)


def test_other_commands_still_run() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "print('ok')"], capture_output=True, text=True, check=True
    )
    assert result.stdout == "ok\n"
