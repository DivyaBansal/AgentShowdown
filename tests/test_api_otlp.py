"""Tests for the OTLP/HTTP receiver and the agent-events endpoint."""

from __future__ import annotations

import gzip
import json
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from backend.api import routes_otlp
from backend.api.models import RunConfigIn
from backend.api.settings import CONFIG_ENV, load_config
from backend.main import app
from backend.orchestrator.agent_otel import DEFAULT_AGENT_OTEL_ENDPOINT
from backend.orchestrator.config import Config
from backend.orchestrator.state import StateStore
from fastapi.testclient import TestClient
from pydantic import ValidationError

if TYPE_CHECKING:
    from pathlib import Path

client = TestClient(app)

SANDBOX = "arena-f1-codex"
URL = f"/otel/{SANDBOX}/v1/logs"

CONFIG_YAML = """
repo_path: /repo
github:
  repo: owner/repo
  pat_secret_name: github
  base_branch: main
agent:
  sbx_agent: claude
  dangerously_skip_permissions: true
  model: claude-haiku-4-5
run:
  status_file: ".agent_status.json"
  poll_interval_seconds: 0
  timeout_minutes: 1
{extra}
state_db_path: "{db}"
"""


def _write_config(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG_YAML.format(db=tmp_path / "state.sqlite3", extra=extra))
    return path


@pytest.fixture(autouse=True)
def _arena(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONFIG_ENV, str(_write_config(tmp_path)))
    with StateStore(load_config().state_db_path) as store:
        store.upsert(SANDBOX, feature_id="f1", agent_id="codex", status="running")


def _attr(key: str, value: object) -> dict[str, object]:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    return {"key": key, "value": {"stringValue": value}}


def _codex_export(*records: dict[str, object]) -> dict[str, object]:
    """Shaped like a real codex-cli 0.147.0 export: name in `event.name`,
    no body, `timeUnixNano` of "0"."""
    return {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        _attr("service.name", "codex_exec"),
                        _attr("service.version", "0.147.0"),
                        _attr("host.name", "sandbox"),
                    ]
                },
                "scopeLogs": [{"scope": {}, "logRecords": list(records)}],
            }
        ]
    }


def _codex_record(name: str, **attributes: object) -> dict[str, object]:
    return {
        "timeUnixNano": "0",
        "observedTimeUnixNano": "1792658929888858527",
        "severityNumber": 9,
        "body": None,
        "eventName": "event otel/src/events/session_telemetry.rs:580",
        "attributes": [_attr("event.name", name), *(_attr(k, v) for k, v in attributes.items())],
    }


def _post(payload: object, **headers: str) -> object:
    return client.post(
        URL,
        content=json.dumps(payload),
        headers={"content-type": "application/json", **headers},
    )


def test_a_codex_export_is_stored_under_its_job() -> None:
    export = _codex_export(
        _codex_record("codex.user_prompt", prompt_length="6"),
        _codex_record(
            "codex.tool_result",
            tool_name="shell",
            success="false",
            duration_ms="40",
            **{"event.timestamp": "2026-10-22T08:48:57.603Z"},
        ),
    )

    response = _post(export)

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {}
    body = client.get(f"/jobs/{SANDBOX}/agent-events").json()
    names = [e["name"] for e in body["events"]]
    assert names == ["codex.user_prompt", "codex.tool_result"]
    tool = body["events"][1]
    assert tool["ts"] == "2026-10-22T08:48:57.603Z"
    assert tool["attributes"]["service.name"] == "codex_exec"
    assert "host.name" not in tool["attributes"]
    assert body["summary"]["tools"] == {"shell": {"calls": 1, "failures": 1, "duration_ms": 40}}


def test_a_zero_event_time_falls_back_to_the_observed_time() -> None:
    _post(_codex_export(_codex_record("codex.user_prompt")))

    (event,) = client.get(f"/jobs/{SANDBOX}/agent-events").json()["events"]
    assert event["ts"].startswith("2026-10-22T")


def test_a_claude_event_is_named_from_its_body() -> None:
    record = {
        "timeUnixNano": "1792658929888858527",
        "body": {"stringValue": "claude_code.api_request"},
        "attributes": [_attr("event.name", "api_request"), _attr("input_tokens", 12)],
    }

    _post(_codex_export(record))

    (event,) = client.get(f"/jobs/{SANDBOX}/agent-events").json()["events"]
    assert event["name"] == "claude_code.api_request"
    assert event["attributes"]["input_tokens"] == 12


def test_a_gzipped_export_is_accepted() -> None:
    body = gzip.compress(json.dumps(_codex_export(_codex_record("codex.user_prompt"))).encode())

    response = client.post(
        URL, content=body, headers={"content-type": "application/json", "content-encoding": "gzip"}
    )

    assert response.status_code == HTTPStatus.OK
    assert len(client.get(f"/jobs/{SANDBOX}/agent-events").json()["events"]) == 1


def test_an_export_for_an_unknown_job_is_rejected() -> None:
    response = client.post(
        "/otel/nobody/v1/logs",
        content=json.dumps(_codex_export(_codex_record("codex.user_prompt"))),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == HTTPStatus.NOT_FOUND


def test_protobuf_is_refused_with_415() -> None:
    """protobuf is the OTLP default; a CLI not told to use JSON lands here."""
    response = client.post(
        URL, content=b"\x0a\x00", headers={"content-type": "application/x-protobuf"}
    )

    assert response.status_code == HTTPStatus.UNSUPPORTED_MEDIA_TYPE


@pytest.mark.parametrize(
    "payload",
    [
        {"resourceLogs": "nope"},
        [1, 2],
        {"resourceLogs": [{"scopeLogs": [{"logRecords": [{"attributes": [{"value": {}}]}]}]}]},
    ],
)
def test_a_malformed_export_is_refused_with_400(payload: object) -> None:
    assert _post(payload).status_code == HTTPStatus.BAD_REQUEST


def test_bad_gzip_is_refused_with_400() -> None:
    response = client.post(
        URL,
        content=b"not gzip",
        headers={"content-type": "application/json", "content-encoding": "gzip"},
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST


def test_an_oversized_export_is_refused_with_413(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes_otlp, "MAX_EXPORT_BYTES", 64)

    response = _post(_codex_export(*[_codex_record("codex.user_prompt")] * 5))

    assert response.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE


def test_a_gzip_bomb_is_refused_with_413(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes_otlp, "MAX_EXPORT_BYTES", 1024)
    body = gzip.compress(b" " * 1_000_000)

    response = client.post(
        URL, content=body, headers={"content-type": "application/json", "content-encoding": "gzip"}
    )

    assert response.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE


def test_latest_turn_limits_events_to_the_current_launch() -> None:
    _post(_codex_export(_codex_record("codex.user_prompt")))
    with StateStore(load_config().state_db_path) as store:
        store.set_agent_launched_at(SANDBOX, "9999-01-01T00:00:00+00:00")

    every = client.get(f"/jobs/{SANDBOX}/agent-events").json()
    latest = client.get(f"/jobs/{SANDBOX}/agent-events", params={"latest_turn": True}).json()

    assert len(every["events"]) == 1
    assert latest["events"] == []


def test_agent_events_limit_is_validated() -> None:
    response = client.get(f"/jobs/{SANDBOX}/agent-events", params={"limit": 0})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_a_job_with_no_events_has_an_empty_list() -> None:
    body = client.get("/jobs/nobody/agent-events").json()

    assert body["events"] == []
    assert body["summary"]["cost_usd"] is None


# --- configuration -----------------------------------------------------------


def test_export_is_on_by_default(tmp_path: Path) -> None:
    assert Config.load(str(_write_config(tmp_path))).agent_otel_endpoint == (
        DEFAULT_AGENT_OTEL_ENDPOINT
    )


@pytest.mark.parametrize("value", ["null", '""'])
def test_export_can_be_turned_off(tmp_path: Path, value: str) -> None:
    config = Config.load(str(_write_config(tmp_path, f"  agent_otel_endpoint: {value}")))

    assert config.agent_otel_endpoint is None


@pytest.mark.parametrize(
    "endpoint", ["file:///etc/passwd", "http://h:8000/ x", 'http://h/"', "h:8000"]
)
def test_the_endpoint_setting_must_be_a_plain_http_url(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        RunConfigIn(agent_otel_endpoint=endpoint)


def test_agent_events_come_with_a_readable_timeline() -> None:
    _post(
        _codex_export(
            _codex_record("codex.user_prompt", prompt_length="6"),
            _codex_record(
                "codex.tool_result",
                tool_name="shell",
                success="true",
                duration_ms="40",
                arguments='{"command": ["ls", "-la"]}',
            ),
        )
    )

    timeline = client.get(f"/jobs/{SANDBOX}/agent-events").json()["timeline"]

    assert [(t["kind"], t["title"], t["detail"]) for t in timeline] == [
        ("prompt", "Prompt", "6 characters"),
        ("tool", "shell", "ls -la"),
    ]
    assert [t["seq"] for t in timeline] == [0, 1]
    assert timeline[1]["ok"] is True
    assert timeline[1]["duration_ms"] == 40
