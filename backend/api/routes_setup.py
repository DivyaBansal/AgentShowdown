"""Routes for pointing agentshowdown at a repository and configuring it.

Split from `routes` so neither module becomes an unreadable wall of nested
handlers; `register_routes` was already carrying a PLR0915 suppression.

Two rules shape every write handler here:

- The destination is never taken from the request. Config and features are
  written to `<active workspace>/.agentshowdown/`, computed server-side, so
  no request body can redirect a write somewhere else.
- The checked-in `demo/` preset is refused. It is version-controlled
  documentation, and saving over it would leave a fresh clone carrying
  someone else's settings and a dirty git tree.
"""

import dataclasses
from pathlib import Path

from fastapi import FastAPI, HTTPException

from backend.api.models import ConfigIn, FeaturesReplaceRequest, WorkspacePathRequest
from backend.api.paths import package_root
from backend.api.settings import (
    config_path,
    config_source,
    features_path,
    jobs_db_path,
    load_features,
)
from backend.api.workspace import (
    active_workspace,
    inspect,
    load_registry,
    select,
)
from backend.logging import log
from backend.orchestrator.config import AgentProfile, AgentSpec, Config, Feature
from backend.orchestrator.config_writer import (
    dump_config,
    dump_features,
    is_bundled_preset,
)
from backend.orchestrator.process import CommandError

DEMO_CONFIG = "demo/langlearn/config.yaml"

# The *name* of the sbx-stored GitHub secret, not a credential --
# agentshowdown never handles the token itself.
_DEFAULT_PAT_NAME = "github"


def _require_workspace() -> Path:
    """Returns the active workspace path, or 409 if none is selected."""
    entry = active_workspace()
    if entry is None:
        raise HTTPException(
            status_code=409,
            detail="No workspace selected. Choose a repository first.",
        )
    return Path(entry.path)


def _refuse_bundled_preset(path: Path) -> None:
    """Raises 409 if `path` is the repo's own checked-in demo preset."""
    if is_bundled_preset(path, package_root()):
        log.warning("demo_preset_write_refused", path=str(path))
        raise HTTPException(
            status_code=409,
            detail=(
                "The bundled demo preset is read-only. Select a repository "
                "of your own to save configuration into."
            ),
        )


def _draft_config(repo_path: Path) -> Config:
    """Builds a starting config for a workspace that has none yet.

    Seeded from the bundled demo so the form opens with sane run settings,
    then repointed at the chosen repo. Nothing is written until Save.
    """
    info = inspect(repo_path)
    try:
        base = Config.load(DEMO_CONFIG)
    except (FileNotFoundError, KeyError):
        base = Config(
            repo_path=str(repo_path),
            github_repo="",
            github_pat_secret_name=_DEFAULT_PAT_NAME,
            base_branch="main",
            sbx_agent="claude",
            model="claude-haiku-4-5",
            dangerously_skip_permissions=True,
            max_turns=None,
            status_file=".agent_status.json",
            poll_interval_seconds=5,
            timeout_minutes=60,
            max_concurrency=3,
            test_command="",
            lint_command="",
            remove_sandbox_on_success=True,
            remove_sandbox_on_failure=True,
            state_db_path=str(jobs_db_path()),
        )
    return dataclasses.replace(
        base,
        repo_path=str(repo_path),
        github_repo=info.github_repo or "",
        base_branch=info.default_branch or base.base_branch,
        state_db_path=str(jobs_db_path()),
    )


def _config_to_payload(config: Config) -> dict[str, object]:
    """Renders a Config as the shape `ConfigIn` accepts."""
    return {
        "github": {
            "repo": config.github_repo,
            "pat_secret_name": config.github_pat_secret_name,
            "base_branch": config.base_branch,
            "open_pr": config.open_pr,
        },
        "agent": {
            "sbx_agent": config.sbx_agent,
            "model": config.model,
            "dangerously_skip_permissions": config.dangerously_skip_permissions,
            "max_turns": config.max_turns,
            "provider": config.provider,
        },
        "run": {
            "status_file": config.status_file,
            "liveness_interval_seconds": config.liveness_interval_seconds,
            "poll_interval_seconds": config.poll_interval_seconds,
            "timeout_minutes": config.timeout_minutes,
            "max_concurrency": config.max_concurrency,
            "verify_on": config.verify_on,
            "test_command": config.test_command,
            "lint_command": config.lint_command,
            "telemetry": config.telemetry,
            "telemetry_interval_seconds": config.telemetry_interval_seconds,
            "capture_usage": config.capture_usage,
            "remove_sandbox_on_success": config.remove_sandbox_on_success,
            "remove_sandbox_on_failure": config.remove_sandbox_on_failure,
        },
        "agents": {
            agent_id: {
                "model": p.model,
                "provider": p.provider,
                "dangerously_skip_permissions": p.dangerously_skip_permissions,
                "kit": list(p.kit),
                "env": dict(p.env),
            }
            for agent_id, p in config.agent_profiles.items()
        },
    }


def _payload_to_config(body: ConfigIn, repo_path: Path) -> Config:
    """Builds a Config from a request body plus server-stamped fields."""
    return Config(
        repo_path=str(repo_path),
        github_repo=body.github.repo,
        github_pat_secret_name=body.github.pat_secret_name,
        base_branch=body.github.base_branch,
        open_pr=body.github.open_pr,
        sbx_agent=body.agent.sbx_agent,
        model=body.agent.model,
        dangerously_skip_permissions=body.agent.dangerously_skip_permissions,
        max_turns=body.agent.max_turns,
        provider=body.agent.provider,
        status_file=body.run.status_file,
        liveness_interval_seconds=body.run.liveness_interval_seconds,
        poll_interval_seconds=body.run.poll_interval_seconds,
        timeout_minutes=body.run.timeout_minutes,
        max_concurrency=body.run.max_concurrency,
        verify_on=body.run.verify_on,
        test_command=body.run.test_command,
        lint_command=body.run.lint_command,
        telemetry=body.run.telemetry,
        telemetry_interval_seconds=body.run.telemetry_interval_seconds,
        capture_usage=body.run.capture_usage,
        remove_sandbox_on_success=body.run.remove_sandbox_on_success,
        remove_sandbox_on_failure=body.run.remove_sandbox_on_failure,
        state_db_path=str(jobs_db_path()),
        agent_profiles={
            agent_id: AgentProfile(
                model=p.model,
                provider=p.provider,
                dangerously_skip_permissions=p.dangerously_skip_permissions,
                kit=list(p.kit),
                env=dict(p.env),
            )
            for agent_id, p in body.agents.items()
        },
    )


def register_workspace_routes(app: FastAPI) -> None:
    """Registers the repository-picker routes on `app`."""

    @app.get("/workspaces")
    def list_workspaces() -> dict[str, object]:
        registry = load_registry()
        return {
            "active": registry.active,
            "workspaces": [w.model_dump() for w in registry.workspaces],
        }

    @app.post("/workspaces/inspect")
    def inspect_workspace(request: WorkspacePathRequest) -> dict[str, object]:
        """Reports what a directory is, without selecting or registering it.

        Looking is a separate step from using: this is what lets the UI show
        "found a git repo with an existing config" before anything changes.
        """
        info = inspect(Path(request.path))
        if not info.exists:
            raise HTTPException(status_code=404, detail=f"No such directory: {info.path}")
        return info.model_dump()

    @app.post("/workspaces/select")
    def select_workspace(request: WorkspacePathRequest) -> dict[str, object]:
        try:
            entry = select(Path(request.path))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return entry.model_dump()


def register_config_routes(app: FastAPI) -> None:
    """Registers the config and features editing routes on `app`."""

    @app.get("/config")
    def get_config() -> dict[str, object]:
        """Returns the active config, or a draft when none is saved yet.

        Never 503s on a missing file: a freshly picked repo has no config,
        and that is precisely when the form needs to open.
        """
        path = config_path()
        try:
            config = Config.load(str(path))
            exists = True
        except (FileNotFoundError, KeyError):
            entry = active_workspace()
            config = _draft_config(Path(entry.path) if entry else Path())
            exists = False
        return {
            "path": str(path),
            "exists": exists,
            "source": config_source(),
            "editable": not is_bundled_preset(path, package_root()),
            "repo_path": config.repo_path,
            "config": _config_to_payload(config),
        }

    @app.put("/config")
    def put_config(request: ConfigIn) -> dict[str, object]:
        repo_path = _require_workspace()
        path = config_path()
        _refuse_bundled_preset(path)

        config = _payload_to_config(request, repo_path)
        if config.verify_on == "host" and config.test_command:
            # Runs on the host via `bash -c`, so it is worth an audit line.
            log.warning(
                "host_verify_command_changed",
                verify_on=config.verify_on,
                test_command=config.test_command,
                lint_command=config.lint_command,
            )
        try:
            dump_config(config, path)
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        log.info("workspace_config_written", path=str(path))
        return {"path": str(path), "written": True}

    @app.put("/features")
    def put_features(request: FeaturesReplaceRequest) -> dict[str, object]:
        _require_workspace()
        path = features_path()
        _refuse_bundled_preset(path)

        features = [
            Feature(
                id=f.id,
                description=f.description,
                acceptance_criteria=list(f.acceptance_criteria),
                agents=[
                    AgentSpec(
                        agent_id=a.agent_id,
                        run_label=a.run_label,
                        model=a.model,
                        command=a.command,
                        dangerously_skip_permissions=a.dangerously_skip_permissions,
                        kit=list(a.kit),
                        provider=a.provider,
                        env=dict(a.env),
                    )
                    for a in f.agents
                ],
            )
            for f in request.features
        ]
        for feature in features:
            for spec in feature.agents:
                if spec.command:
                    log.info(
                        "agent_command_override_set",
                        feature_id=feature.id,
                        agent_id=spec.agent_id,
                    )
        try:
            dump_features(features, path)
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        log.info("workspace_features_written", path=str(path), count=len(features))
        return {"path": str(path), "written": True, "count": len(features)}

    @app.get("/features/meta")
    def features_meta() -> dict[str, object]:
        """Where the feature list is read from, and whether it exists yet."""
        path = features_path()
        try:
            load_features()
            exists = True
        except (FileNotFoundError, KeyError, CommandError):
            exists = False
        return {
            "path": str(path),
            "exists": exists,
            "source": config_source(),
            "editable": not is_bundled_preset(path, package_root()),
        }


def register_setup_routes(app: FastAPI) -> None:
    """Registers every setup route on `app`."""
    register_workspace_routes(app)
    register_config_routes(app)
