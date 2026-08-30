"""Minimal FastAPI app demonstrating the observability + validation baseline."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agentshowdown.logging import configure_logging, log

configure_logging()

app = FastAPI(title="agentshowdown")

# Orders at or above this quantity get a distinct structured log event so
# dashboards can alert on "large order rate" as a signal.
LARGE_ORDER_QUANTITY = 500

# Auto-instrumentation (traces for FastAPI, requests, DB clients, etc.) is
# applied at process startup via:
#   uv run opentelemetry-instrument uvicorn agentshowdown.main:app
# rather than in-code, so instrumentation stays out of business logic.


class Order(BaseModel):
    """Pydantic v2 model — input validation happens at the boundary, not
    scattered through business logic."""

    item_id: str = Field(min_length=1, max_length=64)
    quantity: int = Field(gt=0, le=1000)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/orders")
def create_order(order: Order) -> dict[str, str]:
    log.info("order_received", item_id=order.item_id, quantity=order.quantity)

    if order.quantity > LARGE_ORDER_QUANTITY:
        # Example of a RED-metric-worthy business event: log it distinctly
        # so dashboards can alert on "large order rate" as a signal.
        log.warning("large_order_flagged", item_id=order.item_id, quantity=order.quantity)

    if order.item_id == "unknown":
        raise HTTPException(status_code=404, detail="item not found")

    return {"status": "accepted", "item_id": order.item_id}


# Serve the built React app if present (production/container image). In
# local dev, the frontend runs via `npm run dev` on its own port and
# proxies /api → this server instead, so this mount is skipped when the
# build directory doesn't exist.
_frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")
