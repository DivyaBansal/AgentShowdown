"""Minimal FastAPI app demonstrating the observability + validation baseline."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from agentshowdown.logging import configure_logging

configure_logging()

app = FastAPI(title="agentshowdown")

# Auto-instrumentation (traces for FastAPI, requests, DB clients, etc.) is
# applied at process startup via:
#   uv run opentelemetry-instrument uvicorn agentshowdown.main:app
# rather than in-code, so instrumentation stays out of business logic.


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Serve the built React app if present (production/container image). In
# local dev, the frontend runs via `npm run dev` on its own port and
# proxies /api → this server instead, so this mount is skipped when the
# build directory doesn't exist.
_frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")
