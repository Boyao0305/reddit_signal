"""Build a new cluster run over a rolling window (ported from ``scripts/b_prep_6_recluster_90d.py``).

Creates the ``analysis_runs`` row, runs UMAP + HDBSCAN over the window's embedded pains, records the
clustering-time counts, and computes the v1 ranking scores (still written to the deprecated
``pain_clusters`` columns; kept for parity until it is decided whether anything reads them).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from reddit_signal.pain_extraction import clustering, db, ranking

logger = logging.getLogger(__name__)

RUN_LABEL = "v2-phase2-90d"


class NotEnoughData(RuntimeError):
    """The window holds too few embedded pains to cluster."""


def count_embedded_pains(window_start: datetime, window_end: datetime) -> int:
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM pain_objects
                WHERE embedding IS NOT NULL AND source_created >= %s AND source_created < %s
                """,
                (window_start, window_end),
            )
            return int(cur.fetchone()[0])
    finally:
        conn.rollback()
        db.put_conn(conn)


def build_rolling_run(
    *, now: datetime, window_days: int = 90, min_cluster_size: int = 30, min_samples: int = 10
) -> int:
    window_end = now
    window_start = window_end - timedelta(days=window_days)
    n = count_embedded_pains(window_start, window_end)
    logger.info("Window %s -> %s: %d embedded pains", window_start.date(), window_end.date(), n)
    if n < min_cluster_size:
        raise NotEnoughData(f"only {n} embedded pains in the {window_days}-day window")

    run_id = db.create_analysis_run(
        window_start=window_start,
        window_end=window_end,
        resolution="v2-90d",
        params={
            "min_cluster_size": min_cluster_size,
            "min_samples": min_samples,
            "umap_dims": 50,
            "umap_neighbors": 15,
            "label": RUN_LABEL,
            "v2_phase": "phase2-90d-recluster",
            "rolling_window_days": window_days,
        },
    )
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE analysis_runs SET label = %s, is_legacy = FALSE WHERE id = %s", (RUN_LABEL, run_id))
        conn.commit()
    finally:
        db.put_conn(conn)

    result = clustering.cluster_all(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        window_start=window_start,
        window_end=window_end,
        run_id=run_id,
        deterministic=True,
    )
    db.update_analysis_run(
        run_id=run_id,
        cluster_count=result.get("clusters", 0),
        noise_count=result.get("noise", 0),
        total_points=result.get("total", 0),
    )
    if result.get("clusters", 0) == 0:
        raise NotEnoughData(f"run {run_id}: HDBSCAN formed no clusters")
    ranking.rank_clusters(run_id=run_id)
    logger.info("Rolling run %d built: %s", run_id, result)
    return run_id
