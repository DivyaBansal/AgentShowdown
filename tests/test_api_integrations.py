"""Tests for cloning, secrets, and GitHub issue import."""

from __future__ import annotations

import subprocess
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING

from backend.api import clone as clone_module
from backend.api import issues as issues_module
from backend.api import routes_integrations
from backend.api import secrets as secrets_module
from backend.api.issues import acceptance_criteria_from_body
from backend.api.secrets import parse_secret_table
from backend.api.workspace import select
from backend.main import app
from backend.orchestrator.config import Feature
from backend.orchestrator.process import run
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    import pytest

client = TestClient(app)

TOKEN = "sk-live-do-not-leak-this"  # noqa: S105 -- fake value; the test is that it never leaks

SBX_LS_OUTPUT = """SCOPE      TYPE      NAME         SECRET
(global)   service   anthropic    (stored)
(global)   service   github       (stored)
(global)   service   openai       (oauth configured)
"""


def _git_repo(path: Path, *, origin: str | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    run(["git", "-C", str(path), "init", "-q"])
    if origin is not None:
        run(["git", "-C", str(path), "remote", "add", "origin", origin])
    return path


def _completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# --- clone ------------------------------------------------------------------


def test_clone_rejects_a_dangerous_url_without_running_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected URL must never reach a subprocess at all."""
    called: list[object] = []
    monkeypatch.setattr(clone_module, "run", lambda *a, **kw: called.append(a))

    response = client.post("/workspaces/clone", json={"url": "ext::sh -c 'id'"})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT
    assert called == []


def test_clone_accepts_a_github_url_and_returns_202(monkeypatch: pytest.MonkeyPatch) -> None:
    """202: the work is accepted, not finished -- progress arrives over SSE."""
    monkeypatch.setattr(clone_module._clone_pool, "submit", lambda *a, **kw: None)

    response = client.post("/workspaces/clone", json={"url": "https://github.com/owner/repo.git"})

    assert response.status_code == HTTPStatus.ACCEPTED
    body = response.json()
    assert body["state"] == "running"
    assert body["target_path"].endswith("owner-repo")


def test_clone_worker_uses_safe_git_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """The call site is the real defense: no ext/file helpers, and a `--`."""
    seen: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs["options"].env or {}
        return _completed(returncode=1, stderr="nope")

    monkeypatch.setattr(clone_module, "run", fake_run)
    job = clone_module.CloneJob(
        clone_id="c1", url="https://github.com/o/r.git", target_path="/tmp/x/r"
    )

    clone_module._do_clone(job)

    cmd = seen["cmd"]
    assert "protocol.ext.allow=never" in cmd
    assert "protocol.file.allow=never" in cmd
    # Everything after `--` cannot be parsed as a flag.
    assert cmd[cmd.index("--") + 1] == "https://github.com/o/r.git"
    # A shallow clone would silently zero every diff metric later.
    assert "--depth" not in cmd
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert job.state == "failed"


def test_clone_status_404s_for_an_unknown_id() -> None:
    assert client.get("/workspaces/clone/nope").status_code == HTTPStatus.NOT_FOUND


# --- secrets ----------------------------------------------------------------


def test_parses_the_secret_table() -> None:
    rows = parse_secret_table(SBX_LS_OUTPUT)

    assert [r["name"] for r in rows] == ["anthropic", "github", "openai"]
    assert rows[2]["state"] == "(oauth configured)"


def test_secret_table_skips_malformed_rows() -> None:
    """A future column change must not take the settings page down."""
    rows = parse_secret_table(SBX_LS_OUTPUT + "garbage line\n")

    assert len(rows) == 3


def test_store_secret_with_a_command_builds_the_right_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(secrets_module, "sbx_available", lambda: True)
    monkeypatch.setattr(
        secrets_module,
        "run",
        lambda cmd, **kw: seen.update(cmd=cmd, redact=kw.get("redact")) or _completed(),
    )

    response = client.post("/secrets", json={"service": "github", "command": "gh auth token"})

    assert response.status_code == HTTPStatus.OK
    assert seen["cmd"] == ["sbx", "secret", "set", "github", "--command", "gh auth token"]


def test_a_stored_token_never_reaches_the_logs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """process.run logs the argv and CommandError embeds it -- both must mask."""
    monkeypatch.setattr(secrets_module, "sbx_available", lambda: True)

    response = client.post("/secrets", json={"service": "anthropic", "token": TOKEN})

    out = capsys.readouterr().out
    assert TOKEN not in out
    assert TOKEN not in response.text


def test_secret_requires_exactly_one_source() -> None:
    both = client.post("/secrets", json={"service": "github", "token": "x", "ref": "op://v/i"})
    neither = client.post("/secrets", json={"service": "github"})

    assert both.status_code == HTTPStatus.UNPROCESSABLE_CONTENT
    assert neither.status_code == HTTPStatus.UNPROCESSABLE_CONTENT


def test_secret_rejects_an_unknown_service() -> None:
    response = client.post("/secrets", json={"service": "not-a-service", "token": "x"})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT


def test_list_secrets_reports_unavailable_without_sbx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secrets_module, "sbx_available", lambda: False)

    body = client.get("/secrets").json()

    assert body == {"available": False, "secrets": []}


# --- issues -----------------------------------------------------------------


def test_lifts_acceptance_criteria_from_a_task_list() -> None:
    body = "Intro text\n\n- [ ] first thing\n- [x] second thing\n* [ ] third\n\nTrailing"

    assert acceptance_criteria_from_body(body) == ["first thing", "second thing", "third"]


def test_an_issue_without_a_checklist_gets_no_criteria() -> None:
    assert acceptance_criteria_from_body("just prose") == []


def test_drafting_a_feature_from_an_issue() -> None:
    feature = routes_integrations.draft_feature_from_issue(
        {"number": 42, "title": "Add verbs", "body": "Do it\n\n- [ ] works"}, []
    )

    assert feature.id == "issue-42"
    assert feature.description.startswith("Add verbs")
    assert feature.acceptance_criteria == ["works"]


def test_github_issues_503s_without_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(issues_module, "gh_available", lambda: False)

    response = client.get("/github/issues", params={"repo": "owner/repo"})

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE


def test_import_issues_writes_features(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _git_repo(tmp_path / "app", origin="https://github.com/owner/app.git")
    select(repo)
    monkeypatch.setattr(
        routes_integrations,
        "list_issues",
        lambda *a, **kw: [
            {"number": 7, "title": "Add verbs", "body": "- [ ] works"},
            {"number": 9, "title": "Other", "body": ""},
        ],
    )

    response = client.post("/features/import-issues", json={"issues": [7]})

    assert response.status_code == HTTPStatus.OK
    saved = Feature.load_all(str(repo / ".agentshowdown" / "features.yaml"))
    assert saved["issue-7"].acceptance_criteria == ["works"]
    assert "issue-9" not in saved


def test_import_issues_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A deterministic id means re-importing updates rather than duplicates."""
    repo = _git_repo(tmp_path / "app", origin="https://github.com/owner/app.git")
    select(repo)
    monkeypatch.setattr(
        routes_integrations,
        "list_issues",
        lambda *a, **kw: [{"number": 7, "title": "Add verbs", "body": ""}],
    )

    client.post("/features/import-issues", json={"issues": [7]})
    client.post("/features/import-issues", json={"issues": [7]})

    saved = Feature.load_all(str(repo / ".agentshowdown" / "features.yaml"))
    assert list(saved) == ["issue-7"]


def test_import_issues_404s_for_an_unknown_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    select(_git_repo(tmp_path / "app", origin="https://github.com/owner/app.git"))
    monkeypatch.setattr(routes_integrations, "list_issues", lambda *a, **kw: [])

    response = client.post("/features/import-issues", json={"issues": [123]})

    assert response.status_code == HTTPStatus.NOT_FOUND


def test_import_issues_rejects_an_empty_list(tmp_path: Path) -> None:
    select(_git_repo(tmp_path / "app"))

    response = client.post("/features/import-issues", json={"issues": []})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT
