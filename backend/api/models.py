"""Request and response models for the HTTP layer.

Every endpoint validates its input through a model here rather than
checking fields ad hoc in the handler, so a bad request fails at the
boundary with a 422 describing exactly what was wrong.

Field names are snake_case throughout and mirror the orchestrator's own
vocabulary, so the TypeScript interfaces in the frontend can match them
one-for-one without a translation layer.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.api.validators import DirectoryName, GitCloneUrl, LocalRepoPath

VerifyOn = Literal["host", "sandbox"]


class AgentSpecIn(BaseModel):
    """One agent to run a feature through.

    Mirrors `orchestrator.config.AgentSpec` field for field. The narrower
    earlier version could not express `env`, which is how an agent is
    pointed at a local Ollama or a gateway, nor `command`, which agent kits
    without a built-in launcher require -- so anything using those could
    only be launched from the CLI.
    """

    agent_id: str = Field(min_length=1, max_length=64)
    run_label: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=128)
    # Runs inside the sandbox, which is the isolation boundary. Validated
    # for shape only: a blocklist of "dangerous" commands would be theater.
    command: str | None = Field(default=None, max_length=4096)
    dangerously_skip_permissions: bool | None = None
    kit: list[str] = Field(default_factory=list, max_length=16)
    provider: str | None = Field(default=None, max_length=64)
    env: dict[str, str] = Field(default_factory=dict, max_length=64)


class RunEntryIn(BaseModel):
    """One feature and the agents to run it through."""

    feature_id: str = Field(min_length=1, max_length=128)
    agents: list[AgentSpecIn] = Field(min_length=1, max_length=16)


class StartRunRequest(BaseModel):
    """Launch one or more features, each across its own agents.

    Each entry becomes its own run, so a batch of three features is three
    run ids that execute concurrently. The cost controls below are set once
    and apply to every run in the batch.
    """

    entries: list[RunEntryIn] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def _reject_repeated_features(self) -> StartRunRequest:
        seen = {entry.feature_id for entry in self.entries}
        if len(seen) != len(self.entries):
            raise ValueError(
                "each feature may appear only once -- add the agents to that "
                "feature's existing entry instead of repeating it"
            )
        return self

    # Cost controls, all optional -- omitted means "use the config default".
    poll_interval_seconds: int | None = Field(default=None, ge=0, le=3600)
    liveness_interval_seconds: int | None = Field(default=None, ge=0, le=3600)
    telemetry: bool | None = None
    verify_on: VerifyOn | None = None
    open_pr: bool | None = None
    max_concurrency: int | None = Field(default=None, ge=1, le=32)

    # Sandbox resource limits, passed to `sbx create`. Setting these is what
    # gives the telemetry chart a real denominator.
    cpus: int | None = Field(default=None, ge=1, le=256)
    memory: str | None = Field(default=None, pattern=r"^\d+[mg]$")


class AnswerRequest(BaseModel):
    """A human's answer to an agent that stopped to ask."""

    answer: str = Field(min_length=1, max_length=4000)


class JobQuery(BaseModel):
    """Query params for ``GET /jobs``.

    One shared database holds every workspace's history, so the job board
    needs a way to narrow to a single repo. Bound with `Depends()` so the
    filter is validated at the boundary like every other input.
    """

    repo: str | None = Field(default=None, max_length=4096)


class KillAllRequest(BaseModel):
    """Confirmation for the host-wide sandbox wipe.

    `sbx rm --all --force` removes every sandbox on the machine, including
    ones this app never created, so the phrase must be typed exactly. A
    single click must not be able to trigger it.
    """

    confirm: Literal["KILL ALL"]


class WorkspacePathRequest(BaseModel):
    """A host directory to inspect or select as the active workspace."""

    path: LocalRepoPath


class FeatureIn(BaseModel):
    """A feature to implement."""

    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9._-]+$")
    description: str = Field(min_length=1, max_length=20000)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=64)
    agents: list[AgentSpecIn] = Field(default_factory=list, max_length=16)


class FeaturesReplaceRequest(BaseModel):
    """Replace the whole feature list."""

    features: list[FeatureIn] = Field(max_length=256)

    @model_validator(mode="after")
    def _reject_duplicate_ids(self) -> FeaturesReplaceRequest:
        """Duplicate ids would silently drop features, since they key a dict."""
        seen = [f.id for f in self.features]
        duplicates = sorted({fid for fid in seen if seen.count(fid) > 1})
        if duplicates:
            raise ValueError(f"duplicate feature ids: {', '.join(duplicates)}")
        return self


class GithubConfigIn(BaseModel):
    """The `github:` section of config.yaml."""

    repo: str = Field(min_length=1, max_length=256)
    pat_secret_name: str = Field(default="github", min_length=1, max_length=64)
    base_branch: str = Field(default="main", min_length=1, max_length=255)
    open_pr: bool = False


class AgentConfigIn(BaseModel):
    """The `agent:` section of config.yaml."""

    sbx_agent: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    dangerously_skip_permissions: bool = True
    max_turns: int | None = Field(default=None, ge=1, le=1000)
    provider: str | None = Field(default=None, max_length=64)


class RunConfigIn(BaseModel):
    """The `run:` section of config.yaml."""

    status_file: str = Field(default=".agent_status.json", min_length=1, max_length=255)
    liveness_interval_seconds: int = Field(default=3, ge=0, le=3600)
    poll_interval_seconds: int = Field(default=5, ge=0, le=3600)
    timeout_minutes: int = Field(default=60, ge=1, le=1440)
    max_concurrency: int = Field(default=3, ge=1, le=32)
    verify_on: VerifyOn = "host"
    # Runs on the *host* via `bash -c` when verify_on is "host". See the
    # deployment note in the README: this port is equivalent to a shell.
    test_command: str = Field(default="", max_length=4096)
    lint_command: str = Field(default="", max_length=4096)
    telemetry: bool = False
    telemetry_interval_seconds: int = Field(default=5, ge=1, le=3600)
    capture_usage: bool = True
    remove_sandbox_on_success: bool = True
    remove_sandbox_on_failure: bool = True


class AgentProfileIn(BaseModel):
    """One entry in config.yaml's per-agent `agents:` map."""

    model: str | None = Field(default=None, max_length=128)
    provider: str | None = Field(default=None, max_length=64)
    dangerously_skip_permissions: bool | None = None
    kit: list[str] = Field(default_factory=list, max_length=16)
    env: dict[str, str] = Field(default_factory=dict, max_length=64)


class ConfigIn(BaseModel):
    """The editable config for the active workspace.

    `repo_path` and `state_db_path` are deliberately absent. The first is
    stamped from the active workspace -- accepting it would be a second
    arbitrary-path ingress -- and the second is pinned to the shared jobs
    database, so accepting it would un-pin the one thing keeping run
    history comparable across repos.
    """

    github: GithubConfigIn
    agent: AgentConfigIn
    run: RunConfigIn = Field(default_factory=RunConfigIn)
    agents: dict[str, AgentProfileIn] = Field(default_factory=dict)


# The services sbx will store a secret for, from `sbx secret set --help`.
# Constraining this at the boundary makes a typo a 422 rather than a
# confusing sbx error several layers down.
SecretService = Literal[
    "anthropic",
    "cursor",
    "droid",
    "github",
    "google",
    "groq",
    "mistral",
    "nebius",
    "openai",
    "openrouter",
    "xai",
]

IssueState = Literal["open", "closed", "all"]


class CloneRequest(BaseModel):
    """Clone a repository into the local clones directory."""

    url: GitCloneUrl
    directory_name: DirectoryName | None = None


class SecretRequest(BaseModel):
    """Store a secret with sbx.

    Prefer `ref` or `command`: those store a *reference* that sbx resolves
    on demand, so the secret value never passes through this application at
    all. `token` is supported but goes to sbx on the command line, where it
    is briefly visible in `ps` to other users on the host.
    """

    service: SecretService
    token: str | None = Field(default=None, min_length=1, max_length=4096)
    ref: str | None = Field(default=None, min_length=1, max_length=1024)
    command: str | None = Field(default=None, min_length=1, max_length=1024)
    sandbox: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _exactly_one_source(self) -> SecretRequest:
        """Two sources would silently ignore one; none would store nothing."""
        provided = [v for v in (self.token, self.ref, self.command) if v is not None]
        if len(provided) != 1:
            raise ValueError("provide exactly one of: token, ref, command")
        return self


class IssueQuery(BaseModel):
    """Query params for ``GET /github/issues``."""

    repo: str | None = Field(default=None, max_length=256)
    state: IssueState = "open"
    limit: int = Field(default=30, ge=1, le=200)
    labels: str | None = Field(default=None, max_length=256)


class ImportIssuesRequest(BaseModel):
    """Draft features from a set of GitHub issues.

    Takes a list rather than a single issue because the UI presents a
    multi-select: importing a backlog one round trip at a time would be
    both slower and harder to make atomic.
    """

    issues: list[int] = Field(min_length=1, max_length=100)
    repo: str | None = Field(default=None, max_length=256)
    agents: list[AgentSpecIn] = Field(default_factory=list, max_length=16)
