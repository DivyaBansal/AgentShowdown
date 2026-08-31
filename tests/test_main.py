from __future__ import annotations

from http import HTTPStatus

from fastapi.testclient import TestClient

from agentshowdown.main import app

client = TestClient(app)


def test_health() -> None:
    resp = client.get("/health")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {"status": "ok"}
