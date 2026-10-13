"""API container entry point: ``uvicorn reddit_signal.api.main:app``.

Serves the public read-only API (``/api/public/*``) and, if ``REDDIT_SIGNAL_STATIC_DIR`` points at the
frontend build, the Seedcast site (``/`` and ``/assets``).
"""
from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from reddit_signal import config
from reddit_signal.dashboard.backend.routers import public
from reddit_signal.dashboard.backend.services import public as public_service


def create_app(cfg: config.Config, *, warm_cache: bool = False) -> FastAPI:
    config.configure(cfg)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if warm_cache:  # build the ~10 s snapshot before the first visitor asks for it
            threading.Thread(target=public_service.warm, name="warm-snapshot", daemon=True).start()
        yield

    app = FastAPI(title="reddit_signal", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True}

    app.include_router(public.router)

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
app = create_app(config.Config.from_env(), warm_cache=True)
