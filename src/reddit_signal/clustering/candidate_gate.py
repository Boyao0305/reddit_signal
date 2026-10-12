"""
Compute pain_clusters.is_candidate for a given run_id.

The is_candidate flag gates which clusters flow into Phase 5/6 (competition +
viability). Computing this here keeps the gating logic versioned per-run.

Default thresholds (override via --thresholds JSON):
  - audience_type IN ('B2B', 'B2C', 'mixed')
  - audience_confidence >= 0.6
  - member_count >= 30                   # already enforced by HDBSCAN min_cluster_size
  - coherence >= 0.4
  - clusters carrying recent protected sources can pass the audience gate with
    lower confidence, but still must pass size/coherence/quality gates

Velocity threshold is intentionally NOT enforced here — temporal aggregation
is Phase 2; for now we don't have volume_4w / volume_prior_4w.

Logs every promotion/demotion to candidate_promotion_log.

Usage:
    python -m reddit_signal.clustering.candidate_gate --run-id N
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import psycopg2.extras

from reddit_signal.pain_extraction import db

log = logging.getLogger("candidate_gate")

DEFAULT_THRESHOLDS = {
    "audiences": ["B2B", "B2C", "mixed"],
    "min_audience_confidence": 0.6,
    "min_member_count": 30,
    "min_coherence": 0.4,
    "protected_sources": ["facebook", "twitter", "youtube", "hackernews"],
    "protected_source_lookback_days": 14,
    "min_protected_source_pain_count": 1,
    "min_protected_audience_confidence": 0.35,
    "protected_bypass_thread_diversity": True,
    "min_protected_author_diversity": 0.6,
    "preserve_existing_candidates": True,
    # Phase 2 additions (calibrated on run_id=9 distributions, 2026-05-04):
    "min_thread_diversity":  0.5,   # kills viral-thread echo chambers (cuts ~bottom 10%)
    "min_author_diversity":  0.6,   # kills bot/repeat-poster artifacts
    "min_specificity_mean":  0.15,  # kills generic venting (cuts ~bottom 10%)
    "metrics_version":       "v1",
}


def compute_candidates(run_id: int, thresholds: dict) -> dict:
    """Update pain_clusters.is_candidate for the given run; log changes."""
    audiences = thresholds["audiences"]
    min_conf  = float(thresholds["min_audience_confidence"])
    min_n     = int(thresholds["min_member_count"])
    min_coh   = float(thresholds["min_coherence"])
    min_td    = float(thresholds.get("min_thread_diversity", 0.0))
    min_ad    = float(thresholds.get("min_author_diversity", 0.0))
    min_spec  = float(thresholds.get("min_specificity_mean", 0.0))
    protected_sources = list(thresholds.get("protected_sources") or [])
    protected_lookback_days = int(thresholds.get("protected_source_lookback_days", 14))
    min_protected_source_pain_count = int(thresholds.get("min_protected_source_pain_count", 1))
    min_protected_conf = float(thresholds.get("min_protected_audience_confidence", min_conf))
    protected_bypass_thread_diversity = bool(thresholds.get("protected_bypass_thread_diversity", True))
    min_protected_author_diversity = float(thresholds.get("min_protected_author_diversity", min_ad))
    preserve_existing_candidates = bool(thresholds.get("preserve_existing_candidates", True))
    mver      = thresholds.get("metrics_version", "v1")

    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                UPDATE analysis_runs SET candidate_thresholds=%s WHERE id=%s
            """, (json.dumps(thresholds), run_id))

            # Pull cluster + audience + tier-1 quality metrics in one go.
            cur.execute("""
                WITH protected_source_clusters AS (
                    SELECT
                        pcm.cluster_id,
                        COUNT(*)::INT AS protected_source_pain_count,
                        ARRAY_AGG(DISTINCT se.source ORDER BY se.source)::text[] AS protected_sources
                    FROM pain_clusters pc
                    JOIN pain_cluster_members pcm ON pcm.cluster_id = pc.id
                    JOIN pain_objects po ON po.id = pcm.pain_id
                    JOIN signal_events se ON se.id = po.signal_event_id
                    WHERE pc.run_id = %s
                      AND se.source = ANY(%s)
                      AND po.source_created >= NOW() - (%s::text || ' days')::interval
                    GROUP BY pcm.cluster_id
                )
                SELECT pc.id, pc.member_count, pc.coherence, pc.is_candidate AS prev,
                       ca.audience_type, ca.confidence AS aud_conf,
                       cqm.thread_diversity, cqm.author_diversity, cqm.specificity_mean,
                       COALESCE(ps.protected_source_pain_count, 0) AS protected_source_pain_count,
                       COALESCE(ps.protected_sources, ARRAY[]::text[]) AS protected_sources
                FROM pain_clusters pc
                LEFT JOIN cluster_audience ca
                       ON ca.cluster_id = pc.id
                      AND ca.classifier_version = 'v1'
                LEFT JOIN cluster_quality_metrics cqm
                       ON cqm.cluster_id     = pc.id
                      AND cqm.cluster_run_id = pc.run_id
                      AND cqm.metrics_version = %s
                LEFT JOIN protected_source_clusters ps ON ps.cluster_id = pc.id
                WHERE pc.run_id = %s
            """, (run_id, protected_sources, protected_lookback_days, mver, run_id))
            rows = cur.fetchall()

            n_promoted = n_demoted = n_unchanged = n_total = 0
            changes = []
            for r in rows:
                n_total += 1
                audience_conf = float(r["aud_conf"] or 0)
                protected_source_pain_count = int(r["protected_source_pain_count"] or 0)
                protected_source_hit = (
                    protected_source_pain_count >= min_protected_source_pain_count
                    and audience_conf >= min_protected_conf
                    and r["audience_type"] in ("B2B", "B2C", "mixed", "unclear")
                )
                thread_gate = protected_source_hit if protected_bypass_thread_diversity else False
                thread_gate = thread_gate or ((r["thread_diversity"] or 0) >= min_td)
                author_gate = (
                    (r["author_diversity"] or 0) >= min_ad
                    or (
                        protected_source_hit
                        and (r["author_diversity"] or 0) >= min_protected_author_diversity
                    )
                )
                audience_gate = (
                    (r["audience_type"] in audiences and audience_conf >= min_conf)
                    or protected_source_hit
                )
                want = (
                    audience_gate
                    and ((r["member_count"] or 0) >= min_n)
                    and ((r["coherence"] or 0) >= min_coh)
                    and thread_gate
                    and author_gate
                    and ((r["specificity_mean"] or 0) >= min_spec)
                )
                if preserve_existing_candidates and r["prev"]:
                    want = True
                if r["prev"] == want:
                    n_unchanged += 1
                    continue
                direction = "promoted" if want else "demoted"
                metrics = {
                    "member_count":     r["member_count"],
                    "coherence":        float(r["coherence"] or 0),
                    "audience_type":    r["audience_type"],
                    "aud_conf":         audience_conf,
                    "thread_diversity": float(r["thread_diversity"] or 0),
                    "author_diversity": float(r["author_diversity"] or 0),
                    "specificity_mean": float(r["specificity_mean"] or 0),
                    "protected_source_pain_count": protected_source_pain_count,
                    "protected_sources": list(r["protected_sources"] or []),
                    "protected_source_hit": protected_source_hit,
                    "protected_bypass_thread_diversity": protected_source_hit and protected_bypass_thread_diversity,
                    "min_protected_author_diversity": min_protected_author_diversity,
                    "preserve_existing_candidates": preserve_existing_candidates,
                }
                changes.append(
                    (
                        want,
                        r["id"],
                        run_id,
                        r["id"],
                        json.dumps(metrics),
                        json.dumps(thresholds),
                        direction,
                    )
                )
                if direction == "promoted":
                    n_promoted += 1
                else:
                    n_demoted += 1

            if changes:
                psycopg2.extras.execute_values(
                    cur,
                    """
                    UPDATE pain_clusters AS pc
                    SET is_candidate = data.is_candidate
                    FROM (VALUES %s) AS data(is_candidate, cluster_id)
                    WHERE pc.id = data.cluster_id
                    """,
                    [(want, cluster_id) for want, cluster_id, *_ in changes],
                    template="(%s::boolean, %s::integer)",
                    page_size=500,
                )
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO candidate_promotion_log
                        (cluster_run_id, cluster_id, metrics_snapshot, threshold_snapshot, direction)
                    VALUES %s
                    """,
                    [
                        (log_run_id, log_cluster_id, metrics, threshold, direction)
                        for _, _, log_run_id, log_cluster_id, metrics, threshold, direction in changes
                    ],
                    template="(%s::integer, %s::integer, %s::jsonb, %s::jsonb, %s::text)",
                    page_size=500,
                )

        conn.commit()
    finally:
        db.put_conn(conn)

    return {
        "total": n_total,
        "promoted": n_promoted,
        "demoted": n_demoted,
        "unchanged": n_unchanged,
    }


def calibrate_thresholds(run_id: int, audiences=None, min_conf=0.6, min_n=30,
                         min_coh=0.4, percentile=0.10, mver="v1") -> dict:
    """Compute p10 thresholds for thread/author/spec metrics on the eligible
    population (audience-filtered, member/coherence-gated). Returns a dict
    suitable for compute_candidates(). Also returns the supporting distribution
    so the caller can sanity-check before applying.

    Eligible = passes everything *except* the three diversity/specificity gates.
    Picking p_x of that population is the principled "cut bottom x%" rule.
    """
    audiences = audiences or DEFAULT_THRESHOLDS["audiences"]
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                  ROUND(percentile_cont(%(p)s) WITHIN GROUP (ORDER BY cqm.thread_diversity)::numeric, 3) AS thread_p,
                  ROUND(percentile_cont(%(p)s) WITHIN GROUP (ORDER BY cqm.author_diversity)::numeric, 3) AS author_p,
                  ROUND(percentile_cont(%(p)s) WITHIN GROUP (ORDER BY cqm.specificity_mean)::numeric, 3) AS spec_p,
                  COUNT(*) AS eligible_n,
                  ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY cqm.thread_diversity)::numeric, 3) AS thread_p50,
                  ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY cqm.author_diversity)::numeric, 3) AS author_p50,
                  ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY cqm.specificity_mean)::numeric, 3) AS spec_p50
                FROM pain_clusters pc
                JOIN cluster_audience ca
                   ON ca.cluster_id = pc.id AND ca.classifier_version = 'v1'
                JOIN cluster_quality_metrics cqm
                   ON cqm.cluster_id = pc.id
                  AND cqm.cluster_run_id = pc.run_id
                  AND cqm.metrics_version = %(mver)s
                WHERE pc.run_id = %(run)s
                  AND ca.audience_type = ANY(%(aud)s)
                  AND ca.confidence >= %(conf)s
                  AND pc.member_count >= %(n)s
                  AND pc.coherence >= %(coh)s
            """, {"p": percentile, "mver": mver, "run": run_id,
                  "aud": audiences, "conf": min_conf, "n": min_n, "coh": min_coh})
            row = cur.fetchone()
    finally:
        db.put_conn(conn)

    return {
        "thresholds": {
            "audiences": audiences,
            "min_audience_confidence": min_conf,
            "min_member_count": min_n,
            "min_coherence": min_coh,
            "protected_sources": DEFAULT_THRESHOLDS["protected_sources"],
            "protected_source_lookback_days": DEFAULT_THRESHOLDS["protected_source_lookback_days"],
            "min_protected_source_pain_count": DEFAULT_THRESHOLDS["min_protected_source_pain_count"],
            "min_protected_audience_confidence": DEFAULT_THRESHOLDS["min_protected_audience_confidence"],
            "protected_bypass_thread_diversity": DEFAULT_THRESHOLDS["protected_bypass_thread_diversity"],
            "min_protected_author_diversity": DEFAULT_THRESHOLDS["min_protected_author_diversity"],
            "preserve_existing_candidates": DEFAULT_THRESHOLDS["preserve_existing_candidates"],
            "min_thread_diversity":  float(row["thread_p"] or 0),
            "min_author_diversity":  float(row["author_p"] or 0),
            "min_specificity_mean":  float(row["spec_p"] or 0),
            "metrics_version":       mver,
        },
        "calibration_meta": {
            "eligible_n": int(row["eligible_n"]),
            "percentile": percentile,
            "p50_reference": {
                "thread_diversity": float(row["thread_p50"] or 0),
                "author_diversity": float(row["author_p50"] or 0),
                "specificity_mean": float(row["spec_p50"] or 0),
            },
        },
    }


def calibrate_and_apply(run_id: int, percentile: float = 0.10) -> dict:
    """Pick per-run thresholds at ``percentile`` (p10 by default, LD-#16) and apply them once."""
    cal = calibrate_thresholds(run_id, percentile=percentile)
    log.info("Calibration on run_id=%d (eligible_n=%d, p%d):",
             run_id, cal["calibration_meta"]["eligible_n"], int(percentile * 100))
    log.info("  Suggested thresholds: %s", cal["thresholds"])
    log.info("  Reference p50:        %s", cal["calibration_meta"]["p50_reference"])
    log.info("Computing candidates for run_id=%d, thresholds=%s", run_id, cal["thresholds"])
    stats = compute_candidates(run_id, cal["thresholds"])
    log.info("Result: %s", stats)
    return {"thresholds": cal["thresholds"], "calibration_meta": cal["calibration_meta"], **stats}


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", type=int, required=True)
    ap.add_argument("--thresholds", type=str, default=None,
                    help="Optional JSON overriding defaults.")
    ap.add_argument("--calibrate", action="store_true",
                    help="Auto-pick p10 thresholds for thread/author/spec from "
                         "the eligible population, print + apply them.")
    ap.add_argument("--percentile", type=float, default=0.10,
                    help="Percentile to use for --calibrate (default 0.10 = cut bottom 10%%).")
    ap.add_argument("--dry-run", action="store_true",
                    help="With --calibrate: print suggested thresholds, don't apply.")
    args = ap.parse_args()

    if args.calibrate:
        cal = calibrate_thresholds(args.run_id, percentile=args.percentile)
        log.info("Calibration on run_id=%d (eligible_n=%d, p%d):",
                 args.run_id, cal["calibration_meta"]["eligible_n"],
                 int(args.percentile * 100))
        log.info("  Suggested thresholds: %s", cal["thresholds"])
        log.info("  Reference p50:        %s", cal["calibration_meta"]["p50_reference"])
        if args.dry_run:
            log.info("--dry-run: not applying.")
            return
        thresholds = cal["thresholds"]
    else:
        thresholds = DEFAULT_THRESHOLDS.copy()
        if args.thresholds:
            thresholds.update(json.loads(args.thresholds))

    log.info("Computing candidates for run_id=%d, thresholds=%s", args.run_id, thresholds)
    stats = compute_candidates(args.run_id, thresholds)
    log.info("Result: %s", stats)


if __name__ == "__main__":
    main()
