from __future__ import annotations

from http import HTTPStatus

from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st

from agentshowdown.main import app

client = TestClient(app)


def test_health() -> None:
    resp = client.get("/health")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {"status": "ok"}


def test_create_order_accepted() -> None:
    resp = client.post("/orders", json={"item_id": "sku-123", "quantity": 2})
    assert resp.status_code == HTTPStatus.OK
    assert resp.json()["status"] == "accepted"


def test_create_order_unknown_item() -> None:
    resp = client.post("/orders", json={"item_id": "unknown", "quantity": 1})
    assert resp.status_code == HTTPStatus.NOT_FOUND


@given(quantity=st.integers(min_value=1, max_value=1000))
def test_create_order_accepts_any_valid_quantity(quantity: int) -> None:
    """Property-based test: for any valid quantity in range, the endpoint
    should accept it — catches edge cases (boundary values, off-by-ones)
    that example-based tests tend to miss."""
    resp = client.post("/orders", json={"item_id": "sku-1", "quantity": quantity})
    assert resp.status_code == HTTPStatus.OK


@given(quantity=st.integers(max_value=0))
def test_create_order_rejects_non_positive_quantity(quantity: int) -> None:
    resp = client.post("/orders", json={"item_id": "sku-1", "quantity": quantity})
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
