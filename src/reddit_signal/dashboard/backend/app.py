"""Dashboard FastAPI app: read-only analytics endpoints."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from reddit_signal import runtime_settings
from reddit_signal.dashboard.backend.routers import dashboard_data, public


log = logging.getLogger("dashboard_v2")
logging.basicConfig(level=logging.INFO)
runtime_settings.apply_settings_to_environment()


def create_app() -> FastAPI:
    app = FastAPI(title="reddit_signal dashboard")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True}

    app.include_router(dashboard_data.router)
    app.include_router(public.router)
    return app


app = create_app()

__all__ = ["app"]
