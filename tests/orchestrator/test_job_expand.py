"""Tests for backend.orchestrator.job.expand."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from backend.orchestrator.config import AgentProfile, AgentSpec, Config, Feature
from backend.orchestrator.job import expand as expand_module
from backend.orchestrator.process import CommandError
from backend.orchestrator.state import StateStore


def _config(tmp_path: Path, **overrides) -> Config:
    defaults = {
        "repo_path": "/tmp/repo",
        "github_repo": "owner/repo",
        "github_pat_secret_name": "github",
        "base_branch": "main",
        "sbx_agent": "claude",
        "model": "claude-haiku-4-5",
        "dangerously_skip_permissions": True,
        "max_turns": None,
        "status_file": ".agent_status.json",
        "poll_interval_seconds": 0,
        "liveness_interval_seconds": 0,
        "timeout_minutes": 60,
        "max_concurrency": 3,
        "test_command": "",
        "lint_command": "",
        "remove_sandbox_on_success": True,
        "remove_sandbox_on_failure": False,
        "state_db_path": str(tmp_path / "state.sqlite3"),
        "provider": None,
        "agent_profiles": {},
    }
    defaults.update(overrides)
    return Config(**defaults)


def _feature(agents: list[AgentSpec] | None = None) -> Feature:
    return Feature(
        id="f1",
        description="do the thing",
        acceptance_criteria=["works"],
        agents=agents or [],
    )


def _labelled(spec: AgentSpec, label: str) -> AgentSpec:
    return dataclasses.replace(spec, run_label=label)


def _feature_with_id(feature_id: str, agents: list[AgentSpec]) -> Feature:
    return Feature(id=feature_id, description="d", acceptance_criteria=[], agents=agents)


def _job(store: StateStore, sandbox_name: str) -> dict:
    job = store.get(sandbox_name)
    assert job is not None
    return job


# --- sandbox_name_for / branch_for -----------------------------------------


def test_sandbox_name_for_without_run_label() -> None:
    assert expand_module.sandbox_name_for("f1", AgentSpec(agent_id="claude")) == "arena-f1-claude"


def test_sandbox_name_for_with_run_label() -> None:
    assert (
        expand_module.sandbox_name_for("f1", AgentSpec(agent_id="claude", run_label="b"))
        == "arena-f1-claude-b"
    )


def test_sandbox_name_for_truncates_to_60_chars() -> None:
    long_id = "x" * 100
    name = expand_module.sandbox_name_for(long_id, AgentSpec(agent_id="claude"))
    assert len(name) == 60


def test_sandbox_name_for_replaces_chars_sbx_rejects() -> None:
    # sbx refuses names with underscores ("sandbox names cannot contain
    # underscores"), which run_labels like "claude_qwen_122b" contain.
    spec = AgentSpec(agent_id="claude", run_label="claude_qwen_122b")
    assert expand_module.sandbox_name_for("add-ui", spec) == "arena-add-ui-claude-claude-qwen-122b"


def test_sandbox_name_for_keeps_periods() -> None:
    spec = AgentSpec(agent_id="qwen3.5")
    assert expand_module.sandbox_name_for("f1", spec) == "arena-f1-qwen3.5"


def test_branch_for_without_run_label() -> None:
    assert expand_module.branch_for("f1", AgentSpec(agent_id="claude")) == "agent/f1/claude"


def test_branch_for_with_run_label() -> None:
    assert (
        expand_module.branch_for("f1", AgentSpec(agent_id="claude", run_label="b"))
        == "agent/f1/claude-b"
    )


# --- expand_jobs -------------------------------------------------------------


def test_expand_jobs_no_agents_key_defaults_to_config_agent(tmp_path: Path) -> None:
    config = _config(tmp_path)
    features = {"f1": _feature()}
    jobs = expand_module.expand_jobs(config, features, None)
    assert jobs == [(features["f1"], AgentSpec(agent_id="claude", run_label="claude-haiku-4-5"))]


def test_expand_jobs_multiple_agents(tmp_path: Path) -> None:
    config = _config(tmp_path)
    specs = [AgentSpec(agent_id="claude"), AgentSpec(agent_id="claude", run_label="b")]
    features = {"f1": _feature(agents=specs)}
    jobs = expand_module.expand_jobs(config, features, None)
    assert [spec for _, spec in jobs] == [_labelled(specs[0], "claude-haiku-4-5"), specs[1]]


def test_expand_jobs_unknown_feature_id_raises(tmp_path: Path) -> None:
    config = _config(tmp_path)
    features = {"f1": _feature()}
    with pytest.raises(CommandError, match="Unknown feature"):
        expand_module.expand_jobs(config, features, ["nope"])


def test_expand_jobs_duplicate_sandbox_name_raises(tmp_path: Path) -> None:
    config = _config(tmp_path)
    specs = [AgentSpec(agent_id="claude"), AgentSpec(agent_id="claude")]
    features = {"f1": _feature(agents=specs)}
    with pytest.raises(CommandError, match="Duplicate sandbox name"):
        expand_module.expand_jobs(config, features, None)


def test_expand_jobs_unsupported_agent_without_command_raises(tmp_path: Path) -> None:
    config = _config(tmp_path)
    features = {"f1": _feature(agents=[AgentSpec(agent_id="shell")])}
    with pytest.raises(CommandError, match="no CLI profile wired up"):
        expand_module.expand_jobs(config, features, None)


def test_expand_jobs_unsupported_agent_with_command_is_allowed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    spec = AgentSpec(agent_id="shell", command="./run-my-agent.sh")
    features = {"f1": _feature(agents=[spec])}
    jobs = expand_module.expand_jobs(config, features, None)
    assert jobs == [(features["f1"], spec)]


def test_expand_jobs_accepts_copilot_agent_id(tmp_path: Path) -> None:
    config = _config(tmp_path)
    features = {"f1": _feature(agents=[AgentSpec(agent_id="copilot")])}
    jobs = expand_module.expand_jobs(config, features, None)
    # No DEFAULT_MODELS entry for copilot, so config.model names it.
    assert jobs == [(features["f1"], AgentSpec(agent_id="copilot", run_label="claude-haiku-4-5"))]


def test_expand_jobs_allows_manual_model(tmp_path: Path) -> None:
    config = _config(tmp_path)
    spec = AgentSpec(agent_id="claude", model="not-a-real-model")
    features = {"f1": _feature(agents=[spec])}
    assert expand_module.expand_jobs(config, features, None) == [
        (features["f1"], _labelled(spec, "not-a-real-model"))
    ]


def test_expand_jobs_unlisted_model_warns(tmp_path: Path) -> None:
    config = _config(tmp_path)
    spec = AgentSpec(agent_id="claude", model="not-a-real-model")
    features = {"f1": _feature(agents=[spec])}
    assert expand_module.expand_jobs(config, features, None) == [
        (features["f1"], _labelled(spec, "not-a-real-model"))
    ]


def test_expand_jobs_unvalidated_agent_model_is_allowed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    spec = AgentSpec(agent_id="cursor", model="literally-anything")
    features = {"f1": _feature(agents=[spec])}
    jobs = expand_module.expand_jobs(config, features, None)
    assert jobs == [(features["f1"], _labelled(spec, "literally-anything"))]


def test_expand_jobs_command_override_skips_model_validation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    spec = AgentSpec(agent_id="claude", model="not-a-real-model", command="./run-my-agent.sh")
    features = {"f1": _feature(agents=[spec])}
    jobs = expand_module.expand_jobs(config, features, None)
    assert jobs == [(features["f1"], spec)]


def test_expand_jobs_custom_endpoint_env_skips_model_validation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    spec = AgentSpec(
        agent_id="claude",
        model="qwen2.5-coder:32b",
        env={"ANTHROPIC_BASE_URL": "http://host.docker.internal:11434"},
    )
    features = {"f1": _feature(agents=[spec])}
    jobs = expand_module.expand_jobs(config, features, None)
    assert jobs == [(features["f1"], _labelled(spec, "qwen2.5-coder-32b"))]


def test_expand_jobs_env_without_base_url_allows_manual_model(tmp_path: Path) -> None:
    config = _config(tmp_path)
    spec = AgentSpec(
        agent_id="claude", model="not-a-real-model", env={"ANTHROPIC_AUTH_TOKEN": "ollama"}
    )
    features = {"f1": _feature(agents=[spec])}
    assert expand_module.expand_jobs(config, features, None) == [
        (features["f1"], _labelled(spec, "not-a-real-model"))
    ]


def test_expand_jobs_custom_endpoint_from_agent_profile_skips_validation(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        agent_profiles={
            "claude": AgentProfile(env={"ANTHROPIC_BASE_URL": "http://host.docker.internal:11434"})
        },
    )
    spec = AgentSpec(agent_id="claude", model="qwen2.5-coder:32b")
    features = {"f1": _feature(agents=[spec])}
    jobs = expand_module.expand_jobs(config, features, None)
    assert jobs == [(features["f1"], _labelled(spec, "qwen2.5-coder-32b"))]


def test_expand_jobs_keeps_an_explicit_run_label(tmp_path: Path) -> None:
    spec = AgentSpec(agent_id="claude", model="claude-sonnet-4-5", run_label="b")
    jobs = expand_module.expand_jobs(_config(tmp_path), {"f1": _feature(agents=[spec])}, None)
    assert jobs[0][1] == spec


def test_expand_jobs_leaves_command_agents_unlabelled(tmp_path: Path) -> None:
    spec = AgentSpec(agent_id="shell", command="./run-my-agent.sh")
    jobs = expand_module.expand_jobs(_config(tmp_path), {"f1": _feature(agents=[spec])}, None)
    assert jobs[0][1].run_label is None


def test_expand_jobs_default_label_makes_names_and_branches_name_safe(tmp_path: Path) -> None:
    spec = AgentSpec(agent_id="claude", model="qwen3.5:122b")
    [(_, labelled)] = expand_module.expand_jobs(
        _config(tmp_path), {"add-ui": _feature_with_id("add-ui", [spec])}, None
    )
    assert expand_module.sandbox_name_for("add-ui", labelled) == "arena-add-ui-claude-qwen3.5-122b"
    assert expand_module.branch_for("add-ui", labelled) == "agent/add-ui/claude-qwen3.5-122b"


def test_expand_jobs_same_agent_with_two_models_does_not_collide(tmp_path: Path) -> None:
    # Before default labels both were "arena-f1-claude": rejected as a
    # duplicate, or skipped once one of them had succeeded.
    specs = [AgentSpec(agent_id="claude"), AgentSpec(agent_id="claude", model="qwen3.5:122b")]
    jobs = expand_module.expand_jobs(_config(tmp_path), {"f1": _feature(agents=specs)}, None)
    assert [expand_module.sandbox_name_for("f1", spec) for _, spec in jobs] == [
        "arena-f1-claude-claude-haiku-4-5",
        "arena-f1-claude-qwen3.5-122b",
    ]


def test_expand_jobs_selects_only_requested_features(tmp_path: Path) -> None:
    config = _config(tmp_path)
    features = {
        "f1": _feature(),
        "f2": Feature(id="f2", description="d2", acceptance_criteria=[], agents=[]),
    }
    jobs = expand_module.expand_jobs(config, features, ["f2"])
    assert [f.id for f, _ in jobs] == ["f2"]


# --- find_spec_for_sandbox ----------------------------------------------------


def test_find_spec_for_sandbox_matches_by_derived_name(tmp_path: Path) -> None:
    config = _config(tmp_path)
    specs = [AgentSpec(agent_id="claude"), AgentSpec(agent_id="claude", run_label="b")]
    feature = _feature(agents=specs)
    sandbox_name = expand_module.sandbox_name_for("f1", specs[1])
    assert expand_module.find_spec_for_sandbox(config, feature, sandbox_name) == specs[1]


def test_find_spec_for_sandbox_matches_a_default_labelled_name(tmp_path: Path) -> None:
    config = _config(tmp_path)
    spec = AgentSpec(agent_id="claude", model="qwen3.5:122b")
    found = expand_module.find_spec_for_sandbox(
        config, _feature(agents=[spec]), "arena-f1-claude-qwen3.5-122b"
    )
    assert found == _labelled(spec, "qwen3.5-122b")


def test_find_spec_for_sandbox_no_agents_key_uses_config_default(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    feature = _feature()
    sandbox_name = expand_module.sandbox_name_for("f1", AgentSpec(agent_id="claude"))
    spec = expand_module.find_spec_for_sandbox(config, feature, sandbox_name)
    assert spec == AgentSpec(agent_id="claude")


def test_find_spec_for_sandbox_no_match_raises(tmp_path: Path) -> None:
    config = _config(tmp_path)
    feature = _feature(agents=[AgentSpec(agent_id="claude")])
    with pytest.raises(CommandError, match="No agent entry"):
        expand_module.find_spec_for_sandbox(config, feature, "not-a-real-sandbox")


# --- spec_for_job ------------------------------------------------------------


def test_spec_for_job_falls_back_to_features_file_when_none_recorded(tmp_path: Path) -> None:
    config = _config(tmp_path)
    saved = AgentSpec(agent_id="claude", model="from-yaml")
    feature = _feature([saved])
    sandbox_name = expand_module.sandbox_name_for(feature.id, saved)

    with StateStore(config.state_db_path) as store:
        assert expand_module.spec_for_job(config, store, feature, sandbox_name) == saved


def test_spec_for_job_falls_back_when_recorded_spec_is_unreadable(tmp_path: Path) -> None:
    config = _config(tmp_path)
    saved = AgentSpec(agent_id="claude", model="from-yaml")
    feature = _feature([saved])
    sandbox_name = expand_module.sandbox_name_for(feature.id, saved)

    with StateStore(config.state_db_path) as store:
        store.set_spec(sandbox_name, '{"agent_id": "claude", "no_such_field": 1}')
        assert expand_module.spec_for_job(config, store, feature, sandbox_name) == saved


# --- record_queued -----------------------------------------------------------


def test_run_all_records_every_job_before_any_of_them_start(tmp_path: Path) -> None:
    """Jobs waiting for a worker must still be visible on the board.

    Without this, five jobs at max_concurrency 3 show as three rows and the
    rest appear from nowhere minutes later.
    """
    config = _config(tmp_path)
    jobs = [(_feature(), AgentSpec(agent_id="claude", run_label=str(i))) for i in range(5)]
    store = StateStore(config.state_db_path)

    expand_module.record_queued(config, jobs, run_id="run-1")

    rows = store.all_jobs()
    assert len(rows) == 5
    assert {row["status"] for row in rows} == {"queued"}
    assert {row["run_id"] for row in rows} == {"run-1"}
    # Enough detail to render a board row, not just a name.
    assert rows[0]["feature_id"] == "f1"
    assert rows[0]["agent_id"] == "claude"
    assert rows[0]["model"] == "claude-haiku-4-5"


def test_recording_queued_jobs_keeps_what_an_earlier_run_left(tmp_path: Path) -> None:
    """run_job reads the old status to decide whether to skip or relaunch,
    so overwriting a finished job with "queued" would lose that."""
    config = _config(tmp_path)
    spec = AgentSpec(agent_id="claude")
    feature = _feature()
    sandbox_name = expand_module.sandbox_name_for(feature.id, spec)
    store = StateStore(config.state_db_path)
    store.upsert(
        sandbox_name,
        feature_id=feature.id,
        agent_id="claude",
        branch="b",
        status="succeeded",
        pr_url="https://example.invalid/pr/1",
    )

    expand_module.record_queued(config, [(feature, spec)], run_id="run-2")

    job = _job(store, sandbox_name)
    assert job["status"] == "succeeded"
    assert job["pr_url"] == "https://example.invalid/pr/1"
    # It still belongs to the run that asked for it.
    assert job["run_id"] == "run-2"
