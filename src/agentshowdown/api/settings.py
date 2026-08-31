"""Where the API finds its config and features files.

Both are overridable by environment variable so the app can be pointed at a
different arena without editing anything, and both default to the checked-in
langlearn demo preset so a fresh clone has something to show.
"""

from __future__ import annotations

import os
from pathlib import Path

from agentshowdown.orchestrator.config import Config, Feature

CONFIG_ENV = "AGENTSHOWDOWN_CONFIG"
FEATURES_ENV = "AGENTSHOWDOWN_FEATURES"

DEFAULT_CONFIG = "demo/langlearn/config.yaml"
DEFAULT_FEATURES = "demo/langlearn/features.yaml"


def config_path() -> Path:
    """Returns the active config.yaml path."""
    return Path(os.environ.get(CONFIG_ENV, DEFAULT_CONFIG))


def features_path() -> Path:
    """Returns the active features.yaml path."""
    return Path(os.environ.get(FEATURES_ENV, DEFAULT_FEATURES))


def load_config() -> Config:
    """Loads the active config.

    Read fresh on each call rather than cached, so edits made through the
    config endpoints take effect without a restart.
    """
    return Config.load(str(config_path()))


def load_features() -> dict[str, Feature]:
    """Loads the active feature list, keyed by id."""
    return Feature.load_all(str(features_path()))
