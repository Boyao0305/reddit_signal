"""PostgreSQL-backed queue for daily/monthly/extraction pipeline jobs."""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import psycopg2
import psycopg2.extras

from reddit_signal import config, db


VALID_JOB_TYPES = {"daily", "monthly", "extraction"}
VALID_JOB_STATUSES = {
    "queued",
    "running",
    "cancel_requested",
    "cancelled",
    "succeeded",
    "failed",
}
VALID_STEP_STATUSES = {"pending", "running", "succeeded", "failed", "skipped", "cancelled"}


@dataclass(frozen=True)
class PipelineJob:
    id: int
    job_type: str
    status: str
    requested_by: str | None
    request_source: str
    run_after: datetime
    started_at: datetime | None
    finished_at: datetime | None
    worker_id: str | None
    exit_code: int | None
    error: str | None
    log_tail: str | None
    params: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    cancel_requested_at: datetime | None = None
    cancel_requested_by: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "PipelineJob":
        data = dict(row)
        data.setdefault("params", {})
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "job_type": self.job_type,
            "status": self.status,
            "requested_by": self.requested_by,
            "request_source": self.request_source,
            "run_after": _iso(self.run_after),
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "worker_id": self.worker_id,
            "exit_code": self.exit_code,
            "error": self.error,
            "log_tail": self.log_tail,
            "params": self.params or {},
            "cancel_requested_at": _iso(self.cancel_requested_at),
            "cancel_requested_by": self.cancel_requested_by,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
        }


@dataclass(frozen=True)
class PipelineJobStep:
    id: int
    job_id: int
    stage_name: str
    stage_order: int
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    exit_code: int | None
    error: str | None
    log_tail: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "PipelineJobStep":
        return cls(**dict(row))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "stage_name": self.stage_name,
            "stage_order": self.stage_order,
            "status": self.status,
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "exit_code": self.exit_code,
            "error": self.error,
            "log_tail": self.log_tail,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
        }

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()


@dataclass(frozen=True)
class PipelineJobEvent:
    id: int
    job_id: int
    stage_name: str | None
    event_type: str
    message: str
    details: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "PipelineJobEvent":
        return cls(**dict(row))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "stage_name": self.stage_name,
            "event_type": self.event_type,
            "message": self.message,
            "details": self.details or {},
            "created_at": _iso(self.created_at),
        }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def connect():
    return db.connect()


def _validate_job_type(job_type: str) -> None:
    if job_type not in VALID_JOB_TYPES:
        raise ValueError(f"unsupported pipeline job type: {job_type!r}")


def create_job(
    job_type: str,
    *,
    requested_by: str | None = None,
    request_source: str = "dashboard",
    params: dict[str, Any] | None = None,
) -> PipelineJob:
    _validate_job_type(job_type)
    with connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO pipeline_jobs (job_type, requested_by, request_source, params)
                VALUES (%s, %s, %s, %s::jsonb)
                RETURNING *
                """,
                (
                    job_type,
                    requested_by,
                    request_source,
                    psycopg2.extras.Json(params or {}),
                ),
            )
            return PipelineJob.from_row(cur.fetchone())


def create_job_once_per_window(
    job_type: str,
    *,
    request_source: str,
    window_key: str,
) -> PipelineJob | None:
    """Create a scheduled job once per UTC day/month window.

    The existing schema intentionally stays small; request_source carries the
    deterministic window marker, e.g. scheduler:daily:2026-06-16.
    """
    _validate_job_type(job_type)
    source = f"{request_source}:{job_type}:{window_key}"
    with connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT *
                FROM pipeline_jobs
                WHERE job_type = %s
                  AND request_source = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (job_type, source),
            )
            if cur.fetchone():
                return None
            cur.execute(
                """
                INSERT INTO pipeline_jobs (job_type, request_source)
                VALUES (%s, %s)
                RETURNING *
                """,
                (job_type, source),
            )
            return PipelineJob.from_row(cur.fetchone())


def latest_jobs(limit: int = 10) -> list[PipelineJob]:
    with connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT *
                FROM pipeline_jobs
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [PipelineJob.from_row(row) for row in cur.fetchall()]


def latest_job_by_type(job_type: str) -> PipelineJob | None:
    _validate_job_type(job_type)
    with connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT *
                FROM pipeline_jobs
                WHERE job_type = %s
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (job_type,),
            )
            row = cur.fetchone()
            return PipelineJob.from_row(row) if row else None


def claim_next_job(worker_id: str, *, job_types: list[str] | tuple[str, ...] | set[str] | None = None) -> PipelineJob | None:
    requested_types = list(job_types or [])
    for job_type in requested_types:
        _validate_job_type(job_type)
    type_filter = "AND job_type = ANY(%s)" if requested_types else ""
    params: list[Any] = []
    if requested_types:
        params.append(requested_types)
    params.append(worker_id)
    with connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                WITH next_job AS (
                    SELECT id
                    FROM pipeline_jobs
                    WHERE status = 'queued'
                      AND run_after <= NOW()
                      {type_filter}
                    ORDER BY run_after ASC, id ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE pipeline_jobs j
                SET status = 'running',
                    started_at = NOW(),
                    worker_id = %s,
                    error = NULL,
                    log_tail = NULL,
                    exit_code = NULL
                FROM next_job
                WHERE j.id = next_job.id
                RETURNING j.*
                """,
                tuple(params),
            )
            row = cur.fetchone()
            return PipelineJob.from_row(row) if row else None


def finish_job(
    job_id: int,
    *,
    status: str,
    exit_code: int,
    error: str | None = None,
    log_tail: str | None = None,
) -> PipelineJob:
    if status not in {"succeeded", "failed", "cancelled"}:
        raise ValueError("finish_job status must be 'succeeded', 'failed', or 'cancelled'")
    with connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE pipeline_jobs
                SET status = %s,
                    finished_at = NOW(),
                    exit_code = %s,
                    error = %s,
                    log_tail = %s
                WHERE id = %s
                RETURNING *
                """,
                (status, exit_code, error, log_tail, job_id),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError(f"pipeline_jobs.id={job_id} not found")
            return PipelineJob.from_row(row)


def request_cancel(job_id: int, *, requested_by: str | None = None) -> PipelineJob:
    """Request cancellation for a queued/running job.

    Queued jobs can be marked cancelled immediately. Running jobs move to
    cancel_requested; the worker will terminate the child process and finalize
    the job as cancelled.
    """
    with connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT *
                FROM pipeline_jobs
                WHERE id = %s
                FOR UPDATE
                """,
                (job_id,),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError(f"pipeline_jobs.id={job_id} not found")
            status = row["status"]
            if status == "queued":
                cur.execute(
                    """
                    UPDATE pipeline_jobs
                    SET status = 'cancelled',
                        cancel_requested_at = NOW(),
                        cancel_requested_by = %s,
                        finished_at = NOW(),
                        exit_code = 130,
                        error = 'cancelled before start'
                    WHERE id = %s
                    RETURNING *
                    """,
                    (requested_by, job_id),
                )
                updated = PipelineJob.from_row(cur.fetchone())
                _insert_event_cur(
                    cur,
                    job_id,
                    event_type="cancelled",
                    message="Job cancelled before worker start",
                    details={"requested_by": requested_by},
                )
                return updated
            if status == "running":
                cur.execute(
                    """
                    UPDATE pipeline_jobs
                    SET status = 'cancel_requested',
                        cancel_requested_at = NOW(),
                        cancel_requested_by = %s
                    WHERE id = %s
                    RETURNING *
                    """,
                    (requested_by, job_id),
                )
                updated = PipelineJob.from_row(cur.fetchone())
                _insert_event_cur(
                    cur,
                    job_id,
                    event_type="cancel_requested",
                    message="Cancellation requested",
                    details={"requested_by": requested_by},
                )
                return updated
            if status == "cancel_requested":
                return PipelineJob.from_row(row)
            raise ValueError(f"Cannot cancel job in status {status!r}")


def is_cancel_requested(job_id: int) -> bool:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status = 'cancel_requested' FROM pipeline_jobs WHERE id = %s",
                (job_id,),
            )
            row = cur.fetchone()
            return bool(row and row[0])


def heartbeat(
    worker_id: str,
    *,
    mode: str = "ec2-worker",
    current_job_id: int | None = None,
    version: str | None = None,
) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pipeline_worker_heartbeats
                    (worker_id, mode, current_job_id, version)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (worker_id) DO UPDATE SET
                    mode = EXCLUDED.mode,
                    last_seen_at = NOW(),
                    current_job_id = EXCLUDED.current_job_id,
                    version = EXCLUDED.version
                """,
                (worker_id, mode, current_job_id, version),
            )


def recent_workers(limit: int = 10) -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT worker_id, mode, started_at, last_seen_at, current_job_id, version
                FROM pipeline_worker_heartbeats
                ORDER BY last_seen_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = []
            for row in cur.fetchall():
                item = dict(row)
                for key in ("started_at", "last_seen_at"):
                    item[key] = _iso(item[key])
                rows.append(item)
            return rows


def default_worker_id() -> str:
    return config.get().worker_id or f"{socket.gethostname()}:{os.getpid()}"


def start_step(
    job_id: int,
    stage_name: str,
    *,
    stage_order: int = 0,
) -> PipelineJobStep:
    """Mark a pipeline stage as running, creating the row if needed."""
    with connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO pipeline_job_steps
                    (job_id, stage_name, stage_order, status, started_at)
                VALUES (%s, %s, %s, 'running', NOW())
                ON CONFLICT (job_id, stage_name) DO UPDATE SET
                    status = 'running',
                    started_at = COALESCE(pipeline_job_steps.started_at, NOW()),
                    finished_at = NULL,
                    exit_code = NULL,
                    error = NULL,
                    log_tail = NULL
                RETURNING *
                """,
                (job_id, stage_name, stage_order),
            )
            return PipelineJobStep.from_row(cur.fetchone())


def finish_step(
    job_id: int,
    stage_name: str,
    *,
    status: str,
    exit_code: int,
    error: str | None = None,
    log_tail: str | None = None,
) -> PipelineJobStep:
    """Record the final state of a pipeline stage."""
    if status not in VALID_STEP_STATUSES:
        raise ValueError(f"invalid step status: {status!r}")
    with connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE pipeline_job_steps
                SET status = %s,
                    finished_at = NOW(),
                    exit_code = %s,
                    error = %s,
                    log_tail = %s
                WHERE job_id = %s AND stage_name = %s
                RETURNING *
                """,
                (status, exit_code, error, log_tail, job_id, stage_name),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError(
                    f"pipeline_job_steps job_id={job_id} stage_name={stage_name!r} not found"
                )
            return PipelineJobStep.from_row(row)


def cancel_running_steps(
    job_id: int,
    *,
    error: str | None = None,
    running_status: str = "cancelled",
) -> None:
    if running_status not in {"cancelled", "failed"}:
        raise ValueError("running_status must be 'cancelled' or 'failed'")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pipeline_job_steps
                SET status = CASE
                        WHEN status = 'running' THEN %s
                        WHEN status = 'pending' THEN 'skipped'
                        ELSE status
                    END,
                    finished_at = COALESCE(finished_at, NOW()),
                    exit_code = COALESCE(exit_code, 130),
                    error = COALESCE(error, %s)
                WHERE job_id = %s
                  AND status IN ('running', 'pending')
                """,
                (running_status, error, job_id),
            )


def skip_remaining_steps(job_id: int, from_order: int) -> None:
    """Mark pending stages after `from_order` as skipped when a job aborts early."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pipeline_job_steps
                SET status = 'skipped',
                    finished_at = COALESCE(finished_at, NOW())
                WHERE job_id = %s
                  AND stage_order > %s
                  AND status = 'pending'
                """,
                (job_id, from_order),
            )


def list_jobs(
    *,
    job_type: str | None = None,
    status: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[PipelineJob], int]:
    """Return paginated jobs and total count matching the filters."""
    if job_type is not None:
        _validate_job_type(job_type)
    conditions = ["1=1"]
    params: list[Any] = []
    if job_type:
        conditions.append("job_type = %s")
        params.append(job_type)
    if status:
        if status not in VALID_JOB_STATUSES:
            raise ValueError(f"invalid pipeline job status: {status!r}")
        conditions.append("status = %s")
        params.append(status)
    if from_date:
        conditions.append("created_at >= %s")
        params.append(from_date)
    if to_date:
        conditions.append("created_at < %s")
        params.append(to_date)

    where_clause = " AND ".join(conditions)
    count_sql = f"SELECT COUNT(*) FROM pipeline_jobs WHERE {where_clause}"
    query_sql = f"""
        SELECT *
        FROM pipeline_jobs
        WHERE {where_clause}
        ORDER BY created_at DESC, id DESC
        LIMIT %s OFFSET %s
    """

    offset = max(0, page - 1) * page_size
    with connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(count_sql, params)
            total = cur.fetchone()["count"]
            cur.execute(query_sql, params + [page_size, offset])
            jobs = [PipelineJob.from_row(row) for row in cur.fetchall()]
            return jobs, total


def get_job_with_steps(job_id: int) -> tuple[PipelineJob | None, list[PipelineJobStep]]:
    """Return a single job and its ordered stages."""
    with connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM pipeline_jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
            job = PipelineJob.from_row(row) if row else None
            cur.execute(
                """
                SELECT *
                FROM pipeline_job_steps
                WHERE job_id = %s
                ORDER BY stage_order ASC, id ASC
                """,
                (job_id,),
            )
            steps = [PipelineJobStep.from_row(r) for r in cur.fetchall()]
            return job, steps


def list_job_events(job_id: int, *, limit: int = 200) -> list[PipelineJobEvent]:
    with connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT *
                FROM pipeline_job_events
                WHERE job_id = %s
                ORDER BY created_at ASC, id ASC
                LIMIT %s
                """,
                (job_id, limit),
            )
            return [PipelineJobEvent.from_row(row) for row in cur.fetchall()]


def add_job_event(
    job_id: int,
    *,
    message: str,
    stage_name: str | None = None,
    event_type: str = "log",
    details: dict[str, Any] | None = None,
) -> PipelineJobEvent | None:
    if not message:
        return None
    with connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO pipeline_job_events
                    (job_id, stage_name, event_type, message, details)
                VALUES (%s, %s, %s, %s, %s::jsonb)
                RETURNING *
                """,
                (
                    job_id,
                    stage_name,
                    event_type,
                    message[:2000],
                    psycopg2.extras.Json(details or {}),
                ),
            )
            return PipelineJobEvent.from_row(cur.fetchone())


def _insert_event_cur(
    cur,
    job_id: int,
    *,
    message: str,
    stage_name: str | None = None,
    event_type: str = "log",
    details: dict[str, Any] | None = None,
) -> None:
    cur.execute(
        """
        INSERT INTO pipeline_job_events
            (job_id, stage_name, event_type, message, details)
        VALUES (%s, %s, %s, %s, %s::jsonb)
        """,
        (
            job_id,
            stage_name,
            event_type,
            message[:2000],
            psycopg2.extras.Json(details or {}),
        ),
    )


def get_job_stats(
    *,
    job_type: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate run statistics, optionally filtered by type and date range."""
    if job_type is not None:
        _validate_job_type(job_type)
    conditions = ["1=1"]
    params: list[Any] = []
    if job_type:
        conditions.append("job_type = %s")
        params.append(job_type)
    if from_date:
        conditions.append("created_at >= %s")
        params.append(from_date)
    if to_date:
        conditions.append("created_at < %s")
        params.append(to_date)

    where_clause = " AND ".join(conditions)
    sql = f"""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE status = 'succeeded') AS succeeded,
            COUNT(*) FILTER (WHERE status = 'failed') AS failed,
            COUNT(*) FILTER (WHERE status = 'running') AS running,
            AVG(EXTRACT(EPOCH FROM (finished_at - started_at)))
                FILTER (WHERE started_at IS NOT NULL AND finished_at IS NOT NULL)
                AS avg_duration_seconds
        FROM pipeline_jobs
        WHERE {where_clause}
    """

    with connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            total = row["total"] or 0
            succeeded = row["succeeded"] or 0
            failed = row["failed"] or 0
            running = row["running"] or 0
            success_rate = (succeeded / total) if total > 0 else 0.0
            return {
                "total": total,
                "succeeded": succeeded,
                "failed": failed,
                "running": running,
                "success_rate": round(success_rate, 4),
                "avg_duration_seconds": row["avg_duration_seconds"],
                "failures_by_stage": _failures_by_stage(
                    job_type=job_type, from_date=from_date, to_date=to_date
                ),
            }


def _failures_by_stage(
    *,
    job_type: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> list[dict[str, Any]]:
    """Count failed steps grouped by stage_name within the given job filters."""
    conditions = ["s.status = 'failed'"]
    params: list[Any] = []
    if job_type:
        conditions.append("j.job_type = %s")
        params.append(job_type)
    if from_date:
        conditions.append("j.created_at >= %s")
        params.append(from_date)
    if to_date:
        conditions.append("j.created_at < %s")
        params.append(to_date)

    where_clause = " AND ".join(conditions)
    sql = f"""
        SELECT s.stage_name, COUNT(*) AS count
        FROM pipeline_job_steps s
        JOIN pipeline_jobs j ON j.id = s.job_id
        WHERE {where_clause}
        GROUP BY s.stage_name
        ORDER BY count DESC, s.stage_name ASC
    """

    with connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]


def running_jobs(job_types: list[str] | tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    """Jobs in ``running``/``cancel_requested`` with their claiming worker (for stale-job recovery)."""
    type_filter = "AND job_type = ANY(%s)" if job_types else ""
    with connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT id, job_type, worker_id, started_at
                FROM pipeline_jobs
                WHERE status IN ('running', 'cancel_requested') {type_filter}
                ORDER BY id
                """,
                (list(job_types),) if job_types else (),
            )
            return [dict(r) for r in cur.fetchall()]


def all_heartbeats() -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT worker_id, last_seen_at, current_job_id FROM pipeline_worker_heartbeats")
            return [dict(r) for r in cur.fetchall()]


def fail_stale_job(job_id: int, *, reason: str) -> bool:
    """Mark a job whose worker is gone as failed. Returns False if it is no longer running."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pipeline_jobs
                SET status = 'failed', finished_at = NOW(), exit_code = COALESCE(exit_code, 1),
                    error = %s
                WHERE id = %s AND status IN ('running', 'cancel_requested')
                """,
                (f"worker lost: {reason}", job_id),
            )
            changed = cur.rowcount == 1
    if changed:
        cancel_running_steps(job_id, error=f"worker lost: {reason}", running_status="failed")
        add_job_event(job_id, message=f"Marked failed by recovery: {reason}", event_type="error")
    return changed
