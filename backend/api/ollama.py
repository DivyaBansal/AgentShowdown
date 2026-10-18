"""Which models the host's Ollama serves, for the UI's local-model option.

Read through `ollama list` rather than Ollama's HTTP API: the CLI already
knows where the server is (it honours OLLAMA_HOST), and going through the
package's one subprocess helper keeps the call logged and time-bounded like
every other external command.

This is a convenience listing for suggestions only. A model missing from it
can still be typed by hand, and an unreachable Ollama just means no
suggestions -- never an error that takes the settings page down.
"""

from __future__ import annotations

import shutil

from backend.logging import log
from backend.orchestrator.process import CommandTimeout, RunOptions, run

# `ollama list` can stall when the server is wedged; past this, give up on
# suggestions rather than holding a threadpool worker.
LIST_TIMEOUT_SECONDS = 5


def ollama_available() -> bool:
    """Returns whether the ollama CLI is on PATH."""
    return shutil.which("ollama") is not None


def parse_model_table(stdout: str) -> list[str]:
    """Parses `ollama list` output into model names.

    Args:
        stdout: Raw command output, a whitespace-aligned table whose first
            column is NAME (e.g. `qwen3.5:122b  8b9d11d807c5  81 GB  ...`).

    Returns:
        Model names in the order listed. The header and blank lines are
        skipped.
    """
    names: list[str] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("NAME"):
            continue
        names.append(stripped.split()[0])
    return names


def list_ollama_models() -> dict[str, object]:
    """Lists the models pulled into the host's Ollama.

    Returns:
        `available` says whether Ollama could be queried at all; `models`
        holds the model names, empty when it could not.
    """
    if not ollama_available():
        return {"available": False, "models": []}
    try:
        result = run(
            ["ollama", "list"],
            options=RunOptions(check=False, timeout=LIST_TIMEOUT_SECONDS),
        )
    except CommandTimeout:
        log.warning("ollama_list_timed_out", timeout_seconds=LIST_TIMEOUT_SECONDS)
        return {"available": False, "models": []}
    if result.returncode != 0:
        log.warning("ollama_list_failed", detail=result.stderr.strip()[-500:])
        return {"available": False, "models": []}
    return {"available": True, "models": parse_model_table(result.stdout)}
