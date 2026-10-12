"""Cluster lineage via member-overlap (Jaccard).

Computes parent→child edges across consecutive re-cluster runs.
Pain objects persist across runs, so we use shared pain_ids as
ground truth for membership overlap.

Relations assigned:
  'continued' — child has exactly one parent; jaccard >= 0.6
  'split'     — child has exactly one parent; jaccard < 0.6
                (members were distributed to multiple children)
  'merged'    — child has multiple parents; jaccard >= threshold each
  'born'      — child has no parent above threshold (new cluster)
  'died'      — parent has no child above threshold (cluster dissolved)

Jaccard is computed entirely in SQL (single CTE), so this scales to
thousands of clusters without Python-level pair loops.

Usage:
    python -m reddit_signal.clustering.lineage --parent-run 9 --child-run 10
    python -m reddit_signal.clustering.lineage --parent-run 9 --child-run 10 --threshold 0.4 --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import psycopg2.extras

from reddit_signal.pain_extraction import db

log = logging.getLogger("lineage")

# ─── SQL ──────────────────────────────────────────────────────────────────────

# Returns all (parent_cluster, child_cluster, shared_n, parent_n, child_n,
# jaccard) pairs whose Jaccard meets threshold.
_JACCARD_CTE = """
WITH parent_sizes AS (
    SELECT pcm.cluster_id, COUNT(*) AS n
    FROM pain_cluster_members pcm
    JOIN pain_clusters pc ON pc.id = pcm.cluster_id AND pc.run_id = %(parent_run)s
    GROUP BY pcm.cluster_id
),
child_sizes AS (
    SELECT pcm.cluster_id, COUNT(*) AS n
    FROM pain_cluster_members pcm
    JOIN pain_clusters pc ON pc.id = pcm.cluster_id AND pc.run_id = %(child_run)s
    GROUP BY pcm.cluster_id
),
shared_counts AS (
    SELECT
        p.cluster_id AS parent_cluster,
        c.cluster_id AS child_cluster,
        COUNT(*)     AS shared_n
    FROM pain_cluster_members p
    JOIN pain_clusters ppc ON ppc.id = p.cluster_id AND ppc.run_id = %(parent_run)s
    JOIN pain_cluster_members c ON c.pain_id = p.pain_id
    JOIN pain_clusters cpc ON cpc.id = c.cluster_id AND cpc.run_id = %(child_run)s
    GROUP BY p.cluster_id, c.cluster_id
)
SELECT
    s.parent_cluster,
    s.child_cluster,
    s.shared_n,
    ps.n AS parent_n,
    cs.n AS child_n,
    s.shared_n::float / (ps.n + cs.n - s.shared_n)::float AS jaccard
FROM shared_counts s
JOIN parent_sizes ps ON ps.cluster_id = s.parent_cluster
JOIN child_sizes  cs ON cs.cluster_id = s.child_cluster
WHERE s.shared_n::float / (ps.n + cs.n - s.shared_n)::float >= %(threshold)s
ORDER BY jaccard DESC
"""

_ALL_PARENT_CLUSTERS = """
SELECT id FROM pain_clusters WHERE run_id = %(run_id)s
"""

_ALL_CHILD_CLUSTERS = """
SELECT id FROM pain_clusters WHERE run_id = %(run_id)s
"""

# Upsert on the COALESCE-based unique index.
_UPSERT = """
INSERT INTO cluster_lineage
    (parent_run_id, parent_cluster, child_run_id, child_cluster,
     overlap_jaccard, shared_members, relation)
VALUES
    (%(parent_run_id)s, %(parent_cluster)s, %(child_run_id)s, %(child_cluster)s,
     %(jaccard)s, %(shared)s, %(relation)s)
ON CONFLICT (
    COALESCE(parent_run_id, 0),
    COALESCE(parent_cluster, 0),
    child_run_id,
    COALESCE(child_cluster, 0)
)
DO UPDATE SET
    overlap_jaccard = EXCLUDED.overlap_jaccard,
    shared_members  = EXCLUDED.shared_members,
    relation        = EXCLUDED.relation
"""


# ─── Core ─────────────────────────────────────────────────────────────────────

def _classify_edges(edges: list[dict]) -> list[dict]:
    """Assign relation to each edge; return 'born'/'died' sentinels too.

    edges: list of dicts with keys parent_cluster, child_cluster,
           shared_n, parent_n, child_n, jaccard.
    """
    # Count how many parents each child has, and how many children each parent has.
    child_parent_count: dict[int, int] = defaultdict(int)
    parent_child_count: dict[int, int] = defaultdict(int)
    for e in edges:
        child_parent_count[e["child_cluster"]] += 1
        parent_child_count[e["parent_cluster"]] += 1

    classified = []
    for e in edges:
        n_parents   = child_parent_count[e["child_cluster"]]
        n_children  = parent_child_count[e["parent_cluster"]]
        jaccard     = e["jaccard"]

        if n_parents > 1:
            relation = "merged"
        elif jaccard >= 0.6:
            relation = "continued"
        elif n_children > 1:
            # parent dispersed into multiple children
            relation = "split"
        else:
            # single parent, single child, jaccard 0.4-0.6 — treat as continued
            relation = "continued"

        classified.append({**e, "relation": relation})

    return classified


def compute_lineage(
    parent_run_id: int,
    child_run_id: int,
    threshold: float = 0.4,
    dry_run: bool = False,
) -> dict:
    """Compute and persist cluster lineage edges between two runs.

    Returns a summary dict with counts per relation.
    Idempotent — re-running over the same pair upserts (overwrites).
    """
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 1. Jaccard edges in SQL
            log.info("Computing Jaccard overlap (parent=%d, child=%d, threshold=%.2f)…",
                     parent_run_id, child_run_id, threshold)
            cur.execute(_JACCARD_CTE, {
                "parent_run": parent_run_id,
                "child_run":  child_run_id,
                "threshold":  threshold,
            })
            raw_edges = [dict(r) for r in cur.fetchall()]
            log.info("  %d edges above threshold", len(raw_edges))

            # 2. All cluster ids (to detect born/died)
            cur.execute(_ALL_PARENT_CLUSTERS, {"run_id": parent_run_id})
            all_parents = {r["id"] for r in cur.fetchall()}

            cur.execute(_ALL_CHILD_CLUSTERS, {"run_id": child_run_id})
            all_children = {r["id"] for r in cur.fetchall()}

        # 3. Classify edges (Python pass — O(edges), not O(cluster pairs))
        classified = _classify_edges(raw_edges)

        linked_parents  = {e["parent_cluster"] for e in classified}
        linked_children = {e["child_cluster"]  for e in classified}

        died_parents = all_parents  - linked_parents
        born_children = all_children - linked_children

        # 4. Build full row list
        rows: list[dict] = []

        for e in classified:
            rows.append({
                "parent_run_id": parent_run_id,
                "parent_cluster": e["parent_cluster"],
                "child_run_id":  child_run_id,
                "child_cluster": e["child_cluster"],
                "jaccard":       e["jaccard"],
                "shared":        int(e["shared_n"]),
                "relation":      e["relation"],
            })

        for cid in born_children:
            rows.append({
                "parent_run_id":  None,
                "parent_cluster": None,
                "child_run_id":   child_run_id,
                "child_cluster":  cid,
                "jaccard":        None,
                "shared":         None,
                "relation":       "born",
            })

        for pid in died_parents:
            rows.append({
                "parent_run_id":  parent_run_id,
                "parent_cluster": pid,
                "child_run_id":   child_run_id,
                "child_cluster":  None,
                "jaccard":        None,
                "shared":         None,
                "relation":       "died",
            })

        # Tally
        counts: dict[str, int] = defaultdict(int)
        for r in rows:
            counts[r["relation"]] += 1

        log.info("  born=%d  died=%d  continued=%d  split=%d  merged=%d",
                 counts["born"], counts["died"], counts["continued"],
                 counts["split"], counts["merged"])

        if dry_run:
            log.info("--dry-run: not writing to DB.")
            return dict(counts)

        # 5. Upsert atomically
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(_UPSERT, r)
        conn.commit()
        log.info("  Committed %d rows.", len(rows))

    finally:
        db.put_conn(conn)

    return dict(counts)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    ap = argparse.ArgumentParser(
        description="Compute cluster lineage (Jaccard member-overlap) between two runs."
    )
    ap.add_argument("--parent-run", type=int, required=True,
                    help="analysis_runs.id for the older / parent run")
    ap.add_argument("--child-run",  type=int, required=True,
                    help="analysis_runs.id for the newer / child run")
    ap.add_argument("--threshold", type=float, default=0.4,
                    help="Minimum Jaccard to record an edge (default 0.4)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute and print summary; do not write to DB")
    args = ap.parse_args()

    log.info("Lineage: parent_run=%d  child_run=%d  threshold=%.2f  dry_run=%s",
             args.parent_run, args.child_run, args.threshold, args.dry_run)

    summary = compute_lineage(
        parent_run_id=args.parent_run,
        child_run_id=args.child_run,
        threshold=args.threshold,
        dry_run=args.dry_run,
    )

    # Print summary table
    print("\n── Lineage summary ──────────────────────────────")
    print(f"  parent_run : {args.parent_run}")
    print(f"  child_run  : {args.child_run}")
    print(f"  threshold  : {args.threshold}")
    print()
    for rel in ("continued", "split", "merged", "born", "died"):
        print(f"  {rel:<12} {summary.get(rel, 0):>6}")
    total = sum(summary.values())
    print(f"  {'TOTAL':<12} {total:>6}")
    print("─────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
