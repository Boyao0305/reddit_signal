"""Dashboard DB helpers, delegating to the single shared pool in :mod:`reddit_signal.db`.

``get_conn`` here is a context manager (``with get_conn() as conn``), as the dashboard repositories
expect; it is :func:`reddit_signal.db.connection` under its historical name.
"""
from __future__ import annotations

from reddit_signal.db import connection as get_conn
from reddit_signal.db import get_pool, guc_transaction

__all__ = ["get_conn", "get_pool", "guc_transaction"]
