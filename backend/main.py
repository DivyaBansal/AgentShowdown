"""FastAPI app: serves the arena HTTP API and the built frontend."""

from __future__ import annotations

from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.api.routes import register_routes
from backend.api.runner import recover_in_flight_jobs, stop_sampler
from backend.api.settings import load_config
from backend.logging import configure_logging

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

configure_logging()

# structlog attaches the active OpenTelemetry trace/span id to every log line
# (see logging.py). Span creation itself is not wired: no instrumentation
# packages are installed and nothing starts spans, so those fields stay empty
# until an OTel SDK + instrumentors are added. `opentelemetry-api` is kept as a
# dependency so that wiring stays an install, not a code change.


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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


register_routes(app)

# Serve the built React app if present (production/container image). In
# local dev, the frontend runs via `npm run dev` on its own port and
# proxies /api → this server instead, so this mount is skipped when the
# build directory doesn't exist. Mounted last so it never shadows an API
# route.
_frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")
