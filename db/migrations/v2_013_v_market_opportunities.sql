-- v2_013: create v_market_opportunities — the unified ranking surface for dashboard v2.
--
-- Depends on v_cluster_velocity (v2_012). Apply v2_012 first.
--
-- Joins clusters + audience + tier-1 quality + tier-2 viability + velocity.
-- Parameterized via GUC settings, intended to be SET LOCAL by the API per request:
--   SET LOCAL llm_analyse.cluster_run_id              = 10;
--   SET LOCAL llm_analyse.viability_run_id            = 4;
--   SET LOCAL llm_analyse.audience_classifier_version = 'v1';
--
-- Behavior on missing GUCs: returns empty (the WHERE clause becomes pc.run_id = NULL).
-- This is intentional — refuse to return data without explicit parameterization.
--
-- Hard filter: pc.is_candidate ∧ vs.is_real_pain ∧ vs.is_software_solvable
-- (the binary gates from Decision #8). Clusters without a viability_scores row are
-- excluded — "shown in the dashboard" ⇔ "evaluated and passed both gates." Once a
-- viability run scores a missing cluster, it appears automatically on the next
-- query if both flags are true. No third state ("shown but unevaluated") exists.
--
-- Idempotent.

DROP VIEW IF EXISTS v_market_opportunities;
CREATE VIEW v_market_opportunities AS
SELECT
    pc.id                AS cluster_id,
    pc.run_id            AS cluster_run_id,
    pc.label,
    pc.member_count,
    pc.coherence,
    pc.is_candidate,

    ca.classifier_version AS audience_classifier_version,
    ca.audience_type,
    ca.confidence         AS audience_confidence,
    ca.icp_hint,

    cqm.thread_diversity,
    cqm.author_diversity,
    cqm.subreddit_count,
    cqm.specificity_mean,
    cqm.workaround_ratio,
    cqm.separation,
    cqm.temporal_spread_days,
    cqm.source_diversity,

    vs.viability_run_id,
    vs.is_real_pain,
    vs.is_software_solvable,
    vs.q_specificity,
    vs.q_noise_flag,
    vs.v_buildability,
    vs.v_icp_clarity,
    vs.v_moat_potential,
    vs.v_rationale,

    vcv.pain_count_last_4w,
    vcv.pain_count_prior_4w,
    vcv.weeks_with_data,
    vcv.total_pain,
    vcv.slope_per_week,
    vcv.velocity_4w_ratio,
    vcv.velocity_4w_ratio_normalized

FROM pain_clusters pc
JOIN cluster_audience ca
    ON ca.cluster_id = pc.id
   AND ca.classifier_version = COALESCE(
           NULLIF(current_setting('llm_analyse.audience_classifier_version', true), ''),
           'v1')
JOIN cluster_quality_metrics cqm
    ON cqm.cluster_id     = pc.id
   AND cqm.cluster_run_id = pc.run_id
LEFT JOIN viability_scores vs
    ON vs.cluster_id       = pc.id
   AND vs.viability_run_id = NULLIF(current_setting('llm_analyse.viability_run_id', true), '')::int
LEFT JOIN v_cluster_velocity vcv
    ON vcv.cluster_id     = pc.id
   AND vcv.cluster_run_id = pc.run_id
WHERE pc.run_id = NULLIF(current_setting('llm_analyse.cluster_run_id', true), '')::int
  AND pc.is_candidate
  AND vs.is_real_pain         IS TRUE
  AND vs.is_software_solvable IS TRUE;

ALTER VIEW v_market_opportunities OWNER TO llmanalyse;

COMMENT ON VIEW v_market_opportunities IS
  'Unified ranking surface for dashboard v2. Joins clusters + audience + tier-1
   quality + tier-2 viability + velocity, parameterized via three GUC settings
   (llm_analyse.cluster_run_id, .viability_run_id, .audience_classifier_version).
   Hard filter: is_candidate ∧ is_real_pain ∧ is_software_solvable.';

-- Verification (manual, after applying):
--   SET LOCAL llm_analyse.cluster_run_id = 10;
--   SET LOCAL llm_analyse.viability_run_id = 4;
--   SET LOCAL llm_analyse.audience_classifier_version = 'v1';
--   SELECT COUNT(*) FROM v_market_opportunities WHERE audience_type = 'B2C';
--   -- expected: 740
