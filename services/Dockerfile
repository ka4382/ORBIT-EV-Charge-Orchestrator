# Multi-stage image shared by all Python services. `command:` in compose selects the module.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/
WORKDIR /app

FROM base AS builder
COPY services/pyproject.toml services/uv.lock* /app/services/
COPY services/common/pyproject.toml /app/services/common/
COPY services/ingest/pyproject.toml /app/services/ingest/
COPY services/state/pyproject.toml /app/services/state/
COPY services/alert/pyproject.toml /app/services/alert/
COPY services/archiver/pyproject.toml /app/services/archiver/
COPY services/api/pyproject.toml /app/services/api/
COPY services/planner/pyproject.toml /app/services/planner/
COPY services/ml/pyproject.toml /app/services/ml/
WORKDIR /app/services
RUN --mount=type=cache,target=/root/.cache/uv uv sync --all-packages --no-dev --no-install-workspace
COPY services /app/services
COPY contracts /app/contracts
COPY db /app/db
RUN --mount=type=cache,target=/root/.cache/uv uv sync --all-packages --no-dev

FROM base AS runtime
RUN useradd -r -u 10001 orbit
COPY --from=builder --chown=orbit:orbit /app /app
ENV PATH="/app/services/.venv/bin:$PATH" PYTHONPATH="/app/services"
USER orbit
WORKDIR /app
