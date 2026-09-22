"""Routes for cloning, secrets, and GitHub issue import."""

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException

from backend.api.clone import get_clone, start_clone
from backend.api.issues import (
    GithubUnavailableError,
    acceptance_criteria_from_body,
    feature_id_for_issue,
    list_issues,
)
from backend.api.models import (
    AgentSpecIn,
    CloneRequest,
    ImportIssuesRequest,
    IssueQuery,
    SecretRequest,
)
from backend.api.secrets import list_secrets, store_secret
from backend.api.settings import features_path, load_features
from backend.api.workspace import active_workspace
from backend.logging import log
from backend.orchestrator.config import AgentSpec, Feature
from backend.orchestrator.config_writer import dump_features


def _resolve_repo(explicit: str | None) -> str:
    """Returns the GitHub repo to query, preferring an explicit one."""
    if explicit:
        return explicit
    entry = active_workspace()
    if entry is None or not entry.github_repo:
        raise HTTPException(
            status_code=409,
            detail="No GitHub repository known. Select a repo with a GitHub remote.",
        )
    return entry.github_repo


def _to_specs(agents: list[AgentSpecIn]) -> list[AgentSpec]:
    """Converts request agent entries to orchestrator specs, in full."""
    return [
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
        for a in agents
    ]


def draft_feature_from_issue(issue: dict[str, object], specs: list[AgentSpec]) -> Feature:
    """Turns one GitHub issue into a Feature.

    The title and body become the description, and any task-list lines
    become acceptance criteria -- a checklist is the closest thing an issue
    has to them.

    Args:
        issue: One entry from `gh issue list --json`.
        specs: Agents to attach to the drafted feature.

    Returns:
        The drafted feature.
    """
    number = issue.get("number")
    number = number if isinstance(number, int) else 0
    title = str(issue.get("title") or f"Issue {number}")
    body = str(issue.get("body") or "")
    return Feature(
        id=feature_id_for_issue(number),
        description=f"{title}\n\n{body}".strip(),
        acceptance_criteria=acceptance_criteria_from_body(body),
        agents=specs,
    )


def register_integration_routes(app: FastAPI) -> None:
    """Registers clone, secret and issue routes on `app`."""

    @app.post("/workspaces/clone", status_code=202)
    def clone_repo(request: CloneRequest) -> dict[str, object]:
        """Starts a clone and returns immediately.

        202 rather than 200: the work has been accepted, not completed.
        Progress arrives on the SSE stream as clone_started/finished/failed.
        """
        try:
            job = start_clone(request.url, request.directory_name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return job.as_dict()

    @app.get("/workspaces/clone/{clone_id}")
    def clone_status(clone_id: str) -> dict[str, object]:
        """The fallback for a browser that missed the terminal SSE event."""
        job = get_clone(clone_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Unknown clone: {clone_id}")
        return job.as_dict()

    @app.get("/secrets")
    def get_secrets() -> dict[str, object]:
        """Which services have a secret stored. Names only, never values."""
        return list_secrets()

    @app.post("/secrets")
    def post_secret(request: SecretRequest) -> dict[str, object]:
        try:
            return store_secret(
                request.service,
                token=request.token,
                ref=request.ref,
                command=request.command,
                sandbox=request.sandbox,
            )
        except RuntimeError as exc:
            # The message is built from stderr with secrets masked, never
            # from the argv -- see secrets.store_secret.
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/github/issues")
    def github_issues(query: Annotated[IssueQuery, Depends()]) -> dict[str, object]:
        repo = _resolve_repo(query.repo)
        try:
            issues = list_issues(repo, state=query.state, limit=query.limit, labels=query.labels)
        except GithubUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"repo": repo, "issues": issues}

    @app.post("/features/import-issues")
    def import_issues(request: ImportIssuesRequest) -> dict[str, object]:
        """Drafts features from issues and merges them into the feature list.

        Merges rather than replaces, and uses a deterministic `issue-<n>`
        id, so importing the same issue twice updates it in place instead
        of accumulating near-duplicates.
        """
        if active_workspace() is None:
            raise HTTPException(
                status_code=409,
                detail="No workspace selected. Choose a repository first.",
            )
        repo = _resolve_repo(request.repo)
        try:
            available = list_issues(repo, state="all", limit=200)
        except GithubUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        # gh's JSON is external input: narrow rather than assume, so a
        # malformed row is skipped instead of raising deep in a dict build.
        by_number: dict[int, dict[str, object]] = {}
        for issue in available:
            number = issue.get("number")
            if isinstance(number, int):
                by_number[number] = issue
        missing = [n for n in request.issues if n not in by_number]
        if missing:
            raise HTTPException(
                status_code=404,
                detail=f"Issues not found in {repo}: {', '.join(str(n) for n in missing)}",
            )

        specs = _to_specs(request.agents)
        try:
            existing = load_features()
        except (FileNotFoundError, KeyError):
            existing = {}

        for number in request.issues:
            feature = draft_feature_from_issue(by_number[number], specs)
            existing[feature.id] = feature

        path = features_path()
        dump_features(list(existing.values()), path)
        log.info(
            "features_imported_from_issues",
            repo=repo,
            count=len(request.issues),
            path=str(path),
        )
        return {"imported": len(request.issues), "path": str(path)}
