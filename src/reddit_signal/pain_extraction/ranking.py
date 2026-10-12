"""
Cluster-level scoring and opportunity ranking (v3).

Four component scores per cluster:
  1. Pain Intensity   — count × avg|sentiment| × log-normalized community
  2. Prevalence       — fraction of subreddits represented (replaces velocity)
  3. Coherence        — avg pairwise cosine similarity (already computed)
  4. Market Gap       — 1 - (distinct tools / member count) — less competition = bigger gap

Final opportunity_score = weighted combination, all components normalized to [0, 1].

Fixes from v2:
- Replaced velocity (broken on 7-day data) with cross-subreddit prevalence
- Log-normalized community score to reduce sysadmin/SaaS skew
- Changed competitive_density from penalty to market_gap (inverted)
- Uses log1p normalization instead of min-max for intensity
"""

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import numpy as np
import psycopg2.extras

from . import db

logger = logging.getLogger(__name__)

# FIX-2: Replaced W_COHERENCE with W_SIZE (log1p-normalized member count).
# Coherence still acts as a multiplicative penalty, so double-counting is removed.
W_INTENSITY = 0.35
W_PREVALENCE = 0.25
W_SIZE = 0.25       # was W_COHERENCE=0.25
W_MARKET_GAP = 0.15


def compute_cluster_scores(
    run_id: Optional[int] = None,
    window_end: Optional[datetime] = None,
) -> List[dict]:
    """
    Compute intensity, prevalence, market_gap for clusters.
    If run_id is given: scores only that run's clusters using pain_cluster_members.
    If run_id is None: scores legacy clusters (run_id IS NULL) using pain_objects.cluster_id.
    """
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Get total number of subreddits in dataset
            cur.execute("SELECT COUNT(DISTINCT subreddit_name) AS n FROM pain_objects")
            total_subs = cur.fetchone()["n"] or 1

            # Precompute per-subreddit community_score stats for z-score normalization
            cur.execute("""
                SELECT subreddit_name,
                       AVG(community_score)    AS sub_mean,
                       STDDEV(community_score) AS sub_std
                FROM pain_objects
                WHERE community_score IS NOT NULL
                GROUP BY subreddit_name
            """)
            sub_stats: dict[str, tuple[float, float]] = {
                r["subreddit_name"]: (
                    float(r["sub_mean"] or 0),
                    float(r["sub_std"] or 0),
                )
                for r in cur.fetchall()
            }

            # Get clusters for this run (or legacy clusters if no run_id)
            if run_id is not None:
                cur.execute(
                    "SELECT id, member_count, coherence FROM pain_clusters "
                    "WHERE run_id = %s ORDER BY id",
                    (run_id,),
                )
            else:
                cur.execute(
                    "SELECT id, member_count, coherence FROM pain_clusters "
                    "WHERE run_id IS NULL ORDER BY id"
                )
            clusters = [dict(r) for r in cur.fetchall()]

            if not clusters:
                label = f"run {run_id}" if run_id is not None else "legacy"
                print(f"No clusters found for {label}. Run --cluster or --analyze first.")
                return []

            # FIX-5: Velocity cutoff = last 7 days of the analysis window.
            # Falls back to "last 7 days from now" when window_end is unknown.
            effective_window_end = window_end or datetime.now(tz=timezone.utc)
            velocity_cutoff = effective_window_end - timedelta(days=7)

            # All clusters (legacy and v2) now resolve members via pain_cluster_members.
            # The pain_objects.cluster_id column was dropped in Phase 1 v2_006 — legacy
            # v1 runs were back-filled into pain_cluster_members at the same time.
            intensity_sql = """
                SELECT po.subreddit_name,
                       COALESCE(po.sentiment_score, 0) AS sentiment_score,
                       COALESCE(po.community_score, 0) AS community
                FROM pain_objects po
                JOIN pain_cluster_members pcm ON pcm.pain_id = po.id
                WHERE pcm.cluster_id = %s
            """
            member_from = (
                "FROM pain_objects po "
                "JOIN pain_cluster_members pcm ON pcm.pain_id = po.id "
                "WHERE pcm.cluster_id = %s"
            )
            tool_from = (
                "FROM pain_objects po "
                "JOIN pain_cluster_members pcm ON pcm.pain_id = po.id "
                "WHERE pcm.cluster_id = %s "
                "AND po.mentioned_tools IS NOT NULL "
                "AND array_length(po.mentioned_tools, 1) > 0"
            )
            velocity_sql = """
                SELECT
                    COUNT(*) FILTER (WHERE po.source_created >= %s) AS recent_count,
                    COUNT(*) FILTER (WHERE po.source_created <  %s) AS older_count
                FROM pain_objects po
                JOIN pain_cluster_members pcm ON pcm.pain_id = po.id
                WHERE pcm.cluster_id = %s
            """
            tool_col = "po.mentioned_tools"

            results = []
            for cluster in clusters:
                cid = cluster["id"]

                # ── 1. Pain Intensity (z-score normalized community per subreddit) ──
                cur.execute(intensity_sql, (cid,))
                rows = cur.fetchall()
                count = len(rows)
                if count == 0:
                    intensity_raw = 0.0
                else:
                    # FIX-1: avg negative sentiment — positives cancel negatives.
                    # pain_signal = 0 when avg is positive; abs(avg) when negative.
                    avg_sent = sum(float(r["sentiment_score"]) for r in rows) / count
                    pain_signal = abs(min(avg_sent, 0))
                    z_vals = []
                    for r in rows:
                        sub = r["subreddit_name"]
                        cs = float(r["community"])
                        mean, std = sub_stats.get(sub, (0.0, 0.0))
                        z_vals.append((cs - mean) / std if std > 0 else 0.0)
                    avg_comm_z = sum(z_vals) / count
                    community_norm = math.log1p(abs(avg_comm_z)) / math.log1p(10000)
                    intensity_raw = count * pain_signal * (1 + community_norm)

                # ── 2. Prevalence (density-weighted cross-subreddit presence) ──
                cur.execute(f"""
                    SELECT COUNT(DISTINCT po.subreddit_name) AS n_subs
                    {member_from}
                """, (cid,))
                n_subs = cur.fetchone()["n_subs"] or 0
                member_count_for_prev = cluster["member_count"] or 1
                # FIX-4: density-weighted prevalence combines spread (how many subs)
                # with depth (avg members per sub). 3 subs × 20 members > 3 subs × 2.
                spread = n_subs / total_subs
                avg_members_per_sub = member_count_for_prev / n_subs if n_subs > 0 else 0
                density_norm = math.log1p(avg_members_per_sub) / math.log1p(100)
                prevalence = 0.5 * spread + 0.5 * density_norm

                # ── 3. Coherence (already stored) ──
                coherence = float(cluster["coherence"] or 0)

                # ── 4. Market Gap (inverted competitive density) ──
                cur.execute(f"SELECT {tool_col} {tool_from}", (cid,))
                tool_rows = cur.fetchall()
                all_tools = set()
                for tr in tool_rows:
                    tools = tr["mentioned_tools"]
                    if tools:
                        for t in tools:
                            all_tools.add(t.lower().strip())
                member_count = cluster["member_count"] or 1
                competitive_density = len(all_tools) / member_count
                market_gap = 1.0 - min(competitive_density, 1.0)

                # ── 5. Temporal velocity (FIX-5) ──
                # recent = last 7 days of window; velocity = recent/older ratio.
                # Not weighted into score yet — displayed for analysis only.
                try:
                    cur.execute(velocity_sql, (velocity_cutoff, velocity_cutoff, cid))
                    vrow = cur.fetchone()
                    recent_count = int(vrow["recent_count"] or 0)
                    older_count = int(vrow["older_count"] or 0)
                except Exception:
                    recent_count = 0
                    older_count = 0
                velocity = recent_count / max(older_count, 1)
                velocity_norm = min(velocity / 3.0, 1.0)  # cap: 3× growth → score 1.0

                results.append({
                    "cluster_id": cid,
                    "member_count": member_count,
                    "coherence": coherence,
                    "intensity_raw": intensity_raw,
                    "prevalence": prevalence,
                    "n_subreddits": n_subs,
                    "competitive_density": competitive_density,
                    "market_gap": market_gap,
                    "unique_tools": len(all_tools),
                    "velocity": velocity_norm,
                    "recent_count": recent_count,
                    "older_count": older_count,
                })

        return results
    finally:
        db.put_conn(conn)


def log1p_normalize(values: List[float]) -> List[float]:
    """Log1p normalize: compresses right-skewed distributions."""
    if not values:
        return values
    arr = np.log1p(np.array(values, dtype=float))
    vmax = arr.max()
    if vmax == 0:
        return [0.0] * len(values)
    return (arr / vmax).tolist()


def rank_clusters(
    run_id: Optional[int] = None,
    w_intensity: float = W_INTENSITY,
    w_prevalence: float = W_PREVALENCE,
    # FIX-2: w_size replaces w_coherence; coherence is now only a penalty multiplier.
    w_size: float = W_SIZE,
    w_market_gap: float = W_MARKET_GAP,
    min_coherence: float = 0.0,
    window_end: Optional[datetime] = None,
) -> List[dict]:
    """
    Full ranking pipeline:
    1. Compute raw component scores
    2. Normalize (log1p for intensity, raw for others)
    3. Weighted combination (coherence is a penalty multiplier, not a component)
    4. Save to DB and return sorted results

    run_id: if given, ranks only that run's clusters.
    window_end: end of analysis window (used for velocity computation).
    """
    print("Computing cluster scores...")
    raw_scores = compute_cluster_scores(run_id=run_id, window_end=window_end)

    if not raw_scores:
        return []

    # Filter by coherence if requested
    if min_coherence > 0:
        before = len(raw_scores)
        raw_scores = [r for r in raw_scores if r["coherence"] >= min_coherence]
        filtered = before - len(raw_scores)
        if filtered:
            print(f"Filtered {filtered} clusters below coherence {min_coherence}")

    n = len(raw_scores)
    print(f"Scoring {n} clusters...")

    # Normalize components
    intensities = log1p_normalize([r["intensity_raw"] for r in raw_scores])
    # Prevalence is already [0, 1] (density-weighted)
    # Market gap is already [0, 1]

    # FIX-2: log1p-normalize member_count across clusters for the size component.
    # Larger clusters carry stronger signal.
    max_member_count = max(r["member_count"] for r in raw_scores) or 1

    for i, r in enumerate(raw_scores):
        r["intensity_norm"] = intensities[i]
        r["prevalence_norm"] = r["prevalence"]
        r["coherence_norm"] = r["coherence"]  # kept for display / backward compat
        r["market_gap_norm"] = r["market_gap"]

        # FIX-2: member_count_norm replaces coherence as a weighted component.
        r["member_count_norm"] = (
            math.log1p(r["member_count"]) / math.log1p(max_member_count)
        )

        # Quality flag (still based on coherence — coherence is the penalty, not a component)
        if r["coherence"] >= 0.7:
            r["quality"] = "HIGH"
        elif r["coherence"] >= 0.5:
            r["quality"] = "OK"
        else:
            r["quality"] = "LOW"

        # FIX-2: coherence is NOT a weighted additive component here;
        # it acts only as the multiplicative penalty below.
        raw_score = (
            w_intensity * r["intensity_norm"]
            + w_prevalence * r["prevalence_norm"]
            + w_size * r["member_count_norm"]
            + w_market_gap * r["market_gap_norm"]
        )

        # Coherence penalty: LOW-band clusters get their score damped.
        # Kept from original — this is now the SOLE place coherence affects score.
        if r["coherence"] < 0.4:
            coherence_mult = 0.5   # severe penalty
        elif r["coherence"] < 0.5:
            coherence_mult = 0.75  # moderate penalty
        else:
            coherence_mult = 1.0   # no penalty

        r["opportunity_score"] = raw_score * coherence_mult

    raw_scores.sort(key=lambda x: x["opportunity_score"], reverse=True)

    _save_ranking_to_db(raw_scores)

    return raw_scores


def _save_ranking_to_db(scored: List[dict]):
    """Update pain_clusters with computed scores."""
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            for r in scored:
                # FIX-5: also persist velocity to pain_clusters.velocity column.
                cur.execute("""
                    UPDATE pain_clusters
                    SET intensity = %s,
                        prevalence = %s,
                        competitive_density = %s,
                        opportunity_score = %s,
                        velocity = %s
                    WHERE id = %s
                """, (
                    r["intensity_norm"],
                    r["prevalence_norm"],
                    r["competitive_density"],
                    r["opportunity_score"],
                    r.get("velocity", 0.0),
                    r["cluster_id"],
                ))
        conn.commit()
        logger.info("Updated scores for %d clusters.", len(scored))
    except Exception:
        conn.rollback()
        raise
    finally:
        db.put_conn(conn)


def print_ranking(ranked: List[dict], top_n: int = 30):
    """Pretty-print the top opportunities."""
    if not ranked:
        print("No ranked clusters.")
        return

    # Fetch cluster labels
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, label FROM pain_clusters")
            labels = {r["id"]: r["label"] for r in cur.fetchall()}
    finally:
        db.put_conn(conn)

    # FIX-5: added Veloc column to display temporal velocity signal.
    print(f"\n{'═' * 110}")
    print(f"  TOP {min(top_n, len(ranked))} OPPORTUNITY CLUSTERS")
    print(f"{'═' * 110}")
    print(f"{'Rank':>4}  {'Score':>6}  {'Qual':>4}  {'Size':>5}  {'Intens':>6}  "
          f"{'Prev':>5}  {'Coher':>6}  {'Gap':>5}  {'Veloc':>5}  {'Subs':>4}  {'Tools':>5}  Label")
    print(f"{'─' * 110}")

    for rank, r in enumerate(ranked[:top_n], 1):
        label = labels.get(r["cluster_id"], "")
        # Show first statement only for cleaner output
        first_stmt = label.split(" | ")[0][:50] if label else ""
        print(
            f"{rank:>4}  "
            f"{r['opportunity_score']:>6.3f}  "
            f"{r['quality']:>4}  "
            f"{r['member_count']:>5}  "
            f"{r['intensity_norm']:>6.3f}  "
            f"{r['prevalence_norm']:>5.2f}  "
            f"{r['coherence_norm']:>6.3f}  "
            f"{r['market_gap_norm']:>5.2f}  "
            f"{r.get('velocity', 0):>5.2f}  "
            f"{r['n_subreddits']:>4}  "
            f"{r['unique_tools']:>5}  "
            f"{first_stmt}"
        )

    total_clustered = sum(r["member_count"] for r in ranked)
    n_high = sum(1 for r in ranked if r["quality"] == "HIGH")
    n_ok = sum(1 for r in ranked if r["quality"] == "OK")
    n_low = sum(1 for r in ranked if r["quality"] == "LOW")
    print(f"{'─' * 110}")
    print(f"  {len(ranked)} clusters | {total_clustered} pain objects | "
          f"Quality: {n_high} HIGH, {n_ok} OK, {n_low} LOW")
    print(f"{'═' * 110}\n")


def get_cluster_details(cluster_db_id: int) -> dict:
    """
    Get full details of a cluster: label, scores, and member pain objects.
    Auto-detects whether to use pain_cluster_members (run-based) or
    pain_objects.cluster_id (legacy) based on whether the cluster has a run_id.
    """
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM pain_clusters WHERE id = %s", (cluster_db_id,))
            row = cur.fetchone()
            cluster = dict(row) if row else None

            if not cluster:
                return {}

            # All clusters resolve members via pain_cluster_members (post-Phase-1).
            cur.execute("""
                SELECT po.pain_statement, po.raw_quote, po.mentioned_tools,
                       po.is_workaround, po.sentiment_score, po.specificity,
                       po.community_score, po.subreddit_name, po.source_author
                FROM pain_objects po
                JOIN pain_cluster_members pcm ON pcm.pain_id = po.id
                WHERE pcm.cluster_id = %s
                ORDER BY po.community_score DESC NULLS LAST
                LIMIT 20
            """, (cluster_db_id,))
            members = [dict(r) for r in cur.fetchall()]

            cluster["members"] = members
            return cluster
    finally:
        db.put_conn(conn)
