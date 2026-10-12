"""B-prep.7a — Weekly aggregator for pain_volume_weekly.

Populates one row per (cluster_run_id, cluster_id, week_start, audience_type,
function_tag) from pain_objects via pain_cluster_members. Idempotent (UPSERT).

Modes:
  --mode rebuild     : wipe + repopulate all weeks for this run_id
  --mode incremental : upsert only the current week (used by B-ops.3 daily)

Usage:
  python -m reddit_signal.temporal.aggregate_weekly --run-id 10 --mode rebuild
  python -m reddit_signal.temporal.aggregate_weekly --run-id 10 --mode incremental
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

log = logging.getLogger("aggregate_weekly")


REBUILD_SQL = """
WITH src AS (
    SELECT
        pc.run_id                                              AS cluster_run_id,
        pc.id                                                  AS cluster_id,
        date_trunc('week', po.source_created)::date            AS week_start,
        COALESCE(ca.audience_type, 'unknown')                  AS audience_type,
        'unknown'::varchar(64)                                 AS function_tag,
        COUNT(*)                                               AS pain_count
    FROM pain_clusters pc
    JOIN pain_cluster_members pcm ON pcm.cluster_id = pc.id
    JOIN pain_objects po          ON po.id = pcm.pain_id
    LEFT JOIN cluster_audience ca ON ca.cluster_id = pc.id
                                  AND ca.classifier_version = 'v1'
    WHERE pc.run_id = %(run_id)s
      AND po.source_created IS NOT NULL
    GROUP BY pc.run_id, pc.id, date_trunc('week', po.source_created)::date,
             COALESCE(ca.audience_type, 'unknown')
)
INSERT INTO pain_volume_weekly
    (cluster_run_id, cluster_id, week_start, audience_type, function_tag, pain_count)
SELECT cluster_run_id, cluster_id, week_start, audience_type, function_tag, pain_count
FROM src
ON CONFLICT (cluster_run_id, cluster_id, week_start, audience_type, function_tag)
DO UPDATE SET pain_count = EXCLUDED.pain_count;
"""


INCREMENTAL_SQL = """
WITH this_week AS (
    SELECT date_trunc('week', NOW())::date AS w_start
),
src AS (
    SELECT
        pc.run_id                                              AS cluster_run_id,
        pc.id                                                  AS cluster_id,
        tw.w_start                                             AS week_start,
        COALESCE(ca.audience_type, 'unknown')                  AS audience_type,
        'unknown'::varchar(64)                                 AS function_tag,
        COUNT(*)                                               AS pain_count
    FROM pain_clusters pc
    JOIN pain_cluster_members pcm ON pcm.cluster_id = pc.id
    JOIN pain_objects po          ON po.id = pcm.pain_id
    LEFT JOIN cluster_audience ca ON ca.cluster_id = pc.id
                                  AND ca.classifier_version = 'v1'
    CROSS JOIN this_week tw
    WHERE pc.run_id = %(run_id)s
      AND po.source_created >= tw.w_start
      AND po.source_created <  tw.w_start + INTERVAL '7 days'
    GROUP BY pc.run_id, pc.id, tw.w_start, COALESCE(ca.audience_type, 'unknown')
)
INSERT INTO pain_volume_weekly
    (cluster_run_id, cluster_id, week_start, audience_type, function_tag, pain_count)
SELECT cluster_run_id, cluster_id, week_start, audience_type, function_tag, pain_count
FROM src
ON CONFLICT (cluster_run_id, cluster_id, week_start, audience_type, function_tag)
DO UPDATE SET pain_count = EXCLUDED.pain_count;
"""


# Recompute share_global / share_field for every (week, audience) in the run.
# Runs after both REBUILD_SQL and INCREMENTAL_SQL because INSERT...ON CONFLICT only
# updates pain_count; without this pass the shares would silently revert to NULL on
# the current week each daily run.
# Scope is the whole run (not just the current week) because daily centroid
# assignment can backfill earlier weeks if a new pain has an older source_created.
# Cheap full UPDATE (~32K rows / run, sub-second).
RECOMPUTE_SHARES_SQL = """
WITH week_audience_totals AS (
    SELECT cluster_run_id, week_start, audience_type, SUM(pain_count) AS aud_total
    FROM pain_volume_weekly
    WHERE cluster_run_id = %(run_id)s
    GROUP BY cluster_run_id, week_start, audience_type
),
week_global_totals AS (
    SELECT cluster_run_id, week_start, SUM(pain_count) AS global_total
    FROM pain_volume_weekly
    WHERE cluster_run_id = %(run_id)s
    GROUP BY cluster_run_id, week_start
)
UPDATE pain_volume_weekly pvw
SET share_global = pvw.pain_count::float / NULLIF(wg.global_total, 0),
    share_field  = pvw.pain_count::float / NULLIF(wt.aud_total, 0)
FROM week_global_totals wg, week_audience_totals wt
WHERE pvw.cluster_run_id = %(run_id)s
  AND wg.cluster_run_id  = pvw.cluster_run_id AND wg.week_start = pvw.week_start
  AND wt.cluster_run_id  = pvw.cluster_run_id AND wt.week_start = pvw.week_start
  AND wt.audience_type   = pvw.audience_type;
"""


def aggregate(run_id: int, mode: str) -> dict:
    sql = REBUILD_SQL if mode == "rebuild" else INCREMENTAL_SQL
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            if mode == "rebuild":
                # Clear existing rows for this run so deletions/reassigns don't leave stale data.
                cur.execute(
                    "DELETE FROM pain_volume_weekly WHERE cluster_run_id = %s",
                    (run_id,),
                )
                deleted = cur.rowcount
                log.info("Rebuild mode: deleted %d existing rows", deleted)
            cur.execute(sql, {"run_id": run_id})
            written = cur.rowcount
            # Keep share_global / share_field consistent with pain_count for this run.
            cur.execute(RECOMPUTE_SHARES_SQL, {"run_id": run_id})
            shares_updated = cur.rowcount
            log.info("Recomputed shares: %d rows updated", shares_updated)
        conn.commit()
    finally:
        db.put_conn(conn)
    return {"mode": mode, "rows_written": written, "shares_updated": shares_updated}


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", type=int, required=True)
    ap.add_argument("--mode", choices=["rebuild", "incremental"], default="incremental")
    args = ap.parse_args()
    log.info("Aggregating run_id=%d mode=%s", args.run_id, args.mode)
    res = aggregate(args.run_id, args.mode)
    log.info("Done: %s", res)


if __name__ == "__main__":
    main()
