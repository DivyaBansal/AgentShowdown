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

from pydantic import BaseModel, Field

# The services sbx will store a secret for, taken from `sbx secret set
# --help`. Constraining this at the boundary means a typo is a 422 rather
# than a confusing sbx error several layers down.
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

VerifyOn = Literal["host", "sandbox"]


class AgentSpecIn(BaseModel):
    """One agent to run a feature through."""

    agent_id: str = Field(min_length=1, max_length=64)
    run_label: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=128)


class StartRunRequest(BaseModel):
    """Launch one feature across one or more agents."""

    feature_id: str = Field(min_length=1, max_length=128)
    agents: list[AgentSpecIn] = Field(min_length=1, max_length=16)

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


class KillAllRequest(BaseModel):
    """Confirmation for the host-wide sandbox wipe.

    `sbx rm --all --force` removes every sandbox on the machine, including
    ones this app never created, so the phrase must be typed exactly. A
    single click must not be able to trigger it.
    """

    confirm: Literal["KILL ALL"]


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


class FeatureIn(BaseModel):
    """A feature to implement."""

    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9._-]+$")
    description: str = Field(min_length=1, max_length=20000)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=64)
    agents: list[AgentSpecIn] = Field(default_factory=list, max_length=16)


class FeaturesReplaceRequest(BaseModel):
    """Replace the whole feature list."""

    features: list[FeatureIn] = Field(max_length=256)


class FeatureFromIssueRequest(BaseModel):
    """Draft a feature from a GitHub issue."""

    issue: int = Field(ge=1)
    repo: str | None = Field(default=None, max_length=256)
