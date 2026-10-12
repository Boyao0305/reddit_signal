"""Repository for analysis_runs and viability_runs queries.

Centralise database access for run metadata so routers stay thin.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Tuple

from fastapi import HTTPException

from reddit_signal.dashboard.db import get_conn


def default_ids() -> Tuple[int, int]:
    """Return (cluster_run_id, viability_run_id) for the current default run.

    Prefers the live run; falls back to the most recent non-building, non-failed
    run that is not marked legacy.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM analysis_runs WHERE status = 'live' ORDER BY id DESC LIMIT 1"
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    """
                    SELECT id FROM analysis_runs
                    WHERE NOT is_legacy
                      AND COALESCE(status, 'historical') NOT IN ('building', 'failed')
                    ORDER BY id DESC
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=500, detail="No cluster run available")
            cluster_run_id = int(row[0])

            cur.execute(
                "SELECT id FROM viability_runs WHERE cluster_run_id = %s ORDER BY id DESC LIMIT 1",
                (cluster_run_id,),
            )
            vrow = cur.fetchone()
            viability_run_id = int(vrow[0]) if vrow else 0
    return cluster_run_id, viability_run_id


def list_analysis_runs() -> list[dict]:
    """Return all analysis_runs ordered by id DESC."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, window_start, window_end, cluster_count,
                       is_legacy, status, live_at,
                       (
                           SELECT MAX(day_start)
                           FROM pain_volume_daily pvd
                           WHERE pvd.cluster_run_id = analysis_runs.id
                       ) AS cluster_data_through_day,
                       (
                           SELECT MAX(snapshot_at)
                           FROM dashboard_market_opportunities dmo
                           WHERE dmo.cluster_run_id = analysis_runs.id
                       ) AS snapshot_at
                FROM analysis_runs
                ORDER BY id DESC
                """
            )
            return [
                {
                    "id": r[0],
                    "window_start": r[1],
                    "window_end": r[2],
                    "cluster_count": r[3],
                    "is_legacy": r[4],
                    "status": r[5],
                    "live_at": r[6],
                    "signal_data_through_day": r[7],
                    "snapshot_at": r[8],
                }
                for r in cur.fetchall()
            ]


def list_viability_runs() -> list[dict]:
    """Return all viability_runs ordered by id DESC."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, cluster_run_id, n_clusters, llm_model
                FROM viability_runs
                ORDER BY id DESC
                """
            )
            return [
                {
                    "id": r[0],
                    "cluster_run_id": r[1],
                    "n_clusters": r[2],
                    "llm_model": r[3],
                }
                for r in cur.fetchall()
            ]


def validate_cluster_run_id(cluster_run_id: int) -> None:
    """Raise HTTP 404 if *cluster_run_id* does not exist in ``analysis_runs``."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM analysis_runs WHERE id = %s", (cluster_run_id,))
            if cur.fetchone() is None:
                raise HTTPException(
                    status_code=404, detail=f"Unknown cluster_run_id={cluster_run_id}"
                )


def week_anchor_for_run(cluster_run_id: int) -> date:
    """Return the anchor-week ``date`` used for velocity / sparkline rendering.

    For a live run whose window covers the current calendar week the anchor is
    ``NOW()`` truncated to Monday; otherwise it is the Monday of the run's
    window-end week.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, window_end, date_trunc('week', NOW())::date AS current_week
                FROM analysis_runs
                WHERE id = %s
                """,
                (cluster_run_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise HTTPException(
                    status_code=404, detail=f"Unknown cluster_run_id={cluster_run_id}"
                )

            if hasattr(row, "keys"):
                status: str = row["status"]
                window_end = row["window_end"]
                current_week: date = row["current_week"]
            else:
                status, window_end, current_week = row  # type: ignore[assignment]

            if window_end is None:
                return current_week
            if isinstance(window_end, datetime):
                window_end = window_end.date()
            window_anchor: date = window_end - timedelta(days=window_end.weekday())
            if status == "live" and window_anchor >= current_week:
                return current_week
            return window_anchor
