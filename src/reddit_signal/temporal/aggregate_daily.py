"""Daily pain volume aggregation for burst detection.

Populates one row per (cluster_run_id, cluster_id, day_start). Used by the
dashboard snapshot refresh to compute 3d vs prior 28d burst metrics.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from reddit_signal.pain_extraction import db

log = logging.getLogger("aggregate_daily")


REBUILD_SQL = """
WITH src AS (
    SELECT
        pc.run_id                                   AS cluster_run_id,
        pc.id                                       AS cluster_id,
        po.source_created::date                     AS day_start,
        COUNT(*)                                    AS pain_count
    FROM pain_clusters pc
    JOIN pain_cluster_members pcm ON pcm.cluster_id = pc.id
    JOIN pain_objects po          ON po.id = pcm.pain_id
    WHERE pc.run_id = %(run_id)s
      AND po.source_created IS NOT NULL
    GROUP BY pc.run_id, pc.id, po.source_created::date
)
INSERT INTO pain_volume_daily
    (cluster_run_id, cluster_id, day_start, pain_count)
SELECT cluster_run_id, cluster_id, day_start, pain_count
FROM src
ON CONFLICT (cluster_run_id, cluster_id, day_start)
DO UPDATE SET
    pain_count = EXCLUDED.pain_count,
    created_at = NOW();
"""


INCREMENTAL_SQL = """
WITH recent_days AS (
    SELECT ((NOW() AT TIME ZONE 'UTC')::date - offs) AS day_start
    FROM generate_series(0, 31) AS offs
),
src AS (
    SELECT
        pc.run_id                                   AS cluster_run_id,
        pc.id                                       AS cluster_id,
        rd.day_start                                AS day_start,
        COUNT(po.id)                                AS pain_count
    FROM pain_clusters pc
    JOIN pain_cluster_members pcm ON pcm.cluster_id = pc.id
    JOIN pain_objects po          ON po.id = pcm.pain_id
    JOIN recent_days rd           ON po.source_created::date = rd.day_start
    WHERE pc.run_id = %(run_id)s
      AND po.source_created IS NOT NULL
    GROUP BY pc.run_id, pc.id, rd.day_start
)
INSERT INTO pain_volume_daily
    (cluster_run_id, cluster_id, day_start, pain_count)
SELECT cluster_run_id, cluster_id, day_start, pain_count
FROM src
ON CONFLICT (cluster_run_id, cluster_id, day_start)
DO UPDATE SET
    pain_count = EXCLUDED.pain_count,
    created_at = NOW();
"""


RECOMPUTE_SHARES_SQL = """
WITH day_totals AS (
    SELECT cluster_run_id, day_start, SUM(pain_count) AS total_pain
    FROM pain_volume_daily
    WHERE cluster_run_id = %(run_id)s
    GROUP BY cluster_run_id, day_start
)
UPDATE pain_volume_daily pvd
SET share_global = pvd.pain_count::float / NULLIF(dt.total_pain, 0)
FROM day_totals dt
WHERE pvd.cluster_run_id = %(run_id)s
  AND dt.cluster_run_id = pvd.cluster_run_id
  AND dt.day_start = pvd.day_start;
"""


# ── corpus-level daily volume (assigned vs orphan relative to a run) ──────────
# total_count   = all pain_objects created that day (global, not per-cluster).
# assigned_count = pains that day which belong to a cluster of this run.
# orphan_count  = total_count - assigned_count (mid-month new topics that failed
#                 centroid assignment live here). Powers the corpus burst sentinel.

CORPUS_REBUILD_SQL = """
WITH day_totals AS (
    SELECT
        po.source_created::date AS day_start,
        COUNT(*)                AS total_count
    FROM pain_objects po
    WHERE po.source_created IS NOT NULL
    GROUP BY po.source_created::date
),
assigned AS (
    SELECT
        po.source_created::date AS day_start,
        COUNT(*)                AS assigned_count
    FROM pain_objects po
    JOIN pain_cluster_members pcm ON pcm.pain_id = po.id
    JOIN pain_clusters pc         ON pc.id = pcm.cluster_id
    WHERE pc.run_id = %(run_id)s
      AND po.source_created IS NOT NULL
    GROUP BY po.source_created::date
)
INSERT INTO pain_volume_daily_corpus
    (cluster_run_id, day_start, total_count, assigned_count, orphan_count)
SELECT
    %(run_id)s,
    dt.day_start,
    dt.total_count,
    COALESCE(a.assigned_count, 0),
    dt.total_count - COALESCE(a.assigned_count, 0)
FROM day_totals dt
LEFT JOIN assigned a ON a.day_start = dt.day_start
ON CONFLICT (cluster_run_id, day_start)
DO UPDATE SET
    total_count    = EXCLUDED.total_count,
    assigned_count = EXCLUDED.assigned_count,
    orphan_count   = EXCLUDED.orphan_count,
    created_at     = NOW();
"""

CORPUS_INCREMENTAL_SQL = """
WITH recent_days AS (
    SELECT ((NOW() AT TIME ZONE 'UTC')::date - offs) AS day_start
    FROM generate_series(0, 31) AS offs
),
day_totals AS (
    SELECT
        po.source_created::date AS day_start,
        COUNT(*)                AS total_count
    FROM pain_objects po
    JOIN recent_days rd ON po.source_created::date = rd.day_start
    WHERE po.source_created IS NOT NULL
    GROUP BY po.source_created::date
),
assigned AS (
    SELECT
        po.source_created::date AS day_start,
        COUNT(*)                AS assigned_count
    FROM pain_objects po
    JOIN pain_cluster_members pcm ON pcm.pain_id = po.id
    JOIN pain_clusters pc         ON pc.id = pcm.cluster_id
    JOIN recent_days rd           ON po.source_created::date = rd.day_start
    WHERE pc.run_id = %(run_id)s
      AND po.source_created IS NOT NULL
    GROUP BY po.source_created::date
)
INSERT INTO pain_volume_daily_corpus
    (cluster_run_id, day_start, total_count, assigned_count, orphan_count)
SELECT
    %(run_id)s,
    dt.day_start,
    dt.total_count,
    COALESCE(a.assigned_count, 0),
    dt.total_count - COALESCE(a.assigned_count, 0)
FROM day_totals dt
LEFT JOIN assigned a ON a.day_start = dt.day_start
ON CONFLICT (cluster_run_id, day_start)
DO UPDATE SET
    total_count    = EXCLUDED.total_count,
    assigned_count = EXCLUDED.assigned_count,
    orphan_count   = EXCLUDED.orphan_count,
    created_at     = NOW();
"""


def aggregate(run_id: int, mode: str) -> dict:
    sql = REBUILD_SQL if mode == "rebuild" else INCREMENTAL_SQL
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            if mode == "rebuild":
                cur.execute(
                    "DELETE FROM pain_volume_daily WHERE cluster_run_id = %s",
                    (run_id,),
                )
                deleted = cur.rowcount
                log.info("Rebuild mode: deleted %d existing rows", deleted)
            else:
                cur.execute(
                    """
                    DELETE FROM pain_volume_daily
                    WHERE cluster_run_id = %s
                      AND day_start >= (NOW() AT TIME ZONE 'UTC')::date - 31
                      AND day_start <= (NOW() AT TIME ZONE 'UTC')::date
                    """,
                    (run_id,),
                )
                deleted = cur.rowcount
                log.info("Incremental mode: deleted %d recent rows", deleted)
            cur.execute(sql, {"run_id": run_id})
            written = cur.rowcount
            cur.execute(RECOMPUTE_SHARES_SQL, {"run_id": run_id})
            shares_updated = cur.rowcount
            log.info("Recomputed daily shares: %d rows updated", shares_updated)
        conn.commit()
    finally:
        db.put_conn(conn)
    return {"mode": mode, "rows_written": written, "shares_updated": shares_updated}


def aggregate_corpus(run_id: int, mode: str) -> dict:
    """Populate pain_volume_daily_corpus (assigned vs orphan daily totals).

    Mirrors aggregate() but operates on corpus-level (non per-cluster) counts.
    Used by refresh_dashboard_market_opportunities() for the corpus burst sentinel.
    """
    sql = CORPUS_REBUILD_SQL if mode == "rebuild" else CORPUS_INCREMENTAL_SQL
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            if mode == "rebuild":
                cur.execute(
                    "DELETE FROM pain_volume_daily_corpus WHERE cluster_run_id = %s",
                    (run_id,),
                )
                deleted = cur.rowcount
                log.info("Corpus rebuild: deleted %d existing rows", deleted)
            else:
                cur.execute(
                    """
                    DELETE FROM pain_volume_daily_corpus
                    WHERE cluster_run_id = %s
                      AND day_start >= (NOW() AT TIME ZONE 'UTC')::date - 31
                      AND day_start <= (NOW() AT TIME ZONE 'UTC')::date
                    """,
                    (run_id,),
                )
                deleted = cur.rowcount
                log.info("Corpus incremental: deleted %d recent rows", deleted)
            cur.execute(sql, {"run_id": run_id})
            written = cur.rowcount
        conn.commit()
    finally:
        db.put_conn(conn)
    return {"mode": mode, "rows_written": written}


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", type=int, required=True)
    ap.add_argument("--mode", choices=["rebuild", "incremental"], default="incremental")
    ap.add_argument(
        "--corpus",
        action="store_true",
        help="Aggregate corpus-level (assigned vs orphan) daily volume for the "
             "burst sentinel, instead of per-cluster pain_volume_daily.",
    )
    args = ap.parse_args()
    if args.corpus:
        log.info("Aggregating corpus daily run_id=%d mode=%s", args.run_id, args.mode)
        res = aggregate_corpus(args.run_id, args.mode)
    else:
        log.info("Aggregating daily run_id=%d mode=%s", args.run_id, args.mode)
        res = aggregate(args.run_id, args.mode)
    log.info("Done: %s", res)


if __name__ == "__main__":
    main()
