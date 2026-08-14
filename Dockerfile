# syntax=docker/dockerfile:1

# ---- builder ----------------------------------------------------------------
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependency layer, cached until pyproject/uv.lock change.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---- runtime ----------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# libpq for asyncpg, curl for the healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 hiresignal
WORKDIR /app

COPY --from=builder --chown=hiresignal:hiresignal /app /app

# Set explicitly rather than relying on the host's file mode: a checkout on
# Windows carries no execute bit, and the container would start with
# "permission denied" on the entrypoint.
RUN chmod +x /app/docker-entrypoint.sh

USER hiresignal

# Render, Fly and Cloud Run all assign the port at runtime via $PORT; 8000 is
# only the local default. EXPOSE is documentation — the platform routes to
# whatever it set — but it keeps `docker run -P` working.
ENV PORT=8000
EXPOSE 8000

# Shell form so ${PORT} expands. The exec form would pass the literal string.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

CMD ["/app/docker-entrypoint.sh"]
