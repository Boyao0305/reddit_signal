# syntax=docker/dockerfile:1
# One image recipe, two targets:
#   api    — public read-only API + Seedcast site (slim: no ML stack)
#   worker — pipelines and the operator CLI (full ML stack, CPU-only torch)
# Dependencies come from uv.lock, so the image runs exactly the tested versions.

FROM node:20-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
# psql runs the SQL migrations.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home --home-dir /home/reddit_signal reddit_signal
WORKDIR /app

FROM base AS api
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY db ./db
RUN uv sync --frozen --no-dev --no-editable
COPY --from=frontend /src/frontend/dist /app/frontend/dist
ENV REDDIT_SIGNAL_STATIC_DIR=/app/frontend/dist
USER reddit_signal
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)"
CMD ["uvicorn", "reddit_signal.api.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]

FROM base AS worker-build
# Some ML packages compile C extensions when no wheel matches.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra worker --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --extra worker --no-editable

FROM base AS worker
COPY --from=worker-build /opt/venv /opt/venv
COPY src ./src
COPY db ./db
# The sentence-transformers model is downloaded on first use; keep it on a volume.
ENV HF_HOME=/var/cache/reddit_signal/huggingface
RUN mkdir -p /var/cache/reddit_signal && chown reddit_signal /var/cache/reddit_signal
USER reddit_signal
CMD ["python", "-m", "reddit_signal.worker"]
