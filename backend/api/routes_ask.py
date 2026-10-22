"""Routes for quick questions: one prompt, several models, no repo."""

from fastapi import FastAPI, HTTPException

from backend.api.ask import get_ask, list_providers, start_ask, unconfigured
from backend.api.models import AskRequest
from backend.logging import log


def register_ask_routes(app: FastAPI) -> None:
    """Registers the quick-question routes on `app`."""

    @app.get("/ask/providers")
    def get_ask_providers() -> dict[str, object]:
        """Which providers this host can use, with model suggestions."""
        return {"providers": list_providers()}

    @app.post("/asks", status_code=202)
    def create_ask(request: AskRequest) -> dict[str, str]:
        """Starts every contender and returns at once; poll `GET /asks/{id}`."""
        missing = unconfigured(request)
        if missing:
            log.warning("ask_rejected_unconfigured", providers=missing)
            raise HTTPException(
                status_code=422,
                detail=f"Not set up on this machine: {', '.join(missing)}",
            )
        return {"ask_id": start_ask(request)}

    @app.get("/asks/{ask_id}")
    def read_ask(ask_id: str) -> dict[str, object]:
        ask = get_ask(ask_id)
        if ask is None:
            raise HTTPException(status_code=404, detail=f"Unknown ask: {ask_id}")
        return ask
