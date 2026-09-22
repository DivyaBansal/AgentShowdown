"""HTTP routes, registered on the app created in `main`.

Handlers stay thin: validate through a model, call into the orchestrator or
a helper module, return plain dicts. Anything with real logic lives in
`runner`, `sandboxes` or `orchestrator/` so it can be tested without a
client.

Routes are declared without an `/api` prefix. The frontend calls
`/api/...` and Vite's dev proxy strips it, matching the boilerplate's
existing convention.
"""

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.api import sandboxes as sandbox_ops
from backend.api.models import (
    AnswerRequest,
    JobQuery,
    KillAllRequest,
    StartRunRequest,
)
from backend.api.preflight import preflight
from backend.api.routes_integrations import register_integration_routes
from backend.api.routes_setup import register_setup_routes
from backend.api.runner import RunConflictError, start_run
from backend.api.settings import load_config, load_features
from backend.api.stream import event_stream, parse_last_event_id
from backend.logging import log
from backend.orchestrator.config import (
    DEFAULT_MODELS,
    KNOWN_MODELS,
    Config,
)
from backend.orchestrator.job import resume_job
from backend.orchestrator.process import CommandError
from backend.orchestrator.sbx import (
    AGENT_CLI_BUILDERS,
    SKIP_PERMISSIONS_IGNORED,
    sbx_list,
)
from backend.orchestrator.state import StateStore

# Agents sbx can create a sandbox for. Kept alongside the builder registry
# so the UI can show which are natively supported and which need a custom
# `command:` -- rather than offering all of them as if equal.
SBX_AGENT_KITS = (
    "claude",
    "codex",
    "copilot",
    "cursor",
    "droid",
    "gemini",
    "kiro",
    "opencode",
    "shell",
)

# Only these have had their unattended-run flags checked against a real
# installed CLI; the rest are best-effort, per langlearn's own caveat.
VERIFIED_AGENTS = ("claude", "codex")


def _config() -> Config:
    try:
        return load_config()
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"No orchestrator config found: {exc}",
        ) from exc


def register_routes(app: FastAPI) -> None:  # noqa: PLR0915 -- one call per route
    """Registers every API route on `app`."""

    # Repository picker, config and features editing.
    register_setup_routes(app)
    # Cloning, secrets, and GitHub issue import.
    register_integration_routes(app)

    @app.get("/preflight")
    def get_preflight() -> dict[str, object]:
        return preflight().as_dict()

    @app.get("/agents")
    def list_agents() -> dict[str, object]:
        """Which agents exist, and how well each is actually supported."""
        return {
            "agents": [
                {
                    "agent_id": agent_id,
                    # A kit with no builder can still run, but only via a
                    # custom `command:`; and such a job never receives
                    # --model, so the UI must not offer one.
                    "requires_command": agent_id not in AGENT_CLI_BUILDERS,
                    "accepts_model": agent_id in AGENT_CLI_BUILDERS,
                    "supports_skip_permissions": (
                        agent_id in AGENT_CLI_BUILDERS and agent_id not in SKIP_PERMISSIONS_IGNORED
                    ),
                    "verified": agent_id in VERIFIED_AGENTS,
                    "known_models": sorted(KNOWN_MODELS.get(agent_id, ())),
                    "default_model": DEFAULT_MODELS.get(agent_id),
                    "reports_token_usage": agent_id in ("claude", "codex"),
                }
                for agent_id in SBX_AGENT_KITS
            ]
        }

    @app.get("/features")
    def list_features() -> dict[str, object]:
        try:
            features = load_features()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "features": [
                {
                    "id": f.id,
                    "description": f.description,
                    "acceptance_criteria": f.acceptance_criteria,
                    # The full spec, not just the three scalars: the setup
                    # form prefills from this, and a narrower payload would
                    # silently drop a feature's env/kit/command the moment
                    # anyone saved from the UI.
                    "agents": [
                        {
                            "agent_id": a.agent_id,
                            "run_label": a.run_label,
                            "model": a.model,
                            "command": a.command,
                            "dangerously_skip_permissions": a.dangerously_skip_permissions,
                            "kit": list(a.kit),
                            "provider": a.provider,
                            "env": dict(a.env),
                        }
                        for a in f.agents
                    ],
                }
                for f in features.values()
            ]
        }

    @app.post("/runs")
    def create_run(request: StartRunRequest) -> dict[str, list[str]]:
        config = _config()
        try:
            features = load_features()
            run_ids = start_run(config, features, request)
        except RunConflictError as exc:
            log.warning("run_rejected_conflict", detail=str(exc))
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CommandError as exc:
            # Validation happens before anything is created, so a rejected
            # request has left no sandboxes behind.
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"run_ids": run_ids}

    @app.get("/runs")
    def list_runs() -> dict[str, object]:
        with StateStore(_config().state_db_path) as store:
            return {"runs": store.all_runs()}

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, object]:
        with StateStore(_config().state_db_path) as store:
            run = store.get_run(run_id)
            if run is None:
                raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
            return {"run": run, "jobs": store.jobs_for_run(run_id)}

    @app.get("/jobs")
    def list_jobs(query: Annotated[JobQuery, Depends()]) -> dict[str, object]:
        with StateStore(_config().state_db_path) as store:
            return {"jobs": store.all_jobs(query.repo)}

    @app.get("/jobs/{sandbox_name}/samples")
    def get_samples(sandbox_name: str) -> dict[str, object]:
        with StateStore(_config().state_db_path) as store:
            return {"samples": store.get_samples(sandbox_name)}

    @app.post("/jobs/{sandbox_name}/answer")
    def answer_job(sandbox_name: str, request: AnswerRequest) -> dict[str, str]:
        """Answers an agent that stopped to ask, and resumes it.

        This is pause-then-resume, not live chat: headless agent CLIs have
        no REPL to inject into, so the answer is folded into a follow-up
        prompt and the agent is relaunched in resume mode.
        """
        config = _config()
        try:
            resume_job(config, load_features(), sandbox_name, request.answer)
        except CommandError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "resumed", "sandbox_name": sandbox_name}

    @app.get("/sandboxes")
    def list_sandboxes() -> dict[str, object]:
        return {"sandboxes": list(sbx_list().values())}

    @app.post("/sandboxes/{sandbox_name}/ping")
    def ping_sandbox(sandbox_name: str) -> dict[str, object]:
        return sandbox_ops.ping(sandbox_name, _config().status_file)

    @app.post("/sandboxes/{sandbox_name}/rm")
    def remove_sandbox(sandbox_name: str) -> dict[str, object]:
        return sandbox_ops.remove(sandbox_name, _config().state_db_path)

    @app.post("/sandboxes/kill-all")
    def kill_all_sandboxes(request: KillAllRequest) -> dict[str, object]:
        """Removes every sandbox on the host.

        The typed confirmation is enforced by the request model, so a
        mis-click cannot reach this.
        """
        log.warning("kill_all_requested")
        return sandbox_ops.kill_all(_config().state_db_path)

    @app.get("/events")
    async def events(request: Request) -> StreamingResponse:
        """Live event stream.

        `async def` deliberately: a run can last an hour, and a sync handler
        would hold one of FastAPI's threadpool workers for the duration.
        """
        last_event_id = parse_last_event_id(request.headers.get("last-event-id"))
        return StreamingResponse(
            event_stream(request, last_event_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                # Tells nginx and friends not to buffer, which would defeat
                # the point of streaming.
                "X-Accel-Buffering": "no",
            },
        )
