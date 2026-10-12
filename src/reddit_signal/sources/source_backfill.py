"""Unified historical backfill planning and processing for source adapters."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2
import psycopg2.extras

from reddit_signal import pipeline_jobs, runtime_settings
from reddit_signal.data_ingestion.db import get_conn, put_conn
from reddit_signal.sources import runtime_config
from reddit_signal.sources.registry import discover_adapters, get_adapter

VALID_MODES = {"fetch", "project", "fetch-project", "extract"}
VALID_SCOPES = {"active", "candidate", "all", "query"}
VALID_CHUNK_STATUSES = {
    "queued",
    "running",
    "succeeded",
    "failed",
    "skipped",
    "empty",
    "cancelled",
}
DEFAULT_CHUNK_DAYS = 7
WORKER_ONLINE_SECONDS = 120


@dataclass(frozen=True)
class BackfillTarget:
    source: str
    target_type: str
    target_id: str
    target_label: str
    query: str
    coverage_mode: str
    coverage_note: str


def parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("date value is required")
        if len(text) == 10:
            text = f"{text}T00:00:00+00:00"
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def plan_backfill(
    *,
    sources: list[str],
    date_from: str | datetime,
    date_to: str | datetime,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    scope: str = "active",
    mode: str = "fetch",
    query: str = "",
) -> dict[str, Any]:
    start = parse_datetime(date_from)
    end = parse_datetime(date_to)
    if end <= start:
        raise ValueError("date_to must be after date_from")
    if chunk_days <= 0 or chunk_days > 31:
        raise ValueError("chunk_days must be between 1 and 31")
    if scope not in VALID_SCOPES:
        raise ValueError(f"scope must be one of {', '.join(sorted(VALID_SCOPES))}")
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {', '.join(sorted(VALID_MODES))}")

    normalized_sources = _normalize_sources(sources)
    targets: list[BackfillTarget] = []
    for source in normalized_sources:
        targets.extend(_targets_for_source(source, scope=scope, query=query))

    windows = _windows(start, end, chunk_days)
    source_counts: dict[str, int] = {}
    coverage_counts: dict[str, int] = {}
    chunks: list[dict[str, Any]] = []
    for target in targets:
        source_counts[target.source] = source_counts.get(target.source, 0) + len(windows)
        coverage_counts[target.coverage_mode] = coverage_counts.get(target.coverage_mode, 0) + len(windows)
        for window_start, window_end in windows:
            chunks.append(_chunk_dict(target, window_start, window_end, mode=mode))

    return {
        "dry_run": True,
        "sources": normalized_sources,
        "scope": scope,
        "mode": mode,
        "date_from": _iso(start),
        "date_to": _iso(end),
        "chunk_days": chunk_days,
        "target_count": len(targets),
        "chunk_count": len(chunks),
        "source_counts": source_counts,
        "coverage_counts": coverage_counts,
        "chunks": chunks,
    }


def create_backfill_job(
    *,
    sources: list[str],
    date_from: str | datetime,
    date_to: str | datetime,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    scope: str = "active",
    mode: str = "fetch",
    query: str = "",
    requested_by: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    plan = plan_backfill(
        sources=sources,
        date_from=date_from,
        date_to=date_to,
        chunk_days=chunk_days,
        scope=scope,
        mode=mode,
        query=query,
    )
    if dry_run:
        return plan
    if plan["chunk_count"] <= 0:
        raise ValueError("no backfill chunks planned for the selected sources and scope")

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO source_backfill_jobs (
                    sources, scope, mode, date_from, date_to, chunk_days,
                    requested_by, params, total_chunks, queued_chunks
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    plan["sources"],
                    scope,
                    mode,
                    parse_datetime(date_from),
                    parse_datetime(date_to),
                    chunk_days,
                    requested_by,
                    psycopg2.extras.Json({
                        "query": query,
                        "source_counts": plan["source_counts"],
                        "coverage_counts": plan["coverage_counts"],
                    }),
                    plan["chunk_count"],
                    plan["chunk_count"],
                ),
            )
            job = dict(cur.fetchone())
            values = [
                (
                    job["id"],
                    chunk["source"],
                    chunk["target_type"],
                    chunk["target_id"],
                    chunk["target_label"],
                    chunk["query"],
                    parse_datetime(chunk["window_start"]),
                    parse_datetime(chunk["window_end"]),
                    chunk["coverage_mode"],
                    chunk["coverage_note"],
                    mode,
                )
                for chunk in plan["chunks"]
            ]
            if values:
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO source_backfill_chunks (
                        job_id, source, target_type, target_id, target_label,
                        query, window_start, window_end, coverage_mode,
                        coverage_note, mode
                    )
                    VALUES %s
                    ON CONFLICT (
                        job_id, source, target_type, target_id, query,
                        window_start, window_end
                    ) DO NOTHING
                    """,
                    values,
                    page_size=500,
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)
    return {
        **plan,
        "dry_run": False,
        "job": _job_to_dict(job),
    }


def create_project_job_from_fetch(
    *,
    job_id: int,
    requested_by: str | None = None,
) -> dict[str, Any]:
    """Create a project-only job that mirrors a completed fetch-only job."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT *
                FROM source_backfill_jobs
                WHERE id = %s
                FOR UPDATE
                """,
                (int(job_id),),
            )
            source_job = cur.fetchone()
            if not source_job:
                raise ValueError(f"source backfill job {job_id!r} not found")
            source_job = dict(source_job)
            if source_job["mode"] != "fetch":
                raise ValueError("only fetch-only source backfill jobs can queue a separate project job")
            if source_job["status"] != "succeeded":
                raise ValueError("fetch job must finish successfully before project can be queued")
            if int(source_job.get("queued_chunks") or 0) > 0 or int(source_job.get("running_chunks") or 0) > 0:
                raise ValueError("fetch job still has queued or running chunks")

            cur.execute(
                """
                SELECT id, status
                FROM source_backfill_jobs
                WHERE mode = 'project'
                  AND params ->> 'project_from_job_id' = %s
                  AND status <> 'cancelled'
                ORDER BY id DESC
                LIMIT 1
                """,
                (str(int(job_id)),),
            )
            existing_project = cur.fetchone()
            if existing_project:
                raise ValueError(
                    "project job already exists for this fetch job "
                    f"(#{existing_project['id']} is {existing_project['status']})"
                )

            params = dict(source_job.get("params") or {})
            params["project_from_job_id"] = int(job_id)

            cur.execute(
                """
                INSERT INTO source_backfill_jobs (
                    sources, scope, mode, date_from, date_to, chunk_days,
                    requested_by, params, total_chunks, queued_chunks
                )
                VALUES (%s, %s, 'project', %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    source_job["sources"],
                    source_job["scope"],
                    source_job["date_from"],
                    source_job["date_to"],
                    source_job["chunk_days"],
                    requested_by,
                    psycopg2.extras.Json(params),
                    0,
                    0,
                ),
            )
            project_job = dict(cur.fetchone())
            cur.execute(
                """
                INSERT INTO source_backfill_chunks (
                    job_id, source, target_type, target_id, target_label,
                    query, window_start, window_end, coverage_mode,
                    coverage_note, mode, details
                )
                SELECT
                    %s,
                    source,
                    target_type,
                    target_id,
                    target_label,
                    query,
                    window_start,
                    window_end,
                    coverage_mode,
                    coverage_note,
                    'project',
                    COALESCE(details, '{}'::jsonb) || jsonb_build_object(
                        'project_from_job_id', job_id,
                        'project_from_chunk_id', id
                    )
                FROM source_backfill_chunks
                WHERE job_id = %s
                  AND status IN ('succeeded', 'empty', 'skipped')
                  AND source = 'reddit'
                ORDER BY id ASC
                """,
                (int(project_job["id"]), int(job_id)),
            )
            cur.execute(
                """
                INSERT INTO source_backfill_chunks (
                    job_id, source, target_type, target_id, target_label,
                    query, window_start, window_end, coverage_mode,
                    coverage_note, mode, details
                )
                SELECT
                    %s,
                    source,
                    'source_window',
                    source,
                    source,
                    '',
                    window_start,
                    window_end,
                    MIN(coverage_mode),
                    MIN(coverage_note),
                    'project',
                    jsonb_build_object(
                        'project_from_job_id', %s,
                        'project_from_chunk_ids', jsonb_agg(id ORDER BY id)
                    )
                FROM source_backfill_chunks
                WHERE job_id = %s
                  AND status IN ('succeeded', 'empty', 'skipped')
                  AND source <> 'reddit'
                GROUP BY source, window_start, window_end
                ORDER BY source, window_start
                """,
                (int(project_job["id"]), int(job_id), int(job_id)),
            )
            cur.execute(
                """
                SELECT COUNT(*) AS chunk_count
                FROM source_backfill_chunks
                WHERE job_id = %s
                """,
                (int(project_job["id"]),),
            )
            chunk_count = int((cur.fetchone() or {}).get("chunk_count") or 0)
            if chunk_count <= 0:
                raise ValueError("fetch job has no completed chunks to project")
            cur.execute(
                """
                SELECT source, COUNT(*) AS count
                FROM source_backfill_chunks
                WHERE job_id = %s
                GROUP BY source
                """,
                (int(project_job["id"]),),
            )
            project_source_counts = {row["source"]: int(row["count"] or 0) for row in cur.fetchall()}
            cur.execute(
                """
                SELECT coverage_mode, COUNT(*) AS count
                FROM source_backfill_chunks
                WHERE job_id = %s
                GROUP BY coverage_mode
                """,
                (int(project_job["id"]),),
            )
            project_coverage_counts = {
                row["coverage_mode"]: int(row["count"] or 0)
                for row in cur.fetchall()
            }
            params["project_source_counts"] = project_source_counts
            params["project_coverage_counts"] = project_coverage_counts
            cur.execute(
                """
                UPDATE source_backfill_jobs
                SET params = %s,
                    total_chunks = %s,
                    queued_chunks = %s
                WHERE id = %s
                RETURNING *
                """,
                (
                    psycopg2.extras.Json(params),
                    chunk_count,
                    chunk_count,
                    int(project_job["id"]),
                ),
            )
            project_job = dict(cur.fetchone())
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)

    return {
        "job": _job_to_dict(project_job),
        "source_job": _job_to_dict(source_job),
        "chunk_count": chunk_count,
    }


def list_jobs(*, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    where = []
    params: list[Any] = []
    if status and status != "all":
        where.append("status = %s")
        params.append(status)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                cur.execute(
                    f"""
                    SELECT *
                    FROM source_backfill_jobs
                    {where_sql}
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    tuple(params + [int(limit)]),
                )
            except psycopg2.errors.UndefinedTable:
                conn.rollback()
                return []
            return [_job_to_dict(dict(row)) for row in cur.fetchall()]
    finally:
        put_conn(conn)


def queue_status() -> dict[str, Any]:
    """Return operator-facing queue diagnostics for historical source backfills."""
    try:
        worker_enabled = runtime_settings.get_setting("SOURCE_BACKFILL_WORKER_ENABLED", "true").lower() == "true"
    except Exception:  # noqa: BLE001
        worker_enabled = True

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                cur.execute(
                    """
                    SELECT
                        worker_id,
                        mode,
                        started_at,
                        last_seen_at,
                        current_job_id,
                        version,
                        EXTRACT(EPOCH FROM (NOW() - last_seen_at)) AS seconds_since_seen
                    FROM pipeline_worker_heartbeats
                    ORDER BY last_seen_at DESC
                    LIMIT 5
                    """
                )
                workers = [_worker_row_to_dict(dict(row)) for row in cur.fetchall()]
            except psycopg2.errors.UndefinedTable:
                conn.rollback()
                workers = []

            try:
                cur.execute(
                    """
                    SELECT *
                    FROM pipeline_jobs
                    WHERE status IN ('queued', 'running', 'cancel_requested')
                      AND run_after <= NOW()
                    ORDER BY
                        CASE status
                            WHEN 'running' THEN 0
                            WHEN 'cancel_requested' THEN 1
                            ELSE 2
                        END,
                        run_after ASC,
                        id ASC
                    LIMIT 10
                    """
                )
                pipeline_rows = [dict(row) for row in cur.fetchall()]
            except psycopg2.errors.UndefinedTable:
                conn.rollback()
                pipeline_rows = []

            try:
                cur.execute(
                    """
                    SELECT *
                    FROM source_backfill_chunks
                    WHERE status = 'running'
                    ORDER BY started_at ASC, id ASC
                    LIMIT 20
                    """
                )
                running_chunks = [_chunk_row_to_dict(dict(row)) for row in cur.fetchall()]
                cur.execute(
                    """
                    SELECT
                        COALESCE(SUM(queued_chunks), 0) AS queued_chunks,
                        COALESCE(SUM(running_chunks), 0) AS running_chunks
                    FROM source_backfill_jobs
                    WHERE status IN ('queued', 'running')
                    """
                )
                totals = dict(cur.fetchone() or {})
                cur.execute(
                    """
                    SELECT *
                    FROM source_backfill_jobs
                    WHERE status IN ('queued', 'running', 'cancelled')
                      AND (queued_chunks > 0 OR running_chunks > 0)
                    ORDER BY id ASC
                    LIMIT 200
                    """
                )
                active_jobs = [_job_to_dict(dict(row)) for row in cur.fetchall()]
            except psycopg2.errors.UndefinedTable:
                conn.rollback()
                running_chunks = []
                totals = {"queued_chunks": 0, "running_chunks": 0}
                active_jobs = []
    finally:
        put_conn(conn)

    online_workers = [
        worker for worker in workers
        if worker.get("seconds_since_seen") is not None
        and float(worker["seconds_since_seen"]) <= WORKER_ONLINE_SECONDS
    ]
    source_backfill_workers = [
        worker for worker in online_workers
        if _worker_can_run_source_backfill(worker)
    ]
    pipeline_jobs_dicts = [_pipeline_job_row_to_dict(row) for row in pipeline_rows]
    blocking_pipeline = pipeline_jobs_dicts[0] if pipeline_jobs_dicts else None
    queued_total = int(totals.get("queued_chunks") or 0)
    running_total = max(int(totals.get("running_chunks") or 0), len(running_chunks))
    state, message = _queue_state_message(
        worker_enabled=worker_enabled,
        online_workers=source_backfill_workers,
        running_chunks=running_chunks,
        queued_total=queued_total,
    )
    job_states = _queue_job_states(
        active_jobs=active_jobs,
        state=state,
        global_message=message,
        worker_enabled=worker_enabled,
        has_online_worker=bool(source_backfill_workers),
        running_chunks=running_chunks,
    )
    return {
        "state": state,
        "message": message,
        "worker_enabled": worker_enabled,
        "worker_online": bool(source_backfill_workers),
        "worker_online_seconds": WORKER_ONLINE_SECONDS,
        "workers": workers,
        "blocking_pipeline_job": blocking_pipeline,
        "active_pipeline_jobs": pipeline_jobs_dicts,
        "running_chunks": running_chunks,
        "queued_chunks": queued_total,
        "running_chunks_count": running_total,
        "jobs": job_states,
    }


def get_job(
    job_id: int,
    *,
    chunk_limit: int = 50,
    chunk_offset: int = 0,
) -> dict[str, Any]:
    limit = max(1, min(int(chunk_limit), 500))
    offset = max(0, int(chunk_offset))
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM source_backfill_jobs WHERE id = %s", (int(job_id),))
            job = cur.fetchone()
            if not job:
                raise ValueError(f"source backfill job {job_id!r} not found")
            cur.execute(
                "SELECT COUNT(*) AS total FROM source_backfill_chunks WHERE job_id = %s",
                (int(job_id),),
            )
            total_chunks = int(cur.fetchone()["total"] or 0)
            cur.execute(
                """
                SELECT *
                FROM source_backfill_chunks
                WHERE job_id = %s
                ORDER BY id ASC
                LIMIT %s
                OFFSET %s
                """,
                (int(job_id), limit, offset),
            )
            chunks = [_chunk_row_to_dict(dict(row)) for row in cur.fetchall()]
            cur.execute(
                """
                SELECT *
                FROM source_backfill_chunks
                WHERE job_id = %s
                  AND status = 'running'
                ORDER BY updated_at DESC, id ASC
                LIMIT 20
                """,
                (int(job_id),),
            )
            running_chunks = [_chunk_row_to_dict(dict(row)) for row in cur.fetchall()]
    finally:
        put_conn(conn)
    return {
        "job": _job_to_dict(dict(job)),
        "chunks": chunks,
        "running_chunks": running_chunks,
        "queue": queue_status(),
        "chunk_pagination": {
            "total": total_chunks,
            "limit": limit,
            "offset": offset,
            "page": (offset // limit) + 1,
            "page_size": limit,
            "pages": (total_chunks + limit - 1) // limit if total_chunks else 0,
        },
    }


def process_chunks(*, job_id: int | None = None, limit: int = 1, dry_run: bool = False) -> int:
    processed = 0
    while processed < max(0, int(limit)):
        chunk = _claim_chunk(job_id, dry_run=dry_run)
        if chunk is None:
            break
        if dry_run:
            processed += 1
            continue
        processed += 1
        try:
            result = _process_chunk(chunk)
            status = "succeeded"
            if result.get("skipped"):
                status = "skipped"
            elif int(result.get("rows_fetched", 0) or 0) == 0 and int(result.get("rows_projected", 0) or 0) == 0:
                status = "empty"
            if _job_is_cancelled(int(chunk["job_id"])):
                _finish_chunk(chunk["id"], status="cancelled", result=result, error="cancelled while running")
            else:
                _finish_chunk(chunk["id"], status=status, result=result)
        except Exception as exc:  # noqa: BLE001
            if _job_is_cancelled(int(chunk["job_id"])):
                _finish_chunk(chunk["id"], status="cancelled", error="cancelled while running")
            else:
                _finish_chunk(chunk["id"], status="failed", error=str(exc))
    return processed


def retry_failed_chunks(*, job_id: int | None = None) -> int:
    filters = ["status = 'failed'"]
    params: list[Any] = []
    if job_id is not None:
        filters.append("job_id = %s")
        params.append(int(job_id))
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE source_backfill_chunks
                SET status = 'queued',
                    error = NULL,
                    started_at = NULL,
                    finished_at = NULL,
                    updated_at = NOW()
                WHERE {' AND '.join(filters)}
                """,
                tuple(params),
            )
            count = cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)
    if job_id is not None:
        _refresh_job_counts(job_id)
    return int(count)


def cancel_job(*, job_id: int, requested_by: str | None = None) -> dict[str, Any]:
    """Cancel queued chunks for a backfill job and prevent further processing."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status
                FROM source_backfill_jobs
                WHERE id = %s
                FOR UPDATE
                """,
                (int(job_id),),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"source backfill job {job_id!r} not found")
            status = row[0]
            if status in {"succeeded", "failed", "cancelled"}:
                raise ValueError(f"Cannot cancel source backfill job in status {status!r}")
            cur.execute(
                """
                UPDATE source_backfill_jobs
                SET status = 'cancelled',
                    cancel_requested_at = COALESCE(cancel_requested_at, NOW()),
                    cancel_requested_by = COALESCE(cancel_requested_by, %s),
                    finished_at = CASE
                        WHEN running_chunks = 0 THEN COALESCE(finished_at, NOW())
                        ELSE finished_at
                    END
                WHERE id = %s
                """,
                (requested_by, int(job_id)),
            )
            cur.execute(
                """
                UPDATE source_backfill_chunks
                SET status = 'cancelled',
                    error = COALESCE(error, 'cancelled before start'),
                    finished_at = COALESCE(finished_at, NOW()),
                    updated_at = NOW()
                WHERE job_id = %s
                  AND status = 'queued'
                """,
                (int(job_id),),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)
    _refresh_job_counts(job_id)
    return get_job(job_id)["job"]


def delete_job(job_id: int) -> bool:
    """Delete a backfill job after ensuring no chunk is actively running."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    j.id,
                    j.status,
                    j.running_chunks,
                    EXISTS (
                        SELECT 1
                        FROM source_backfill_chunks c
                        WHERE c.job_id = j.id
                          AND c.status = 'running'
                    ) AS has_running_chunk
                FROM source_backfill_jobs j
                WHERE j.id = %s
                FOR UPDATE
                """,
                (int(job_id),),
            )
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return False
            if row["status"] in {"queued", "running"}:
                raise ValueError(
                    "Cannot delete source backfill job before it is stopped; "
                    "stop it first and wait until running_chunks is 0"
                )
            if row["status"] == "cancelled" and (
                int(row["running_chunks"] or 0) > 0 or bool(row["has_running_chunk"])
            ):
                raise ValueError(
                    "Cannot delete source backfill job while a chunk is still stopping; "
                    "wait until running_chunks is 0"
                )
            cur.execute("DELETE FROM source_backfill_jobs WHERE id = %s", (int(job_id),))
            deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    except psycopg2.errors.UndefinedTable:
        conn.rollback()
        return False
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def has_cancel_requested_running_chunks() -> bool:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    SELECT 1
                    FROM source_backfill_chunks c
                    JOIN source_backfill_jobs j ON j.id = c.job_id
                    WHERE c.status = 'running'
                      AND j.status = 'cancelled'
                    LIMIT 1
                    """
                )
            except psycopg2.errors.UndefinedTable:
                conn.rollback()
                return False
            return cur.fetchone() is not None
    finally:
        put_conn(conn)


def cancel_running_chunks_for_cancelled_jobs(error: str = "cancelled while running") -> int:
    conn = get_conn()
    job_ids: list[int] = []
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    UPDATE source_backfill_chunks c
                    SET status = 'cancelled',
                        error = COALESCE(c.error, %s),
                        finished_at = COALESCE(c.finished_at, NOW()),
                        updated_at = NOW()
                    FROM source_backfill_jobs j
                    WHERE c.job_id = j.id
                      AND c.status = 'running'
                      AND j.status = 'cancelled'
                    RETURNING c.job_id
                    """,
                    (error,),
                )
            except psycopg2.errors.UndefinedTable:
                conn.rollback()
                return 0
            job_ids = sorted({int(row[0]) for row in cur.fetchall()})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)
    for affected_job_id in job_ids:
        _refresh_job_counts(affected_job_id)
    return len(job_ids)


def _job_is_cancelled(job_id: int) -> bool:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status = 'cancelled' FROM source_backfill_jobs WHERE id = %s", (int(job_id),))
            row = cur.fetchone()
            return bool(row and row[0])
    finally:
        put_conn(conn)


def _worker_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key in ("started_at", "last_seen_at"):
        out[key] = _iso(out.get(key))
    if out.get("seconds_since_seen") is not None:
        out["seconds_since_seen"] = float(out["seconds_since_seen"])
    return out


def _pipeline_job_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return pipeline_jobs.PipelineJob.from_row(row).to_dict()


def _worker_can_run_source_backfill(worker: dict[str, Any]) -> bool:
    mode = str(worker.get("mode") or "")
    return mode in {"ec2-worker", "ec2-worker:all", "ec2-worker:source-backfill"}


def _chunk_label(chunk: dict[str, Any]) -> str:
    label = chunk.get("target_label") or chunk.get("query") or chunk.get("target_id") or "unknown target"
    return f"{chunk.get('source', 'source')} · {label}"


def _pipeline_label(job: dict[str, Any]) -> str:
    return f"pipeline job #{job['id']} {job['job_type']} is {job['status']}"


def _queue_state_message(
    *,
    worker_enabled: bool,
    online_workers: list[dict[str, Any]],
    running_chunks: list[dict[str, Any]],
    queued_total: int,
) -> tuple[str, str]:
    if not worker_enabled:
        return "paused", "Source backfill worker is disabled in runtime settings."
    if running_chunks and online_workers:
        first = running_chunks[0]
        return "running", f"Processing {_chunk_label(first)} since {first.get('started_at') or 'unknown time'}."
    if running_chunks:
        first = running_chunks[0]
        return (
            "blocked",
            f"Running chunk is recorded for {_chunk_label(first)}, but no active worker heartbeat was seen; it may be stale or the worker is connected to another database.",
        )
    if not online_workers:
        return (
            "blocked",
            "No active source-backfill worker heartbeat in the last 120 seconds; the worker may be stopped or connected to another database.",
        )
    if queued_total > 0:
        return "waiting", "Source-backfill worker is online; queued chunks should be claimed on the next polling cycle."
    return "idle", "No queued source backfill chunks."


def _queue_job_states(
    *,
    active_jobs: list[dict[str, Any]],
    state: str,
    global_message: str,
    worker_enabled: bool,
    has_online_worker: bool,
    running_chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    running_job_ids = {int(chunk["job_id"]) for chunk in running_chunks if chunk.get("job_id") is not None}
    first_running = running_chunks[0] if running_chunks else None
    states: list[dict[str, Any]] = []
    queued_position = 0
    for job in active_jobs:
        queued_chunks = int(job.get("queued_chunks") or 0)
        status = str(job.get("status") or "")
        if queued_chunks > 0:
            queued_position += 1
        reason = global_message
        if status == "cancelled":
            if int(job.get("running_chunks") or 0) > 0:
                reason = "Stop requested; waiting for the running chunk process to terminate."
            else:
                reason = "Stopped. It can be deleted."
        elif int(job["id"]) in running_job_ids:
            reason = f"Currently processing {_chunk_label(first_running or {})}."
        elif not worker_enabled:
            reason = "Worker disabled; enable SOURCE_BACKFILL_WORKER_ENABLED to consume this queue."
        elif not has_online_worker:
            reason = "No online source-backfill worker heartbeat; restart/check that lane and confirm it uses the same database as the API."
        elif status == "queued" and queued_position > 1:
            reason = f"Waiting behind {queued_position - 1} earlier queued backfill job(s)."
        elif state == "running" and first_running:
            reason = f"Waiting for current chunk {_chunk_label(first_running)}."
        states.append({
            "job_id": int(job["id"]),
            "status": status,
            "queue_position": queued_position if queued_chunks > 0 else None,
            "reason": reason,
        })
    return states


def _normalize_sources(sources: list[str]) -> list[str]:
    discover_adapters()
    configured = {cfg.source for cfg in runtime_config.list_source_configs(include_disabled=True)}
    requested = [str(source).strip().lower() for source in sources if str(source).strip()]
    if not requested:
        requested = sorted(configured)
    known = {"reddit", "hackernews"}
    bad = sorted(set(requested) - known)
    if bad:
        raise ValueError(f"unsupported source(s): {', '.join(bad)}")
    return requested


def _targets_for_source(
    source: str,
    *,
    scope: str,
    query: str,
) -> list[BackfillTarget]:
    if source == "reddit":
        return _reddit_targets(scope=scope, query=query)
    if source == "hackernews":
        return _hackernews_targets(query=query)
    raise ValueError(f"unsupported source: {source}")


def _reddit_targets(*, scope: str, query: str) -> list[BackfillTarget]:
    from reddit_signal.sources import reddit_subreddits

    names = reddit_subreddits.parse_subreddit_list(query) if scope == "query" and query.strip() else []
    if not names:
        rows = reddit_subreddits.list_sources(status="all" if scope == "all" else scope, limit=10_000)
        names = [row["subreddit_name"] for row in rows if scope == "all" or row["status"] == scope]
    return [
        BackfillTarget(
            source="reddit",
            target_type="subreddit",
            target_id=name,
            target_label=f"r/{name}",
            query=name,
            coverage_mode="full_window",
            coverage_note="Arctic Shift supports explicit after/before windows.",
        )
        for name in names
    ]


def split_queries(raw: str) -> list[str]:
    """Split a newline-separated query list, dropping blanks and case-insensitive duplicates."""
    seen: set[str] = set()
    queries: list[str] = []
    for part in (line.strip() for line in str(raw or "").splitlines()):
        if part and part.lower() not in seen:
            seen.add(part.lower())
            queries.append(part)
    return queries


def _hackernews_targets(*, query: str) -> list[BackfillTarget]:
    raw = query.strip() or "newstories\naskstories\nshowstories"
    return [
        BackfillTarget(
            source="hackernews",
            target_type="story_list",
            target_id=item,
            target_label=item,
            query=item,
            coverage_mode="recent_only",
            coverage_note="HN Firebase story lists expose recent ranked lists, not full historical date search.",
        )
        for item in split_queries(raw.replace(",", "\n"))
    ]


def _windows(start: datetime, end: datetime, chunk_days: int) -> list[tuple[datetime, datetime]]:
    windows = []
    cursor = start
    delta = timedelta(days=chunk_days)
    while cursor < end:
        window_end = min(cursor + delta, end)
        windows.append((cursor, window_end))
        cursor = window_end
    return windows


def _chunk_dict(target: BackfillTarget, start: datetime, end: datetime, *, mode: str) -> dict[str, Any]:
    return {
        "source": target.source,
        "target_type": target.target_type,
        "target_id": target.target_id,
        "target_label": target.target_label,
        "query": target.query,
        "window_start": _iso(start),
        "window_end": _iso(end),
        "coverage_mode": target.coverage_mode,
        "coverage_note": target.coverage_note,
        "mode": mode,
    }


def _claim_chunk(job_id: int | None, *, dry_run: bool) -> dict[str, Any] | None:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            extra = "AND c.job_id = %s" if job_id is not None else ""
            params = (int(job_id),) if job_id is not None else ()
            if dry_run:
                cur.execute(
                    f"""
                    SELECT c.*
                    FROM source_backfill_chunks c
                    JOIN source_backfill_jobs j ON j.id = c.job_id
                    WHERE c.status = 'queued'
                      AND j.status <> 'cancelled'
                    {extra}
                    ORDER BY c.job_id ASC, c.id ASC
                    LIMIT 1
                    """,
                    params,
                )
                row = cur.fetchone()
                return dict(row) if row else None
            cur.execute(
                f"""
                WITH next_chunk AS (
                    SELECT c.id
                    FROM source_backfill_chunks c
                    JOIN source_backfill_jobs j ON j.id = c.job_id
                    WHERE c.status = 'queued'
                      AND j.status <> 'cancelled'
                    {extra}
                    ORDER BY c.job_id ASC, c.id ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE source_backfill_chunks c
                SET status = 'running',
                    started_at = NOW(),
                    error = NULL
                FROM next_chunk
                WHERE c.id = next_chunk.id
                RETURNING c.*
                """,
                params,
            )
            row = cur.fetchone()
        conn.commit()
        if row:
            _mark_job_started(int(row["job_id"]))
        return dict(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def _process_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    mode = chunk["mode"]
    source = chunk["source"]
    since = chunk["window_start"]
    until = chunk["window_end"]
    if isinstance(since, str):
        since = parse_datetime(since)
    if isinstance(until, str):
        until = parse_datetime(until)
    if chunk["coverage_mode"] == "unsupported":
        return {"skipped": True, "reason": chunk["coverage_note"]}
    if mode == "extract":
        return {
            "skipped": True,
            "reason": (
                "extract mode is intentionally not run from source_backfill; "
                "run the pain extraction pipeline separately after fetch/project"
            ),
            "details": {
                "query": chunk["query"],
                "coverage_mode": chunk["coverage_mode"],
                "coverage_note": chunk["coverage_note"],
            },
        }
    adapter = get_adapter(source)
    rows_fetched = 0
    rows_projected = 0
    if mode in {"fetch", "fetch-project"}:
        rows_fetched = adapter.fetch(query=chunk["query"], since=since, until=until, limit=_chunk_limit(source))
    if mode in {"project", "fetch-project"}:
        if source == "reddit" and hasattr(adapter, "project_subreddit_window"):
            rows_projected = adapter.project_subreddit_window(
                subreddit=str(chunk["target_id"] or chunk["query"]),
                since=since,
                until=until,
            )
        else:
            rows_projected = adapter.project(since=since, until=until)
    return {
        "rows_fetched": rows_fetched,
        "rows_projected": rows_projected,
        "details": {
            "query": chunk["query"],
            "coverage_mode": chunk["coverage_mode"],
            "coverage_note": chunk["coverage_note"],
        },
    }


def _chunk_limit(source: str) -> int:
    cfg = runtime_config.get_source_config(source)
    return int(cfg.fetch_limit if cfg else 1000)


def _finish_chunk(
    chunk_id: int,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    if status not in VALID_CHUNK_STATUSES:
        raise ValueError(f"invalid chunk status: {status}")
    result = result or {}
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE source_backfill_chunks
                SET status = %s,
                    finished_at = NOW(),
                    rows_fetched = %s,
                    rows_projected = %s,
                    rows_extracted = %s,
                    error = %s,
                    details = COALESCE(details, '{}'::jsonb) || %s::jsonb
                WHERE id = %s
                RETURNING job_id
                """,
                (
                    status,
                    int(result.get("rows_fetched", 0) or 0),
                    int(result.get("rows_projected", 0) or 0),
                    int(result.get("rows_extracted", 0) or 0),
                    error,
                    psycopg2.extras.Json(result.get("details", {})),
                    int(chunk_id),
                ),
            )
            row = cur.fetchone()
            job_id = int(row[0]) if row else None
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)
    if job_id is not None:
        _refresh_job_counts(job_id)


def _mark_job_started(job_id: int) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE source_backfill_jobs
                SET status = CASE WHEN status = 'queued' THEN 'running' ELSE status END,
                    started_at = COALESCE(started_at, NOW())
                WHERE id = %s
                """,
                (int(job_id),),
            )
        conn.commit()
    finally:
        put_conn(conn)


def _refresh_job_counts(job_id: int) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH counts AS (
                    SELECT
                        job_id,
                        COUNT(*) FILTER (WHERE status = 'queued') AS queued_chunks,
                        COUNT(*) FILTER (WHERE status = 'running') AS running_chunks,
                        COUNT(*) FILTER (WHERE status = 'succeeded') AS succeeded_chunks,
                        COUNT(*) FILTER (WHERE status = 'failed') AS failed_chunks,
                        COUNT(*) FILTER (WHERE status = 'skipped') AS skipped_chunks,
                        COUNT(*) FILTER (WHERE status = 'empty') AS empty_chunks,
                        COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled_chunks,
                        COALESCE(SUM(rows_fetched), 0) AS rows_fetched,
                        COALESCE(SUM(rows_projected), 0) AS rows_projected,
                        COALESCE(SUM(rows_extracted), 0) AS rows_extracted,
                        COUNT(*) FILTER (
                            WHERE status IN ('succeeded', 'failed', 'skipped', 'empty', 'cancelled')
                        ) AS done_chunks,
                        COUNT(*) AS total_chunks
                    FROM source_backfill_chunks
                    WHERE job_id = %s
                    GROUP BY job_id
                )
                UPDATE source_backfill_jobs j
                SET queued_chunks = counts.queued_chunks,
                    running_chunks = counts.running_chunks,
                    succeeded_chunks = counts.succeeded_chunks,
                    failed_chunks = counts.failed_chunks,
                    skipped_chunks = counts.skipped_chunks,
                    empty_chunks = counts.empty_chunks,
                    cancelled_chunks = counts.cancelled_chunks,
                    rows_fetched = counts.rows_fetched,
                    rows_projected = counts.rows_projected,
                    rows_extracted = counts.rows_extracted,
                    status = CASE
                        WHEN j.status = 'cancelled'
                         AND counts.running_chunks > 0 THEN 'cancelled'
                        WHEN j.status = 'cancelled' THEN 'cancelled'
                        WHEN counts.failed_chunks > 0
                         AND counts.done_chunks = counts.total_chunks THEN 'failed'
                        WHEN counts.done_chunks = counts.total_chunks THEN 'succeeded'
                        WHEN counts.running_chunks > 0 THEN 'running'
                        WHEN counts.queued_chunks > 0 THEN 'queued'
                        ELSE j.status
                    END,
                    finished_at = CASE
                        WHEN counts.done_chunks = counts.total_chunks THEN COALESCE(finished_at, NOW())
                        WHEN j.status = 'cancelled'
                         AND counts.running_chunks = 0 THEN COALESCE(finished_at, NOW())
                        ELSE finished_at
                    END
                FROM counts
                WHERE j.id = counts.job_id
                """,
                (int(job_id),),
            )
        conn.commit()
    finally:
        put_conn(conn)


def _job_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key in (
        "date_from",
        "date_to",
        "created_at",
        "started_at",
        "finished_at",
        "cancel_requested_at",
    ):
        out[key] = _iso(out.get(key))
    return out


def _chunk_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key in ("window_start", "window_end", "created_at", "updated_at", "started_at", "finished_at"):
        out[key] = _iso(out.get(key))
    return out


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
