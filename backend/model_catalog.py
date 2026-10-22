"""Saved model suggestions. Only an explicit refresh performs discovery."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from backend.api.paths import home_dir
from backend.orchestrator.process import CommandError, CommandTimeout, run_bounded

if TYPE_CHECKING:
    from collections.abc import Iterable

REFRESH_SECONDS = 600.0
MAX_REFRESH_HISTORY = 20
PROVIDERS = ("claude", "codex", "cursor", "opencode", "copilot", "ollama")


def now() -> str:
    """Returns a timestamp suitable for persisted catalog metadata."""
    return dt.datetime.now(dt.UTC).isoformat()


def read_catalogs() -> dict[str, dict]:
    """Reads saved suggestions without invoking a CLI or making requests."""
    try:
        payload = json.loads((home_dir() / "models.json").read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        name: entry
        for name, entry in payload.items()
        if name in PROVIDERS
        and isinstance(entry, dict)
        and isinstance(entry.get("models"), list)
        and all(isinstance(model, str) for model in entry["models"])
        and isinstance(entry.get("updated_at"), str)
        and isinstance(entry.get("source"), str)
    }


def catalog(provider: str, fallback: Iterable[str] = ()) -> dict:
    """Returns one saved catalog or explicitly labeled built-in suggestions."""
    return read_catalogs().get(
        provider, {"models": list(fallback), "source": "builtin", "updated_at": None}
    )


def find_cli(name: str) -> str | None:
    """Locates CLIs on PATH or in the standard user-local install directory."""
    found = shutil.which(name)
    local = Path.home() / ".local" / "bin" / name
    return found or (str(local) if local.is_file() and os.access(local, os.X_OK) else None)


def remaining(deadline: float) -> float:
    """Bounds every operation by the update's shared monotonic deadline."""
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise CommandTimeout("Model discovery reached the 10-minute limit")
    return seconds


def command(name: str, args: list[str], deadline: float) -> str:
    """Runs only a model listing command with no stdin and a process-group timeout."""
    executable = find_cli(name)
    if executable is None:
        raise CommandError(f"{name} is not installed; keeping previous suggestions")
    return run_bounded([executable, *args], remaining(deadline)).stdout


def parse_codex_models(payload: object) -> list[str]:
    """Accepts the CLI catalog and cache shapes, excluding hidden entries."""
    entries = payload.get("models") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise ValueError("Unrecognized Codex model catalog")
    return list(
        dict.fromkeys(
            entry["slug"].strip()
            for entry in entries
            if isinstance(entry, dict)
            and isinstance(entry.get("slug"), str)
            and entry["slug"].strip()
            and entry.get("visibility", "list") == "list"
            and not entry.get("hidden", False)
        )
    )


def codex_models(deadline: float) -> tuple[list[str], str]:
    """Uses the CLI's refreshed catalog, with its local cache as a fallback."""
    try:
        return parse_codex_models(
            json.loads(command("codex", ["debug", "models"], deadline))
        ), "cli"
    except (CommandError, ValueError):
        remaining(deadline)
        root = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
        return parse_codex_models(json.loads((root / "models_cache.json").read_text())), "cache"


def claude_models(deadline: float) -> list[str]:
    """Lists API models using an explicitly configured API key, never OAuth files."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise CommandError("Claude API key unavailable; keeping fallback suggestions")
    models: list[str] = []
    cursor = ""
    with httpx.Client() as client:
        while True:
            params = {"limit": "100"}
            if cursor:
                params["after_id"] = cursor
            response = client.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                params=params,
                timeout=min(30.0, remaining(deadline)),
            )
            response.raise_for_status()
            payload = response.json()
            models.extend(entry["id"] for entry in payload["data"] if isinstance(entry["id"], str))
            if not payload.get("has_more"):
                return models
            next_cursor = payload.get("last_id")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                raise ValueError("Invalid Claude model pagination")
            cursor = next_cursor


def parse_cli_models(provider: str, stdout: str) -> list[str]:
    """Parses only model identifiers from listing output, ignoring headings and ANSI."""
    clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", stdout)
    if provider == "ollama" and not clean.lstrip().startswith("NAME"):
        raise ValueError("Unrecognized Ollama model listing")
    if provider == "copilot":
        section = re.search(r"--model\b([^\n]*(?:\n(?!\s*-)[^\n]*)*)", clean)
        choices = re.search(r"[\[(]choices:\s*([^\]\)]+)", section[1]) if section else None
        if choices is None:
            raise ValueError("Copilot CLI does not advertise model choices; enter a model manually")
        return re.findall(r'["\']([^"\']+)["\']', choices[1])
    models = []
    for line in clean.splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        model_id = parts[0]
        if provider == "ollama":
            if model_id != "NAME" and len(parts) >= 3:  # noqa: PLR2004 -- NAME ID SIZE columns
                models.append(model_id)
        elif provider == "opencode":
            if re.fullmatch(r"[\w.-]+/[^\s]+", model_id):
                models.append(model_id)
        elif re.fullmatch(r"[\w][\w.:-]*", model_id) and (
            "-" in model_id or model_id in ("auto", "default")
        ):
            models.append(model_id)
    return list(dict.fromkeys(models))


def discover(provider: str, deadline: float) -> dict:
    """Discovers one provider; failure never replaces a previously saved catalog."""
    source = "cli"
    if provider == "codex":
        models, source = codex_models(deadline)
    elif provider == "claude":
        models, source = claude_models(deadline), "api"
    else:
        cli, args = {
            "cursor": ("cursor-agent", ["--list-models"]),
            "opencode": ("opencode", ["models", "--refresh"]),
            "copilot": ("copilot", ["--help"]),
            "ollama": ("ollama", ["list"]),
        }[provider]
        if provider == "cursor" and find_cli(cli) is None:
            cli, args = "agent", ["models"]
        models = parse_cli_models(provider, command(cli, args, deadline))
        if provider == "copilot":
            source = "cli_suggestions"
    remaining(deadline)
    if not models and provider != "ollama":
        raise ValueError("No models returned; keeping previous suggestions")
    return {"models": list(dict.fromkeys(models)), "source": source, "updated_at": now()}


@dataclass
class Refresh:
    """Progress is copied under the manager lock before being exposed to HTTP clients."""

    refresh_id: str
    started_at: str
    state: str = "running"
    finished_at: str | None = None
    results: dict[str, dict] = field(default_factory=dict)
    error: str | None = None


class CatalogUpdates:
    """One update at a time, independent of browser connections and workspace selection."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.jobs: dict[str, Refresh] = {}

    def start(self) -> dict:
        """Starts a background update or returns the already running update."""
        with self.lock:
            for job in self.jobs.values():
                if job.state == "running":
                    return asdict(job)
            job = Refresh(uuid.uuid4().hex, now())
            # Keep recent completed IDs so a slower browser can still collect
            # its result after another browser starts the next update.
            if len(self.jobs) >= MAX_REFRESH_HISTORY:
                self.jobs.pop(next(iter(self.jobs)))
            self.jobs[job.refresh_id] = job
            deadline = time.monotonic() + REFRESH_SECONDS
            threading.Thread(
                target=self._execute,
                args=(job, home_dir(), deadline),
                daemon=True,
                name="model-refresh",
            ).start()
            return asdict(job)

    def status(self, refresh_id: str | None = None) -> dict | None:
        """Returns detached progress, including the latest job when no ID is supplied."""
        with self.lock:
            job = (
                self.jobs.get(refresh_id)
                if refresh_id
                else next(reversed(self.jobs.values()), None)
            )
            return asdict(job) if job else None

    def _execute(self, job: Refresh, root: Path, deadline: float) -> None:
        pool = ThreadPoolExecutor(max_workers=len(PROVIDERS), thread_name_prefix="model-discovery")
        futures = {pool.submit(discover, provider, deadline): provider for provider in PROVIDERS}
        saved: dict[str, dict] = {}
        try:
            for future in as_completed(futures, timeout=remaining(deadline)):
                provider = futures[future]
                try:
                    entry = future.result()
                    saved[provider] = entry
                    result = {
                        "status": "updated",
                        "count": len(entry["models"]),
                        "source": entry["source"],
                        "detail": "Model suggestions updated",
                    }
                except Exception as exc:
                    # Never return raw command output or HTTP errors containing credentials.
                    result = {
                        "status": "timed_out" if isinstance(exc, CommandTimeout) else "failed",
                        "detail": self._failure(provider, exc),
                    }
                with self.lock:
                    job.results[provider] = result
        except (TimeoutError, CommandTimeout):
            pass
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
            with self.lock:
                for provider in PROVIDERS:
                    job.results.setdefault(
                        provider,
                        {
                            "status": "timed_out",
                            "detail": "Reached the 10-minute update limit",
                        },
                    )
                try:
                    if saved:
                        self._save(root, saved)
                except OSError:
                    job.error = "Could not save model catalogs; previous catalogs were retained"
                    for provider in saved:
                        job.results[provider] = {
                            "status": "failed",
                            "detail": (
                                "Could not save discovered models; previous suggestions retained"
                            ),
                        }
                job.finished_at = now()
                job.state = "finished"

    @staticmethod
    def _failure(provider: str, exc: Exception) -> str:
        if isinstance(exc, CommandTimeout):
            return "Reached the 10-minute update limit"
        if provider == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
            return "No Claude API key; keeping previous or built-in suggestions"
        return "Discovery unavailable; keeping previous or built-in suggestions"

    @staticmethod
    def _save(root: Path, entries: dict[str, dict]) -> None:
        # root is captured when the update starts, never taken from a request body.
        path = root / "models.json"
        try:
            previous = json.loads(path.read_text())
        except (OSError, ValueError):
            previous = {}
        if not isinstance(previous, dict):
            previous = {}
        previous.update(entries)
        root.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(previous, indent=2) + "\n")
        os.replace(temporary, path)


updates = CatalogUpdates()
