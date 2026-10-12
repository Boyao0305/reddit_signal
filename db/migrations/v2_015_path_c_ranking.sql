-- v2_015: Path C ranking — Q/M/S axes + Pareto layers + harmonic balance score.
--
-- Re-derives colourpinke's Path C from the column shape of v_market_opportunities
-- and lands it cleanly on localhost:5432/reddit_raw (NOT the SSD socket the
-- colourpinke branch deployed against). Implements locked decisions #36 / #37 / #38.
--
-- Surfaces built here (dependency order):
--   1. fn_compute_pareto_layers(p_run_id, p_audience)  — iterative Pareto peel (≤5 layers)
--   2. v_market_opportunities_pregate                  — Q/M/S axes + balance + eligibility
--   3. v_market_opportunities_ranked                   — pregate + pareto_layer (API reads this)
--   4. v_market_opportunities                          — backward-compat shim (legacy 34 cols)
--
-- Parameterized via the same three GUCs as the original v_market_opportunities:
--   SET LOCAL llm_analyse.cluster_run_id              = 10;
--   SET LOCAL llm_analyse.viability_run_id            = 4;
--   SET LOCAL llm_analyse.audience_classifier_version = 'v1';
--
-- Idempotent: re-applying produces identical state. The old v_market_opportunities
-- (a standalone view from v2_013) is dropped CASCADE up front because the shim at
-- the bottom recreates it on top of the ranked view; on a second apply nothing
-- depends on it so CASCADE drops only the shim.
--
-- Hard constraints honoured:
--   * Percentile population = the gated market-opportunity set (is_candidate ∧
--     is_real_pain ∧ is_software_solvable), identical to v_market_opportunities.
--   * pareto_layer / balance_score are display + default-sort signals only — they
--     never enter is_candidate (LD-#8 / LD-#36).
--   * is_eligible is a display filter, never a gate (LD-#37). The view returns
--     ineligible rows (is_eligible = FALSE, pareto_layer = NULL); the dashboard
--     hides them by default and can toggle them on.
--   * ε = 0.05 zero-floor: any axis < 0.05 ⇒ balance_score = NULL ⇒ pareto_layer
--     = NULL (LD-#38).

-- ---------------------------------------------------------------------------
-- 0. Drop the legacy standalone view so the shim can be rebuilt on top of ranked.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_market_opportunities CASCADE;


-- ---------------------------------------------------------------------------
-- 1. fn_compute_pareto_layers — within (run, audience) iterative Pareto peel.
--
-- Reads the axes / eligibility / balance from v_market_opportunities_pregate,
-- which is itself parameterized by the cluster_run_id GUC the caller has SET.
-- Marked STABLE per spec: the body performs only SELECTs (the peel runs over
-- in-memory parallel arrays via unnest, so STABLE's no-write rule is respected
-- and pregate is scanned exactly once per call).
--
-- Candidate set = eligible clusters with a non-NULL balance_score (axis ≥ ε on
-- all three). Layer 1 = non-dominated candidates; peel and repeat. Clusters
-- beyond layer 5 — and every ineligible / balance-NULL cluster — are simply not
-- returned, so the LEFT JOIN in the ranked view leaves pareto_layer NULL.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_compute_pareto_layers(p_run_id INT, p_audience TEXT)
RETURNS TABLE(cluster_id INT, pareto_layer INT)
LANGUAGE plpgsql
STABLE
AS $fn$
DECLARE
    v_layer INT := 1;
    v_id    INT[];
    v_q     FLOAT8[];
    v_m     FLOAT8[];
    v_s     FLOAT8[];
    v_this  INT[];
BEGIN
    -- Load the candidate set into parallel arrays (single pregate scan, aligned
    -- because every array_agg in one SELECT sees rows in the same order).
    SELECT array_agg(pg.cluster_id),
           array_agg(pg.q_axis),
           array_agg(pg.m_axis),
           array_agg(pg.s_axis)
      INTO v_id, v_q, v_m, v_s
      FROM v_market_opportunities_pregate pg
     WHERE pg.audience_type = p_audience
       AND pg.is_eligible IS TRUE
       AND pg.balance_score IS NOT NULL;

    WHILE v_layer <= 5 AND array_length(v_id, 1) > 0 LOOP
        -- Non-dominated within the current candidate arrays.
        SELECT array_agg(a.id)
          INTO v_this
          FROM unnest(v_id, v_q, v_m, v_s) AS a(id, q, m, s)
         WHERE NOT EXISTS (
             SELECT 1
               FROM unnest(v_id, v_q, v_m, v_s) AS b(id, q, m, s)
              WHERE b.id <> a.id
                AND b.q >= a.q AND b.m >= a.m AND b.s >= a.s
                AND (b.q > a.q OR b.m > a.m OR b.s > a.s)
         );

        EXIT WHEN v_this IS NULL;  -- safety: a finite set always has a frontier

        RETURN QUERY SELECT unnest(v_this), v_layer;

        -- Drop the assigned layer from the candidate arrays.
        SELECT array_agg(t.id), array_agg(t.q), array_agg(t.m), array_agg(t.s)
          INTO v_id, v_q, v_m, v_s
          FROM unnest(v_id, v_q, v_m, v_s) AS t(id, q, m, s)
         WHERE t.id <> ALL (v_this);

        v_layer := v_layer + 1;
    END LOOP;

    RETURN;
END;
$fn$;

ALTER FUNCTION fn_compute_pareto_layers(INT, TEXT) OWNER TO llmanalyse;

COMMENT ON FUNCTION fn_compute_pareto_layers(INT, TEXT) IS
  'Within (run, audience) iterative Pareto peel over (q_axis, m_axis, s_axis) of
   v_market_opportunities_pregate. Returns layer 1..5 for eligible, non-balance-NULL
   clusters; clusters beyond layer 5 (and ineligible / balance-NULL ones) are not
   returned so their pareto_layer resolves to NULL. STABLE; reads the cluster_run_id
   GUC via the pregate view.';


-- ---------------------------------------------------------------------------
-- 2. v_market_opportunities_pregate — the Q/M/S axes, balance score, eligibility.
--
-- Replicates the gated join of v_market_opportunities (cannot select FROM it —
-- that view is recreated downstream as a shim, which would be circular), then
-- adds tool_density, within-run axis-input percentiles, the three axes, the
-- ε-floored harmonic balance score, the low-confidence-S flag, and the
-- within-audience eligibility partition.
--
-- Percentiles are within-run (over the gated opportunity set) via percent_rank();
-- NULLS FIRST makes a missing input rank lowest (worst), so a sparse momentum
-- signal never inflates the axis. Eligibility p25 is within-audience (LD-#9).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_market_opportunities_pregate AS
WITH base AS (
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
      AND vs.is_software_solvable IS TRUE
),
-- §3.2 — fraction of cluster members that mention at least one tool. Lower is
-- more underserved. members_with_tools also drives low_confidence_s (< 20).
cluster_tool_density AS (
    SELECT pcm.cluster_id,
           COUNT(*) FILTER (WHERE array_length(po.mentioned_tools, 1) > 0)::float
             / NULLIF(COUNT(*), 0)                                            AS tool_density,
           COUNT(*) FILTER (WHERE array_length(po.mentioned_tools, 1) > 0)    AS members_with_tools
    FROM pain_cluster_members pcm
    JOIN pain_objects po ON po.id = pcm.pain_id
    JOIN pain_clusters pc ON pc.id = pcm.cluster_id
    WHERE pc.run_id = NULLIF(current_setting('llm_analyse.cluster_run_id', true), '')::int
    GROUP BY pcm.cluster_id
),
joined AS (
    SELECT b.*,
           COALESCE(td.tool_density, 0)        AS tool_density,
           COALESCE(td.members_with_tools, 0)  AS members_with_tools
    FROM base b
    LEFT JOIN cluster_tool_density td ON td.cluster_id = b.cluster_id
),
-- §3.1 / §3.2 — within-run percentile ranks. NULLS FIRST ⇒ missing input ranks
-- lowest. tool_density is "lower = better", so p_tool_inv inverts it.
pct AS (
    SELECT j.*,
        percent_rank() OVER (ORDER BY thread_diversity     ASC NULLS FIRST) AS p_thread,
        percent_rank() OVER (ORDER BY author_diversity      ASC NULLS FIRST) AS p_author,
        percent_rank() OVER (ORDER BY specificity_mean      ASC NULLS FIRST) AS p_spec,
        percent_rank() OVER (ORDER BY separation            ASC NULLS FIRST) AS p_sep,
        percent_rank() OVER (ORDER BY temporal_spread_days  ASC NULLS FIRST) AS p_temp,
        percent_rank() OVER (ORDER BY slope_per_week        ASC NULLS FIRST) AS p_slope,
        percent_rank() OVER (ORDER BY pain_count_prior_4w   ASC NULLS FIRST) AS p_prior,
        percent_rank() OVER (ORDER BY workaround_ratio      ASC NULLS FIRST) AS p_workaround,
        1.0 - percent_rank() OVER (ORDER BY tool_density    ASC NULLS FIRST) AS p_tool_inv
    FROM joined j
),
axes AS (
    SELECT p.*,
        (0.25 * p_thread + 0.20 * p_author + 0.25 * p_spec
              + 0.15 * p_sep + 0.15 * p_temp)        AS q_axis,
        (0.6 * p_slope + 0.4 * p_prior)              AS m_axis,
        (0.5 * p_workaround + 0.5 * p_tool_inv)      AS s_axis,
        (members_with_tools < 20)                    AS low_confidence_s
    FROM pct p
),
-- §3.4 / LD-#38 — ε-floored harmonic mean of (Q, M, S).
balanced AS (
    SELECT a.*,
        CASE
            WHEN a.q_axis < 0.05 OR a.m_axis < 0.05 OR a.s_axis < 0.05 THEN NULL
            ELSE 3.0 / (1.0 / a.q_axis + 1.0 / a.m_axis + 1.0 / a.s_axis)
        END AS balance_score
    FROM axes a
),
-- §3.3 / LD-#37 — within-audience p25 of Q and S for the eligibility partition.
aud_p25 AS (
    SELECT audience_type,
           percentile_cont(0.25) WITHIN GROUP (ORDER BY q_axis) AS q_p25,
           percentile_cont(0.25) WITHIN GROUP (ORDER BY s_axis) AS s_p25
    FROM balanced
    GROUP BY audience_type
)
SELECT b.*,
       (b.q_axis >= p25.q_p25 AND b.s_axis >= p25.s_p25) AS is_eligible
FROM balanced b
JOIN aud_p25 p25 ON p25.audience_type IS NOT DISTINCT FROM b.audience_type;

ALTER VIEW v_market_opportunities_pregate OWNER TO llmanalyse;

COMMENT ON VIEW v_market_opportunities_pregate IS
  'Internal Path C surface: gated v_market_opportunities columns + tool_density,
   within-run axis-input percentiles, Q/M/S axes, ε-floored harmonic balance_score,
   low_confidence_s, and within-audience is_eligible (p25 on Q and S). Feeds
   fn_compute_pareto_layers and v_market_opportunities_ranked.';


-- ---------------------------------------------------------------------------
-- 3. v_market_opportunities_ranked — the surface the API reads.
--
-- pregate + pareto_layer. The function is called once per distinct audience
-- (cheap CROSS JOIN LATERAL over the small audience set), each call peeling that
-- audience's frontier. cluster_id is unique across the result so the LEFT JOIN
-- is unambiguous.
--
-- PERFORMANCE (2026-06-08): pregate is expensive to evaluate — nine within-run
-- window functions over the gated set plus the ~653K-row tool_density join
-- (pain_cluster_members ⋈ pain_objects), ~6.6s per evaluation. The naive form of
-- this view (a plain `layers` CTE selecting FROM the pregate view) caused Postgres
-- to evaluate pregate THREE times per query: once for the DISTINCT audience list,
-- once inside fn_compute_pareto_layers, and once for the outer projection. A wider
-- query (e.g. GROUP BY pareto_layer over the B2C surface) therefore took ~24s and a
-- cross-audience GROUP BY hit the 60s statement timeout.
--
-- Fix: pin pregate to a SINGLE materialization with `WITH pg AS MATERIALIZED`
-- (PG12+ optimization fence; reddit_raw is PG16). The audience list and the outer
-- projection then share that one scan, leaving only the function's own internal
-- scan — two evaluations instead of three. The B2C `GROUP BY pareto_layer`
-- verification query drops from ~24s to ~12.5s. `layers` is likewise MATERIALIZED
-- so the LATERAL peel runs exactly once. Identical row counts / layer assignment
-- to the un-materialized form (L1=28, L2=51, L3=48, L4=51, L5=48, NULL=514 for
-- B2C run 10). For multi-audience future runs the function still re-scans pregate
-- per audience; revisit if that surfaces in dashboard timings (see handoff).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_market_opportunities_ranked AS
WITH pg AS MATERIALIZED (
    SELECT * FROM v_market_opportunities_pregate
),
layers AS MATERIALIZED (
    SELECT f.cluster_id, f.pareto_layer
    FROM (
        SELECT DISTINCT audience_type
        FROM pg
        WHERE audience_type IS NOT NULL
    ) a
    CROSS JOIN LATERAL fn_compute_pareto_layers(
        NULLIF(current_setting('llm_analyse.cluster_run_id', true), '')::int,
        a.audience_type
    ) f
)
SELECT pg.*,
       l.pareto_layer
FROM pg
LEFT JOIN layers l ON l.cluster_id = pg.cluster_id;

ALTER VIEW v_market_opportunities_ranked OWNER TO llmanalyse;

COMMENT ON VIEW v_market_opportunities_ranked IS
  'Path C ranking surface for dashboard v2. v_market_opportunities_pregate plus
   pareto_layer (1 = optimal frontier, NULL = ineligible / balance-NULL / beyond
   layer 5). Default API sort: pareto_layer ASC NULLS LAST, balance_score DESC
   NULLS LAST. Same three GUCs as v_market_opportunities. LD-#36 / #37 / #38.';


-- ---------------------------------------------------------------------------
-- 4. v_market_opportunities — backward-compat shim (legacy columns only).
--
-- Old probes (test fixtures, ad-hoc psql, the count(*) invariant) keep working;
-- new code reads v_market_opportunities_ranked. Column list is exactly the
-- v2_013 projection (the spec calls it "35"; the actual legacy set is 34 — see
-- the handoff note).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_market_opportunities AS
SELECT
    cluster_id,
    cluster_run_id,
    label,
    member_count,
    coherence,
    is_candidate,
    audience_classifier_version,
    audience_type,
    audience_confidence,
    icp_hint,
    thread_diversity,
    author_diversity,
    subreddit_count,
    specificity_mean,
    workaround_ratio,
    separation,
    temporal_spread_days,
    source_diversity,
    viability_run_id,
    is_real_pain,
    is_software_solvable,
    q_specificity,
    q_noise_flag,
    v_buildability,
    v_icp_clarity,
    v_moat_potential,
    v_rationale,
    pain_count_last_4w,
    pain_count_prior_4w,
    weeks_with_data,
    total_pain,
    slope_per_week,
    velocity_4w_ratio,
    velocity_4w_ratio_normalized
FROM v_market_opportunities_ranked;

ALTER VIEW v_market_opportunities OWNER TO llmanalyse;

COMMENT ON VIEW v_market_opportunities IS
  'Backward-compat shim (LD-#31): legacy 34-column projection of
   v_market_opportunities_ranked. New code reads v_market_opportunities_ranked.';


-- ---------------------------------------------------------------------------
-- 5. Smoke-test invariants (run manually after applying; NOT executed here):
--
--   SET llm_analyse.cluster_run_id='10';
--   SET llm_analyse.viability_run_id='4';
--   SET llm_analyse.audience_classifier_version='v1';
--
--   -- LD-#33: still exactly 740 B2C, both surfaces.
--   SELECT count(*) FROM v_market_opportunities          WHERE audience_type='B2C';  -- 740
--   SELECT count(*) FROM v_market_opportunities_ranked   WHERE audience_type='B2C';  -- 740
--
--   -- Layer 1 non-empty; every < ε axis ⇒ balance & layer NULL.
--   SELECT count(*) FROM v_market_opportunities_ranked WHERE pareto_layer = 1;       -- >= 5
--   SELECT count(*) FROM v_market_opportunities_ranked
--    WHERE (q_axis < 0.05 OR m_axis < 0.05 OR s_axis < 0.05)
--      AND (balance_score IS NOT NULL OR pareto_layer IS NOT NULL);                  -- 0
--
--   -- Re-apply this file: idempotent (identical state).
-- ---------------------------------------------------------------------------
