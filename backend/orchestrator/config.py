"""Config / data model for the orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from backend.orchestrator.process import CommandError

# Known-valid --model values per agent_id, used by validate_model to catch
# typos/stale model names before any sandbox is created. An agent_id with no
# entry here (cursor, opencode, copilot) isn't validated at all -- same
# "best-effort, verify against the installed CLI" caveat as their
# AGENT_CLI_BUILDERS entries in sbx.py, since we have no verified model list
# for them yet. Add an entry once one is confirmed against a real installed
# CLI (see the "claude" and "codex" entries below for how).
#
# claude: from this environment's own live model-id roster (Fable 5, Opus 5,
# Sonnet 5, Haiku 4.5) plus the undated "claude-haiku-4-5" alias already used
# as this repo's example/default model.
# codex: read directly from ~/.codex/models_cache.json (the installed codex
# CLI's own cached model list), verified 2026-08-25.
KNOWN_MODELS: dict[str, frozenset[str]] = {
    "claude": frozenset(
        {
            "claude-opus-5",
            "claude-sonnet-5",
            "claude-fable-5",
            "claude-haiku-4-5",
            "claude-haiku-4-5-20251001",
        }
    ),
    "codex": frozenset(
        {
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "gpt-5.6-luna",
            "gpt-5.5",
            "gpt-5.4",
            "gpt-5.4-mini",
            "codex-auto-review",
        }
    ),
}

# Built-in per-agent-id default model, used by resolve_model when neither
# AgentSpec.model nor AgentProfile.model set one. Takes priority over the
# single global Config.model fallback so e.g. a codex job with no model
# anywhere gets a real codex model instead of whatever Config.model happens
# to hold (which may be a different agent's model string entirely). Only
# populated for agent_ids with a KNOWN_MODELS entry; every value here must
# be a member of KNOWN_MODELS[agent_id].
DEFAULT_MODELS: dict[str, str] = {
    "claude": "claude-haiku-4-5",
    "codex": "gpt-5.6-sol",
}


def validate_model(agent_id: str, model: str) -> None:
    """Raises if `model` isn't a recognized model for `agent_id`.

    Agents with no KNOWN_MODELS entry are skipped rather than treated as
    invalid -- see the KNOWN_MODELS module docstring comment.

    Args:
        agent_id: The sbx agent type (e.g. "claude", "codex").
        model: The resolved model name (from resolve_model).

    Raises:
        CommandError: If agent_id has a KNOWN_MODELS entry and model isn't
            in it.
    """
    valid = KNOWN_MODELS.get(agent_id)
    if valid is None or model in valid:
        return
    raise CommandError(
        f"Unknown model '{model}' for agent_id '{agent_id}'. "
        f"Known models: {', '.join(sorted(valid))}"
    )


# Env vars that repoint an agent CLI at a non-default, OpenAI/Anthropic-compatible
# endpoint (a local Ollama, a gateway, a self-hosted proxy). When a job's
# resolved env sets one of these, KNOWN_MODELS validation is skipped: the valid
# model names are then whatever that endpoint serves, which this process has no
# way to know ahead of time.
CUSTOM_ENDPOINT_ENV_KEYS = frozenset(
    {
        "ANTHROPIC_BASE_URL",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
    }
)


def uses_custom_model_endpoint(env: dict[str, str]) -> bool:
    """Returns whether `env` repoints the agent at a non-default model endpoint.

    Args:
        env: The job's resolved env (from resolve_env).

    Returns:
        True if any CUSTOM_ENDPOINT_ENV_KEYS key is present.
    """
    return any(key in env for key in CUSTOM_ENDPOINT_ENV_KEYS)


def _str_dict(raw: object) -> dict[str, str]:
    """Coerces a YAML `env:` mapping to a plain dict[str, str].

    Values are stringified so a bare number or bool in the YAML (e.g.
    `PORT: 11434`) still becomes a valid `sbx create -e` argument.

    Args:
        raw: The value of an `env:` key, or None if it was omitted.

    Returns:
        The coerced mapping; empty when raw is falsy.

    Raises:
        CommandError: If raw is present but not a mapping.
    """
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise CommandError(f"`env` must be a mapping of NAME: value, got {type(raw).__name__}")
    return {str(k): str(v) for k, v in raw.items()}


def host_repo_path(config: Config) -> str:
    """Returns `config.repo_path` as an absolute host path.

    `repo_path` names a directory *on the host*: it is what `sbx create`
    clones and what every `vcs` call operates on. Canonicalizing it in one
    place keeps the recorded `repo` value stable no matter whether the
    config said "../langlearn" or "/home/me/code/langlearn", and gives the
    host role an explicit name so it is not confused with any path inside
    a container (which is discovered from the container instead).

    Args:
        config: Orchestrator config.

    Returns:
        The resolved absolute path, as a string.
    """
    return str(Path(config.repo_path).expanduser().resolve())


VERIFY_ON_CHOICES = ("host", "sandbox")


def _validated_verify_on(value: str) -> str:
    """Returns `value` if it names a supported verification location.

    Args:
        value: The raw `run.verify_on` setting.

    Returns:
        The validated value.

    Raises:
        CommandError: If it isn't one of VERIFY_ON_CHOICES.
    """
    if value not in VERIFY_ON_CHOICES:
        raise CommandError(
            f"Unknown verify_on '{value}'. Choose one of: {', '.join(VERIFY_ON_CHOICES)}"
        )
    return value


@dataclass
class AgentProfile:
    """Per-agent-id defaults, from config.yaml's `agents:` section.

    Attributes:
        model: Default model for this agent_id. Falls back to
            Config.model when unset.
        provider: Default sbx --provider for this agent_id (e.g.
            "ollama", sbx >=0.39.0). Falls back to Config.provider when
            unset -- most agents have no provider concept, so this is
            usually None.
        dangerously_skip_permissions: Default yolo-mode toggle for this
            agent_id. Falls back to Config.dangerously_skip_permissions
            when unset.
        kit: sbx `--kit` refs (dir/zip/OCI) passed at `sbx create` time
            for every job using this agent_id -- e.g. an auth-wrapper or
            shared skill/mixin kit. Concatenated with, not replaced by,
            any per-job AgentSpec.kit (see resolve_kit_paths).
        env: Environment variables baked into the sandbox at `sbx create`
            time (`-e KEY=VALUE`) for every job using this agent_id --
            e.g. `ANTHROPIC_BASE_URL` to repoint the agent CLI at a local
            Ollama or a gateway. Merged with, not replaced by, any per-job
            AgentSpec.env, which wins key-by-key (see resolve_env).
    """

    model: str | None = None
    provider: str | None = None
    dangerously_skip_permissions: bool | None = None
    kit: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class Config:
    """Orchestrator-wide settings loaded from config/config.yaml."""

    repo_path: str
    github_repo: str
    github_pat_secret_name: str
    base_branch: str
    sbx_agent: str
    model: str
    dangerously_skip_permissions: bool
    max_turns: int | None
    status_file: str
    poll_interval_seconds: int
    timeout_minutes: int
    max_concurrency: int
    test_command: str
    lint_command: str
    remove_sandbox_on_success: bool
    remove_sandbox_on_failure: bool
    state_db_path: str
    provider: str | None = None
    agent_profiles: dict[str, AgentProfile] = field(default_factory=dict)

    # --- sandbox cost controls -------------------------------------------
    # Two tiers of polling, because they cost very different things:
    # `sbx ls --json` is a host-side query that spawns nothing inside the
    # sandbox, while reading the status file costs a process per tick. The
    # cheap tier runs often; the expensive one is what users can turn down.
    liveness_interval_seconds: int = 3
    # Sampling resource usage costs one `sbx exec` per sandbox per tick and
    # needs the sandbox alive, so it is off unless explicitly asked for.
    telemetry: bool = False
    telemetry_interval_seconds: int = 5
    # "host" runs test/lint on the fetched branch (nothing extra executes in
    # the container); "sandbox" keeps the original in-sandbox behavior for
    # suites that need the sandbox's toolchain.
    verify_on: str = "host"
    # Adds the agent CLI's JSON output flag so token usage can be parsed
    # back out of the run log.
    capture_usage: bool = True
    # Opening a PR is an outward-facing side effect on a real repo, so a UI
    # that can trigger runs must opt into it. The branch is still pushed.
    open_pr: bool = False

    @staticmethod
    def load(path: str) -> Config:
        """Loads a Config from a YAML file.

        Args:
            path: Path to a config.yaml.

        Returns:
            The parsed Config.
        """
        with open(path) as f:
            raw = yaml.safe_load(f)
        agents_raw = raw.get("agents", {}) or {}
        agent_profiles = {
            agent_id: AgentProfile(
                model=entry.get("model"),
                provider=entry.get("provider"),
                dangerously_skip_permissions=entry.get("dangerously_skip_permissions"),
                kit=list(entry.get("kit", []) or []),
                env=_str_dict(entry.get("env")),
            )
            for agent_id, entry in agents_raw.items()
        }
        return Config(
            repo_path=raw["repo_path"],
            github_repo=raw["github"]["repo"],
            github_pat_secret_name=raw["github"]["pat_secret_name"],
            base_branch=raw["github"]["base_branch"],
            sbx_agent=raw["agent"]["sbx_agent"],
            model=raw["agent"]["model"],
            dangerously_skip_permissions=raw["agent"]["dangerously_skip_permissions"],
            max_turns=raw["agent"].get("max_turns"),
            provider=raw["agent"].get("provider"),
            # status_file supersedes the old boolean done_marker; fall back
            # to done_marker so an unmigrated config.yaml still loads.
            status_file=(
                raw["run"].get("status_file")
                or raw["run"].get("done_marker")
                or ".agent_status.json"
            ),
            poll_interval_seconds=raw["run"]["poll_interval_seconds"],
            timeout_minutes=raw["run"]["timeout_minutes"],
            max_concurrency=raw["run"].get("max_concurrency", 3),
            test_command=raw["run"].get("test_command", "") or "",
            lint_command=raw["run"].get("lint_command", "") or "",
            remove_sandbox_on_success=raw["run"].get("remove_sandbox_on_success", True),
            # Defaults to True (langlearn used False): a sandbox kept after a
            # failed job only helps if someone inspects it, and the default
            # path here tears down promptly.
            remove_sandbox_on_failure=raw["run"].get("remove_sandbox_on_failure", True),
            liveness_interval_seconds=raw["run"].get("liveness_interval_seconds", 3),
            telemetry=raw["run"].get("telemetry", False),
            telemetry_interval_seconds=raw["run"].get("telemetry_interval_seconds", 5),
            verify_on=_validated_verify_on(raw["run"].get("verify_on", "host")),
            capture_usage=raw["run"].get("capture_usage", True),
            open_pr=raw["github"].get("open_pr", False),
            state_db_path=raw.get("state_db_path", "./orchestrator_state.sqlite3"),
            agent_profiles=agent_profiles,
        )


@dataclass
class AgentSpec:
    """One agent to run a feature through.

    Attributes:
        agent_id: The sbx agent type (e.g. "claude", "codex", "cursor",
            "opencode", "shell"). Determines both the sbx sandbox kit
            (`sbx create --clone <agent_id> ...`) and, unless `command`
            overrides it, which entry in `sbx.AGENT_CLI_BUILDERS` builds
            the in-sandbox invocation.
        run_label: Optional disambiguator, needed when the same agent_id
            appears more than once for one feature (e.g. running "claude"
            twice to compare non-determinism).
        model: Per-spec model override. Falls back to the agent's
            AgentProfile default, then its DEFAULT_MODELS entry (if any),
            then config.model (see resolve_model). Validated against
            KNOWN_MODELS in expand_jobs before any sandbox is created.
        command: Custom shell command that replaces the registered
            AGENT_CLI_BUILDERS entry entirely -- run verbatim inside the
            sandbox with ORCH_PROMPT/ORCH_STATUS_FILE/ORCH_RESUME
            exported (see sbx.build_agent_invocation). Required for
            agent_id "shell" (sbx's bare shell kit has no coding-agent
            CLI of its own); also usable as an escape hatch for any other
            agent_id whose registered builder flags don't match your
            installed CLI version.
        dangerously_skip_permissions: Per-job override. Falls back to
            the agent's AgentProfile default, then
            config.dangerously_skip_permissions (see
            resolve_dangerously_skip_permissions).
        kit: Per-job sbx `--kit` refs, concatenated after (not instead
            of) the agent's AgentProfile.kit list (see
            resolve_kit_paths).
        provider: Per-job sbx `--provider` override (e.g. "ollama",
            sbx >=0.39.0). Falls back to the agent's AgentProfile
            default, then config.provider (see resolve_provider).
        env: Per-job env vars baked into the sandbox at `sbx create` time
            (`-e KEY=VALUE`). Merged over the agent's AgentProfile.env
            key-by-key (see resolve_env). Setting a
            CUSTOM_ENDPOINT_ENV_KEYS var (e.g. `ANTHROPIC_BASE_URL`) also
            waives KNOWN_MODELS validation for this job.
    """

    agent_id: str
    run_label: str | None = None
    model: str | None = None
    command: str | None = None
    dangerously_skip_permissions: bool | None = None
    kit: list[str] = field(default_factory=list)
    provider: str | None = None
    env: dict[str, str] = field(default_factory=dict)


def resolve_model(spec: AgentSpec, profile: AgentProfile | None, config: Config) -> str:
    """Resolves the model to run with: AgentSpec > AgentProfile > DEFAULT_MODELS > Config.

    Args:
        spec: The job's AgentSpec.
        profile: The agent_id's AgentProfile, or None if it has none.
        config: Orchestrator config (supplies the last-resort fallback, for
            agent_ids with no DEFAULT_MODELS entry).

    Returns:
        The resolved model name.
    """
    if spec.model is not None:
        return spec.model
    if profile is not None and profile.model is not None:
        return profile.model
    if spec.agent_id in DEFAULT_MODELS:
        return DEFAULT_MODELS[spec.agent_id]
    return config.model


def resolve_provider(spec: AgentSpec, profile: AgentProfile | None, config: Config) -> str | None:
    """Resolves the sbx --provider to create with: AgentSpec > AgentProfile > Config.

    Args:
        spec: The job's AgentSpec.
        profile: The agent_id's AgentProfile, or None if it has none.
        config: Orchestrator config (supplies the fallback default).

    Returns:
        The resolved provider name, or None if nothing sets one.
    """
    if spec.provider is not None:
        return spec.provider
    if profile is not None and profile.provider is not None:
        return profile.provider
    return config.provider


def resolve_dangerously_skip_permissions(
    spec: AgentSpec, profile: AgentProfile | None, config: Config
) -> bool:
    """Resolves the skip-permissions toggle: AgentSpec > AgentProfile > Config.

    Uses `is not None` (not truthiness) at each level so an explicit
    `false` at a more specific level can override a `true` at a less
    specific one.

    Args:
        spec: The job's AgentSpec.
        profile: The agent_id's AgentProfile, or None if it has none.
        config: Orchestrator config (supplies the fallback default).

    Returns:
        The resolved skip-permissions toggle.
    """
    if spec.dangerously_skip_permissions is not None:
        return spec.dangerously_skip_permissions
    if profile is not None and profile.dangerously_skip_permissions is not None:
        return profile.dangerously_skip_permissions
    return config.dangerously_skip_permissions


def resolve_env(spec: AgentSpec, profile: AgentProfile | None) -> dict[str, str]:
    """Resolves the sbx `-e` env vars to bake in: AgentProfile.env then AgentSpec.env.

    Merged key-by-key rather than replaced, so a job inherits the agent's
    baseline env (e.g. a shared gateway URL) and can override or add
    individual keys.

    Args:
        spec: The job's AgentSpec.
        profile: The agent_id's AgentProfile, or None if it has none.

    Returns:
        The merged env mapping, with AgentSpec.env winning on conflicts.
    """
    return {**(profile.env if profile else {}), **spec.env}


def resolve_kit_paths(spec: AgentSpec, profile: AgentProfile | None) -> list[str]:
    """Resolves the sbx --kit refs to attach: AgentProfile.kit then AgentSpec.kit.

    Concatenated rather than overridden, so a job gets the agent's
    baseline kit(s) (e.g. an auth wrapper) plus anything job-specific.

    Args:
        spec: The job's AgentSpec.
        profile: The agent_id's AgentProfile, or None if it has none.

    Returns:
        The resolved list of --kit refs, in attach order.
    """
    return [*(profile.kit if profile else []), *spec.kit]


@dataclass
class Feature:
    """A single feature to implement, optionally across multiple agents."""

    id: str
    description: str
    acceptance_criteria: list[str]
    agents: list[AgentSpec]

    @staticmethod
    def load_all(path: str) -> dict[str, Feature]:
        """Loads every feature from a YAML file.

        Args:
            path: Path to a features.yaml.

        Returns:
            A dict mapping feature id to Feature. Each feature's `agents`
            list is normalized from either bare agent_id strings or
            `{agent_id, run_label}` dicts; it is empty when the `agents`
            key is omitted, meaning "use the config's default sbx_agent".
        """
        with open(path) as f:
            raw = yaml.safe_load(f)
        out = {}
        for item in raw["features"]:
            agents: list[AgentSpec] = []
            for entry in item.get("agents", []):
                if isinstance(entry, str):
                    agents.append(AgentSpec(agent_id=entry))
                else:
                    agents.append(
                        AgentSpec(
                            agent_id=entry["agent_id"],
                            run_label=entry.get("run_label"),
                            model=entry.get("model"),
                            command=entry.get("command"),
                            dangerously_skip_permissions=entry.get("dangerously_skip_permissions"),
                            kit=list(entry.get("kit", []) or []),
                            provider=entry.get("provider"),
                            env=_str_dict(entry.get("env")),
                        )
                    )
            out[item["id"]] = Feature(
                id=item["id"],
                description=item["description"].strip(),
                acceptance_criteria=item.get("acceptance_criteria", []),
                agents=agents,
            )
        return out
