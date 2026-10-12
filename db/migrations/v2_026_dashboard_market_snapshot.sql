-- v2_026: Dashboard market opportunity snapshots.
--
-- /api/v2/clusters must not recompute v_market_opportunities_ranked on every
-- request. The ranked view is still the source of truth, but the EC2 worker
-- refreshes this snapshot table on a schedule and after successful pipeline
-- jobs. API reads stay fast and predictable.

CREATE TABLE IF NOT EXISTS dashboard_market_opportunities (
    cluster_run_id              INT NOT NULL,
    viability_run_id            INT NOT NULL,
    audience_classifier_version TEXT NOT NULL,
    cluster_id                  INT NOT NULL,
    audience_type               TEXT,
    is_eligible                 BOOLEAN,
    pareto_layer                INT,
    balance_score               DOUBLE PRECISION,
    member_count                INT,
    pain_count_last_4w          INT,
    label                       TEXT,
    row_data                    JSONB NOT NULL,
    snapshot_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (
        cluster_run_id,
        viability_run_id,
        audience_classifier_version,
        cluster_id
    )
);

CREATE INDEX IF NOT EXISTS ix_dashboard_market_opportunities_list
    ON dashboard_market_opportunities (
        cluster_run_id,
        viability_run_id,
        audience_classifier_version,
        audience_type,
        pareto_layer ASC NULLS LAST,
        balance_score DESC NULLS LAST,
        cluster_id
    );

CREATE INDEX IF NOT EXISTS ix_dashboard_market_opportunities_eligible
    ON dashboard_market_opportunities (
        cluster_run_id,
        viability_run_id,
        audience_classifier_version,
        audience_type,
        is_eligible
    );

CREATE TABLE IF NOT EXISTS dashboard_market_refresh_state (
    cluster_run_id              INT NOT NULL,
    viability_run_id            INT NOT NULL,
    audience_classifier_version TEXT NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'skipped')),
    started_at                  TIMESTAMPTZ,
    finished_at                 TIMESTAMPTZ,
    duration_seconds            NUMERIC(12, 3),
    row_count                   INT,
    error                       TEXT,
    last_success_at             TIMESTAMPTZ,
    last_success_row_count      INT,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (
        cluster_run_id,
        viability_run_id,
        audience_classifier_version
    )
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

    DROP TABLE IF EXISTS pg_temp.tmp_dashboard_market_opportunities;
    CREATE TEMP TABLE tmp_dashboard_market_opportunities
    ON COMMIT DROP
    AS
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
        to_jsonb(ranked) AS row_data,
        clock_timestamp() AS snapshot_at
    FROM v_market_opportunities_ranked ranked;

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

COMMENT ON TABLE dashboard_market_opportunities IS
  'Fast dashboard list snapshot refreshed from v_market_opportunities_ranked by the EC2 worker.';

COMMENT ON TABLE dashboard_market_refresh_state IS
  'Refresh status for dashboard_market_opportunities by run and classifier version.';
