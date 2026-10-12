"""The single PostgreSQL connection pool shared by every module in a process.

Replaces four per-package pools that were each a non-thread-safe ``SimpleConnectionPool`` with a
hard-coded fallback DSN. This pool is thread-safe and *blocks* when exhausted instead of raising, so
the threaded extraction stage waits for a connection rather than failing.

Two usage styles are supported because both exist in the codebase:

* ``conn = get_conn(); try: ... finally: put_conn(conn)``
* ``with connection() as conn: ...`` (rolls back any open transaction before returning it)
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

from reddit_signal import config

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_pool: ThreadedConnectionPool | None = None
_slots: threading.BoundedSemaphore | None = None


def get_pool() -> ThreadedConnectionPool:
    global _pool, _slots
    if _pool is None:
        with _lock:
            if _pool is None:
                cfg = config.get()
                dsn = cfg.require_database_url()
                _slots = threading.BoundedSemaphore(cfg.db_pool_max)
                _pool = ThreadedConnectionPool(minconn=1, maxconn=cfg.db_pool_max, dsn=dsn)
                logger.info(
                    "DB pool created (max=%d, %s)", cfg.db_pool_max, config.redact_dsn(dsn)
                )
    return _pool


def get_conn():
    """Borrow a connection; blocks while all ``db_pool_max`` connections are in use."""
    pool = get_pool()
    assert _slots is not None
    _slots.acquire()
    try:
        return pool.getconn()
    except BaseException:
        _slots.release()
        raise


def put_conn(conn) -> None:
    """Return a connection borrowed with :func:`get_conn`."""
    pool = get_pool()
    try:
        pool.putconn(conn)
    finally:
        assert _slots is not None
        _slots.release()


@contextmanager
def connection() -> Iterator:
    """Borrow a connection for a block; any transaction left open is rolled back."""
    conn = get_conn()
    try:
        yield conn
    finally:
        try:
            conn.rollback()
        except Exception:
            pass
        put_conn(conn)


def connect():
    """Open a dedicated, unpooled connection (long-running jobs, LISTEN, explicit lifecycles)."""
    return psycopg2.connect(config.get().require_database_url())


def close_pool() -> None:
    """Close all pooled connections (tests, orderly shutdown)."""
    global _pool, _slots
    with _lock:
        if _pool is not None:
            _pool.closeall()
        _pool = None
        _slots = None


@contextmanager
def guc_transaction(
    cluster_run_id: int | None = None,
    viability_run_id: int | None = None,
    audience_classifier_version: str | None = None,
):
    """Yield ``(conn, cursor)`` with the ranking-view GUCs ``SET LOCAL`` for one transaction.

    Commits on success, rolls back on error. The GUCs parameterise the Path C views.
    """
    with connection() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cur.execute("BEGIN")
            if cluster_run_id is not None:
                cur.execute("SET LOCAL llm_analyse.cluster_run_id = %s", (str(cluster_run_id),))
            if viability_run_id is not None:
                cur.execute("SET LOCAL llm_analyse.viability_run_id = %s", (str(viability_run_id),))
            if audience_classifier_version is not None:
                cur.execute(
                    "SET LOCAL llm_analyse.audience_classifier_version = %s",
                    (audience_classifier_version,),
                )
            yield conn, cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
