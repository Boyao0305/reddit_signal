"""Reddit subreddit source management and backfill tracking."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2
import psycopg2.extras

from reddit_signal.data_ingestion.db import get_conn, put_conn


VALID_STATUSES = {"candidate", "active", "paused", "rejected"}
VALID_SORTS = {
    "priority",
    "pain_yield",
    "b2c_contribution",
    "assigned_rate",
    "pain_objects",
    "last_evaluated",
}
DEFAULT_SUBREDDITS = [
    "smallbusiness",
    "sysadmin",
    "msp",
    "SaaS",
    "Entrepreneur",
    "sales",
]


@dataclass(frozen=True)
class RedditSubredditSource:
    subreddit_name: str
    status: str
    priority: int
    daily_lookback_days: int
    backfill_months: int
    backfill_enabled: bool
    tags: list[str]
    notes: str
    last_fetch_at: datetime | None
    last_backfill_at: datetime | None
    last_evaluated_at: datetime | None
    metrics: dict[str, Any]
    created_at: datetime | None
    updated_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "subreddit_name": self.subreddit_name,
            "status": self.status,
            "priority": self.priority,
            "daily_lookback_days": self.daily_lookback_days,
            "backfill_months": self.backfill_months,
            "backfill_enabled": self.backfill_enabled,
            "tags": self.tags,
            "notes": self.notes,
            "last_fetch_at": _iso(self.last_fetch_at),
            "last_backfill_at": _iso(self.last_backfill_at),
            "last_evaluated_at": _iso(self.last_evaluated_at),
            "metrics": self.metrics,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
        }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_subreddit_name(value: str) -> str:
    name = str(value or "").strip()
    if name.startswith("r/"):
        name = name[2:]
    if name.startswith("/r/"):
        name = name[3:]
    name = name.strip().strip("/")
    if not name:
        raise ValueError("subreddit_name is required")
    if len(name) > 100:
        raise ValueError("subreddit_name must be at most 100 characters")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
    if any(ch not in allowed for ch in name):
        raise ValueError(f"invalid subreddit_name: {value!r}")
    return name


def parse_subreddit_list(raw: str | list[str] | tuple[str, ...]) -> list[str]:
    if isinstance(raw, (list, tuple)):
        parts = [str(item) for item in raw]
    else:
        text = str(raw or "")
        for sep in [",", "\n", "\t", ";"]:
            text = text.replace(sep, " ")
        parts = text.split()
    seen: set[str] = set()
    names: list[str] = []
    for part in parts:
        name = normalize_subreddit_name(part)
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _row_to_source(row: dict[str, Any]) -> RedditSubredditSource:
    return RedditSubredditSource(
        subreddit_name=row["subreddit_name"],
        status=row["status"],
        priority=int(row["priority"]),
        daily_lookback_days=int(row["daily_lookback_days"]),
        backfill_months=int(row["backfill_months"]),
        backfill_enabled=bool(row["backfill_enabled"]),
        tags=list(row["tags"] or []),
        notes=row["notes"] or "",
        last_fetch_at=row["last_fetch_at"],
        last_backfill_at=row["last_backfill_at"],
        last_evaluated_at=row["last_evaluated_at"],
        metrics=dict(row["metrics"] or {}),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def ensure_default_rows() -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute("SELECT 1 FROM reddit_subreddit_sources LIMIT 1")
            except psycopg2.errors.UndefinedTable:
                conn.rollback()
                return
            cur.execute(
                """
                INSERT INTO reddit_subreddit_sources (
                    subreddit_name,
                    status,
                    priority,
                    daily_lookback_days,
                    backfill_months,
                    notes
                )
                SELECT name, 'active', priority, 7, 18, 'Seeded default subreddit.'
                FROM (
                    VALUES
                        ('smallbusiness', 10),
                        ('sysadmin', 20),
                        ('msp', 30),
                        ('SaaS', 40),
                        ('Entrepreneur', 50),
                        ('sales', 60)
                ) AS defaults(name, priority)
                ON CONFLICT (subreddit_name) DO NOTHING
                """
            )
        conn.commit()
    finally:
        put_conn(conn)


def _default_source_dict(name: str, priority: int) -> dict[str, Any]:
    now = None
    return RedditSubredditSource(
        subreddit_name=name,
        status="active",
        priority=priority,
        daily_lookback_days=7,
        backfill_months=18,
        backfill_enabled=True,
        tags=[],
        notes="Default subreddit fallback.",
        last_fetch_at=None,
        last_backfill_at=None,
        last_evaluated_at=None,
        metrics={},
        created_at=now,
        updated_at=now,
    ).to_dict()


def list_sources(
    *,
    status: str | None = None,
    search: str = "",
    sort: str = "priority",
    limit: int = 500,
) -> list[dict[str, Any]]:
    ensure_default_rows()
    where: list[str] = []
    params: list[Any] = []
    if status and status != "all":
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {status!r}")
        where.append("rss.status = %s")
        params.append(status)
    q = search.strip()
    if q:
        where.append("rss.subreddit_name ILIKE %s")
        params.append(f"%{q}%")
    if sort not in VALID_SORTS:
        raise ValueError(f"invalid sort: {sort!r}")
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    order_sql = {
        "priority": "rss.priority ASC, rss.subreddit_name ASC",
        "pain_yield": "NULLIF(rss.metrics->>'pain_yield', '')::FLOAT DESC NULLS LAST, rss.priority ASC, rss.subreddit_name ASC",
        "b2c_contribution": "NULLIF(rss.metrics->>'b2c_contribution_rate', '')::FLOAT DESC NULLS LAST, rss.priority ASC, rss.subreddit_name ASC",
        "assigned_rate": "NULLIF(rss.metrics->>'assigned_rate', '')::FLOAT DESC NULLS LAST, rss.priority ASC, rss.subreddit_name ASC",
        "pain_objects": "NULLIF(rss.metrics->>'pain_objects_count', '')::INT DESC NULLS LAST, rss.priority ASC, rss.subreddit_name ASC",
        "last_evaluated": "rss.last_evaluated_at DESC NULLS LAST, rss.priority ASC, rss.subreddit_name ASC",
    }[sort]
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                cur.execute(
                    f"""
                    SELECT rss.*
                    FROM reddit_subreddit_sources rss
                    {where_sql}
                    ORDER BY {order_sql}
                    LIMIT %s
                    """,
                    tuple(params + [limit]),
                )
            except psycopg2.errors.UndefinedTable:
                conn.rollback()
                return [
                    _default_source_dict(name, i * 10)
                    for i, name in enumerate(DEFAULT_SUBREDDITS, start=1)
                ]
            return [_row_to_source(dict(row)).to_dict() for row in cur.fetchall()]
    finally:
        put_conn(conn)


def active_subreddits() -> list[dict[str, Any]]:
    rows = list_sources(status="active", limit=10_000)
    if not rows:
        return [
            {
                "subreddit_name": name,
                "daily_lookback_days": 7,
                "priority": i * 10,
            }
            for i, name in enumerate(DEFAULT_SUBREDDITS, start=1)
        ]
    return rows


def validate_source_payload(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "status" in payload:
        status = str(payload["status"] or "").strip()
        if status not in VALID_STATUSES:
            raise ValueError(f"status must be one of {', '.join(sorted(VALID_STATUSES))}")
        out["status"] = status
    for key, lo, hi in (
        ("priority", 0, 10_000),
        ("daily_lookback_days", 0, 3650),
        ("backfill_months", 0, 120),
    ):
        if key not in payload:
            continue
        try:
            value = int(payload[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be an integer") from exc
        if value < lo or value > hi:
            raise ValueError(f"{key} must be between {lo} and {hi}")
        out[key] = value
    if "backfill_enabled" in payload:
        if not isinstance(payload["backfill_enabled"], bool):
            raise ValueError("backfill_enabled must be a boolean")
        out["backfill_enabled"] = payload["backfill_enabled"]
    if "tags" in payload:
        tags = payload["tags"] or []
        if isinstance(tags, str):
            tags = parse_subreddit_list(tags)
        if not isinstance(tags, list):
            raise ValueError("tags must be a list or string")
        out["tags"] = [str(tag).strip() for tag in tags if str(tag).strip()]
    if "notes" in payload:
        notes = str(payload["notes"] or "")
        if len(notes) > 4000:
            raise ValueError("notes must be at most 4000 characters")
        out["notes"] = notes
    return out


def upsert_source(
    subreddit_name: str,
    payload: dict[str, Any] | None = None,
) -> RedditSubredditSource:
    name = normalize_subreddit_name(subreddit_name)
    updates = validate_source_payload(payload or {})
    insert_values = {
        "status": updates.get("status", "candidate"),
        "priority": updates.get("priority", 100),
        "daily_lookback_days": updates.get("daily_lookback_days", 7),
        "backfill_months": updates.get("backfill_months", 18),
        "backfill_enabled": updates.get("backfill_enabled", True),
        "tags": updates.get("tags", []),
        "notes": updates.get("notes", ""),
    }
    mutable_cols = [
        "status",
        "priority",
        "daily_lookback_days",
        "backfill_months",
        "backfill_enabled",
        "tags",
        "notes",
    ]
    update_sql = ",\n                    ".join(
        f"{col} = EXCLUDED.{col}" if col in updates else f"{col} = reddit_subreddit_sources.{col}"
        for col in mutable_cols
    )
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                INSERT INTO reddit_subreddit_sources (
                    subreddit_name,
                    status,
                    priority,
                    daily_lookback_days,
                    backfill_months,
                    backfill_enabled,
                    tags,
                    notes
                ) VALUES (
                    %(subreddit_name)s,
                    %(status)s,
                    %(priority)s,
                    %(daily_lookback_days)s,
                    %(backfill_months)s,
                    %(backfill_enabled)s,
                    %(tags)s,
                    %(notes)s
                )
                ON CONFLICT (subreddit_name) DO UPDATE SET
                    {update_sql}
                RETURNING *
                """,
                {"subreddit_name": name, **insert_values},
            )
            row = cur.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)
    return _row_to_source(dict(row))


def import_sources(names: str | list[str], *, status: str = "candidate") -> list[dict[str, Any]]:
    return [upsert_source(name, {"status": status}).to_dict() for name in parse_subreddit_list(names)]


def evaluate_source(subreddit_name: str) -> dict[str, Any]:
    name = normalize_subreddit_name(subreddit_name)
    metrics = _compute_metrics(name)
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE reddit_subreddit_sources
                SET metrics = %s,
                    last_evaluated_at = NOW()
                WHERE subreddit_name = %s
                RETURNING *
                """,
                (psycopg2.extras.Json(metrics), name),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"subreddit {name!r} is not configured")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)
    return _row_to_source(dict(row)).to_dict()


def _compute_metrics(name: str) -> dict[str, Any]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                WITH raw_counts AS (
                    SELECT
                        COUNT(DISTINCT p.id)::INT AS posts_count,
                        COUNT(c.id)::INT AS comments_count,
                        MIN(p.created_utc) AS oldest_post_at,
                        MAX(p.created_utc) AS newest_post_at
                    FROM subreddits s
                    LEFT JOIN posts p ON p.subreddit_id = s.id
                    LEFT JOIN comments c ON c.post_id = p.id
                    WHERE s.name = %s
                ),
                event_counts AS (
                    SELECT COUNT(*)::INT AS signal_events_count
                    FROM signal_events
                    WHERE source = 'reddit'
                      AND surface = %s
                ),
                pain_counts AS (
                    SELECT
                        COUNT(*)::INT AS pain_objects_count,
                        COUNT(DISTINCT pcm.cluster_id)::INT AS assigned_clusters_count,
                        COUNT(DISTINCT pcm.pain_id)::INT AS assigned_pain_count
                    FROM pain_objects po
                    LEFT JOIN pain_cluster_members pcm ON pcm.pain_id = po.id
                    WHERE po.subreddit_name = %s
                ),
                b2c_counts AS (
                    SELECT COUNT(DISTINCT po.id)::INT AS b2c_pain_count
                    FROM pain_objects po
                    JOIN pain_cluster_members pcm ON pcm.pain_id = po.id
                    JOIN cluster_audience ca ON ca.cluster_id = pcm.cluster_id
                    WHERE po.subreddit_name = %s
                      AND ca.classifier_version = 'v1'
                      AND ca.audience_type IN ('B2C', 'mixed')
                )
                SELECT *
                FROM raw_counts, event_counts, pain_counts, b2c_counts
                """,
                (name, name, name, name),
            )
            row = dict(cur.fetchone() or {})
    finally:
        put_conn(conn)
    posts_count = int(row.get("posts_count") or 0)
    signal_events_count = int(row.get("signal_events_count") or 0)
    pain_objects_count = int(row.get("pain_objects_count") or 0)
    assigned_pain_count = int(row.get("assigned_pain_count") or 0)
    return {
        **row,
        "oldest_post_at": _iso(row.get("oldest_post_at")),
        "newest_post_at": _iso(row.get("newest_post_at")),
        "pain_yield": pain_objects_count / signal_events_count if signal_events_count else None,
        "assigned_rate": assigned_pain_count / pain_objects_count if pain_objects_count else None,
        "b2c_contribution_rate": (
            int(row.get("b2c_pain_count") or 0) / pain_objects_count
            if pain_objects_count else None
        ),
    }


def create_backfill_job(
    subreddits: list[str],
    *,
    months: int = 18,
    chunk_days: int = 7,
    requested_by: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    names = parse_subreddit_list(subreddits)
    if not names:
        raise ValueError("at least one subreddit is required")
    if months < 0 or months > 120:
        raise ValueError("months must be between 0 and 120")
    if chunk_days <= 0 or chunk_days > 31:
        raise ValueError("chunk_days must be between 1 and 31")
    disabled = _backfill_disabled_names(names)
    if disabled:
        raise ValueError(f"backfill is disabled for: {', '.join(disabled)}")
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=months * 31)
    chunks: list[tuple[str, datetime, datetime]] = []
    for name in names:
        start = window_start
        while start < now:
            end = min(start + timedelta(days=chunk_days), now)
            chunks.append((name, start, end))
            start = end
    if dry_run:
        return {
            "dry_run": True,
            "subreddits": names,
            "months": months,
            "chunk_days": chunk_days,
            "chunk_count": len(chunks),
            "window_start": _iso(window_start),
            "window_end": _iso(now),
        }

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO reddit_backfill_jobs (
                    requested_by,
                    subreddits,
                    months,
                    chunk_days,
                    total_chunks
                )
                VALUES (%s, %s, %s, %s, %s)
                RETURNING *
                """,
                (requested_by, names, months, chunk_days, len(chunks)),
            )
            job = dict(cur.fetchone())
            psycopg2.extras.execute_batch(
                cur,
                """
                INSERT INTO reddit_backfill_chunks (
                    job_id,
                    subreddit_name,
                    window_start,
                    window_end
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (job_id, subreddit_name, window_start, window_end) DO NOTHING
                """,
                [(job["id"], name, start, end) for name, start, end in chunks],
                page_size=500,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)
    return {
        "dry_run": False,
        "job": _job_to_dict(job),
        "chunk_count": len(chunks),
    }


def _backfill_disabled_names(names: list[str]) -> list[str]:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    SELECT subreddit_name
                    FROM reddit_subreddit_sources
                    WHERE subreddit_name = ANY(%s)
                      AND backfill_enabled IS FALSE
                    """,
                    (names,),
                )
            except psycopg2.errors.UndefinedTable:
                conn.rollback()
                return []
            return [row[0] for row in cur.fetchall()]
    finally:
        put_conn(conn)


def _job_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key in ("created_at", "started_at", "finished_at"):
        out[key] = _iso(out.get(key))
    return out
