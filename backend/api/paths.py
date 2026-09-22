"""Filesystem locations used across the API layer.

Split out from `settings` so it depends on nothing: `workspace` needs these
paths, and `settings` needs `workspace` to resolve the active config, which
would otherwise be a circular import.
"""

from __future__ import annotations

import os
from pathlib import Path

HOME_ENV = "AGENTSHOWDOWN_HOME"
JOBS_DB_ENV = "AGENTSHOWDOWN_JOBS_DB"
WORKSPACE_ROOT_ENV = "AGENTSHOWDOWN_WORKSPACE_ROOT"

DEFAULT_HOME = "~/.agentshowdown"

# Directory inside a target repo holding its agentshowdown files. A dotted
# directory rather than two loose files, so the repo gains one entry and the
# pair is obviously related.
CONFIG_DIRNAME = ".agentshowdown"


def package_root() -> Path:
    """Returns the agentshowdown checkout root.

    Used to recognize the bundled demo preset, which must never be written
    over: it is version-controlled documentation, not a workspace.
    """
    return Path(__file__).resolve().parents[2]


def home_dir() -> Path:
    """Returns the directory holding cross-workspace state."""
    return Path(os.environ.get(HOME_ENV, DEFAULT_HOME)).expanduser()


def registry_path() -> Path:
    """Returns the workspace registry file."""
    return home_dir() / "workspaces.json"


def clones_dir() -> Path:
    """Returns the directory cloned repositories are placed in."""
    return home_dir() / "clones"


def jobs_db_path() -> Path:
    """Returns the one jobs database shared by every workspace."""
    override = os.environ.get(JOBS_DB_ENV)
    return Path(override).expanduser() if override else home_dir() / "jobs.sqlite3"


def workspace_config_dir(repo_path: Path | str) -> Path:
    """Returns the `.agentshowdown` directory inside a target repo."""
    return Path(repo_path) / CONFIG_DIRNAME
