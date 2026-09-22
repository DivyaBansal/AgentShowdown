"""Tests for the YAML writer.

The property that matters is the round trip: anything this module writes,
`Config.load` / `Feature.load_all` must read back as an equal object. A
writer that drifts from the loader corrupts a workspace on save.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
import yaml
from backend.orchestrator.config import (
    AgentProfile,
    AgentSpec,
    Config,
    Feature,
)
from backend.orchestrator.config_writer import (
    HEADER,
    dump_config,
    dump_features,
    feature_to_mapping,
    is_bundled_preset,
)


def _config(**overrides) -> Config:
    defaults = {
        "repo_path": "/home/me/code/app",
        "github_repo": "owner/app",
        "github_pat_secret_name": "github",
        "base_branch": "main",
        "sbx_agent": "claude",
        "model": "claude-haiku-4-5",
        "dangerously_skip_permissions": True,
        "max_turns": None,
        "status_file": ".agent_status.json",
        "poll_interval_seconds": 5,
        "timeout_minutes": 60,
        "max_concurrency": 3,
        "test_command": "pytest",
        "lint_command": "",
        "remove_sandbox_on_success": True,
        "remove_sandbox_on_failure": False,
        "state_db_path": "./state.sqlite3",
        "provider": None,
        "agent_profiles": {},
    }
    defaults.update(overrides)
    return Config(**defaults)


def test_config_survives_a_round_trip(tmp_path: Path) -> None:
    config = _config()
    path = tmp_path / ".agentshowdown" / "config.yaml"

    dump_config(config, path)

    assert Config.load(str(path)) == config


def test_config_round_trip_keeps_agent_profiles(tmp_path: Path) -> None:
    """The `agents:` map carries the env passthrough, so it must survive."""
    config = _config(
        agent_profiles={
            "claude": AgentProfile(
                model="claude-sonnet-5",
                kit=["./kits/auth"],
                env={"ANTHROPIC_BASE_URL": "http://host.docker.internal:11434"},
            )
        }
    )
    path = tmp_path / "config.yaml"

    dump_config(config, path)

    assert Config.load(str(path)) == config


def test_config_round_trip_preserves_non_default_scalars(tmp_path: Path) -> None:
    """Every field is written, so a non-default value cannot be dropped."""
    config = _config(
        max_turns=12,
        provider="ollama",
        verify_on="sandbox",
        telemetry=True,
        capture_usage=False,
        open_pr=True,
        liveness_interval_seconds=9,
        telemetry_interval_seconds=11,
    )
    path = tmp_path / "config.yaml"

    dump_config(config, path)

    assert Config.load(str(path)) == config


def test_features_survive_a_round_trip(tmp_path: Path) -> None:
    features = [
        Feature(
            id="minimal",
            description="one line",
            acceptance_criteria=[],
            agents=[AgentSpec(agent_id="claude")],
        ),
        Feature(
            id="maximal",
            description="line one\nline two\n",
            acceptance_criteria=["a", "b"],
            agents=[
                AgentSpec(
                    agent_id="claude",
                    run_label="a",
                    model="qwen2.5-coder:32b",
                    command="echo hi",
                    dangerously_skip_permissions=False,
                    kit=["./kit"],
                    provider="ollama",
                    env={"ANTHROPIC_BASE_URL": "http://x:11434"},
                )
            ],
        ),
    ]
    path = tmp_path / "features.yaml"

    dump_features(features, path)

    # Descriptions come back stripped: `Feature.load_all` normalizes them, so
    # that is the canonical stored form rather than a lossy write.
    expected = {f.id: dataclasses.replace(f, description=f.description.strip()) for f in features}
    assert Feature.load_all(str(path)) == expected


def test_an_all_default_agent_entry_stays_a_single_key(tmp_path: Path) -> None:
    """A feature's agents are overrides; nulls would bury the real setting."""
    mapping = feature_to_mapping(
        Feature(id="f", description="d", acceptance_criteria=[], agents=[AgentSpec("claude")])
    )

    assert mapping["agents"] == [{"agent_id": "claude"}]


def test_multi_line_description_is_written_as_a_block_scalar(tmp_path: Path) -> None:
    """Readability matters: these files stay hand-editable."""
    path = tmp_path / "features.yaml"
    dump_features(
        [Feature(id="f", description="line one\nline two\n", acceptance_criteria=[], agents=[])],
        path,
    )

    text = path.read_text()
    assert "description: |" in text


def test_written_files_carry_the_managed_header(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    dump_config(_config(), path)

    text = path.read_text()
    assert text.startswith(HEADER)
    # The header must not stop it being valid YAML.
    assert yaml.safe_load(text)["repo_path"] == "/home/me/code/app"


def test_a_failed_round_trip_leaves_the_original_file_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A writer/loader disagreement must not destroy existing config."""
    path = tmp_path / "config.yaml"
    dump_config(_config(), path)
    original = path.read_text()

    monkeypatch.setattr(
        "backend.orchestrator.config_writer.Config.load",
        lambda _p: _config(repo_path="/somewhere/else"),
    )

    with pytest.raises(ValueError, match="round trip"):
        dump_config(_config(), path)

    assert path.read_text() == original
    assert not (tmp_path / "config.yaml.tmp").exists()


def test_is_bundled_preset_flags_the_checked_in_demo(tmp_path: Path) -> None:
    root = tmp_path / "agentshowdown"
    (root / "demo" / "langlearn").mkdir(parents=True)
    (root / "demo" / "langlearn" / "config.yaml").touch()

    assert is_bundled_preset(root / "demo" / "langlearn" / "config.yaml", root)
    assert not is_bundled_preset(tmp_path / "elsewhere" / "config.yaml", root)


def test_description_whitespace_is_normalized_not_lost(tmp_path: Path) -> None:
    """The stripping is normalization -- the text itself must survive."""
    path = tmp_path / "features.yaml"
    dump_features(
        [
            Feature(
                id="f",
                description="\n  keep every word\n  across two lines\n\n",
                acceptance_criteria=[],
                agents=[],
            )
        ],
        path,
    )

    reloaded = Feature.load_all(str(path))["f"]

    assert "keep every word" in reloaded.description
    assert "across two lines" in reloaded.description
