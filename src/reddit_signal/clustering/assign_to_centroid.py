"""
Daily centroid-assignment module (B-ops.2).

For each pain_object NOT yet assigned to the given run_id, find the nearest
cluster centroid via pgvector cosine distance and INSERT into
pain_cluster_members if similarity >= threshold.

Two modes
---------
Default (no --calibrate):
    Scan unassigned pain_objects, assign nearest centroid when sim >= threshold.
    Idempotent — primary key is (pain_id, cluster_id).

--calibrate:
    Distribution-based threshold heuristic.
    Sample 5000 already-assigned pains (true positives) and 5000 unassigned
    pains.  Compute similarity-to-nearest-centroid for each group.  Print
    percentile breakdowns and suggest threshold = p10 of TP distribution.

Usage
-----
    python -m reddit_signal.clustering.assign_to_centroid --run-id 10
    python -m reddit_signal.clustering.assign_to_centroid --run-id 10 --threshold 0.55 --limit 1000
    python -m reddit_signal.clustering.assign_to_centroid --run-id 10 --dry-run --limit 100
    python -m reddit_signal.clustering.assign_to_centroid --run-id 10 --calibrate [--dry-run]
    python -m reddit_signal.clustering.assign_to_centroid --run-id 10 --since 2026-05-01T00:00:00Z
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import psycopg2.extras

from reddit_signal.pain_extraction import db

log = logging.getLogger("assign_to_centroid")

DEFAULT_THRESHOLD = 0.55
CALIBRATE_SAMPLE  = 5_000


# ─── Index advisory ──────────────────────────────────────────────────────────

_INDEX_ADVISORY = """
[PERF] No ivfflat/hnsw index detected on pain_clusters.centroid.
       The CROSS JOIN LATERAL scans all K centroids per query row: O(N×K).
       With N=100K unassigned pains and K=2594 centroids this is ~260M
       comparisons — may take several minutes.
       To create one (do NOT run now, it locks the table briefly):
         CREATE INDEX ON pain_clusters
           USING ivfflat (centroid vector_cosine_ops)
           WITH (lists=100);
       After creation, SET ivfflat.probes=10 at query time for recall ~0.95.
"""


def _has_centroid_index(conn) -> bool:
    """Return True if any ivfflat/hnsw index exists on pain_clusters.centroid."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1
            FROM pg_indexes
            WHERE tablename = 'pain_clusters'
              AND indexdef ILIKE '%centroid%'
              AND (indexdef ILIKE '%ivfflat%' OR indexdef ILIKE '%hnsw%')
            LIMIT 1
        """)
        return cur.fetchone() is not None


# ─── Assignment ───────────────────────────────────────────────────────────────

def assign_to_centroid(
    run_id: int,
    threshold: float = DEFAULT_THRESHOLD,
    dry_run: bool = False,
    limit: int | None = None,
    since: str | None = None,
) -> dict:
    """
    For each pain_object not yet in any cluster of run_id, find nearest
    centroid and insert into pain_cluster_members if sim >= threshold.

    Returns a dict with counts: total_candidates, assigned, below_threshold,
    already_assigned (would-be inserts suppressed by ON CONFLICT), index_present.
    """
    conn = db.get_conn()
    try:
        has_idx = _has_centroid_index(conn)
        if not has_idx:
            log.warning(_INDEX_ADVISORY)

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            # ── count candidates first (cheap) ────────────────────────────
            count_params: dict = {"run_id": run_id, "threshold": threshold}
            since_clause = ""
            if since:
                since_clause = "AND po.created_at >= %(since)s"
                count_params["since"] = since

            limit_clause = ""
            if limit:
                limit_clause = f"LIMIT {int(limit)}"

            cur.execute(f"""
                SELECT COUNT(*) AS n
                FROM pain_objects po
                WHERE po.embedding IS NOT NULL
                  {since_clause}
                  AND NOT EXISTS (
                      SELECT 1 FROM pain_cluster_members pcm
                      JOIN pain_clusters pc2
                        ON pc2.id = pcm.cluster_id AND pc2.run_id = %(run_id)s
                      WHERE pcm.pain_id = po.id
                  )
            """, count_params)
            total_candidates = cur.fetchone()["n"]
            if limit:
                total_candidates = min(total_candidates, limit)

            log.info(
                "run_id=%d  threshold=%.3f  candidates=%d  dry_run=%s",
                run_id, threshold, total_candidates, dry_run,
            )

            if total_candidates == 0:
                log.info("Nothing to assign.")
                return {
                    "total_candidates": 0, "assigned": 0,
                    "below_threshold": 0, "already_assigned": 0,
                    "index_present": has_idx,
                }

            # ── fetch candidates + nearest centroid similarity ─────────────
            cur.execute(f"""
                SELECT po.id AS pain_id,
                       nearest.cluster_id,
                       nearest.sim
                FROM pain_objects po
                CROSS JOIN LATERAL (
                    SELECT pc.id AS cluster_id,
                           1 - (pc.centroid <=> po.embedding) AS sim
                    FROM pain_clusters pc
                    WHERE pc.run_id = %(run_id)s
                    ORDER BY pc.centroid <=> po.embedding
                    LIMIT 1
                ) nearest
                WHERE po.embedding IS NOT NULL
                  {since_clause}
                  AND NOT EXISTS (
                      SELECT 1 FROM pain_cluster_members pcm
                      JOIN pain_clusters pc2
                        ON pc2.id = pcm.cluster_id AND pc2.run_id = %(run_id)s
                      WHERE pcm.pain_id = po.id
                  )
                {limit_clause}
            """, count_params)
            rows = cur.fetchall()

        n_assigned = 0
        n_below    = 0
        insert_pairs: list[tuple[int, int]] = []

        for r in rows:
            if r["sim"] >= threshold:
                insert_pairs.append((r["pain_id"], r["cluster_id"]))
                n_assigned += 1
            else:
                n_below += 1

        log.info(
            "Similarity scan done: %d above threshold, %d below (%.1f%%)",
            n_assigned, n_below,
            100.0 * n_assigned / len(rows) if rows else 0.0,
        )

        if dry_run:
            log.info("--dry-run: skipping INSERT (%d rows would be inserted)", n_assigned)
            return {
                "total_candidates": len(rows),
                "assigned": n_assigned,
                "below_threshold": n_below,
                "already_assigned": 0,
                "index_present": has_idx,
            }

        # ── INSERT ────────────────────────────────────────────────────────
        if insert_pairs:
            conn2 = db.get_conn()
            try:
                with conn2.cursor() as cur2:
                    # Count before so we can compute true inserts across all batches.
                    # (execute_values page_size batching makes cur.rowcount unreliable.)
                    cur2.execute(
                        "SELECT COUNT(*) FROM pain_cluster_members pcm "
                        "JOIN pain_clusters pc ON pc.id = pcm.cluster_id "
                        "WHERE pc.run_id = %(run_id)s",
                        {"run_id": run_id},
                    )
                    count_before = cur2.fetchone()[0]
                    psycopg2.extras.execute_values(
                        cur2,
                        """INSERT INTO pain_cluster_members (pain_id, cluster_id)
                           VALUES %s ON CONFLICT DO NOTHING""",
                        insert_pairs,
                        page_size=500,
                    )
                    cur2.execute(
                        "SELECT COUNT(*) FROM pain_cluster_members pcm "
                        "JOIN pain_clusters pc ON pc.id = pcm.cluster_id "
                        "WHERE pc.run_id = %(run_id)s",
                        {"run_id": run_id},
                    )
                    count_after = cur2.fetchone()[0]
                conn2.commit()
            except Exception:
                conn2.rollback()
                raise
            finally:
                db.put_conn(conn2)

            affected        = count_after - count_before
            already_assigned = n_assigned - affected
            log.info(
                "Inserted %d rows (ON CONFLICT skipped %d already-existing).",
                affected, already_assigned,
            )
        else:
            affected = 0
            already_assigned = 0

        return {
            "total_candidates": len(rows),
            "assigned": n_assigned,
            "below_threshold": n_below,
            "already_assigned": already_assigned,
            "index_present": has_idx,
        }
    finally:
        db.put_conn(conn)


# ─── Calibration ──────────────────────────────────────────────────────────────

def _percentiles(values: list[float], ps=(10, 25, 50, 75, 90)) -> dict:
    """Return a dict of {pN: value} for the given sorted values list."""
    if not values:
        return {f"p{p}": None for p in ps}
    sv = sorted(values)
    n  = len(sv)
    result = {}
    for p in ps:
        idx = (p / 100.0) * (n - 1)
        lo, hi = int(idx), min(int(idx) + 1, n - 1)
        frac = idx - lo
        result[f"p{p}"] = round(sv[lo] * (1 - frac) + sv[hi] * frac, 4)
    return result


def calibrate(run_id: int, sample: int = CALIBRATE_SAMPLE) -> dict:
    """
    Distribution-based threshold heuristic.

    True-positive sample: pain_objects already assigned to a cluster in run_id.
    Compute similarity to that cluster's centroid.

    Noise sample: pain_objects with embeddings NOT in any cluster of run_id.
    Compute similarity to their nearest centroid (worst-case floor).

    Suggested threshold = p10 of TP distribution.
    """
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:

            # ── TP sample: assigned pains → their cluster centroid sim ────
            cur.execute("""
                SELECT 1 - (pc.centroid <=> po.embedding) AS sim
                FROM pain_cluster_members pcm
                JOIN pain_clusters pc ON pc.id = pcm.cluster_id
                JOIN pain_objects  po ON po.id = pcm.pain_id
                WHERE pc.run_id = %(run_id)s
                  AND po.embedding IS NOT NULL
                  AND pc.centroid  IS NOT NULL
                ORDER BY RANDOM()
                LIMIT %(sample)s
            """, {"run_id": run_id, "sample": sample})
            tp_sims = [r[0] for r in cur.fetchall() if r[0] is not None]

            # ── Noise sample: unassigned pains → nearest centroid sim ─────
            # Use TABLESAMPLE to pre-filter to ~2× sample size before the
            # expensive CROSS JOIN LATERAL — avoids a full 560K-row scan.
            # BERNOULLI(2) ≈ 2% of ~830K rows ≈ 16K candidates, then LIMIT.
            cur.execute("""
                SELECT nearest.sim
                FROM (
                    SELECT po.id, po.embedding
                    FROM pain_objects po TABLESAMPLE BERNOULLI(2)
                    WHERE po.embedding IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM pain_cluster_members pcm
                          JOIN pain_clusters pc2
                            ON pc2.id = pcm.cluster_id AND pc2.run_id = %(run_id)s
                          WHERE pcm.pain_id = po.id
                      )
                    LIMIT %(sample)s
                ) po
                CROSS JOIN LATERAL (
                    SELECT 1 - (pc.centroid <=> po.embedding) AS sim
                    FROM pain_clusters pc
                    WHERE pc.run_id = %(run_id)s
                    ORDER BY pc.centroid <=> po.embedding
                    LIMIT 1
                ) nearest
            """, {"run_id": run_id, "sample": sample})
            noise_sims = [r[0] for r in cur.fetchall() if r[0] is not None]

    finally:
        db.put_conn(conn)

    tp_pct    = _percentiles(tp_sims)
    noise_pct = _percentiles(noise_sims)

    suggested = tp_pct["p10"]

    # Overlap warning: if noise p90 >= TP p10, threshold is noisy
    tp_p10    = tp_pct["p10"] or 0.0
    noise_p90 = noise_pct["p90"] or 0.0
    overlap   = noise_p90 >= tp_p10

    # Predicted impact: what fraction of noise sample would be assigned at p10?
    if noise_sims and suggested is not None:
        n_above = sum(1 for s in noise_sims if s >= suggested)
        pct_new = round(100.0 * n_above / len(noise_sims), 1)
    else:
        pct_new = None

    return {
        "tp_sample_n":    len(tp_sims),
        "noise_sample_n": len(noise_sims),
        "tp_percentiles":    tp_pct,
        "noise_percentiles": noise_pct,
        "suggested_threshold": suggested,
        "overlap_warning": overlap,
        "predicted_new_assign_pct": pct_new,
    }


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    ap = argparse.ArgumentParser(
        description="Daily centroid-assignment for a given cluster run_id."
    )
    ap.add_argument("--run-id",    type=int, required=True,
                    help="The analysis_runs.id whose clusters to assign against.")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                    help=f"Cosine-similarity floor for assignment (default {DEFAULT_THRESHOLD}).")
    ap.add_argument("--dry-run",   action="store_true",
                    help="Compute candidates and sims but skip INSERT.")
    ap.add_argument("--limit",     type=int, default=None,
                    help="Process at most N candidates (useful for testing).")
    ap.add_argument("--since",     type=str, default=None,
                    help="Only consider pain_objects with created_at >= this "
                         "TIMESTAMPTZ (e.g. 2026-05-01T00:00:00Z).")
    ap.add_argument("--calibrate", action="store_true",
                    help="Run distribution-based threshold calibration and exit.")
    args = ap.parse_args()

    if args.calibrate:
        log.info("Calibrating threshold for run_id=%d (sample=%d each)…",
                 args.run_id, CALIBRATE_SAMPLE)
        cal = calibrate(args.run_id)

        log.info("─── TRUE-POSITIVE distribution (assigned pains → their centroid) ───")
        log.info("  n=%d  %s", cal["tp_sample_n"], cal["tp_percentiles"])

        log.info("─── NOISE distribution (unassigned pains → nearest centroid) ─────")
        log.info("  n=%d  %s", cal["noise_sample_n"], cal["noise_percentiles"])

        log.info("─── Suggestion ──────────────────────────────────────────────────")
        log.info("  Suggested threshold (TP p10): %.4f", cal["suggested_threshold"] or 0)
        log.info("  Predicted %% of new pains assigned at this threshold: %s%%",
                 cal["predicted_new_assign_pct"])
        if cal["overlap_warning"]:
            log.warning(
                "OVERLAP WARNING: noise p90 (%.4f) >= TP p10 (%.4f) — "
                "threshold is not cleanly separating signal from noise.",
                cal["noise_percentiles"]["p90"] or 0,
                cal["tp_percentiles"]["p10"] or 0,
            )
        else:
            log.info(
                "  Separation OK: noise p90=%.4f < TP p10=%.4f",
                cal["noise_percentiles"]["p90"] or 0,
                cal["tp_percentiles"]["p10"] or 0,
            )

        if args.dry_run:
            log.info("--dry-run: not applying any changes.")
        return

    # ── assignment mode ───────────────────────────────────────────────────────
    stats = assign_to_centroid(
        run_id=args.run_id,
        threshold=args.threshold,
        dry_run=args.dry_run,
        limit=args.limit,
        since=args.since,
    )

    log.info("─── Assignment summary ──────────────────────────────────────────")
    log.info("  run_id          : %d", args.run_id)
    log.info("  threshold       : %.4f", args.threshold)
    log.info("  total_candidates: %d", stats["total_candidates"])
    log.info("  assigned        : %d", stats["assigned"])
    log.info("  below_threshold : %d", stats["below_threshold"])
    log.info("  already_assigned: %d", stats["already_assigned"])
    log.info("  index_present   : %s", stats["index_present"])
    if not stats["index_present"]:
        log.warning("No ivfflat/hnsw index on pain_clusters.centroid — "
                    "consider CREATE INDEX … USING ivfflat … for large daily runs.")


if __name__ == "__main__":
    main()
