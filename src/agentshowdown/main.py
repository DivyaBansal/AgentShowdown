"""FastAPI app: the observability + validation baseline, plus the arena API."""

from __future__ import annotations

from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agentshowdown.api.routes import register_routes
from agentshowdown.api.runner import recover_in_flight_jobs, stop_sampler
from agentshowdown.api.settings import load_config
from agentshowdown.logging import configure_logging, log

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

configure_logging()

# Auto-instrumentation (traces for FastAPI, requests, DB clients, etc.) is
# applied at process startup via:
#   uv run opentelemetry-instrument uvicorn agentshowdown.main:app
# rather than in-code, so instrumentation stays out of business logic.


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Reconciles anything a previous process left mid-flight.

    Runs outlive the request that started them and survive a browser
    disconnect, but not a process exit -- so on startup every job still
    marked running is checked against the live sandbox listing and marked
    lost when its sandbox is gone. Without this it would sit "running"
    forever in the UI. `uvicorn --reload` restarts on every code change, so
    this path runs constantly in development.

    A missing or malformed config must not stop the app booting: the UI
    still has to come up in order to *show* that preflight failed.
    """
    with suppress(FileNotFoundError, KeyError):
        recover_in_flight_jobs(load_config())
    try:
        yield
    finally:
        stop_sampler()


app = FastAPI(title="agentshowdown", lifespan=lifespan)

# Orders at or above this quantity get a distinct structured log event so
# dashboards can alert on "large order rate" as a signal.
LARGE_ORDER_QUANTITY = 500


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
        # so dashboards can alert on "large order rate" as a signal. This is
        # the pattern CLAUDE.md points new endpoints at.
        log.warning("large_order_flagged", item_id=order.item_id, quantity=order.quantity)

    if order.item_id == "unknown":
        raise HTTPException(status_code=404, detail="item not found")

    return {"status": "accepted", "item_id": order.item_id}


register_routes(app)

# Serve the built React app if present (production/container image). In
# local dev, the frontend runs via `npm run dev` on its own port and
# proxies /api → this server instead, so this mount is skipped when the
# build directory doesn't exist. Mounted last so it never shadows an API
# route.
_frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")
