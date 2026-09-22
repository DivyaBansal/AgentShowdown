"""Where the API finds its config, features, and shared state.

Three sources are consulted for the config and features files, in order:

1. `AGENTSHOWDOWN_CONFIG` / `AGENTSHOWDOWN_FEATURES` -- an explicit override,
   which always wins so a deployment (or a test) can pin an exact arena.
2. The active workspace, whose files live at `<repo>/.agentshowdown/`. This
   is what the UI's repo picker sets.
3. The checked-in `demo/langlearn/` preset, so a fresh clone still has
   something to show before anything is configured.

The jobs database is deliberately *not* part of that resolution. Every
workspace shares one database (`AGENTSHOWDOWN_JOBS_DB`, else
`<home>/jobs.sqlite3`) so runs stay comparable across repos, and
`load_config` overrides whatever a workspace's own `state_db_path` says.
The CLI, which calls `Config.load` directly, keeps per-config behavior.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Literal

from backend.api.paths import (
    CONFIG_DIRNAME,
    home_dir,
    jobs_db_path,
    package_root,
    registry_path,
    workspace_config_dir,
)
from backend.api.workspace import active_workspace
from backend.orchestrator.config import Config, Feature

CONFIG_ENV = "AGENTSHOWDOWN_CONFIG"
FEATURES_ENV = "AGENTSHOWDOWN_FEATURES"

DEFAULT_CONFIG = "demo/langlearn/config.yaml"
DEFAULT_FEATURES = "demo/langlearn/features.yaml"

ConfigSource = Literal["env", "workspace", "demo"]

__all__ = [
    "CONFIG_DIRNAME",
    "CONFIG_ENV",
    "DEFAULT_CONFIG",
    "DEFAULT_FEATURES",
    "FEATURES_ENV",
    "ConfigSource",
    "config_path",
    "config_source",
    "features_path",
    "home_dir",
    "jobs_db_path",
    "load_config",
    "load_features",
    "package_root",
    "registry_path",
    "workspace_config_dir",
]


def _active_workspace_path() -> Path | None:
    """Returns the active workspace's repo path, or None."""
    entry = active_workspace()
    return Path(entry.path) if entry is not None else None


def config_source() -> ConfigSource:
    """Returns which of the three sources the active config comes from."""
    if os.environ.get(CONFIG_ENV):
        return "env"
    return "workspace" if _active_workspace_path() is not None else "demo"


def config_path() -> Path:
    """Returns the active config.yaml path."""
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override)
    workspace = _active_workspace_path()
    if workspace is not None:
        return workspace_config_dir(workspace) / "config.yaml"
    return Path(DEFAULT_CONFIG)


def features_path() -> Path:
    """Returns the active features.yaml path."""
    override = os.environ.get(FEATURES_ENV)
    if override:
        return Path(override)
    workspace = _active_workspace_path()
    if workspace is not None:
        return workspace_config_dir(workspace) / "features.yaml"
    return Path(DEFAULT_FEATURES)


def load_config() -> Config:
    """Loads the active config, pinned to the shared jobs database.

    Read fresh on each call rather than cached, so edits made through the
    config endpoints take effect without a restart.

    The `state_db_path` override happens here rather than at each call site
    so the ~9 downstream users need no change and a future one cannot
    forget it.
    """
    return dataclasses.replace(Config.load(str(config_path())), state_db_path=str(jobs_db_path()))


def load_features() -> dict[str, Feature]:
    """Loads the active feature list, keyed by id."""
    return Feature.load_all(str(features_path()))
