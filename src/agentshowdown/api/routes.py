"""HTTP routes, registered on the app created in `main`.

Handlers stay thin: validate through a model, call into the orchestrator or
a helper module, return plain dicts. Anything with real logic lives in
`runner`, `sandboxes` or `orchestrator/` so it can be tested without a
client.

Routes are declared without an `/api` prefix. The frontend calls
`/api/...` and Vite's dev proxy strips it, matching the boilerplate's
existing convention.
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from agentshowdown.api import sandboxes as sandbox_ops
from agentshowdown.api.models import (
    AnswerRequest,
    KillAllRequest,
    StartRunRequest,
)
from agentshowdown.api.preflight import preflight
from agentshowdown.api.runner import start_run
from agentshowdown.api.settings import load_config, load_features
from agentshowdown.api.stream import event_stream, parse_last_event_id
from agentshowdown.orchestrator.config import (
    DEFAULT_MODELS,
    KNOWN_MODELS,
    Config,
)
from agentshowdown.orchestrator.job import resume_job
from agentshowdown.orchestrator.log import log
from agentshowdown.orchestrator.process import CommandError
from agentshowdown.orchestrator.sbx import AGENT_CLI_BUILDERS, sbx_list
from agentshowdown.orchestrator.state import StateStore

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
                    "has_native_builder": agent_id in AGENT_CLI_BUILDERS,
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
                    "agents": [
                        {
                            "agent_id": a.agent_id,
                            "run_label": a.run_label,
                            "model": a.model,
                        }
                        for a in f.agents
                    ],
                }
                for f in features.values()
            ]
        }

    @app.post("/runs")
    def create_run(request: StartRunRequest) -> dict[str, str]:
        config = _config()
        try:
            features = load_features()
            run_id = start_run(config, features, request)
        except CommandError as exc:
            # Validation happens before anything is created, so a rejected
            # request has left no sandboxes behind.
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"run_id": run_id}

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
    def list_jobs() -> dict[str, object]:
        with StateStore(_config().state_db_path) as store:
            return {"jobs": store.all_jobs()}

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
