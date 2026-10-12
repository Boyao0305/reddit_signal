"""Read-only queries behind ``cli status``."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from reddit_signal import db


def _rows(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def jobs_by_status() -> dict[str, int]:
    rows = _rows("SELECT status, COUNT(*) AS n FROM pipeline_jobs WHERE status IN "
                 "('queued','running','cancel_requested') GROUP BY 1")
    return {r["status"]: int(r["n"]) for r in rows}


def latest_jobs(limit: int = 8) -> list[dict[str, Any]]:
    return _rows(
        """
        SELECT id, job_type, status, created_at, started_at, finished_at, worker_id, LEFT(error, 200) AS error
        FROM pipeline_jobs ORDER BY id DESC LIMIT %s
        """,
        (limit,),
    )


def last_job_of_type(job_type: str) -> dict[str, Any] | None:
    rows = _rows(
        "SELECT id, status, started_at, finished_at, LEFT(error, 200) AS error FROM pipeline_jobs "
        "WHERE job_type = %s ORDER BY id DESC LIMIT 1",
        (job_type,),
    )
    return rows[0] if rows else None


def workers() -> list[dict[str, Any]]:
    return _rows(
        "SELECT worker_id, mode, current_job_id, last_seen_at, NOW() - last_seen_at AS age "
        "FROM pipeline_worker_heartbeats WHERE last_seen_at > NOW() - INTERVAL '3 days' "
        "ORDER BY last_seen_at DESC LIMIT 8"
    )


def live_run() -> dict[str, Any] | None:
    rows = _rows(
        """
        SELECT ar.id, ar.window_start, ar.window_end, ar.cluster_count, ar.live_at,
               (SELECT COUNT(*) FROM pain_clusters pc WHERE pc.run_id = ar.id AND pc.is_candidate) AS candidates
        FROM analysis_runs ar WHERE ar.status = 'live' ORDER BY ar.id DESC LIMIT 1
        """
    )
    return rows[0] if rows else None


def extraction_coverage(source: str, since: datetime, until: datetime) -> dict[str, int]:
    """Parent posts in ``[since, until)`` and how many are marked processed.

    Joins on the indexed side of each marker table (``processed_posts.post_id`` is a bigint, so the
    event's text id is cast — not the other way round, which disables the index).
    """
    rows = _rows(
        """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE pse.signal_event_id IS NOT NULL OR pp.post_id IS NOT NULL) AS done
        FROM signal_events se
        LEFT JOIN processed_signal_events pse ON pse.signal_event_id = se.id
        LEFT JOIN processed_posts pp
               ON pp.post_id = CASE WHEN se.source_table = 'posts' AND se.source_row_id ~ '^[0-9]+$'
                                    THEN se.source_row_id::bigint END
        WHERE se.source = %s AND se.parent_event_id IS NULL AND se.event_time >= %s AND se.event_time < %s
        """,
        (source, since, until),
    )
    return {"total": int(rows[0]["total"]), "done": int(rows[0]["done"])}


def dead_letters() -> dict[str, int]:
    rows = _rows("SELECT COUNT(*) FILTER (WHERE failed) AS parked, COUNT(*) FILTER (WHERE NOT failed) AS retrying "
                 "FROM pain_extraction_attempts")
    return {"parked": int(rows[0]["parked"] or 0), "retrying": int(rows[0]["retrying"] or 0)}


def database_size() -> str:
    return _rows("SELECT pg_size_pretty(pg_database_size(current_database())) AS size")[0]["size"]
