-- v2_033: Production L5 competition layer MVP.
--
-- Adds run-scoped competition assessments and a wrapper ranking surface that
-- exposes the competition-derived S axis as the canonical Explore supply axis.
-- The original Path C view remains unchanged for historical comparison and
-- rollback, but the new Explore surface does not use Reddit-proxy S as a
-- fallback when competition data is absent or low-confidence.

CREATE TABLE IF NOT EXISTS competition_runs (
    id                          BIGSERIAL PRIMARY KEY,
    cluster_run_id              INT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    viability_run_id            INT NOT NULL REFERENCES viability_runs(id) ON DELETE CASCADE,
    audience_classifier_version TEXT NOT NULL DEFAULT 'v1',
    audience_type               TEXT NOT NULL DEFAULT 'B2C',
    scope                       TEXT NOT NULL DEFAULT 'top_explore',
    limit_count                 INT NOT NULL DEFAULT 50,
    source_policy               JSONB NOT NULL DEFAULT '{}'::jsonb,
    status                      TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    is_active                   BOOLEAN NOT NULL DEFAULT FALSE,
    requested_by                TEXT,
    error_log                   TEXT,
    started_at                  TIMESTAMPTZ,
    finished_at                 TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_competition_runs_context_active
    ON competition_runs (
        cluster_run_id,
        viability_run_id,
        audience_classifier_version,
        audience_type,
        is_active,
        id DESC
    );

CREATE TABLE IF NOT EXISTS cluster_competition_assessments (
    competition_run_id          BIGINT NOT NULL REFERENCES competition_runs(id) ON DELETE CASCADE,
    cluster_id                  INT NOT NULL REFERENCES pain_clusters(id) ON DELETE CASCADE,
    cluster_run_id              INT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    viability_run_id            INT NOT NULL REFERENCES viability_runs(id) ON DELETE CASCADE,
    audience_classifier_version TEXT NOT NULL DEFAULT 'v1',
    market_structure            TEXT NOT NULL DEFAULT 'unknown'
        CHECK (market_structure IN (
            'strong_incumbent',
            'crowded_mature',
            'fragmented_low_quality',
            'adjacent_only',
            'emerging_greenfield',
            'unknown'
        )),
    research_confidence         DOUBLE PRECISION NOT NULL DEFAULT 0
        CHECK (research_confidence >= 0 AND research_confidence <= 1),
    external_supply_gap         DOUBLE PRECISION
        CHECK (external_supply_gap IS NULL OR (external_supply_gap >= 0 AND external_supply_gap <= 1)),
    competition_intensity       DOUBLE PRECISION NOT NULL DEFAULT 0
        CHECK (competition_intensity >= 0 AND competition_intensity <= 1),
    competition_adjusted_s_axis DOUBLE PRECISION
        CHECK (
            competition_adjusted_s_axis IS NULL
            OR (competition_adjusted_s_axis >= 0 AND competition_adjusted_s_axis <= 1)
        ),
    solution_gap_evidence       DOUBLE PRECISION NOT NULL DEFAULT 0
        CHECK (solution_gap_evidence >= 0 AND solution_gap_evidence <= 1),
    verified_direct_count       INT NOT NULL DEFAULT 0,
    direct_competitor_count     INT NOT NULL DEFAULT 0,
    incumbent_pressure          DOUBLE PRECISION NOT NULL DEFAULT 0,
    max_direct_strength         DOUBLE PRECISION NOT NULL DEFAULT 0,
    rationale                   TEXT,
    raw_research                JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw_scores                  JSONB NOT NULL DEFAULT '{}'::jsonb,
    model_used                  TEXT,
    tokens_in                   INT,
    tokens_out                  INT,
    cost_usd                    NUMERIC(10, 4) DEFAULT 0,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (competition_run_id, cluster_id)
);

CREATE INDEX IF NOT EXISTS ix_cca_cluster_context
    ON cluster_competition_assessments (
        cluster_run_id,
        viability_run_id,
        audience_classifier_version,
        cluster_id
    );

CREATE TABLE IF NOT EXISTS cluster_competitors (
    id                     BIGSERIAL PRIMARY KEY,
    competition_run_id     BIGINT NOT NULL REFERENCES competition_runs(id) ON DELETE CASCADE,
    cluster_id             INT NOT NULL REFERENCES pain_clusters(id) ON DELETE CASCADE,
    name                   TEXT NOT NULL,
    url                    TEXT,
    category               TEXT NOT NULL
        CHECK (category IN ('direct', 'adjacent', 'incumbent')),
    pricing                TEXT,
    business_model         TEXT,
    positioning            TEXT,
    evidence               TEXT,
    confidence             TEXT NOT NULL DEFAULT 'unverified'
        CHECK (confidence IN ('verified', 'unverified')),
    relevance_score        DOUBLE PRECISION NOT NULL DEFAULT 0
        CHECK (relevance_score >= 0 AND relevance_score <= 1),
    strength_score         DOUBLE PRECISION NOT NULL DEFAULT 0
        CHECK (strength_score >= 0 AND strength_score <= 1),
    satisfaction_gap       DOUBLE PRECISION NOT NULL DEFAULT 0
        CHECK (satisfaction_gap >= 0 AND satisfaction_gap <= 1),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_cluster_competitors_cluster
    ON cluster_competitors (competition_run_id, cluster_id, category, strength_score DESC);

-- Dashboard snapshot columns. Nullable by design: NULL means no usable
-- competition-derived Explore S is available for that row.
ALTER TABLE dashboard_market_opportunities
    ADD COLUMN IF NOT EXISTS competition_run_id BIGINT,
    ADD COLUMN IF NOT EXISTS competition_status TEXT,
    ADD COLUMN IF NOT EXISTS market_structure TEXT,
    ADD COLUMN IF NOT EXISTS competition_confidence DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS external_supply_gap DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS competition_adjusted_s_axis DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS competition_adjusted_balance_score DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS competition_adjusted_pareto_layer INT,
    ADD COLUMN IF NOT EXISTS s_axis_source TEXT NOT NULL DEFAULT 'missing';

CREATE INDEX IF NOT EXISTS ix_dashboard_market_competition_rank
    ON dashboard_market_opportunities (
        cluster_run_id,
        viability_run_id,
        audience_classifier_version,
        audience_type,
        competition_adjusted_pareto_layer ASC NULLS LAST,
        competition_adjusted_balance_score DESC NULLS LAST,
        pareto_layer ASC NULLS LAST,
        cluster_id
    );

-- Pregate wrapper: old Path C columns + active competition assessment fields.
CREATE OR REPLACE VIEW v_market_opportunities_competition_pregate AS
WITH active_runs AS (
    SELECT DISTINCT ON (
        cluster_run_id,
        viability_run_id,
        audience_classifier_version,
        audience_type
    )
        *
    FROM competition_runs
    WHERE status = 'succeeded'
      AND is_active IS TRUE
    ORDER BY
        cluster_run_id,
        viability_run_id,
        audience_classifier_version,
        audience_type,
        id DESC
),
joined AS (
    SELECT
        ranked.*,
        cr.id AS competition_run_id,
        cr.status AS competition_status,
        cca.market_structure,
        cca.research_confidence AS competition_confidence,
        cca.external_supply_gap,
        cca.competition_intensity,
        cca.competition_adjusted_s_axis,
        cca.solution_gap_evidence,
        cca.verified_direct_count,
        cca.direct_competitor_count,
        cca.rationale AS competition_rationale,
        (cca.cluster_id IS NOT NULL) AS has_competition_assessment,
        CASE
            WHEN cca.competition_adjusted_s_axis IS NOT NULL
                THEN 'competition_adjusted'
            ELSE 'missing'
        END AS s_axis_source,
        cca.competition_adjusted_s_axis AS effective_s_axis
    FROM v_market_opportunities_ranked ranked
    LEFT JOIN active_runs cr
      ON cr.cluster_run_id = ranked.cluster_run_id
     AND cr.viability_run_id = ranked.viability_run_id
     AND cr.audience_classifier_version = ranked.audience_classifier_version
     AND cr.audience_type IS NOT DISTINCT FROM ranked.audience_type
    LEFT JOIN cluster_competition_assessments cca
      ON cca.competition_run_id = cr.id
     AND cca.cluster_id = ranked.cluster_id
)
SELECT
    joined.*,
    CASE
        WHEN joined.has_competition_assessment IS NOT TRUE THEN NULL
        WHEN joined.competition_adjusted_s_axis IS NULL THEN NULL
        WHEN joined.q_axis < 0.05 OR joined.m_axis < 0.05 OR joined.effective_s_axis < 0.05 THEN NULL
        ELSE 3.0 / (
            1.0 / joined.q_axis
            + 1.0 / joined.m_axis
            + 1.0 / joined.effective_s_axis
        )
    END AS competition_adjusted_balance_score
FROM joined;

ALTER VIEW v_market_opportunities_competition_pregate OWNER TO llmanalyse;

CREATE OR REPLACE FUNCTION fn_compute_competition_pareto_layers(
    p_run_id INT,
    p_viability_run_id INT,
    p_audience_classifier_version TEXT,
    p_audience TEXT
)
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
    SELECT array_agg(pg.cluster_id),
           array_agg(pg.q_axis),
           array_agg(pg.m_axis),
           array_agg(pg.effective_s_axis)
      INTO v_id, v_q, v_m, v_s
      FROM v_market_opportunities_competition_pregate pg
     WHERE pg.cluster_run_id = p_run_id
       AND pg.viability_run_id = p_viability_run_id
       AND pg.audience_classifier_version = p_audience_classifier_version
       AND pg.audience_type = p_audience
       AND pg.competition_run_id IS NOT NULL
       AND pg.has_competition_assessment IS TRUE
       AND pg.competition_adjusted_s_axis IS NOT NULL
       AND pg.is_eligible IS TRUE
       AND pg.competition_adjusted_balance_score IS NOT NULL;

    WHILE v_layer <= 5 AND array_length(v_id, 1) > 0 LOOP
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

        EXIT WHEN v_this IS NULL;
        RETURN QUERY SELECT unnest(v_this), v_layer;

        SELECT array_agg(t.id), array_agg(t.q), array_agg(t.m), array_agg(t.s)
          INTO v_id, v_q, v_m, v_s
          FROM unnest(v_id, v_q, v_m, v_s) AS t(id, q, m, s)
         WHERE t.id <> ALL (v_this);

        v_layer := v_layer + 1;
    END LOOP;

    RETURN;
END;
$fn$;

ALTER FUNCTION fn_compute_competition_pareto_layers(INT, INT, TEXT, TEXT) OWNER TO llmanalyse;

CREATE OR REPLACE VIEW v_market_opportunities_competition_ranked AS
WITH pg AS MATERIALIZED (
    SELECT * FROM v_market_opportunities_competition_pregate
),
layers AS MATERIALIZED (
    SELECT f.cluster_id, f.pareto_layer
    FROM (
        SELECT DISTINCT
            cluster_run_id,
            viability_run_id,
            audience_classifier_version,
            audience_type
        FROM pg
        WHERE competition_run_id IS NOT NULL
          AND audience_type IS NOT NULL
    ) a
    CROSS JOIN LATERAL fn_compute_competition_pareto_layers(
        a.cluster_run_id,
        a.viability_run_id,
        a.audience_classifier_version,
        a.audience_type
    ) f
)
SELECT
    pg.*,
    l.pareto_layer AS competition_adjusted_pareto_layer
FROM pg
LEFT JOIN layers l ON l.cluster_id = pg.cluster_id;

ALTER VIEW v_market_opportunities_competition_ranked OWNER TO llmanalyse;

-- Snapshot refresh now reads the competition wrapper and stores the adjusted
-- nullable fields in both columns and row_data.
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
