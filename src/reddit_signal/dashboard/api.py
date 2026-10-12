"""Compatibility entrypoint: ``uvicorn reddit_signal.dashboard.api:app``."""
from __future__ import annotations

from reddit_signal.dashboard.backend.app import app, create_app

__all__ = ["app", "create_app"]
