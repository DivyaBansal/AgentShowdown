# syntax=docker/dockerfile:1

# ---------- Stage 1: build the React frontend ----------
FROM node:22-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: build the Python wheel ----------
FROM python:3.12-slim AS python-build
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY backend/ backend/
RUN uv sync --frozen --no-dev

# ---------- Stage 3: runtime ----------
FROM python:3.12-slim AS runtime

# Run as a non-root user — don't ship a container that runs as root.
RUN groupadd --system app && useradd --system --gid app app

WORKDIR /app
COPY --from=python-build /app/.venv /app/.venv
COPY --from=python-build /app/backend /app/backend
COPY pyproject.toml uv.lock ./
COPY --from=frontend-build /frontend/dist /app/frontend/dist

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER app
EXPOSE 8000

# Serves the arena API and the built frontend from one port. Put a reverse
# proxy in front that maps /api/* -> /* if the SPA is served from this image
# (see README "Status & limitations").
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
