"""Daily subreddit-scoped pain volume aggregation for dashboard filters."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from reddit_signal.pain_extraction import db

log = logging.getLogger("aggregate_daily_subreddit")


REBUILD_SQL = """
WITH src AS (
    SELECT
        pc.run_id AS cluster_run_id,
        pc.id AS cluster_id,
        po.source_created::date AS day_start,
        COALESCE(po.subreddit_name, 'unknown') AS subreddit_name,
        COUNT(*) AS pain_count
    FROM pain_clusters pc
    JOIN pain_cluster_members pcm ON pcm.cluster_id = pc.id
    JOIN pain_objects po ON po.id = pcm.pain_id
    WHERE pc.run_id = %(run_id)s
      AND po.source_created IS NOT NULL
    GROUP BY pc.run_id, pc.id, po.source_created::date, COALESCE(po.subreddit_name, 'unknown')
)
INSERT INTO pain_volume_daily_subreddit (
    cluster_run_id,
    cluster_id,
    day_start,
    subreddit_name,
    pain_count
)
SELECT cluster_run_id, cluster_id, day_start, subreddit_name, pain_count
FROM src
ON CONFLICT (cluster_run_id, cluster_id, day_start, subreddit_name)
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
        pc.run_id AS cluster_run_id,
        pc.id AS cluster_id,
        rd.day_start AS day_start,
        COALESCE(po.subreddit_name, 'unknown') AS subreddit_name,
        COUNT(po.id) AS pain_count
    FROM pain_clusters pc
    JOIN pain_cluster_members pcm ON pcm.cluster_id = pc.id
    JOIN pain_objects po ON po.id = pcm.pain_id
    JOIN recent_days rd ON po.source_created::date = rd.day_start
    WHERE pc.run_id = %(run_id)s
      AND po.source_created IS NOT NULL
    GROUP BY pc.run_id, pc.id, rd.day_start, COALESCE(po.subreddit_name, 'unknown')
)
INSERT INTO pain_volume_daily_subreddit (
    cluster_run_id,
    cluster_id,
    day_start,
    subreddit_name,
    pain_count
)
SELECT cluster_run_id, cluster_id, day_start, subreddit_name, pain_count
FROM src
ON CONFLICT (cluster_run_id, cluster_id, day_start, subreddit_name)
DO UPDATE SET
    pain_count = EXCLUDED.pain_count,
    created_at = NOW();
"""


def aggregate(run_id: int, mode: str) -> dict:
    sql = REBUILD_SQL if mode == "rebuild" else INCREMENTAL_SQL
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            if mode == "rebuild":
                cur.execute(
                    "DELETE FROM pain_volume_daily_subreddit WHERE cluster_run_id = %s",
                    (run_id,),
                )
                deleted = cur.rowcount
                log.info("Rebuild mode: deleted %d existing rows", deleted)
            else:
                cur.execute(
                    """
                    DELETE FROM pain_volume_daily_subreddit
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--mode", choices=["rebuild", "incremental"], default="incremental")
    args = parser.parse_args()
    log.info("Aggregating daily subreddit run_id=%d mode=%s", args.run_id, args.mode)
    res = aggregate(args.run_id, args.mode)
    log.info("Done: %s", res)


if __name__ == "__main__":
    main()
