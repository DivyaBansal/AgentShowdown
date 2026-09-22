"""Tests for backend.orchestrator.config."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from backend.orchestrator.config import (
    DEFAULT_MODELS,
    KNOWN_MODELS,
    AgentProfile,
    AgentSpec,
    Config,
    Feature,
    resolve_dangerously_skip_permissions,
    resolve_env,
    resolve_kit_paths,
    resolve_model,
    resolve_provider,
    uses_custom_model_endpoint,
    validate_model,
)
from backend.orchestrator.process import CommandError

BASE_CONFIG = """
repo_path: /tmp/repo
github:
  repo: owner/repo
  pat_secret_name: github
  base_branch: main
agent:
  sbx_agent: claude
  dangerously_skip_permissions: true
  model: claude-haiku-4-5
run:
{run_block}
"""


def _write(tmp_path: Path, name: str, content: str) -> str:
    path = tmp_path / name
    path.write_text(content)
    return str(path)


def test_config_load_new_style_status_file(tmp_path: Path) -> None:
    run_block = textwrap.indent(
        "status_file: .agent_status.json\npoll_interval_seconds: 15\ntimeout_minutes: 60\n"
        "max_concurrency: 5\n",
        "    ",
    )
    path = _write(tmp_path, "config.yaml", BASE_CONFIG.format(run_block=run_block))
    config = Config.load(path)
    assert config.status_file == ".agent_status.json"
    assert config.max_concurrency == 5


def test_config_load_falls_back_to_done_marker(tmp_path: Path) -> None:
    run_block = textwrap.indent(
        "done_marker: .sbx_done\npoll_interval_seconds: 15\ntimeout_minutes: 60\n",
        "    ",
    )
    path = _write(tmp_path, "config.yaml", BASE_CONFIG.format(run_block=run_block))
    config = Config.load(path)
    assert config.status_file == ".sbx_done"


def test_config_load_max_concurrency_defaults_to_three(tmp_path: Path) -> None:
    run_block = textwrap.indent(
        "status_file: .agent_status.json\npoll_interval_seconds: 15\ntimeout_minutes: 60\n",
        "    ",
    )
    path = _write(tmp_path, "config.yaml", BASE_CONFIG.format(run_block=run_block))
    config = Config.load(path)
    assert config.max_concurrency == 3


def test_config_load_status_file_defaults_when_neither_key_present(
    tmp_path: Path,
) -> None:
    run_block = textwrap.indent("poll_interval_seconds: 15\ntimeout_minutes: 60\n", "    ")
    path = _write(tmp_path, "config.yaml", BASE_CONFIG.format(run_block=run_block))
    config = Config.load(path)
    assert config.status_file == ".agent_status.json"


def test_feature_load_all_no_agents_key(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "features.yaml",
        """
features:
  - id: f1
    description: "  do the thing  "
    acceptance_criteria:
      - it works
""",
    )
    features = Feature.load_all(path)
    assert features["f1"].agents == []
    assert features["f1"].description == "do the thing"
    assert features["f1"].acceptance_criteria == ["it works"]


def test_feature_load_all_string_shorthand_agents(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "features.yaml",
        """
features:
  - id: f1
    description: "do the thing"
    agents:
      - claude
      - codex
""",
    )
    features = Feature.load_all(path)
    assert features["f1"].agents == [
        AgentSpec(agent_id="claude"),
        AgentSpec(agent_id="codex"),
    ]


def test_feature_load_all_dict_agents_with_run_label(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "features.yaml",
        """
features:
  - id: f1
    description: "do the thing"
    agents:
      - agent_id: claude
      - agent_id: claude
        run_label: b
""",
    )
    features = Feature.load_all(path)
    assert features["f1"].agents == [
        AgentSpec(agent_id="claude"),
        AgentSpec(agent_id="claude", run_label="b"),
    ]


def test_feature_load_all_dict_agents_with_model_and_command(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "features.yaml",
        """
features:
  - id: f1
    description: "do the thing"
    agents:
      - agent_id: codex
        model: gpt-5-codex
      - agent_id: shell
        run_label: custom
        command: "./run-my-agent.sh"
""",
    )
    features = Feature.load_all(path)
    assert features["f1"].agents == [
        AgentSpec(agent_id="codex", model="gpt-5-codex"),
        AgentSpec(agent_id="shell", run_label="custom", command="./run-my-agent.sh"),
    ]


def test_feature_load_all_missing_acceptance_criteria_defaults_empty(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path,
        "features.yaml",
        """
features:
  - id: f1
    description: "do the thing"
""",
    )
    features = Feature.load_all(path)
    assert features["f1"].acceptance_criteria == []


def test_feature_load_all_string_shorthand_agent_kit_defaults_empty(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path,
        "features.yaml",
        """
features:
  - id: f1
    description: "do the thing"
    agents:
      - claude
""",
    )
    features = Feature.load_all(path)
    assert features["f1"].agents == [AgentSpec(agent_id="claude")]
    assert features["f1"].agents[0].kit == []


def test_feature_load_all_dict_agents_with_skip_permissions_kit_and_provider(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path,
        "features.yaml",
        """
features:
  - id: f1
    description: "do the thing"
    agents:
      - agent_id: claude
        dangerously_skip_permissions: false
        kit: ["./kits/a", "./kits/b"]
      - agent_id: claude
        run_label: ollama
        provider: ollama
        model: "gemma3:e4b-it-q4_K_M"
""",
    )
    features = Feature.load_all(path)
    assert features["f1"].agents == [
        AgentSpec(
            agent_id="claude",
            dangerously_skip_permissions=False,
            kit=["./kits/a", "./kits/b"],
        ),
        AgentSpec(
            agent_id="claude",
            run_label="ollama",
            provider="ollama",
            model="gemma3:e4b-it-q4_K_M",
        ),
    ]


# --- Config.load agent_profiles ----------------------------------------------


def test_config_load_agents_section_parses_agent_profiles(tmp_path: Path) -> None:
    run_block = textwrap.indent(
        "status_file: .agent_status.json\npoll_interval_seconds: 15\ntimeout_minutes: 60\n",
        "    ",
    )
    path = _write(
        tmp_path,
        "config.yaml",
        BASE_CONFIG.format(run_block=run_block)
        + """
agents:
  claude:
    dangerously_skip_permissions: false
    kit: ["./kits/claude-extra"]
  opencode:
    model: "openrouter/anthropic/claude-sonnet-4"
""",
    )
    config = Config.load(path)
    assert config.agent_profiles == {
        "claude": AgentProfile(dangerously_skip_permissions=False, kit=["./kits/claude-extra"]),
        "opencode": AgentProfile(model="openrouter/anthropic/claude-sonnet-4"),
    }


def test_config_load_agent_profiles_defaults_to_empty_dict(tmp_path: Path) -> None:
    run_block = textwrap.indent(
        "status_file: .agent_status.json\npoll_interval_seconds: 15\ntimeout_minutes: 60\n",
        "    ",
    )
    path = _write(tmp_path, "config.yaml", BASE_CONFIG.format(run_block=run_block))
    config = Config.load(path)
    assert config.agent_profiles == {}


def test_config_load_provider_defaults_to_none(tmp_path: Path) -> None:
    run_block = textwrap.indent(
        "status_file: .agent_status.json\npoll_interval_seconds: 15\ntimeout_minutes: 60\n",
        "    ",
    )
    path = _write(tmp_path, "config.yaml", BASE_CONFIG.format(run_block=run_block))
    config = Config.load(path)
    assert config.provider is None


# --- resolve_model -------------------------------------------------------------


def _config(**overrides) -> Config:
    defaults = {
        "repo_path": "/tmp/repo",
        "github_repo": "owner/repo",
        "github_pat_secret_name": "github",
        "base_branch": "main",
        "sbx_agent": "claude",
        "model": "config-model",
        "dangerously_skip_permissions": False,
        "max_turns": None,
        "status_file": ".agent_status.json",
        "poll_interval_seconds": 15,
        "timeout_minutes": 60,
        "max_concurrency": 3,
        "test_command": "",
        "lint_command": "",
        "remove_sandbox_on_success": True,
        "remove_sandbox_on_failure": False,
        "state_db_path": ":memory:",
        "provider": None,
        "agent_profiles": {},
    }
    defaults.update(overrides)
    return Config(**defaults)


def test_resolve_model_spec_wins_over_profile_and_config() -> None:
    spec = AgentSpec(agent_id="claude", model="spec-model")
    profile = AgentProfile(model="profile-model")
    assert resolve_model(spec, profile, _config()) == "spec-model"


def test_resolve_model_profile_wins_over_config_when_spec_unset() -> None:
    spec = AgentSpec(agent_id="claude")
    profile = AgentProfile(model="profile-model")
    assert resolve_model(spec, profile, _config()) == "profile-model"


def test_resolve_model_falls_back_to_default_models_when_no_overrides() -> None:
    spec = AgentSpec(agent_id="claude")
    assert resolve_model(spec, None, _config()) == DEFAULT_MODELS["claude"]


def test_resolve_model_falls_back_to_config_for_agent_with_no_default() -> None:
    spec = AgentSpec(agent_id="cursor")
    assert resolve_model(spec, None, _config()) == "config-model"


def test_resolve_model_profile_wins_over_default_models() -> None:
    spec = AgentSpec(agent_id="claude")
    profile = AgentProfile(model="profile-model")
    assert resolve_model(spec, profile, _config()) == "profile-model"


# --- KNOWN_MODELS / DEFAULT_MODELS ----------------------------------------------


def test_default_models_are_all_known_models() -> None:
    for agent_id, model in DEFAULT_MODELS.items():
        assert model in KNOWN_MODELS[agent_id]


# --- validate_model --------------------------------------------------------------


def test_validate_model_accepts_known_model() -> None:
    validate_model("claude", "claude-haiku-4-5")  # should not raise


def test_validate_model_rejects_unknown_model_for_known_agent() -> None:
    with pytest.raises(CommandError, match="Unknown model 'nope'"):
        validate_model("claude", "nope")


def test_validate_model_skips_agent_with_no_known_models_entry() -> None:
    validate_model("cursor", "literally-anything")  # should not raise


# --- resolve_provider ----------------------------------------------------------


def test_resolve_provider_spec_wins_over_profile_and_config() -> None:
    spec = AgentSpec(agent_id="claude", provider="spec-provider")
    profile = AgentProfile(provider="profile-provider")
    assert resolve_provider(spec, profile, _config(provider="config-provider")) == "spec-provider"


def test_resolve_provider_defaults_to_none_when_nothing_sets_it() -> None:
    spec = AgentSpec(agent_id="claude")
    assert resolve_provider(spec, None, _config()) is None


def test_resolve_provider_profile_wins_over_config() -> None:
    spec = AgentSpec(agent_id="claude")
    profile = AgentProfile(provider="ollama")
    assert resolve_provider(spec, profile, _config(provider="config-provider")) == "ollama"


# --- resolve_dangerously_skip_permissions --------------------------------------


def test_resolve_dangerously_skip_permissions_spec_overrides_profile_true_to_false() -> None:
    spec = AgentSpec(agent_id="claude", dangerously_skip_permissions=False)
    profile = AgentProfile(dangerously_skip_permissions=True)
    result = resolve_dangerously_skip_permissions(
        spec, profile, _config(dangerously_skip_permissions=True)
    )
    assert result is False


def test_resolve_dangerously_skip_permissions_profile_wins_over_config() -> None:
    spec = AgentSpec(agent_id="claude")
    profile = AgentProfile(dangerously_skip_permissions=True)
    result = resolve_dangerously_skip_permissions(
        spec, profile, _config(dangerously_skip_permissions=False)
    )
    assert result is True


def test_resolve_dangerously_skip_permissions_falls_back_to_config_when_profile_none() -> None:
    spec = AgentSpec(agent_id="claude")
    result = resolve_dangerously_skip_permissions(
        spec, None, _config(dangerously_skip_permissions=True)
    )
    assert result is True


# --- resolve_kit_paths -----------------------------------------------------------


def test_resolve_kit_paths_concatenates_profile_then_spec() -> None:
    spec = AgentSpec(agent_id="claude", kit=["./kits/s"])
    profile = AgentProfile(kit=["./kits/p"])
    assert resolve_kit_paths(spec, profile) == ["./kits/p", "./kits/s"]


def test_resolve_kit_paths_with_no_profile_returns_spec_kit_only() -> None:
    spec = AgentSpec(agent_id="claude", kit=["./kits/s"])
    assert resolve_kit_paths(spec, None) == ["./kits/s"]


def test_resolve_kit_paths_with_neither_returns_empty_list() -> None:
    spec = AgentSpec(agent_id="claude")
    assert resolve_kit_paths(spec, None) == []


# --- resolve_env ---------------------------------------------------------------


def test_resolve_env_merges_profile_and_spec_with_spec_winning() -> None:
    profile = AgentProfile(env={"ANTHROPIC_BASE_URL": "http://old", "SHARED": "p"})
    spec = AgentSpec(agent_id="claude", env={"ANTHROPIC_BASE_URL": "http://new", "EXTRA": "s"})
    assert resolve_env(spec, profile) == {
        "ANTHROPIC_BASE_URL": "http://new",
        "SHARED": "p",
        "EXTRA": "s",
    }


def test_resolve_env_with_no_profile_returns_spec_env_only() -> None:
    spec = AgentSpec(agent_id="claude", env={"FOO": "bar"})
    assert resolve_env(spec, None) == {"FOO": "bar"}


def test_resolve_env_with_neither_returns_empty_dict() -> None:
    assert resolve_env(AgentSpec(agent_id="claude"), None) == {}


# --- uses_custom_model_endpoint ----------------------------------------------


def test_uses_custom_model_endpoint_true_for_anthropic_base_url() -> None:
    assert uses_custom_model_endpoint({"ANTHROPIC_BASE_URL": "http://host.docker.internal:11434"})


def test_uses_custom_model_endpoint_false_for_unrelated_env() -> None:
    assert not uses_custom_model_endpoint({"ANTHROPIC_AUTH_TOKEN": "ollama"})


def test_uses_custom_model_endpoint_false_for_empty_env() -> None:
    assert not uses_custom_model_endpoint({})


# --- env parsing in loaders --------------------------------------------------


def test_feature_load_all_parses_agent_env_mapping(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "features.yaml",
        """
features:
  - id: f1
    description: "do the thing"
    agents:
      - agent_id: claude
        model: "qwen2.5-coder:32b"
        env:
          ANTHROPIC_BASE_URL: http://host.docker.internal:11434
          ANTHROPIC_AUTH_TOKEN: ollama
""",
    )
    features = Feature.load_all(path)
    assert features["f1"].agents == [
        AgentSpec(
            agent_id="claude",
            model="qwen2.5-coder:32b",
            env={
                "ANTHROPIC_BASE_URL": "http://host.docker.internal:11434",
                "ANTHROPIC_AUTH_TOKEN": "ollama",
            },
        )
    ]


def test_feature_load_all_stringifies_non_string_env_values(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "features.yaml",
        """
features:
  - id: f1
    description: "do the thing"
    agents:
      - agent_id: claude
        env:
          PORT: 11434
""",
    )
    features = Feature.load_all(path)
    assert features["f1"].agents[0].env == {"PORT": "11434"}


def test_feature_load_all_rejects_non_mapping_env(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "features.yaml",
        """
features:
  - id: f1
    description: "do the thing"
    agents:
      - agent_id: claude
        env:
          - ANTHROPIC_BASE_URL=http://x
""",
    )
    with pytest.raises(CommandError, match="`env` must be a mapping"):
        Feature.load_all(path)


def test_feature_load_all_env_defaults_empty_when_omitted(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "features.yaml",
        """
features:
  - id: f1
    description: "do the thing"
    agents:
      - agent_id: claude
""",
    )
    assert Feature.load_all(path)["f1"].agents[0].env == {}


def test_config_load_parses_agent_profile_env(tmp_path: Path) -> None:
    run_block = textwrap.indent(
        "status_file: .agent_status.json\npoll_interval_seconds: 15\ntimeout_minutes: 60\n",
        "    ",
    )
    path = _write(
        tmp_path,
        "config.yaml",
        BASE_CONFIG.format(run_block=run_block)
        + """
agents:
  claude:
    env:
      ANTHROPIC_BASE_URL: http://host.docker.internal:11434
""",
    )
    config = Config.load(path)
    assert config.agent_profiles["claude"].env == {
        "ANTHROPIC_BASE_URL": "http://host.docker.internal:11434"
    }
