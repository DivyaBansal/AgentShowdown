"""Explicit model catalog updates. Reads never perform discovery."""

from fastapi import FastAPI, HTTPException

from backend.api.routes_setup import _refuse_bundled_preset, _require_workspace
from backend.api.settings import config_path
from backend.model_catalog import read_catalogs, updates


def register_model_routes(app: FastAPI) -> None:
    """Registers persisted catalog metadata and background refresh endpoints."""

    @app.get("/models")
    def get_models() -> dict[str, object]:
        return {"catalogs": read_catalogs(), "refresh": updates.status()}

    @app.post("/models/refresh", status_code=202)
    def refresh_models() -> dict:
        _require_workspace()
        _refuse_bundled_preset(config_path())
        return updates.start()

    @app.get("/models/refresh/{refresh_id}")
    def refresh_status(refresh_id: str) -> dict:
        result = updates.status(refresh_id)
        if result is None:
            raise HTTPException(
                status_code=404, detail="Model update not found; it may have restarted"
            )
        return result
