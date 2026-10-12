-- v2_028: 3d vs prior 28d short-term burst detection for dashboard clusters.

CREATE TABLE IF NOT EXISTS pain_volume_daily (
    cluster_run_id INT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    cluster_id INT NOT NULL REFERENCES pain_clusters(id) ON DELETE CASCADE,
    day_start DATE NOT NULL,
    pain_count INT NOT NULL,
    share_global DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (cluster_run_id, cluster_id, day_start)
);

CREATE INDEX IF NOT EXISTS ix_pvd_run_day
    ON pain_volume_daily (cluster_run_id, day_start, cluster_id);

ALTER TABLE dashboard_market_opportunities
    ADD COLUMN IF NOT EXISTS burst_score DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS burst_lift DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS burst_zscore DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS burst_recent_count INT,
    ADD COLUMN IF NOT EXISTS burst_baseline_count DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS burst_corpus_lift DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS burst_label TEXT;

CREATE INDEX IF NOT EXISTS ix_dashboard_market_opportunities_burst
    ON dashboard_market_opportunities (
        cluster_run_id,
        viability_run_id,
        audience_classifier_version,
        audience_type,
        burst_score DESC NULLS LAST,
        cluster_id
    );

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
        FROM v_market_opportunities_ranked
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
        FROM v_market_opportunities_ranked ranked
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
                ELSE NULL
            END AS burst_label,
            CASE
                WHEN bs.burst_lift IS NULL OR bs.burst_zscore IS NULL THEN NULL
                WHEN bs.burst_recent_count <= 0 THEN NULL
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
        bl.burst_score,
        bl.burst_lift,
        bl.burst_zscore,
        bl.burst_recent_count,
        bl.burst_baseline_count,
        bl.burst_corpus_lift,
        bl.burst_label,
        clock_timestamp() AS snapshot_at
    FROM v_market_opportunities_ranked ranked
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
        burst_score,
        burst_lift,
        burst_zscore,
        burst_recent_count,
        burst_baseline_count,
        burst_corpus_lift,
        burst_label,
        snapshot_at
    FROM tmp_dashboard_market_opportunities;

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
