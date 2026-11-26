# syntax=docker/dockerfile:1

# Base images are pinned by digest (multi-arch index) so a build is
# reproducible and a re-pushed tag can't silently change what ships.
# Dependabot (.github/dependabot.yml) bumps these digests.

# ---------- Stage 1: build the React frontend ----------
FROM node:22-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: build the Python wheel ----------
FROM python:3.12-slim@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b AS python-build
# Pinned uv binary from its official image instead of an unpinned `pip install uv`.
COPY --from=ghcr.io/astral-sh/uv:0.5.31@sha256:7bff3c3776ec467fc1437960f2c469d8beb30f536a6465a3350c647ccd260ec2 /uv /bin/uv
# Use the base image's Python; never download a managed interpreter (it
# would land outside .venv and not be copied to the runtime stage anyway).
ENV UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY backend/ backend/
RUN uv sync --frozen --no-dev

# ---------- Stage 3: runtime ----------
FROM python:3.12-slim@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b AS runtime

# Run as a non-root user — don't ship a container that runs as root.
# Fixed numeric IDs so orchestrators can enforce runAsNonRoot / runAsUser.
# The app's venv has no pip; drop the system pip too — nothing needs it at
# runtime and it is a common source of scanner findings.
# apt-get upgrade applies Debian security fixes released since the base image
# was published, so CI's "fixable only" Trivy gate passes without waiting for
# the next digest bump. Trade-off: two builds of the same commit can differ
# in OS patch versions.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && python -m pip uninstall --yes pip \
    && apt-get update \
    && apt-get upgrade --yes \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Files stay root-owned: the app user can read but not modify its own code.
COPY --from=python-build /app/.venv /app/.venv
COPY --from=python-build /app/backend /app/backend
COPY pyproject.toml uv.lock ./
COPY --from=frontend-build /frontend/dist /app/frontend/dist

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER 10001:10001
EXPOSE 8000

# Serves the arena API and the built frontend from one port. Put a reverse
# proxy in front that maps /api/* -> /* if the SPA is served from this image
# (see README "Status & limitations").
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
