-- v2_034: Burst sentinel + cold-start (Emerging) fix.
--
-- Two structural blind spots in short-term burst detection are addressed here,
-- WITHOUT touching the monthly recluster cadence (Decision #17) or the centroid
-- assignment threshold (Decision #23):
--
--   1. Structural blind spot — pains that fail centroid assignment (~72% at the
--      0.70 threshold) never reach pain_volume_daily (which is keyed off
--      pain_cluster_members), so a mid-month emerging topic fully escapes burst
--      detection until the next monthly recluster.
--   2. Cold-start miss — even when an emerging topic's pains attach to a nearby
--      cluster, burst_baseline_count = 0 forces cluster_lift = NULL, and the
--      'Bursting' label requires burst_lift >= 2, so the label falls NULL.
--
-- This migration:
--   * Adds pain_volume_daily_corpus (run-scoped corpus-level daily totals split
--     into assigned / orphan counts relative to the live run) populated by
--     aggregate_daily --mode corpus.
--   * Adds corpus_burst_snapshot (one run-level row per refresh) so the dashboard
--     can show a sentinel banner when unclustered pain is surging.
--   * Rewrites refresh_dashboard_market_opportunities() on top of the v2_033
--     competition-aware version: the burst CTE gains an 'Emerging' label branch
--     (no prior baseline + recent >= 5 + corpus steady), and the function upserts
--     a corpus burst snapshot row.
--
-- Backward compatible: existing 'Bursting' / 'Low-volume spike' semantics and the
-- competition wrapper logic are unchanged. Rolling back = DROP the two tables and
-- revert the function; Emerging clusters simply fall back to NULL (original).

-- ── corpus-level daily volume (assigned vs orphan relative to a run) ──────────

CREATE TABLE IF NOT EXISTS pain_volume_daily_corpus (
    cluster_run_id INT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    day_start      DATE NOT NULL,
    total_count    INT NOT NULL,
    assigned_count INT NOT NULL,
    orphan_count   INT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (cluster_run_id, day_start)
);

CREATE INDEX IF NOT EXISTS ix_pvd_corpus_run_day
    ON pain_volume_daily_corpus (cluster_run_id, day_start);

-- ── run-level corpus burst snapshot (one row per refresh) ─────────────────────

CREATE TABLE IF NOT EXISTS corpus_burst_snapshot (
    cluster_run_id    INT PRIMARY KEY REFERENCES analysis_runs(id) ON DELETE CASCADE,
    day_anchor        DATE,
    total_recent      INT,
    total_baseline    DOUBLE PRECISION,
    orphan_recent     INT,
    orphan_baseline   DOUBLE PRECISION,
    orphan_lift       DOUBLE PRECISION,
    orphan_zscore     DOUBLE PRECISION,
    orphan_label      TEXT,
    corpus_lift       DOUBLE PRECISION,
    snapshot_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── rewrite refresh function on top of the v2_033 competition-aware version ──

CREATE OR REPLACE FUNCTION refresh_dashboard_market_opportunities(
    p_cluster_run_id INT,
    p_viability_run_id INT,
    p_audience_classifier_version TEXT DEFAULT 'v1'
)
RETURNS TABLE (
    refreshed_rows INT,
    refreshed_at TIMESTAMPTZ,
    skipped BOOLEAN,
    error TEXT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_started_at TIMESTAMPTZ := clock_timestamp();
    v_finished_at TIMESTAMPTZ;
    v_rows INT := 0;
    v_lock_acquired BOOLEAN;
    v_anchor_day DATE;
BEGIN
    v_lock_acquired := pg_try_advisory_xact_lock(
        hashtext('dashboard_market_snapshot'),
        p_cluster_run_id
    );

    IF NOT v_lock_acquired THEN
        refreshed_rows := 0;
        refreshed_at := clock_timestamp();
        skipped := TRUE;
        error := 'snapshot refresh already running';

        INSERT INTO dashboard_market_refresh_state (
            cluster_run_id,
            viability_run_id,
            audience_classifier_version,
            status,
            started_at,
            finished_at,
            duration_seconds,
            row_count,
            error,
            updated_at
        )
        VALUES (
            p_cluster_run_id,
            p_viability_run_id,
            p_audience_classifier_version,
            'skipped',
            refreshed_at,
            refreshed_at,
            0,
            0,
            error,
            refreshed_at
        )
        ON CONFLICT (
            cluster_run_id,
            viability_run_id,
            audience_classifier_version
        ) DO UPDATE SET
        status = EXCLUDED.status,
        started_at = EXCLUDED.started_at,
        finished_at = EXCLUDED.finished_at,
        duration_seconds = EXCLUDED.duration_seconds,
        row_count = EXCLUDED.row_count,
        error = EXCLUDED.error,
        updated_at = EXCLUDED.updated_at;

        RETURN NEXT;
        RETURN;
    END IF;

    INSERT INTO dashboard_market_refresh_state (
        cluster_run_id,
        viability_run_id,
        audience_classifier_version,
        status,
        started_at,
        finished_at,
        duration_seconds,
        row_count,
        error,
        updated_at
    )
    VALUES (
        p_cluster_run_id,
        p_viability_run_id,
        p_audience_classifier_version,
        'running',
        v_started_at,
        NULL,
        NULL,
        NULL,
        NULL,
        clock_timestamp()
    )
    ON CONFLICT (
        cluster_run_id,
        viability_run_id,
        audience_classifier_version
    ) DO UPDATE SET
    status = EXCLUDED.status,
    started_at = EXCLUDED.started_at,
    finished_at = NULL,
    duration_seconds = NULL,
    row_count = NULL,
    error = NULL,
    updated_at = EXCLUDED.updated_at;

    PERFORM set_config('llm_analyse.cluster_run_id', p_cluster_run_id::TEXT, TRUE);
    PERFORM set_config('llm_analyse.viability_run_id', p_viability_run_id::TEXT, TRUE);
    PERFORM set_config(
        'llm_analyse.audience_classifier_version',
        p_audience_classifier_version,
        TRUE
    );

    SELECT COALESCE(
        MAX(day_start),
        (
            SELECT
                CASE
                    WHEN ar.window_end IS NULL THEN (NOW() AT TIME ZONE 'UTC')::date
                    ELSE ar.window_end::date
                END
            FROM analysis_runs ar
            WHERE ar.id = p_cluster_run_id
        ),
        (NOW() AT TIME ZONE 'UTC')::date
    )
    INTO v_anchor_day
    FROM pain_volume_daily
    WHERE cluster_run_id = p_cluster_run_id;

    DROP TABLE IF EXISTS pg_temp.tmp_dashboard_market_opportunities;
    CREATE TEMP TABLE tmp_dashboard_market_opportunities
    ON COMMIT DROP
    AS
    WITH corpus AS (
        SELECT
            COALESCE(SUM(pain_count) FILTER (
                WHERE day_start > v_anchor_day - INTERVAL '3 days'
                  AND day_start <= v_anchor_day
            ), 0)::DOUBLE PRECISION AS recent_count,
            COALESCE(SUM(pain_count) FILTER (
                WHERE day_start > v_anchor_day - INTERVAL '31 days'
                  AND day_start <= v_anchor_day - INTERVAL '3 days'
            ), 0)::DOUBLE PRECISION AS baseline_count
        FROM pain_volume_daily
        WHERE cluster_run_id = p_cluster_run_id
    ),
    ranked_clusters AS (
        SELECT cluster_id
        FROM v_market_opportunities_competition_ranked
    ),
    baseline_days AS (
        SELECT generate_series(
            v_anchor_day - INTERVAL '30 days',
            v_anchor_day - INTERVAL '3 days',
            INTERVAL '1 day'
        )::date AS day_start
    ),
    baseline_grid AS (
        SELECT
            rc.cluster_id,
            bd.day_start,
            COALESCE(pvd.pain_count, 0)::DOUBLE PRECISION AS pain_count
        FROM ranked_clusters rc
        CROSS JOIN baseline_days bd
        LEFT JOIN pain_volume_daily pvd
          ON pvd.cluster_run_id = p_cluster_run_id
         AND pvd.cluster_id = rc.cluster_id
         AND pvd.day_start = bd.day_start
    ),
    daily_stats AS (
        SELECT
            ranked.cluster_id,
            COALESCE(SUM(pvd.pain_count) FILTER (
                WHERE pvd.day_start > v_anchor_day - INTERVAL '3 days'
                  AND pvd.day_start <= v_anchor_day
            ), 0)::INT AS burst_recent_count,
            (
                SELECT SUM(bg.pain_count)
                FROM baseline_grid bg
                WHERE bg.cluster_id = ranked.cluster_id
            ) AS burst_baseline_total,
            (
                SELECT AVG(bg.pain_count)
                FROM baseline_grid bg
                WHERE bg.cluster_id = ranked.cluster_id
            ) AS baseline_mean_daily,
            (
                SELECT STDDEV_POP(bg.pain_count)
                FROM baseline_grid bg
                WHERE bg.cluster_id = ranked.cluster_id
            ) AS baseline_std_daily
        FROM v_market_opportunities_competition_ranked ranked
        LEFT JOIN pain_volume_daily pvd
          ON pvd.cluster_run_id = p_cluster_run_id
         AND pvd.cluster_id = ranked.cluster_id
         AND pvd.day_start > v_anchor_day - INTERVAL '3 days'
         AND pvd.day_start <= v_anchor_day
        GROUP BY ranked.cluster_id
    ),
    burst AS (
        SELECT
            ds.cluster_id,
            ds.burst_recent_count,
            (ds.burst_baseline_total * 3.0 / 28.0) AS burst_baseline_count,
            CASE
                WHEN ds.burst_baseline_total > 0
                    THEN (ds.burst_recent_count / 3.0) / (ds.burst_baseline_total / 28.0)
                WHEN ds.burst_recent_count > 0
                    THEN NULL
                ELSE NULL
            END AS cluster_lift,
            CASE
                WHEN corpus.baseline_count > 0
                    THEN (corpus.recent_count / 3.0) / (corpus.baseline_count / 28.0)
                ELSE NULL
            END AS burst_corpus_lift,
            CASE
                WHEN COALESCE(ds.baseline_std_daily, 0) > 0
                    THEN ((ds.burst_recent_count / 3.0) - ds.baseline_mean_daily)
                         / ds.baseline_std_daily
                WHEN ds.burst_recent_count > 0 AND COALESCE(ds.baseline_mean_daily, 0) = 0
                    THEN ds.burst_recent_count::DOUBLE PRECISION
                ELSE NULL
            END AS burst_zscore
        FROM daily_stats ds
        CROSS JOIN corpus
    ),
    burst_scored AS (
        SELECT
            b.*,
            CASE
                WHEN b.cluster_lift IS NOT NULL AND b.burst_corpus_lift IS NOT NULL
                    THEN b.cluster_lift / NULLIF(b.burst_corpus_lift, 0)
                ELSE NULL
            END AS burst_lift
        FROM burst b
    ),
    burst_labeled AS (
        SELECT
            bs.*,
            CASE
                WHEN bs.burst_recent_count >= 5
                 AND bs.burst_lift >= 2.0
                 AND bs.burst_zscore >= 2.0
                    THEN 'Bursting'
                WHEN bs.burst_recent_count > 0
                 AND bs.burst_recent_count < 5
                 AND bs.burst_lift >= 3.0
                    THEN 'Low-volume spike'
                -- Cold-start (Emerging): a cluster with NO prior baseline that
                -- suddenly has recent activity, while the overall corpus is steady
                -- (corpus_lift < 1.5) — i.e. a genuine mid-month new topic rather
                -- than a backfill surge. baseline_count = 0 already forced
                -- cluster_lift = NULL, so it cannot match Bursting above.
                WHEN COALESCE(bs.burst_baseline_count, 0) = 0
                 AND bs.burst_recent_count >= 5
                 AND COALESCE(bs.burst_corpus_lift, 0) < 1.5
                    THEN 'Emerging'
                ELSE NULL
            END AS burst_label,
            CASE
                WHEN bs.burst_recent_count <= 0 THEN NULL
                -- Emerging score: conservative, capped below the Bursting formula.
                WHEN COALESCE(bs.burst_baseline_count, 0) = 0
                 AND bs.burst_recent_count >= 5
                 AND COALESCE(bs.burst_corpus_lift, 0) < 1.5
                    THEN LEAST(1.0, bs.burst_recent_count / 10.0)
                         * LEAST(1.0, bs.burst_corpus_lift / 3.0)
                         * 0.7
                WHEN bs.burst_lift IS NULL OR bs.burst_zscore IS NULL THEN NULL
                ELSE LEAST(
                    1.0,
                    GREATEST(0.0, LN(GREATEST(bs.burst_lift, 1.0)) / LN(6.0))
                    * LEAST(1.0, bs.burst_recent_count / 5.0)
                    * LEAST(1.0, GREATEST(bs.burst_zscore, 0.0) / 3.0)
                )
            END AS burst_score
        FROM burst_scored bs
    )
    SELECT
        p_cluster_run_id              AS cluster_run_id,
        p_viability_run_id            AS viability_run_id,
        p_audience_classifier_version AS audience_classifier_version,
        ranked.cluster_id,
        ranked.audience_type,
        ranked.is_eligible,
        ranked.pareto_layer,
        ranked.balance_score::DOUBLE PRECISION AS balance_score,
        ranked.member_count::INT AS member_count,
        ranked.pain_count_last_4w::INT AS pain_count_last_4w,
        ranked.label,
        jsonb_strip_nulls(
            to_jsonb(ranked)
            || jsonb_build_object(
                'burst', jsonb_build_object(
                    'score', bl.burst_score,
                    'lift', bl.burst_lift,
                    'zscore', bl.burst_zscore,
                    'recent_count', bl.burst_recent_count,
                    'baseline_count', bl.burst_baseline_count,
                    'corpus_lift', bl.burst_corpus_lift,
                    'label', bl.burst_label,
                    'window', '3d_vs_prior_28d'
                )
            )
        ) AS row_data,
        ranked.competition_run_id,
        ranked.competition_status,
        ranked.market_structure,
        ranked.competition_confidence,
        ranked.external_supply_gap,
        ranked.competition_adjusted_s_axis,
        ranked.competition_adjusted_balance_score,
        ranked.competition_adjusted_pareto_layer,
        ranked.s_axis_source,
        bl.burst_score,
        bl.burst_lift,
        bl.burst_zscore,
        bl.burst_recent_count,
        bl.burst_baseline_count,
        bl.burst_corpus_lift,
        bl.burst_label,
        clock_timestamp() AS snapshot_at
    FROM v_market_opportunities_competition_ranked ranked
    LEFT JOIN burst_labeled bl ON bl.cluster_id = ranked.cluster_id;

    SELECT COUNT(*) INTO v_rows FROM tmp_dashboard_market_opportunities;

    DELETE FROM dashboard_market_opportunities
    WHERE cluster_run_id = p_cluster_run_id
      AND viability_run_id = p_viability_run_id
      AND audience_classifier_version = p_audience_classifier_version;

    INSERT INTO dashboard_market_opportunities (
        cluster_run_id,
        viability_run_id,
        audience_classifier_version,
        cluster_id,
        audience_type,
        is_eligible,
        pareto_layer,
        balance_score,
        member_count,
        pain_count_last_4w,
        label,
        row_data,
        competition_run_id,
        competition_status,
        market_structure,
        competition_confidence,
        external_supply_gap,
        competition_adjusted_s_axis,
        competition_adjusted_balance_score,
        competition_adjusted_pareto_layer,
        s_axis_source,
        burst_score,
        burst_lift,
        burst_zscore,
        burst_recent_count,
        burst_baseline_count,
        burst_corpus_lift,
        burst_label,
        snapshot_at
    )
    SELECT
        cluster_run_id,
        viability_run_id,
        audience_classifier_version,
        cluster_id,
        audience_type,
        is_eligible,
        pareto_layer,
        balance_score,
        member_count,
        pain_count_last_4w,
        label,
        row_data,
        competition_run_id,
        competition_status,
        market_structure,
        competition_confidence,
        external_supply_gap,
        competition_adjusted_s_axis,
        competition_adjusted_balance_score,
        competition_adjusted_pareto_layer,
        s_axis_source,
        burst_score,
        burst_lift,
        burst_zscore,
        burst_recent_count,
        burst_baseline_count,
        burst_corpus_lift,
        burst_label,
        snapshot_at
    FROM tmp_dashboard_market_opportunities;

    -- ── corpus burst sentinel: upsert one run-level row ──────────────────────
    -- Measures orphan pain (unclustered relative to this run) recent 3d vs prior
    -- 28d, using the same anchor_day and corpus guard as the per-cluster burst.
    INSERT INTO corpus_burst_snapshot (
        cluster_run_id,
        day_anchor,
        total_recent,
        total_baseline,
        orphan_recent,
        orphan_baseline,
        orphan_lift,
        orphan_zscore,
        orphan_label,
        corpus_lift,
        snapshot_at
    )
    SELECT
        p_cluster_run_id,
        v_anchor_day,
        cb.total_recent,
        cb.total_baseline,
        cb.orphan_recent,
        cb.orphan_baseline,
        cb.orphan_lift,
        cb.orphan_zscore,
        cb.orphan_label,
        cb.corpus_lift,
        clock_timestamp()
    FROM (
        SELECT
            COALESCE(SUM(total_count) FILTER (
                WHERE day_start > v_anchor_day - INTERVAL '3 days'
                  AND day_start <= v_anchor_day
            ), 0)::INT AS total_recent,
            COALESCE(SUM(total_count) FILTER (
                WHERE day_start > v_anchor_day - INTERVAL '31 days'
                  AND day_start <= v_anchor_day - INTERVAL '3 days'
            ), 0)::DOUBLE PRECISION AS total_baseline,
            COALESCE(SUM(orphan_count) FILTER (
                WHERE day_start > v_anchor_day - INTERVAL '3 days'
                  AND day_start <= v_anchor_day
            ), 0)::INT AS orphan_recent,
            COALESCE(SUM(orphan_count) FILTER (
                WHERE day_start > v_anchor_day - INTERVAL '31 days'
                  AND day_start <= v_anchor_day - INTERVAL '3 days'
            ), 0)::DOUBLE PRECISION AS orphan_baseline,
            COALESCE(STDDEV_POP(orphan_count) FILTER (
                WHERE day_start > v_anchor_day - INTERVAL '31 days'
                  AND day_start <= v_anchor_day - INTERVAL '3 days'
            ), 0)::DOUBLE PRECISION AS orphan_std_daily
        FROM pain_volume_daily_corpus
        WHERE cluster_run_id = p_cluster_run_id
    ) raw
    CROSS JOIN LATERAL (
        SELECT
            raw.total_recent,
            raw.total_baseline,
            raw.orphan_recent,
            raw.orphan_baseline,
            CASE
                WHEN raw.orphan_baseline > 0
                    THEN (raw.orphan_recent / 3.0) / (raw.orphan_baseline / 28.0)
                ELSE NULL
            END AS orphan_lift,
            CASE
                WHEN raw.orphan_std_daily > 0
                    THEN ((raw.orphan_recent / 3.0) - (raw.orphan_baseline / 28.0))
                         / raw.orphan_std_daily
                WHEN raw.orphan_recent > 0 AND raw.orphan_baseline = 0
                    THEN raw.orphan_recent::DOUBLE PRECISION
                ELSE NULL
            END AS orphan_zscore,
            CASE
                WHEN raw.total_baseline > 0
                    THEN (raw.total_recent / 3.0) / (raw.total_baseline / 28.0)
                ELSE NULL
            END AS corpus_lift
    ) cb
    CROSS JOIN LATERAL (
        SELECT
            CASE
                WHEN cb.orphan_recent >= 5
                 AND cb.orphan_lift >= 2.0
                 AND cb.orphan_zscore >= 2.0
                    THEN 'Bursting'
                WHEN cb.orphan_recent > 0
                 AND cb.orphan_recent < 5
                 AND cb.orphan_lift >= 3.0
                    THEN 'Low-volume spike'
                ELSE NULL
            END AS orphan_label
    ) lbl
    ON CONFLICT (cluster_run_id) DO UPDATE SET
        day_anchor      = EXCLUDED.day_anchor,
        total_recent    = EXCLUDED.total_recent,
        total_baseline  = EXCLUDED.total_baseline,
        orphan_recent   = EXCLUDED.orphan_recent,
        orphan_baseline = EXCLUDED.orphan_baseline,
        orphan_lift     = EXCLUDED.orphan_lift,
        orphan_zscore   = EXCLUDED.orphan_zscore,
        orphan_label    = EXCLUDED.orphan_label,
        corpus_lift     = EXCLUDED.corpus_lift,
        snapshot_at     = EXCLUDED.snapshot_at;

    v_finished_at := clock_timestamp();

    INSERT INTO dashboard_market_refresh_state (
        cluster_run_id,
        viability_run_id,
        audience_classifier_version,
        status,
        started_at,
        finished_at,
        duration_seconds,
        row_count,
        error,
        last_success_at,
        last_success_row_count,
        updated_at
    )
    VALUES (
        p_cluster_run_id,
        p_viability_run_id,
        p_audience_classifier_version,
        'succeeded',
        v_started_at,
        v_finished_at,
        EXTRACT(EPOCH FROM (v_finished_at - v_started_at)),
        v_rows,
        NULL,
        v_finished_at,
        v_rows,
        v_finished_at
    )
    ON CONFLICT (
        cluster_run_id,
        viability_run_id,
        audience_classifier_version
    ) DO UPDATE SET
    status = EXCLUDED.status,
    started_at = EXCLUDED.started_at,
    finished_at = EXCLUDED.finished_at,
    duration_seconds = EXCLUDED.duration_seconds,
    row_count = EXCLUDED.row_count,
    error = NULL,
    last_success_at = EXCLUDED.last_success_at,
    last_success_row_count = EXCLUDED.last_success_row_count,
    updated_at = EXCLUDED.updated_at;

    refreshed_rows := v_rows;
    refreshed_at := v_finished_at;
    skipped := FALSE;
    error := NULL;
    RETURN NEXT;
    RETURN;

EXCEPTION WHEN OTHERS THEN
    v_finished_at := clock_timestamp();

    INSERT INTO dashboard_market_refresh_state (
        cluster_run_id,
        viability_run_id,
        audience_classifier_version,
        status,
        started_at,
        finished_at,
        duration_seconds,
        row_count,
        error,
        updated_at
    )
    VALUES (
        p_cluster_run_id,
        p_viability_run_id,
        p_audience_classifier_version,
        'failed',
        v_started_at,
        v_finished_at,
        EXTRACT(EPOCH FROM (v_finished_at - v_started_at)),
        NULL,
        SQLERRM,
        v_finished_at
    )
    ON CONFLICT (
        cluster_run_id,
        viability_run_id,
        audience_classifier_version
    ) DO UPDATE SET
    status = EXCLUDED.status,
    started_at = EXCLUDED.started_at,
    finished_at = EXCLUDED.finished_at,
    duration_seconds = EXCLUDED.duration_seconds,
    row_count = NULL,
    error = EXCLUDED.error,
    updated_at = EXCLUDED.updated_at;

    refreshed_rows := 0;
    refreshed_at := v_finished_at;
    skipped := FALSE;
    error := SQLERRM;
    RETURN NEXT;
    RETURN;
END;
$$;
