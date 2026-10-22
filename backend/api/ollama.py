"""Saved Ollama model suggestions for the UI's local-model option.

Only the explicit model update action runs `ollama list`. HTTP reads use
the saved catalog, even when no catalog has been discovered yet.
"""

from __future__ import annotations

import shutil

from backend.model_catalog import catalog


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
    """Returns saved suggestions. Discovery is exclusive to the Setup update button."""
    entry = catalog("ollama")
    return {
        "available": ollama_available(),
        "models": entry["models"],
        "model_source": entry["source"],
        "models_updated_at": entry["updated_at"],
    }
