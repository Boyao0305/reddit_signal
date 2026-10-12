"""
Compute tier-1 deterministic cluster quality metrics for a given run.

Populates cluster_quality_metrics with:
  source_diversity, thread_diversity, author_diversity, subreddit_count,
  specificity_mean, workaround_ratio, separation, nearest_cluster_id,
  temporal_spread_days

Idempotent: ON CONFLICT DO UPDATE keyed on (cluster_run_id, cluster_id).

Usage:
    python -m reddit_signal.clustering.quality_metrics --run-id 9
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np
import psycopg2.extras

from reddit_signal.pain_extraction import db

log = logging.getLogger("quality_metrics")

# Single SQL for the SQL-derivable metrics
AGG_SQL = """
WITH members AS (
  SELECT pc.id AS cluster_id, pc.member_count,
    po.id AS pain_id, po.signal_event_id, po.source_author, po.subreddit_name,
    po.specificity, po.is_workaround, po.source_created,
    se.event_time, COALESCE(se.parent_event_id, se.id) AS thread_id
  FROM pain_clusters pc
  JOIN pain_cluster_members pcm ON pcm.cluster_id = pc.id
  JOIN pain_objects po ON po.id = pcm.pain_id
  LEFT JOIN signal_events se ON se.id = po.signal_event_id
  WHERE pc.run_id = %s
)
SELECT cluster_id, member_count,
  COUNT(DISTINCT signal_event_id)::float    / NULLIF(member_count,0) AS source_diversity,
  COUNT(DISTINCT thread_id)::float          / NULLIF(member_count,0) AS thread_diversity,
  COUNT(DISTINCT source_author)::float      / NULLIF(member_count,0) AS author_diversity,
  COUNT(DISTINCT subreddit_name)            AS subreddit_count,
  AVG(specificity)                          AS specificity_mean,
  SUM(CASE WHEN is_workaround THEN 1 ELSE 0 END)::float / NULLIF(member_count,0) AS workaround_ratio,
  EXTRACT(EPOCH FROM (
    MAX(COALESCE(event_time, source_created)) - MIN(COALESCE(event_time, source_created))
  ))/86400.0 AS temporal_spread_days
FROM members
GROUP BY cluster_id, member_count
"""

CENTROIDS_SQL = """
SELECT id, centroid::text FROM pain_clusters
WHERE run_id = %s AND centroid IS NOT NULL
"""

UPSERT_SQL = """
INSERT INTO cluster_quality_metrics (
    cluster_run_id, cluster_id,
    source_diversity, thread_diversity, author_diversity, subreddit_count,
    specificity_mean, workaround_ratio, separation, nearest_cluster_id,
    temporal_spread_days, metrics_version
) VALUES %s
ON CONFLICT (cluster_run_id, cluster_id) DO UPDATE SET
    source_diversity     = EXCLUDED.source_diversity,
    thread_diversity     = EXCLUDED.thread_diversity,
    author_diversity     = EXCLUDED.author_diversity,
    subreddit_count      = EXCLUDED.subreddit_count,
    specificity_mean     = EXCLUDED.specificity_mean,
    workaround_ratio     = EXCLUDED.workaround_ratio,
    separation           = EXCLUDED.separation,
    nearest_cluster_id   = EXCLUDED.nearest_cluster_id,
    temporal_spread_days = EXCLUDED.temporal_spread_days,
    computed_at          = NOW(),
    metrics_version      = EXCLUDED.metrics_version
"""


def parse_vector(s: str) -> np.ndarray:
    """Parse pgvector text format '[0.1,0.2,...]' into numpy array."""
    return np.fromstring(s.strip("[]"), sep=",")


def compute_separation(cluster_ids, centroids):
    """For each cluster, return (min cosine distance to any other centroid, that other id)."""
    if len(centroids) < 2:
        return {cid: (None, None) for cid in cluster_ids}
    M = np.vstack(centroids)
    # normalize
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    Mn = M / norms
    # cosine sim matrix
    sim = Mn @ Mn.T
    # cosine distance = 1 - sim; mask diagonal
    np.fill_diagonal(sim, -np.inf)  # so nearest is highest off-diagonal sim
    nearest_idx = np.argmax(sim, axis=1)
    nearest_sim = sim[np.arange(len(cluster_ids)), nearest_idx]
    nearest_dist = 1.0 - nearest_sim
    out = {}
    for i, cid in enumerate(cluster_ids):
        out[cid] = (float(nearest_dist[i]), int(cluster_ids[nearest_idx[i]]))
    return out


def compute_run(run_id: int, metrics_version: str = "v1") -> int:
    """Compute and upsert tier-1 quality metrics for every cluster of ``run_id``; returns rows written."""
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            log.info("Running aggregate SQL for run_id=%d ...", run_id)
            cur.execute(AGG_SQL, (run_id,))
            agg_rows = cur.fetchall()
            log.info("  %d clusters aggregated", len(agg_rows))

            log.info("Loading centroids ...")
            cur.execute(CENTROIDS_SQL, (run_id,))
            cent_rows = cur.fetchall()
            log.info("  %d centroids", len(cent_rows))

        cluster_ids = [r["id"] for r in cent_rows]
        centroids = [parse_vector(r["centroid"]) for r in cent_rows]
        sep = compute_separation(cluster_ids, centroids)

        # Combine
        rows = []
        for r in agg_rows:
            cid = r["cluster_id"]
            sep_dist, near = sep.get(cid, (None, None))
            ts = r["temporal_spread_days"]
            ts = int(ts) if ts is not None else None
            rows.append((
                run_id, cid,
                r["source_diversity"], r["thread_diversity"], r["author_diversity"],
                r["subreddit_count"], r["specificity_mean"], r["workaround_ratio"],
                sep_dist, near, ts, metrics_version,
            ))

        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, UPSERT_SQL, rows, page_size=500)
        conn.commit()
        log.info("Upserted %d rows into cluster_quality_metrics", len(rows))
        return len(rows)
    finally:
        db.put_conn(conn)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", type=int, required=True)
    ap.add_argument("--metrics-version", default="v1")
    args = ap.parse_args()
    compute_run(args.run_id, args.metrics_version)


if __name__ == "__main__":
    main()
