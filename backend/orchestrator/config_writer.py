"""Writes Config and Feature objects back out as YAML.

The exact inverse of `Config.load` and `Feature.load_all`, so a file this
module writes is one those functions can read. That round trip is asserted
before anything is committed to disk (see `_write_checked`) -- a writer that
silently disagrees with the loader would corrupt a user's workspace on save,
which is the failure worth spending a re-read to prevent.

Comments are not preserved. `yaml.safe_dump` has no concept of them, and
pulling in a round-tripping YAML library to keep them is not worth the
dependency, so a header says plainly that the file is UI-managed.
"""

from __future__ import annotations

import dataclasses
import os
from typing import TYPE_CHECKING, Any

import yaml

from backend.orchestrator.config import Config, Feature

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from pathlib import Path

    from backend.orchestrator.config import AgentProfile, AgentSpec

HEADER = (
    "# Managed by agentshowdown.\n"
    "#\n"
    "# This file is rewritten whenever it is saved from the web UI, which\n"
    "# does not preserve comments or key ordering beyond the layout below.\n"
    "# Keep hand-written notes somewhere else.\n"
)


class _BlockDumper(yaml.SafeDumper):
    """A dumper that renders multi-line strings as block scalars.

    Subclassed rather than configured globally: registering a representer on
    `yaml.SafeDumper` itself would change the output of every other
    `safe_dump` in the process.
    """


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    """Renders a multi-line string as `|` instead of one long quoted line."""
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_BlockDumper.add_representer(str, _represent_str)


def config_to_mapping(config: Config) -> dict[str, Any]:
    """Renders a Config as the mapping `Config.load` expects.

    Every field is written, including ones sitting at their default. A
    workspace's config is a contract: if a default changes in a later
    version, a saved workspace must keep behaving the way it did when it
    was saved.

    Args:
        config: The config to render.

    Returns:
        A plain mapping ready for `yaml.dump`.
    """
    mapping: dict[str, Any] = {
        "repo_path": config.repo_path,
        "github": {
            "repo": config.github_repo,
            "pat_secret_name": config.github_pat_secret_name,
            "base_branch": config.base_branch,
            "open_pr": config.open_pr,
        },
        "agent": {
            "sbx_agent": config.sbx_agent,
            "model": config.model,
            "dangerously_skip_permissions": config.dangerously_skip_permissions,
            "max_turns": config.max_turns,
            "provider": config.provider,
        },
        "run": {
            "status_file": config.status_file,
            "liveness_interval_seconds": config.liveness_interval_seconds,
            "poll_interval_seconds": config.poll_interval_seconds,
            "timeout_minutes": config.timeout_minutes,
            "max_concurrency": config.max_concurrency,
            "verify_on": config.verify_on,
            "test_command": config.test_command,
            "lint_command": config.lint_command,
            "telemetry": config.telemetry,
            "telemetry_interval_seconds": config.telemetry_interval_seconds,
            "capture_usage": config.capture_usage,
            "remove_sandbox_on_success": config.remove_sandbox_on_success,
            "remove_sandbox_on_failure": config.remove_sandbox_on_failure,
        },
        "state_db_path": config.state_db_path,
    }
    if config.agent_profiles:
        mapping["agents"] = {
            agent_id: _profile_to_mapping(profile)
            for agent_id, profile in config.agent_profiles.items()
        }
    return mapping


def _profile_to_mapping(profile: AgentProfile) -> dict[str, Any]:
    """Renders one `agents:` entry, omitting anything left at its default."""
    out: dict[str, Any] = {}
    if profile.model is not None:
        out["model"] = profile.model
    if profile.provider is not None:
        out["provider"] = profile.provider
    if profile.dangerously_skip_permissions is not None:
        out["dangerously_skip_permissions"] = profile.dangerously_skip_permissions
    if profile.kit:
        out["kit"] = list(profile.kit)
    if profile.env:
        out["env"] = dict(profile.env)
    return out


def _spec_to_mapping(spec: AgentSpec) -> dict[str, Any]:
    """Renders one feature agent entry, omitting anything at its default.

    Unlike config.yaml, a feature's `agents:` list is an *override* list.
    Emitting every null and empty collection would turn a two-line entry
    into eight and bury the one field that was actually set.
    """
    out: dict[str, Any] = {"agent_id": spec.agent_id}
    if spec.run_label is not None:
        out["run_label"] = spec.run_label
    if spec.model is not None:
        out["model"] = spec.model
    if spec.command is not None:
        out["command"] = spec.command
    if spec.dangerously_skip_permissions is not None:
        out["dangerously_skip_permissions"] = spec.dangerously_skip_permissions
    if spec.kit:
        out["kit"] = list(spec.kit)
    if spec.provider is not None:
        out["provider"] = spec.provider
    if spec.env:
        out["env"] = dict(spec.env)
    return out


def feature_to_mapping(feature: Feature) -> dict[str, Any]:
    """Renders a Feature as the mapping `Feature.load_all` expects.

    Args:
        feature: The feature to render.

    Returns:
        A plain mapping. `acceptance_criteria` is kept even when empty,
        since an empty list documents "none yet" rather than "unset".
    """
    return {
        "id": feature.id,
        # `Feature.load_all` strips descriptions, so write the stripped form:
        # otherwise a trailing newline could never survive a round trip and
        # every save would fail its own verification.
        "description": feature.description.strip(),
        "acceptance_criteria": list(feature.acceptance_criteria),
        "agents": [_spec_to_mapping(spec) for spec in feature.agents],
    }


def _dump(mapping: dict[str, Any]) -> str:
    """Serializes a mapping with the house layout."""
    body = yaml.dump(
        mapping,
        Dumper=_BlockDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=100,
    )
    return f"{HEADER}\n{body}"


def _write_checked(path: Path, text: str, verify: Callable[[Path], None]) -> None:
    """Writes `text` to `path` only if it reads back as the original object.

    Writes to a sibling temp file, re-parses it with the real loader,
    compares, and only then renames into place. A disagreement between this
    writer and the loader leaves the existing file untouched.

    Args:
        path: Destination file.
        text: Serialized YAML.
        verify: Callable taking the temp path and returning the reloaded
            object to compare against.

    Raises:
        ValueError: If the written file does not reload to an equal object.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        verify(tmp)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)


def dump_config(config: Config, path: Path) -> None:
    """Writes `config` to `path` as YAML.

    Args:
        config: The config to write.
        path: Destination file; parent directories are created.

    Raises:
        ValueError: If the result does not reload to an equal Config.
    """

    def verify(tmp: Path) -> None:
        reloaded = Config.load(str(tmp))
        # state_db_path is pinned by the API layer at load time, so compare
        # the written file against what it literally says rather than
        # against a value some caller may have overridden in memory.
        if reloaded != config:
            raise ValueError("config did not survive a write/read round trip; file not saved")

    _write_checked(path, _dump(config_to_mapping(config)), verify)


def dump_features(features: Iterable[Feature], path: Path) -> None:
    """Writes `features` to `path` as YAML.

    Args:
        features: Features to write, in order.
        path: Destination file; parent directories are created.

    Raises:
        ValueError: If the result does not reload to an equal feature list.
    """
    ordered: Sequence[Feature] = list(features)
    mapping = {"features": [feature_to_mapping(f) for f in ordered]}

    def verify(tmp: Path) -> None:
        reloaded = Feature.load_all(str(tmp))
        # Compare against the loader's canonical form, not the caller's raw
        # object -- `load_all` strips descriptions, and that normalization is
        # expected rather than a round-trip failure.
        expected = {
            f.id: dataclasses.replace(f, description=f.description.strip()) for f in ordered
        }
        if reloaded != expected:
            raise ValueError("features did not survive a write/read round trip; file not saved")

    _write_checked(path, _dump(mapping), verify)


def is_bundled_preset(path: Path, package_root: Path) -> bool:
    """Returns whether `path` is inside the repo's checked-in demo preset.

    The demo files are version-controlled documentation, not a workspace.
    Saving over them would leave a fresh clone with someone else's settings
    and a dirty git tree.

    Args:
        path: The file about to be written.
        package_root: The agentshowdown checkout root.

    Returns:
        True if the path lies under `<package_root>/demo`.
    """
    try:
        return path.resolve().is_relative_to((package_root / "demo").resolve())
    except OSError:
        return False


__all__ = [
    "HEADER",
    "config_to_mapping",
    "dump_config",
    "dump_features",
    "feature_to_mapping",
    "is_bundled_preset",
]
