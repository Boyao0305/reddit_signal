"""
DB layer for signal_events (Layer 2).

Reuses the connection pool from reddit_signal.data_ingestion.db.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Iterable, Optional

import psycopg2.extras

from reddit_signal.data_ingestion.db import get_conn, put_conn
from reddit_signal.sources.base import EventBundle, SignalEvent

logger = logging.getLogger(__name__)


# ─── upserts ──────────────────────────────────────────────────────────────────

_UPSERT_SQL = """
INSERT INTO signal_events (
    source, source_table, source_row_id,
    event_type, event_time, surface, author,
    parent_event_id, title, text, engagement_score, raw_meta
) VALUES (
    %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s, %s
)
ON CONFLICT (source, source_table, source_row_id) DO UPDATE SET
    event_type       = EXCLUDED.event_type,
    event_time       = EXCLUDED.event_time,
    surface          = EXCLUDED.surface,
    author           = EXCLUDED.author,
    parent_event_id  = EXCLUDED.parent_event_id,
    title            = EXCLUDED.title,
    text             = EXCLUDED.text,
    engagement_score = EXCLUDED.engagement_score,
    raw_meta         = EXCLUDED.raw_meta
RETURNING id;
"""


def upsert_events(events: Iterable[SignalEvent]) -> int:
    """Insert or update a batch of events. Returns count written.

    Each event's ``id`` attribute is populated in place.
    """
    events = list(events)
    if not events:
        return 0
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for ev in events:
                cur.execute(_UPSERT_SQL, (
                    ev.source, ev.source_table, ev.source_row_id,
                    ev.event_type, ev.event_time, ev.surface, ev.author,
                    ev.parent_event_id, ev.title, ev.text, ev.engagement_score,
                    json.dumps(ev.raw_meta) if ev.raw_meta else None,
                ))
                row = cur.fetchone()
                if row:
                    ev.id = row[0]
        conn.commit()
        return len(events)
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def upsert_events_fast(events: Iterable[SignalEvent]) -> int:
    """Bulk upsert using execute_values. Does NOT populate event.id.

    Use this when you don't need the returned ids (e.g. comments, where
    the parent_event_id has already been resolved up-front).
    """
    events = list(events)
    if not events:
        return 0
    sql = """
    INSERT INTO signal_events (
        source, source_table, source_row_id,
        event_type, event_time, surface, author,
        parent_event_id, title, text, engagement_score, raw_meta
    ) VALUES %s
    ON CONFLICT (source, source_table, source_row_id) DO UPDATE SET
        event_type       = EXCLUDED.event_type,
        event_time       = EXCLUDED.event_time,
        surface          = EXCLUDED.surface,
        author           = EXCLUDED.author,
        parent_event_id  = EXCLUDED.parent_event_id,
        title            = EXCLUDED.title,
        text             = EXCLUDED.text,
        engagement_score = EXCLUDED.engagement_score,
        raw_meta         = EXCLUDED.raw_meta;
    """
    rows = [
        (
            ev.source, ev.source_table, ev.source_row_id,
            ev.event_type, ev.event_time, ev.surface, ev.author,
            ev.parent_event_id, ev.title, ev.text, ev.engagement_score,
            json.dumps(ev.raw_meta) if ev.raw_meta else None,
        )
        for ev in events
    ]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, sql, rows, page_size=1000)
        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


# ─── reads ────────────────────────────────────────────────────────────────────

def event_count(source: Optional[str] = None) -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if source:
                cur.execute(
                    "SELECT COUNT(*) FROM signal_events WHERE source=%s", (source,)
                )
            else:
                cur.execute("SELECT COUNT(*) FROM signal_events")
            return cur.fetchone()[0]
    finally:
        put_conn(conn)


def get_event_by_id(event_id: int) -> Optional[SignalEvent]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM signal_events WHERE id=%s", (event_id,)
            )
            row = cur.fetchone()
            return _row_to_event(row) if row else None
    finally:
        put_conn(conn)


def lookup_event_by_source(
    source: str, source_table: str, source_row_id: str
) -> Optional[int]:
    """Resolve (source, source_table, source_row_id) → signal_events.id."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id FROM signal_events
                   WHERE source=%s AND source_table=%s AND source_row_id=%s""",
                (source, source_table, source_row_id),
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        put_conn(conn)


def build_source_id_map(
    source: str, source_table: str
) -> dict[str, int]:
    """Return {source_row_id: signal_events.id} for one (source, source_table).

    Used after bulk parent-event projection to resolve parent_event_id for
    children in one round-trip.
    """
    conn = get_conn()
    try:
        with conn.cursor("source_id_map_cur") as cur:  # named = server-side cursor
            cur.itersize = 50_000
            cur.execute(
                """SELECT source_row_id, id FROM signal_events
                   WHERE source=%s AND source_table=%s""",
                (source, source_table),
            )
            return {row[0]: row[1] for row in cur}
    finally:
        put_conn(conn)


def build_source_id_map_for_rows(
    source: str,
    source_table: str,
    source_row_ids: Iterable[str | int],
) -> dict[str, int]:
    """Return event ids for a bounded set of source rows."""
    ids = [str(row_id) for row_id in source_row_ids]
    if not ids:
        return {}
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT source_row_id, id FROM signal_events
                   WHERE source=%s
                     AND source_table=%s
                     AND source_row_id = ANY(%s)""",
                (source, source_table, ids),
            )
            return {row[0]: row[1] for row in cur.fetchall()}
    finally:
        put_conn(conn)


def get_event_bundles_batch(
    source: str,
    *,
    prompt_version: str = "v1",
    offset: int = 0,
    limit: int = 100,
    only_unprocessed: bool = True,
    window: tuple[datetime, datetime] | None = None,
    before: tuple[datetime, int] | None = None,
) -> list[EventBundle]:
    """Return parent events with their children, paginated.

    If only_unprocessed=True, skip parents that already have a pain_objects row
    for the given prompt_version.

    With ``window=(start, end)`` only parents with ``start <= event_time < end`` are returned,
    **newest first** (see core/extraction_policy.py; served by ix_signal_events_parent_source_time).
    Without a window the order is insertion order (``signal_events.id``), as before.

    ``before=(event_time, id)`` (window only) continues after the last parent of the previous batch, so a
    long run does not re-scan every parent it has already processed.
    """
    window_sql = ""
    window_params: tuple = ()
    order_sql = "se.id"
    if window is not None:
        window_sql = "AND se.event_time >= %s AND se.event_time < %s"
        window_params = (window[0], window[1])
        if before is not None:
            window_sql += " AND (se.event_time, se.id) < (%s, %s)"
            window_params += (before[0], before[1])
        order_sql = "se.event_time DESC, se.id DESC"
    if only_unprocessed:
        _ensure_processed_signal_events_table()
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if only_unprocessed:
                reddit_processed_sql = ""
                if source == "reddit":
                    reddit_processed_sql = """
                         AND NOT EXISTS (
                            SELECT 1 FROM processed_posts pp
                            WHERE se.source_table = 'posts'
                              AND pp.post_id::text = se.source_row_id
                         )
                    """
                cur.execute(
                    f"""SELECT se.*
                       FROM signal_events se
                       WHERE se.source = %s
                         AND se.parent_event_id IS NULL
                         AND NOT EXISTS (
                            SELECT 1 FROM pain_objects po
                            WHERE po.signal_event_id = se.id
                              AND po.prompt_version = %s
                         )
                         AND NOT EXISTS (
                            SELECT 1 FROM processed_signal_events pse
                            WHERE pse.signal_event_id = se.id
                         )
                         AND NOT EXISTS (
                            -- failed recently (retry later) or parked after repeated failures
                            SELECT 1 FROM pain_extraction_attempts pea
                            WHERE pea.signal_event_id = se.id
                              AND (pea.failed OR pea.last_attempt_at > NOW() - INTERVAL '6 hours')
                         )
                         {reddit_processed_sql}
                         {window_sql}
                       ORDER BY {order_sql}
                       LIMIT %s OFFSET %s""",
                    (source, prompt_version, *window_params, limit, offset),
                )
            else:
                cur.execute(
                    f"""SELECT se.*
                       FROM signal_events se
                       WHERE se.source = %s
                         AND se.parent_event_id IS NULL
                         {window_sql}
                       ORDER BY {order_sql}
                       LIMIT %s OFFSET %s""",
                    (source, *window_params, limit, offset),
                )
            parent_rows = cur.fetchall()
            if not parent_rows:
                return []

            parent_ids = [r["id"] for r in parent_rows]
            cur.execute(
                """SELECT * FROM signal_events
                   WHERE parent_event_id = ANY(%s)
                   ORDER BY parent_event_id, id""",
                (parent_ids,),
            )
            child_rows = cur.fetchall()

        # Group children under parents
        bundles_by_id: dict[int, EventBundle] = {
            r["id"]: EventBundle(parent=_row_to_event(r))
            for r in parent_rows
        }
        for cr in child_rows:
            ev = _row_to_event(cr)
            bundle = bundles_by_id.get(cr["parent_event_id"])
            if bundle is not None:
                bundle.children.append(ev)
        # Preserve parent_rows order
        return [bundles_by_id[r["id"]] for r in parent_rows]
    finally:
        put_conn(conn)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _ensure_processed_signal_events_table() -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_signal_events (
                    signal_event_id BIGINT PRIMARY KEY REFERENCES signal_events(id) ON DELETE CASCADE,
                    source TEXT NOT NULL,
                    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_processed_signal_events_source
                    ON processed_signal_events (source, processed_at DESC)
                """
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)

def _row_to_event(row) -> SignalEvent:
    """Convert a RealDictCursor row into a SignalEvent."""
    return SignalEvent(
        id=row["id"],
        source=row["source"],
        source_table=row["source_table"],
        source_row_id=row["source_row_id"],
        event_type=row["event_type"],
        event_time=row["event_time"],
        surface=row.get("surface"),
        author=row.get("author"),
        parent_event_id=row.get("parent_event_id"),
        title=row.get("title"),
        text=row.get("text"),
        engagement_score=row.get("engagement_score"),
        raw_meta=row.get("raw_meta") or {},
    )
