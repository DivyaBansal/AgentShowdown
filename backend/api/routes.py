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
    FollowupRequest,
    JobQuery,
    KillAllRequest,
    LogQuery,
    RemoveSandboxRequest,
    StartRunRequest,
)
from backend.api.preflight import preflight
from backend.api.routes_ask import register_ask_routes
from backend.api.routes_integrations import register_integration_routes
from backend.api.routes_models import register_model_routes
from backend.api.routes_otlp import register_otlp_routes
from backend.api.routes_setup import register_setup_routes
from backend.api.runner import (
    RunConflictError,
    start_answer,
    start_followup,
    start_respawn,
    start_run,
)
from backend.api.settings import load_config, load_features
from backend.api.stream import event_stream, parse_last_event_id
from backend.logging import log
from backend.model_catalog import catalog
from backend.orchestrator.config import (
    DEFAULT_MODELS,
    KNOWN_MODELS,
    Config,
)
from backend.orchestrator.process import CommandError
from backend.orchestrator.sbx import (
    AGENT_CLI_BUILDERS,
    AGENT_LOG_PATH,
    SKIP_PERMISSIONS_IGNORED,
    sbx_list,
    sbx_tail_log,
)
from backend.orchestrator.state import StateStore
from backend.orchestrator.transcript import parse_transcript

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
    # Quick questions across models, no repo needed.
    register_ask_routes(app)
    register_model_routes(app)
    register_otlp_routes(app)

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
                    "known_models": catalog(agent_id, sorted(KNOWN_MODELS.get(agent_id, ())))[
                        "models"
                    ],
                    "model_source": catalog(agent_id)["source"],
                    "models_updated_at": catalog(agent_id)["updated_at"],
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

    @app.get("/jobs/{sandbox_name}/log")
    def get_log(sandbox_name: str, query: Annotated[LogQuery, Depends()]) -> dict[str, object]:
        """An on-demand deeper log tail, read straight from the sandbox.

        Returns "" rather than 404 when the sandbox is gone or the log
        doesn't exist yet -- same leniency as `/samples`, since "nothing to
        show yet" and "never existed" look the same from here.
        """
        tail = sbx_tail_log(sandbox_name, AGENT_LOG_PATH, query.tail)
        return {"sandbox_name": sandbox_name, "tail": tail}

    @app.get("/jobs/{sandbox_name}/transcript")
    def get_transcript(
        sandbox_name: str, query: Annotated[LogQuery, Depends()]
    ) -> dict[str, object]:
        """A best-effort structured view of the agent's output.

        Parsed from the same tail `/log` reads, so it works mid-run, not
        just after a job finishes. Agents whose output isn't JSON (cursor,
        opencode, copilot, a custom `command:`) yield an empty entry list --
        the UI falls back to the raw tail for those, same as `/log`'s
        leniency for a gone sandbox.
        """
        tail = sbx_tail_log(sandbox_name, AGENT_LOG_PATH, query.tail)
        entries = parse_transcript(tail)
        return {
            "sandbox_name": sandbox_name,
            "entries": [{"index": e.index, "kind": e.kind, "text": e.text} for e in entries],
        }

    @app.post("/jobs/{sandbox_name}/answer", status_code=202)
    def answer_job(sandbox_name: str, request: AnswerRequest) -> dict[str, str]:
        """Answers an agent that stopped to ask, and returns immediately.

        This is pause-then-resume, not live chat: headless agent CLIs have
        no REPL to inject into, so the answer is folded into a follow-up
        prompt and the agent is relaunched in resume mode -- in the
        background (see `start_answer`), since the resumed run can last as
        long as the job's timeout. Progress arrives over the event stream.
        """
        config = _config()
        try:
            start_answer(config, load_features(), sandbox_name, request.reply)
        except RunConflictError as exc:
            log.warning("answer_rejected_conflict", detail=str(exc))
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CommandError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "resuming", "sandbox_name": sandbox_name}

    @app.post("/jobs/{sandbox_name}/followup", status_code=202)
    def followup_job(sandbox_name: str, request: FollowupRequest) -> dict[str, str]:
        """Sends a job a new instruction, and returns immediately.

        For a job whose agent isn't waiting on anything -- it finished,
        failed verification, or got stuck -- but whose sandbox is still
        around to continue the same session.
        """
        config = _config()
        try:
            start_followup(config, load_features(), sandbox_name, request.prompt)
        except RunConflictError as exc:
            log.warning("followup_rejected_conflict", detail=str(exc))
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CommandError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "following_up", "sandbox_name": sandbox_name}

    @app.post("/jobs/{sandbox_name}/respawn", status_code=202)
    def respawn_job(sandbox_name: str) -> dict[str, str]:
        """Restarts a stopped or dead job, and returns immediately.

        Unlike `/answer`, this runs in the background (see `start_respawn`):
        restarting a stopped sandbox or relaunching a fresh one can take as
        long as the agent's own startup, which has no reason to hold the
        request open.
        """
        config = _config()
        try:
            start_respawn(config, load_features(), sandbox_name)
        except RunConflictError as exc:
            log.warning("respawn_rejected_conflict", detail=str(exc))
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CommandError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "respawning", "sandbox_name": sandbox_name}

    @app.get("/sandboxes")
    def list_sandboxes() -> dict[str, object]:
        return {"sandboxes": list(sbx_list().values())}

    @app.post("/sandboxes/{sandbox_name}/ping")
    def ping_sandbox(sandbox_name: str) -> dict[str, object]:
        return sandbox_ops.ping(sandbox_name, _config().status_file)

    @app.post("/sandboxes/{sandbox_name}/stop")
    def stop_sandbox(sandbox_name: str) -> dict[str, object]:
        """Stops a sandbox's container without removing it (see `respawn`)."""
        try:
            return sandbox_ops.stop(sandbox_name, _config().state_db_path)
        except CommandError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/sandboxes/{sandbox_name}/rm")
    def remove_sandbox(
        sandbox_name: str, request: RemoveSandboxRequest | None = None
    ) -> dict[str, object]:
        config = _config()
        force = request.force if request is not None else False
        try:
            return sandbox_ops.remove(
                sandbox_name,
                config.state_db_path,
                config.repo_path,
                config.base_branch,
                force=force,
            )
        except CommandError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

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
