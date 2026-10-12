"""API container entry point: ``uvicorn reddit_signal.api.main:app``.

Serves the public read-only API (``/api/public/*``) and, if ``REDDIT_SIGNAL_STATIC_DIR`` points at the
frontend build, the Seedcast site (``/`` and ``/assets``). The internal analytics endpoints
(``/api/v2/*``, which include viability scores) are mounted only with ``REDDIT_SIGNAL_ANALYTICS_API=1``,
for local use over an SSH tunnel (ADR 0003).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from reddit_signal import config
from reddit_signal.dashboard.backend.routers import public

logger = logging.getLogger("reddit_signal.api")


def create_app(cfg: config.Config) -> FastAPI:
    config.configure(cfg)
    app = FastAPI(title="reddit_signal", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True}

    app.include_router(public.router)
    if cfg.analytics_api:
        from reddit_signal.dashboard.backend.routers import dashboard_data

        app.include_router(dashboard_data.router)
        logger.warning("internal analytics API (/api/v2) is enabled")

    if cfg.static_dir:
        root = Path(cfg.static_dir)
        page = root / "seedcast.html"
        if not page.is_file():
            raise FileNotFoundError(f"REDDIT_SIGNAL_STATIC_DIR has no seedcast.html: {root}")
        app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        def seedcast() -> FileResponse:
            return FileResponse(page, headers={"Cache-Control": "no-cache"})

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
app = create_app(config.Config.from_env())
