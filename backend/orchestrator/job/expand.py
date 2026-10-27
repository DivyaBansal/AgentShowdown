"""Turning requested features into named (Feature, AgentSpec) jobs."""

from __future__ import annotations

import dataclasses
import re

from backend.logging import log
from backend.orchestrator.config import (
    AgentSpec,
    Config,
    Feature,
    host_repo_path,
    resolve_env,
    resolve_model,
    spec_from_json,
    uses_custom_model_endpoint,
    validate_model,
)
from backend.orchestrator.process import CommandError
from backend.orchestrator.sbx import AGENT_CLI_BUILDERS
from backend.orchestrator.state import StateStore

# sbx sandbox names allow only letters, numbers, hyphens and periods.
_INVALID_SANDBOX_NAME_CHARS = re.compile(r"[^A-Za-z0-9.-]")
# Model names like "qwen3.5:122b" also end up in a git branch name, where
# ":" and ".." are invalid, so the label is reduced to a safe subset.
_INVALID_LABEL_CHARS = re.compile(r"[^A-Za-z0-9.-]+|\.{2,}")


def sandbox_name_for(feature_id: str, spec: AgentSpec) -> str:
    """Derives the sbx sandbox name for a (feature, agent) job.

    Characters sbx rejects (e.g. the underscores in a run_label like
    "claude_qwen_122b") become hyphens.
    """
    base = f"arena-{feature_id}-{spec.agent_id}"
    if spec.run_label:
        base += f"-{spec.run_label}"
    return _INVALID_SANDBOX_NAME_CHARS.sub("-", base)[:60]


def with_default_run_label(config: Config, spec: AgentSpec) -> AgentSpec:
    """Labels an unlabelled spec with its resolved model.

    So the sandbox, branch and job row say which model ran, and running the
    same agent with another model is a new job rather than a collision with
    (or a skip of) the earlier one. An explicit run_label is kept as is, and
    `command` agents get none: they never receive --model.

    Args:
        config: Orchestrator config (supplies the model fallbacks).
        spec: The agent entry as requested.

    Returns:
        `spec`, with run_label set to a name-safe form of the model when it
        had none.
    """
    if spec.run_label or spec.command:
        return spec
    model = resolve_model(spec, config.agent_profiles.get(spec.agent_id), config)
    label = _INVALID_LABEL_CHARS.sub("-", model).strip(".-")
    return dataclasses.replace(spec, run_label=label or None)


def branch_for(feature_id: str, spec: AgentSpec) -> str:
    """Derives the git branch name for a (feature, agent) job."""
    branch = f"agent/{feature_id}/{spec.agent_id}"
    if spec.run_label:
        branch += f"-{spec.run_label}"
    return branch


def expand_jobs(
    config: Config, features: dict[str, Feature], feature_ids: list[str] | None
) -> list[tuple[Feature, AgentSpec]]:
    """Flattens requested features into (Feature, AgentSpec) job pairs.

    Args:
        config: Orchestrator config (supplies the default agent).
        features: All loaded features, keyed by id.
        feature_ids: Which feature ids to include, or None for all of them.

    Returns:
        One (Feature, AgentSpec) pair per job. A feature with no `agents`
        entry expands to a single job using config.sbx_agent. Unlabelled
        specs come back labelled with their model (with_default_run_label).

    Raises:
        CommandError: If an unknown feature id or unsupported agent is
            requested, or if two agent entries for one feature would
            collide on sandbox name.
    """
    if feature_ids is None:
        selected = list(features.values())
    else:
        missing = [fid for fid in feature_ids if fid not in features]
        if missing:
            raise CommandError(
                f"Unknown feature id(s): {', '.join(missing)}. Available: {', '.join(features)}"
            )
        selected = [features[fid] for fid in feature_ids]

    jobs: list[tuple[Feature, AgentSpec]] = []
    seen_names: set[str] = set()
    for feature in selected:
        specs = feature.agents or [AgentSpec(agent_id=config.sbx_agent)]
        for requested in specs:
            spec = with_default_run_label(config, requested)
            if not spec.command and spec.agent_id not in AGENT_CLI_BUILDERS:
                raise CommandError(
                    f"Feature '{feature.id}': no CLI profile wired up for "
                    f"agent_id '{spec.agent_id}' yet (supported: "
                    f"{', '.join(AGENT_CLI_BUILDERS)}). Set `command:` on "
                    f"its entry in features.yaml to run it via a custom "
                    f"shell command instead (required for agent_id 'shell')."
                )
            if not spec.command:
                # command-based agents never receive --model (see
                # build_agent_invocation), so there's nothing to validate.
                profile = config.agent_profiles.get(spec.agent_id)
                # A custom model endpoint (Ollama, a gateway) serves its own
                # model names, which KNOWN_MODELS can't know -- skip the check.
                if not uses_custom_model_endpoint(resolve_env(spec, profile)):
                    try:
                        validate_model(spec.agent_id, resolve_model(spec, profile, config))
                    except CommandError as e:
                        raise CommandError(f"Feature '{feature.id}': {e}") from e
            name = sandbox_name_for(feature.id, spec)
            if name in seen_names:
                raise CommandError(
                    f"Duplicate sandbox name '{name}' for feature '{feature.id}' -- "
                    f"give one of its agent entries a distinct run_label."
                )
            seen_names.add(name)
            jobs.append((feature, spec))
    return jobs


def find_spec_for_sandbox(config: Config, feature: Feature, sandbox_name: str) -> AgentSpec:
    """Reconstructs the AgentSpec a sandbox_name maps to.

    Used on resume, where the state DB only records agent_id (a plain
    string) but relaunching needs the full spec (model/command overrides
    too).

    Args:
        config: Orchestrator config (supplies the default agent).
        feature: The feature this sandbox belongs to.
        sandbox_name: Sandbox to find the originating spec for.

    Returns:
        The matching AgentSpec.

    Raises:
        CommandError: If no agent entry for this feature maps to
            sandbox_name (e.g. features.yaml was edited since the job
            was launched).
    """
    specs = feature.agents or [AgentSpec(agent_id=config.sbx_agent)]
    for requested in specs:
        # Unlabelled names predate model-derived default labels; both resolve.
        for spec in (with_default_run_label(config, requested), requested):
            if sandbox_name_for(feature.id, spec) == sandbox_name:
                return spec
    raise CommandError(
        f"No agent entry in feature '{feature.id}' maps to sandbox "
        f"'{sandbox_name}' -- was features.yaml edited since this job was "
        f"launched?"
    )


def spec_for_job(
    config: Config, store: StateStore, feature: Feature, sandbox_name: str
) -> AgentSpec:
    """Returns the AgentSpec a job was actually launched with.

    A run can carry agent settings that were never saved to features.yaml
    (the run launcher sends its own copy of each agent), so rebuilding the
    spec from the features file would silently drop them -- e.g. the env
    that points claude at a local Ollama. Prefer the spec `run_job`
    recorded, and fall back to the features file only for jobs recorded
    before specs were stored.

    Args:
        config: Orchestrator config.
        store: The state store holding the job.
        feature: The feature this sandbox belongs to.
        sandbox_name: The job's sandbox.

    Returns:
        The recorded AgentSpec, else the one from find_spec_for_sandbox.

    Raises:
        CommandError: If nothing is recorded and no features.yaml entry
            maps to sandbox_name.
    """
    raw = store.get_spec(sandbox_name)
    spec = spec_from_json(raw) if raw is not None else None
    if spec is not None:
        return spec
    if raw is not None:
        log.warning("job_spec_unreadable", sandbox_name=sandbox_name)
    return find_spec_for_sandbox(config, feature, sandbox_name)


def record_queued(
    config: Config, jobs: list[tuple[Feature, AgentSpec]], run_id: str | None
) -> None:
    """Gives every job a row up front, so waiting jobs are visible.

    Without this a job has no row until a worker picks it up, so five jobs
    at max_concurrency 3 show as three on the board and the rest appear
    from nowhere later.

    A job that already has a row keeps its status: `run_job` decides what
    to do from it -- skipping work that already succeeded, relaunching what
    failed -- and overwriting that with "queued" would lose that history.
    Such a row still gets `run_id`, so it belongs to this run either way.

    Args:
        config: Orchestrator config.
        jobs: (Feature, AgentSpec) pairs, e.g. from expand_jobs.
        run_id: The run these jobs belong to, if any (the CLI has none).
    """
    with StateStore(config.state_db_path) as store:
        for feature, spec in jobs:
            sandbox_name = sandbox_name_for(feature.id, spec)
            if store.get(sandbox_name) is not None:
                store.upsert(sandbox_name, run_id=run_id)
                continue
            store.upsert(
                sandbox_name,
                feature_id=feature.id,
                agent_id=spec.agent_id,
                branch=branch_for(feature.id, spec),
                status="queued",
                run_id=run_id,
                model=resolve_model(spec, config.agent_profiles.get(spec.agent_id), config),
                run_label=spec.run_label,
                repo=host_repo_path(config),
            )
