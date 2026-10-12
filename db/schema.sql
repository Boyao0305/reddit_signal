-- reddit_signal schema snapshot for new installations.
-- schema snapshot through: v2_048_comments_created_utc_index.sql
-- `migrate apply` loads this file (then db/seed.sql) into an empty database and records every migration
-- up to the one named above as applied; later migrations then run normally. Regenerate with pg_dump
-- --schema-only --no-owner --no-privileges after adding migrations.

--
--



SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


--
-- Name: _t1(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public._t1() RETURNS integer
    LANGUAGE plpgsql STABLE
    AS $$
BEGIN
  CREATE TEMP TABLE _z(a int) ON COMMIT DROP;
  INSERT INTO _z VALUES (1),(2);
  DELETE FROM _z WHERE a=1;
  RETURN (SELECT count(*)::int FROM _z);
END; $$;


--
-- Name: fn_compute_competition_pareto_layers(integer, integer, text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_compute_competition_pareto_layers(p_run_id integer, p_viability_run_id integer, p_audience_classifier_version text, p_audience text) RETURNS TABLE(cluster_id integer, pareto_layer integer)
    LANGUAGE plpgsql STABLE
    AS $$
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
$$;


--
-- Name: fn_compute_pareto_layers(integer, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_compute_pareto_layers(p_run_id integer, p_audience text) RETURNS TABLE(cluster_id integer, pareto_layer integer)
    LANGUAGE plpgsql STABLE
    AS $$
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
$$;


--
-- Name: FUNCTION fn_compute_pareto_layers(p_run_id integer, p_audience text); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.fn_compute_pareto_layers(p_run_id integer, p_audience text) IS 'Within (run, audience) iterative Pareto peel over (q_axis, m_axis, s_axis) of
   v_market_opportunities_pregate. Returns layer 1..5 for eligible, non-balance-NULL
   clusters; clusters beyond layer 5 (and ineligible / balance-NULL ones) are not
   returned so their pareto_layer resolves to NULL. STABLE; reads the cluster_run_id
   GUC via the pregate view.';


--
-- Name: refresh_dashboard_market_opportunities(integer, integer, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.refresh_dashboard_market_opportunities(p_cluster_run_id integer, p_viability_run_id integer, p_audience_classifier_version text DEFAULT 'v1'::text) RETURNS TABLE(refreshed_rows integer, refreshed_at timestamp with time zone, skipped boolean, error text)
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
        lbl.orphan_label,
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


--
-- Name: touch_dashboard_user_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.touch_dashboard_user_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: touch_pipeline_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.touch_pipeline_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: touch_reddit_backfill_chunks_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.touch_reddit_backfill_chunks_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: touch_reddit_subreddit_sources_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.touch_reddit_subreddit_sources_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: touch_social_source_venues_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.touch_social_source_venues_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: touch_source_backfill_chunks_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.touch_source_backfill_chunks_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: touch_system_settings_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.touch_system_settings_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: analysis_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.analysis_runs (
    id integer NOT NULL,
    window_start timestamp with time zone NOT NULL,
    window_end timestamp with time zone NOT NULL,
    resolution character varying(20) DEFAULT 'adhoc'::character varying NOT NULL,
    params jsonb,
    cluster_count integer,
    noise_count integer,
    total_points integer,
    created_at timestamp with time zone DEFAULT now(),
    label text,
    pain_filter jsonb,
    is_legacy boolean DEFAULT false NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    candidate_thresholds jsonb,
    status character varying(16) DEFAULT 'historical'::character varying NOT NULL,
    live_at timestamp with time zone,
    CONSTRAINT analysis_runs_status_check CHECK (((status)::text = ANY (ARRAY[('live'::character varying)::text, ('historical'::character varying)::text, ('legacy'::character varying)::text, ('building'::character varying)::text, ('failed'::character varying)::text])))
);


--
-- Name: COLUMN analysis_runs.status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.analysis_runs.status IS 'Lifecycle state: live = current canonical run; historical = past non-legacy snapshot; legacy = v1.
     Orchestrator transitions previous live to historical when creating a new run.
     See 05_potential_implementations/next_tasks/cluster_run_continuation.md for the broader story.';


--
-- Name: COLUMN analysis_runs.live_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.analysis_runs.live_at IS 'Timestamp when the run became the production live run. Backfilled historical values are approximate from finished_at/started_at/created_at/window_end.';


--
-- Name: analysis_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.analysis_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: analysis_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.analysis_runs_id_seq OWNED BY public.analysis_runs.id;


--
-- Name: app_search_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.app_search_cache (
    keyword text NOT NULL,
    result_app_ids text[] NOT NULL,
    result_count integer,
    searched_at timestamp with time zone DEFAULT now()
);


--
-- Name: app_store_apps; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.app_store_apps (
    app_id text NOT NULL,
    store text DEFAULT 'google_play'::text NOT NULL,
    title text NOT NULL,
    developer text,
    developer_id text,
    developer_email text,
    category text,
    genre_id text,
    description text,
    summary text,
    score double precision,
    ratings_count integer,
    histogram integer[],
    real_installs bigint,
    installs_bucket text,
    price double precision,
    free boolean,
    offers_iap boolean,
    contains_ads boolean,
    released text,
    last_updated text,
    version text,
    content_rating text,
    embedding public.vector(384),
    scraped_at timestamp with time zone DEFAULT now()
);


--
-- Name: candidate_promotion_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.candidate_promotion_log (
    id integer NOT NULL,
    cluster_run_id integer NOT NULL,
    cluster_id integer NOT NULL,
    promoted_at timestamp with time zone DEFAULT now(),
    metrics_snapshot jsonb NOT NULL,
    threshold_snapshot jsonb NOT NULL,
    direction character varying(16) NOT NULL,
    CONSTRAINT candidate_promotion_log_direction_check CHECK (((direction)::text = ANY (ARRAY[('promoted'::character varying)::text, ('demoted'::character varying)::text])))
);


--
-- Name: candidate_promotion_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.candidate_promotion_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: candidate_promotion_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.candidate_promotion_log_id_seq OWNED BY public.candidate_promotion_log.id;


--
-- Name: cluster_audience; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_audience (
    id integer NOT NULL,
    cluster_id integer NOT NULL,
    classifier_version character varying(32) DEFAULT 'v1'::character varying NOT NULL,
    audience_type character varying(16) NOT NULL,
    confidence double precision NOT NULL,
    icp_hint text,
    reasoning text,
    sample_pain_ids integer[],
    surface_distribution jsonb,
    llm_model character varying(64),
    llm_input_tokens integer,
    llm_output_tokens integer,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT cluster_audience_audience_type_check CHECK (((audience_type)::text = ANY (ARRAY[('B2B'::character varying)::text, ('B2C'::character varying)::text, ('mixed'::character varying)::text, ('unclear'::character varying)::text]))),
    CONSTRAINT cluster_audience_confidence_check CHECK (((confidence >= (0)::double precision) AND (confidence <= (1)::double precision)))
);


--
-- Name: cluster_audience_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cluster_audience_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cluster_audience_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cluster_audience_id_seq OWNED BY public.cluster_audience.id;


--
-- Name: cluster_competition; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_competition (
    id integer NOT NULL,
    cluster_id integer,
    analysis_run_id integer,
    app_id text,
    similarity double precision,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: cluster_competition_assessments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_competition_assessments (
    competition_run_id bigint NOT NULL,
    cluster_id integer NOT NULL,
    cluster_run_id integer NOT NULL,
    viability_run_id integer NOT NULL,
    audience_classifier_version text DEFAULT 'v1'::text CONSTRAINT cluster_competition_assessm_audience_classifier_versio_not_null NOT NULL,
    market_structure text DEFAULT 'unknown'::text NOT NULL,
    research_confidence double precision DEFAULT 0 NOT NULL,
    external_supply_gap double precision,
    competition_intensity double precision DEFAULT 0 NOT NULL,
    competition_adjusted_s_axis double precision,
    solution_gap_evidence double precision DEFAULT 0 NOT NULL,
    verified_direct_count integer DEFAULT 0 NOT NULL,
    direct_competitor_count integer DEFAULT 0 CONSTRAINT cluster_competition_assessment_direct_competitor_count_not_null NOT NULL,
    incumbent_pressure double precision DEFAULT 0 NOT NULL,
    max_direct_strength double precision DEFAULT 0 NOT NULL,
    rationale text,
    raw_research jsonb DEFAULT '{}'::jsonb NOT NULL,
    raw_scores jsonb DEFAULT '{}'::jsonb NOT NULL,
    model_used text,
    tokens_in integer,
    tokens_out integer,
    cost_usd numeric(10,4) DEFAULT 0,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    domain_id integer,
    research_scope text DEFAULT 'per_cluster'::text NOT NULL,
    CONSTRAINT cluster_competition_assessmen_competition_adjusted_s_axis_check CHECK (((competition_adjusted_s_axis IS NULL) OR ((competition_adjusted_s_axis >= (0)::double precision) AND (competition_adjusted_s_axis <= (1)::double precision)))),
    CONSTRAINT cluster_competition_assessments_competition_intensity_check CHECK (((competition_intensity >= (0)::double precision) AND (competition_intensity <= (1)::double precision))),
    CONSTRAINT cluster_competition_assessments_external_supply_gap_check CHECK (((external_supply_gap IS NULL) OR ((external_supply_gap >= (0)::double precision) AND (external_supply_gap <= (1)::double precision)))),
    CONSTRAINT cluster_competition_assessments_market_structure_check CHECK ((market_structure = ANY (ARRAY['strong_incumbent'::text, 'crowded_mature'::text, 'fragmented_low_quality'::text, 'adjacent_only'::text, 'emerging_greenfield'::text, 'unknown'::text]))),
    CONSTRAINT cluster_competition_assessments_research_confidence_check CHECK (((research_confidence >= (0)::double precision) AND (research_confidence <= (1)::double precision))),
    CONSTRAINT cluster_competition_assessments_research_scope_check CHECK ((research_scope = ANY (ARRAY['per_cluster'::text, 'per_domain'::text]))),
    CONSTRAINT cluster_competition_assessments_solution_gap_evidence_check CHECK (((solution_gap_evidence >= (0)::double precision) AND (solution_gap_evidence <= (1)::double precision)))
);


--
-- Name: cluster_competition_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cluster_competition_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cluster_competition_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cluster_competition_id_seq OWNED BY public.cluster_competition.id;


--
-- Name: cluster_competitors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_competitors (
    id bigint NOT NULL,
    competition_run_id bigint NOT NULL,
    cluster_id integer NOT NULL,
    name text NOT NULL,
    url text,
    category text NOT NULL,
    pricing text,
    business_model text,
    positioning text,
    evidence text,
    confidence text DEFAULT 'unverified'::text NOT NULL,
    relevance_score double precision DEFAULT 0 NOT NULL,
    strength_score double precision DEFAULT 0 NOT NULL,
    satisfaction_gap double precision DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT cluster_competitors_category_check CHECK ((category = ANY (ARRAY['direct'::text, 'adjacent'::text, 'incumbent'::text]))),
    CONSTRAINT cluster_competitors_confidence_check CHECK ((confidence = ANY (ARRAY['verified'::text, 'unverified'::text]))),
    CONSTRAINT cluster_competitors_relevance_score_check CHECK (((relevance_score >= (0)::double precision) AND (relevance_score <= (1)::double precision))),
    CONSTRAINT cluster_competitors_satisfaction_gap_check CHECK (((satisfaction_gap >= (0)::double precision) AND (satisfaction_gap <= (1)::double precision))),
    CONSTRAINT cluster_competitors_strength_score_check CHECK (((strength_score >= (0)::double precision) AND (strength_score <= (1)::double precision)))
);


--
-- Name: cluster_competitors_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cluster_competitors_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cluster_competitors_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cluster_competitors_id_seq OWNED BY public.cluster_competitors.id;


--
-- Name: cluster_domain_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_domain_groups (
    competition_run_id bigint NOT NULL,
    domain_id integer NOT NULL,
    domain_label text NOT NULL,
    representative_cluster_id integer NOT NULL,
    cluster_count integer DEFAULT 0 NOT NULL,
    member_seed_tools text[] DEFAULT '{}'::text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cluster_domain_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_domain_members (
    competition_run_id bigint NOT NULL,
    domain_id integer NOT NULL,
    cluster_id integer NOT NULL,
    is_representative boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cluster_lineage; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_lineage (
    parent_run_id integer,
    parent_cluster integer,
    child_run_id integer NOT NULL,
    child_cluster integer,
    overlap_jaccard double precision,
    shared_members integer,
    relation character varying(20) NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: cluster_quality_metrics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_quality_metrics (
    cluster_run_id integer NOT NULL,
    cluster_id integer NOT NULL,
    source_diversity double precision,
    thread_diversity double precision,
    author_diversity double precision,
    subreddit_count integer,
    specificity_mean double precision,
    workaround_ratio double precision,
    separation double precision,
    nearest_cluster_id integer,
    temporal_spread_days integer,
    computed_at timestamp with time zone DEFAULT now(),
    metrics_version character varying(16) DEFAULT 'v1'::character varying NOT NULL
);


--
-- Name: clustering_runs; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.clustering_runs AS
 SELECT id,
    label,
    COALESCE(params, '{}'::jsonb) AS params,
    pain_filter,
    is_legacy,
    cluster_count,
    noise_count,
    total_points,
    started_at,
    finished_at,
    candidate_thresholds,
    window_start,
    window_end,
    resolution,
    created_at
   FROM public.analysis_runs;


--
-- Name: comments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.comments (
    id integer NOT NULL,
    reddit_id character varying(20) NOT NULL,
    post_id integer,
    parent_reddit_id character varying(20),
    author character varying(100),
    body text,
    score integer DEFAULT 0,
    is_submitter boolean DEFAULT false,
    depth integer DEFAULT 0,
    created_utc timestamp without time zone,
    fetched_at timestamp without time zone DEFAULT now(),
    raw_json jsonb
);


--
-- Name: comments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.comments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: comments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.comments_id_seq OWNED BY public.comments.id;


--
-- Name: competition_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.competition_runs (
    id bigint NOT NULL,
    cluster_run_id integer NOT NULL,
    viability_run_id integer NOT NULL,
    audience_classifier_version text DEFAULT 'v1'::text NOT NULL,
    audience_type text DEFAULT 'B2C'::text NOT NULL,
    scope text DEFAULT 'top_explore'::text NOT NULL,
    limit_count integer DEFAULT 50 NOT NULL,
    source_policy jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    is_active boolean DEFAULT false NOT NULL,
    requested_by text,
    error_log text,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    domain_count integer,
    CONSTRAINT competition_runs_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'succeeded'::text, 'failed'::text])))
);


--
-- Name: competition_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.competition_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: competition_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.competition_runs_id_seq OWNED BY public.competition_runs.id;


--
-- Name: corpus_burst_snapshot; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.corpus_burst_snapshot (
    cluster_run_id integer NOT NULL,
    day_anchor date,
    total_recent integer,
    total_baseline double precision,
    orphan_recent integer,
    orphan_baseline double precision,
    orphan_lift double precision,
    orphan_zscore double precision,
    orphan_label text,
    corpus_lift double precision,
    snapshot_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dashboard_market_opportunities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dashboard_market_opportunities (
    cluster_run_id integer NOT NULL,
    viability_run_id integer NOT NULL,
    audience_classifier_version text CONSTRAINT dashboard_market_opportunit_audience_classifier_versio_not_null NOT NULL,
    cluster_id integer NOT NULL,
    audience_type text,
    is_eligible boolean,
    pareto_layer integer,
    balance_score double precision,
    member_count integer,
    pain_count_last_4w integer,
    label text,
    row_data jsonb NOT NULL,
    snapshot_at timestamp with time zone DEFAULT now() NOT NULL,
    burst_score double precision,
    burst_lift double precision,
    burst_zscore double precision,
    burst_recent_count integer,
    burst_baseline_count double precision,
    burst_corpus_lift double precision,
    burst_label text,
    window_pain_count integer,
    window_days_active integer,
    window_subreddit_count integer,
    window_filter jsonb,
    competition_run_id bigint,
    competition_status text,
    market_structure text,
    competition_confidence double precision,
    external_supply_gap double precision,
    competition_adjusted_s_axis double precision,
    competition_adjusted_balance_score double precision,
    competition_adjusted_pareto_layer integer,
    s_axis_source text DEFAULT 'missing'::text NOT NULL
);


--
-- Name: TABLE dashboard_market_opportunities; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.dashboard_market_opportunities IS 'Fast dashboard list snapshot refreshed from v_market_opportunities_ranked by the EC2 worker.';


--
-- Name: dashboard_market_refresh_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dashboard_market_refresh_state (
    cluster_run_id integer NOT NULL,
    viability_run_id integer NOT NULL,
    audience_classifier_version text CONSTRAINT dashboard_market_refresh_st_audience_classifier_versio_not_null NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    duration_seconds numeric(12,3),
    row_count integer,
    error text,
    last_success_at timestamp with time zone,
    last_success_row_count integer,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT dashboard_market_refresh_state_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'succeeded'::text, 'failed'::text, 'skipped'::text])))
);


--
-- Name: TABLE dashboard_market_refresh_state; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.dashboard_market_refresh_state IS 'Refresh status for dashboard_market_opportunities by run and classifier version.';


--
-- Name: dashboard_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dashboard_users (
    id integer NOT NULL,
    username text NOT NULL,
    password_hash text NOT NULL,
    is_admin boolean DEFAULT false NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    last_login_at timestamp with time zone
);


--
-- Name: dashboard_users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dashboard_users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dashboard_users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dashboard_users_id_seq OWNED BY public.dashboard_users.id;


--
-- Name: emerging_opportunities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.emerging_opportunities (
    id bigint NOT NULL,
    emerging_run_id bigint NOT NULL,
    cluster_run_id integer NOT NULL,
    label text NOT NULL,
    pain_count integer NOT NULL,
    author_count integer NOT NULL,
    subreddit_count integer NOT NULL,
    coherence double precision DEFAULT 0 NOT NULL,
    burst_score double precision DEFAULT 0 NOT NULL,
    source_window_start date,
    source_window_end date,
    sample_pain_ids integer[] DEFAULT '{}'::integer[] NOT NULL,
    sample_quotes jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: emerging_opportunities_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.emerging_opportunities_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: emerging_opportunities_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.emerging_opportunities_id_seq OWNED BY public.emerging_opportunities.id;


--
-- Name: emerging_opportunity_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.emerging_opportunity_members (
    emerging_opportunity_id bigint NOT NULL,
    pain_id integer NOT NULL,
    similarity double precision DEFAULT 0 NOT NULL
);


--
-- Name: emerging_opportunity_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.emerging_opportunity_runs (
    id bigint NOT NULL,
    cluster_run_id integer NOT NULL,
    lookback_days integer DEFAULT 7 NOT NULL,
    min_size integer DEFAULT 8 NOT NULL,
    eps double precision DEFAULT 0.18 NOT NULL,
    status text DEFAULT 'running'::text NOT NULL,
    candidate_count integer DEFAULT 0 NOT NULL,
    opportunity_count integer DEFAULT 0 NOT NULL,
    error_log text,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT emerging_opportunity_runs_status_check CHECK ((status = ANY (ARRAY['running'::text, 'succeeded'::text, 'failed'::text])))
);


--
-- Name: emerging_opportunity_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.emerging_opportunity_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: emerging_opportunity_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.emerging_opportunity_runs_id_seq OWNED BY public.emerging_opportunity_runs.id;


--
-- Name: hackernews_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hackernews_items (
    hn_id bigint NOT NULL,
    item_type text NOT NULL,
    by_author text,
    time_utc timestamp with time zone,
    title text,
    text text,
    url text,
    score integer,
    descendants integer,
    parent_hn_id bigint,
    root_hn_id bigint,
    depth integer DEFAULT 0 NOT NULL,
    fetched_from text DEFAULT ''::text NOT NULL,
    kids bigint[] DEFAULT '{}'::bigint[] NOT NULL,
    parts bigint[] DEFAULT '{}'::bigint[] NOT NULL,
    deleted boolean DEFAULT false NOT NULL,
    dead boolean DEFAULT false NOT NULL,
    raw_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: pain_cluster_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pain_cluster_members (
    pain_id integer NOT NULL,
    cluster_id integer NOT NULL
);


--
-- Name: pain_clusters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pain_clusters (
    id integer NOT NULL,
    label text,
    member_count integer NOT NULL,
    intensity double precision,
    prevalence double precision,
    coherence double precision,
    competitive_density double precision,
    opportunity_score double precision,
    centroid public.vector(384),
    created_at timestamp with time zone DEFAULT now(),
    run_id integer,
    velocity double precision,
    competition_density double precision,
    market_gap double precision,
    hhi double precision,
    cr4 double precision,
    weighted_rating double precision,
    complaint_ratio double precision,
    active_ratio double precision,
    total_installs bigint,
    golden_quadrant character varying(20),
    practicality_score double precision,
    practicality_reasoning text,
    practicality_tags text[],
    is_candidate boolean DEFAULT false NOT NULL,
    volume_4w integer,
    volume_prior_4w integer
);


--
-- Name: pain_clusters_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.pain_clusters_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: pain_clusters_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.pain_clusters_id_seq OWNED BY public.pain_clusters.id;


--
-- Name: pain_extraction_attempts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pain_extraction_attempts (
    signal_event_id bigint NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    last_error text,
    last_attempt_at timestamp with time zone DEFAULT now(),
    failed boolean DEFAULT false NOT NULL
);


--
-- Name: pain_objects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pain_objects (
    id integer NOT NULL,
    post_id integer,
    comment_id integer,
    subreddit_name character varying(100),
    pain_statement text NOT NULL,
    raw_quote text NOT NULL,
    mentioned_tools text[],
    is_workaround boolean DEFAULT false NOT NULL,
    source_author character varying(100),
    sentiment_score double precision,
    specificity double precision,
    community_score double precision,
    source_created timestamp with time zone,
    embedding public.vector(384),
    created_at timestamp with time zone DEFAULT now(),
    signal_event_id bigint,
    prompt_version character varying(32) DEFAULT 'v1'::character varying NOT NULL
);


--
-- Name: pain_objects_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.pain_objects_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: pain_objects_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.pain_objects_id_seq OWNED BY public.pain_objects.id;


--
-- Name: pain_volume_daily; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pain_volume_daily (
    cluster_run_id integer NOT NULL,
    cluster_id integer NOT NULL,
    day_start date NOT NULL,
    pain_count integer NOT NULL,
    share_global double precision,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: pain_volume_daily_corpus; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pain_volume_daily_corpus (
    cluster_run_id integer NOT NULL,
    day_start date NOT NULL,
    total_count integer NOT NULL,
    assigned_count integer NOT NULL,
    orphan_count integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: pain_volume_daily_subreddit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pain_volume_daily_subreddit (
    cluster_run_id integer NOT NULL,
    cluster_id integer NOT NULL,
    day_start date NOT NULL,
    subreddit_name text NOT NULL,
    pain_count integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: pain_volume_weekly; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pain_volume_weekly (
    cluster_run_id integer NOT NULL,
    cluster_id integer NOT NULL,
    week_start date NOT NULL,
    audience_type character varying(16) DEFAULT 'unknown'::character varying NOT NULL,
    function_tag character varying(64) DEFAULT 'unknown'::character varying NOT NULL,
    pain_count integer NOT NULL,
    share_global double precision,
    share_field double precision
);


--
-- Name: pipeline_job_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pipeline_job_events (
    id bigint NOT NULL,
    job_id bigint NOT NULL,
    stage_name text,
    event_type text DEFAULT 'log'::text NOT NULL,
    message text NOT NULL,
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: pipeline_job_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.pipeline_job_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: pipeline_job_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.pipeline_job_events_id_seq OWNED BY public.pipeline_job_events.id;


--
-- Name: pipeline_job_steps; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pipeline_job_steps (
    id bigint NOT NULL,
    job_id bigint NOT NULL,
    stage_name text NOT NULL,
    stage_order integer DEFAULT 0 NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    exit_code integer,
    error text,
    log_tail text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT pipeline_job_steps_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'succeeded'::text, 'failed'::text, 'skipped'::text, 'cancelled'::text])))
);


--
-- Name: pipeline_job_steps_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.pipeline_job_steps_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: pipeline_job_steps_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.pipeline_job_steps_id_seq OWNED BY public.pipeline_job_steps.id;


--
-- Name: pipeline_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pipeline_jobs (
    id bigint NOT NULL,
    job_type text NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    requested_by text,
    request_source text DEFAULT 'dashboard'::text NOT NULL,
    run_after timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    worker_id text,
    exit_code integer,
    error text,
    log_tail text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    cancel_requested_at timestamp with time zone,
    cancel_requested_by text,
    params jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT pipeline_jobs_job_type_check CHECK ((job_type = ANY (ARRAY['daily'::text, 'monthly'::text, 'extraction'::text]))),
    CONSTRAINT pipeline_jobs_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'cancel_requested'::text, 'cancelled'::text, 'succeeded'::text, 'failed'::text])))
);


--
-- Name: pipeline_jobs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.pipeline_jobs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: pipeline_jobs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.pipeline_jobs_id_seq OWNED BY public.pipeline_jobs.id;


--
-- Name: pipeline_worker_heartbeats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pipeline_worker_heartbeats (
    worker_id text NOT NULL,
    mode text DEFAULT 'ec2-worker'::text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    current_job_id bigint,
    version text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: posts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.posts (
    id integer NOT NULL,
    reddit_id character varying(20) NOT NULL,
    subreddit_id integer,
    title text NOT NULL,
    selftext text,
    author character varying(100),
    score integer DEFAULT 0,
    upvote_ratio double precision,
    num_comments integer DEFAULT 0,
    permalink text,
    url text,
    link_flair_text character varying(200),
    is_self boolean DEFAULT true,
    over_18 boolean DEFAULT false,
    created_utc timestamp without time zone,
    fetched_at timestamp without time zone DEFAULT now(),
    raw_json jsonb
);


--
-- Name: posts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.posts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: posts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.posts_id_seq OWNED BY public.posts.id;


--
-- Name: processed_posts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.processed_posts (
    post_id integer NOT NULL,
    processed_at timestamp without time zone DEFAULT now()
);


--
-- Name: processed_signal_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.processed_signal_events (
    signal_event_id bigint NOT NULL,
    source text NOT NULL,
    processed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: reddit_backfill_chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reddit_backfill_chunks (
    id bigint NOT NULL,
    job_id bigint NOT NULL,
    subreddit_name text NOT NULL,
    window_start timestamp with time zone NOT NULL,
    window_end timestamp with time zone NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    posts_count integer DEFAULT 0 NOT NULL,
    comments_count integer DEFAULT 0 NOT NULL,
    projected_count integer DEFAULT 0 NOT NULL,
    extracted_count integer DEFAULT 0 NOT NULL,
    assigned_count integer DEFAULT 0 NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT reddit_backfill_chunks_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'succeeded'::text, 'failed'::text, 'skipped'::text])))
);


--
-- Name: reddit_backfill_chunks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.reddit_backfill_chunks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: reddit_backfill_chunks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.reddit_backfill_chunks_id_seq OWNED BY public.reddit_backfill_chunks.id;


--
-- Name: reddit_backfill_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reddit_backfill_jobs (
    id bigint NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    requested_by text,
    subreddits text[] DEFAULT '{}'::text[] NOT NULL,
    months integer DEFAULT 18 NOT NULL,
    chunk_days integer DEFAULT 7 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    error text,
    total_chunks integer DEFAULT 0 NOT NULL,
    succeeded_chunks integer DEFAULT 0 NOT NULL,
    failed_chunks integer DEFAULT 0 NOT NULL,
    CONSTRAINT reddit_backfill_jobs_chunk_days_check CHECK ((chunk_days > 0)),
    CONSTRAINT reddit_backfill_jobs_months_check CHECK ((months >= 0)),
    CONSTRAINT reddit_backfill_jobs_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'succeeded'::text, 'failed'::text, 'cancelled'::text])))
);


--
-- Name: reddit_backfill_jobs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.reddit_backfill_jobs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: reddit_backfill_jobs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.reddit_backfill_jobs_id_seq OWNED BY public.reddit_backfill_jobs.id;


--
-- Name: reddit_subreddit_sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reddit_subreddit_sources (
    subreddit_name text NOT NULL,
    status text DEFAULT 'candidate'::text NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    daily_lookback_days integer DEFAULT 7 NOT NULL,
    backfill_months integer DEFAULT 18 NOT NULL,
    backfill_enabled boolean DEFAULT true NOT NULL,
    tags text[] DEFAULT '{}'::text[] NOT NULL,
    notes text DEFAULT ''::text NOT NULL,
    last_fetch_at timestamp with time zone,
    last_backfill_at timestamp with time zone,
    last_evaluated_at timestamp with time zone,
    metrics jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT reddit_subreddit_sources_backfill_months_check CHECK ((backfill_months >= 0)),
    CONSTRAINT reddit_subreddit_sources_daily_lookback_days_check CHECK ((daily_lookback_days >= 0)),
    CONSTRAINT reddit_subreddit_sources_status_check CHECK ((status = ANY (ARRAY['candidate'::text, 'active'::text, 'paused'::text, 'rejected'::text])))
);


--
-- Name: report_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.report_jobs (
    id bigint NOT NULL,
    cluster_id bigint NOT NULL,
    analysis_run_id bigint,
    status text DEFAULT 'queued'::text NOT NULL,
    current_stage smallint DEFAULT 0 NOT NULL,
    requested_by text,
    cost_usd numeric(10,4) DEFAULT 0,
    error_log text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    viability_run_id integer,
    audience_classifier_version text
);


--
-- Name: report_jobs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.report_jobs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: report_jobs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.report_jobs_id_seq OWNED BY public.report_jobs.id;


--
-- Name: report_stage_outputs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.report_stage_outputs (
    id bigint NOT NULL,
    job_id bigint NOT NULL,
    stage_name text NOT NULL,
    model_used text,
    raw_output jsonb NOT NULL,
    tokens_in integer,
    tokens_out integer,
    cost_usd numeric(10,4) DEFAULT 0,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: report_stage_outputs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.report_stage_outputs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: report_stage_outputs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.report_stage_outputs_id_seq OWNED BY public.report_stage_outputs.id;


--
-- Name: reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reports (
    id bigint NOT NULL,
    job_id bigint NOT NULL,
    cluster_id bigint NOT NULL,
    verdict text,
    verdict_reason text,
    product_logic text,
    competitors jsonb,
    market_structure text,
    report_md text NOT NULL,
    why_no_direct text,
    opportunity_or_trap text,
    condition_difficulty text,
    condition_summary text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT reports_verdict_check CHECK ((verdict = ANY (ARRAY['go'::text, 'no_go'::text, 'conditional'::text])))
);


--
-- Name: reports_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.reports_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: reports_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.reports_id_seq OWNED BY public.reports.id;


--
-- Name: signal_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.signal_events (
    id bigint NOT NULL,
    source character varying(32) NOT NULL,
    source_table character varying(64) NOT NULL,
    source_row_id character varying(64) NOT NULL,
    event_type character varying(32) NOT NULL,
    event_time timestamp with time zone NOT NULL,
    surface character varying(200),
    author character varying(200),
    parent_event_id bigint,
    title text,
    text text,
    engagement_score double precision,
    raw_meta jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: signal_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.signal_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: signal_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.signal_events_id_seq OWNED BY public.signal_events.id;


--
-- Name: social_source_venues; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.social_source_venues (
    id bigint NOT NULL,
    source text NOT NULL,
    venue_type text NOT NULL,
    name text NOT NULL,
    url_or_id text NOT NULL,
    status text DEFAULT 'candidate'::text NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    daily_lookback_days integer DEFAULT 7 NOT NULL,
    fetch_limit integer DEFAULT 20 NOT NULL,
    sort_mode text DEFAULT ''::text NOT NULL,
    notes text DEFAULT ''::text NOT NULL,
    metrics jsonb DEFAULT '{}'::jsonb NOT NULL,
    last_fetch_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT social_source_venues_daily_lookback_days_check CHECK (((daily_lookback_days >= 0) AND (daily_lookback_days <= 3650))),
    CONSTRAINT social_source_venues_fetch_limit_check CHECK (((fetch_limit >= 0) AND (fetch_limit <= 100000))),
    CONSTRAINT social_source_venues_priority_check CHECK (((priority >= 0) AND (priority <= 10000))),
    CONSTRAINT social_source_venues_source_check CHECK ((source = ANY (ARRAY['facebook'::text, 'twitter'::text]))),
    CONSTRAINT social_source_venues_status_check CHECK ((status = ANY (ARRAY['candidate'::text, 'active'::text, 'paused'::text])))
);


--
-- Name: social_source_venues_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.social_source_venues_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: social_source_venues_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.social_source_venues_id_seq OWNED BY public.social_source_venues.id;


--
-- Name: socialcrawl_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.socialcrawl_items (
    id bigint NOT NULL,
    source character varying(32) NOT NULL,
    item_kind character varying(32) NOT NULL,
    source_item_id character varying(56) NOT NULL,
    raw_source_item_id text,
    parent_source_item_id character varying(56),
    root_source_item_id character varying(56),
    event_time timestamp with time zone NOT NULL,
    surface text,
    author text,
    title text,
    text text,
    url text,
    engagement_score double precision,
    query text DEFAULT ''::text NOT NULL,
    provider text DEFAULT 'socialcrawl'::text NOT NULL,
    provider_endpoint text DEFAULT ''::text NOT NULL,
    raw_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: socialcrawl_items_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.socialcrawl_items_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: socialcrawl_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.socialcrawl_items_id_seq OWNED BY public.socialcrawl_items.id;


--
-- Name: source_backfill_chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.source_backfill_chunks (
    id bigint NOT NULL,
    job_id bigint NOT NULL,
    source text NOT NULL,
    target_type text DEFAULT 'source'::text NOT NULL,
    target_id text DEFAULT ''::text NOT NULL,
    target_label text DEFAULT ''::text NOT NULL,
    query text DEFAULT ''::text NOT NULL,
    window_start timestamp with time zone NOT NULL,
    window_end timestamp with time zone NOT NULL,
    coverage_mode text DEFAULT 'best_effort_window'::text NOT NULL,
    coverage_note text DEFAULT ''::text NOT NULL,
    mode text DEFAULT 'fetch'::text NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    rows_fetched integer DEFAULT 0 NOT NULL,
    rows_projected integer DEFAULT 0 NOT NULL,
    rows_extracted integer DEFAULT 0 NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    error text,
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT source_backfill_chunks_check CHECK ((window_end > window_start)),
    CONSTRAINT source_backfill_chunks_coverage_mode_check CHECK ((coverage_mode = ANY (ARRAY['full_window'::text, 'best_effort_window'::text, 'recent_only'::text, 'unsupported'::text]))),
    CONSTRAINT source_backfill_chunks_mode_check CHECK ((mode = ANY (ARRAY['fetch'::text, 'project'::text, 'fetch-project'::text, 'extract'::text]))),
    CONSTRAINT source_backfill_chunks_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'succeeded'::text, 'failed'::text, 'skipped'::text, 'empty'::text, 'cancelled'::text])))
);


--
-- Name: source_backfill_chunks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.source_backfill_chunks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: source_backfill_chunks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.source_backfill_chunks_id_seq OWNED BY public.source_backfill_chunks.id;


--
-- Name: source_backfill_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.source_backfill_jobs (
    id bigint NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    sources text[] DEFAULT '{}'::text[] NOT NULL,
    scope text DEFAULT 'active'::text NOT NULL,
    mode text DEFAULT 'fetch'::text NOT NULL,
    date_from timestamp with time zone NOT NULL,
    date_to timestamp with time zone NOT NULL,
    chunk_days integer DEFAULT 7 NOT NULL,
    requested_by text,
    params jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    error text,
    total_chunks integer DEFAULT 0 NOT NULL,
    queued_chunks integer DEFAULT 0 NOT NULL,
    running_chunks integer DEFAULT 0 NOT NULL,
    succeeded_chunks integer DEFAULT 0 NOT NULL,
    failed_chunks integer DEFAULT 0 NOT NULL,
    skipped_chunks integer DEFAULT 0 NOT NULL,
    empty_chunks integer DEFAULT 0 NOT NULL,
    rows_fetched integer DEFAULT 0 NOT NULL,
    rows_projected integer DEFAULT 0 NOT NULL,
    rows_extracted integer DEFAULT 0 NOT NULL,
    cancelled_chunks integer DEFAULT 0 NOT NULL,
    cancel_requested_at timestamp with time zone,
    cancel_requested_by text,
    CONSTRAINT source_backfill_jobs_check CHECK ((date_to > date_from)),
    CONSTRAINT source_backfill_jobs_chunk_days_check CHECK (((chunk_days > 0) AND (chunk_days <= 31))),
    CONSTRAINT source_backfill_jobs_mode_check CHECK ((mode = ANY (ARRAY['fetch'::text, 'project'::text, 'fetch-project'::text, 'extract'::text]))),
    CONSTRAINT source_backfill_jobs_scope_check CHECK ((scope = ANY (ARRAY['active'::text, 'candidate'::text, 'all'::text, 'selected'::text, 'query'::text]))),
    CONSTRAINT source_backfill_jobs_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'succeeded'::text, 'failed'::text, 'cancelled'::text])))
);


--
-- Name: source_backfill_jobs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.source_backfill_jobs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: source_backfill_jobs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.source_backfill_jobs_id_seq OWNED BY public.source_backfill_jobs.id;


--
-- Name: source_capabilities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.source_capabilities (
    source character varying(32) NOT NULL,
    produces_pain boolean DEFAULT false NOT NULL,
    produces_competitor boolean DEFAULT false NOT NULL,
    produces_traction boolean DEFAULT false NOT NULL,
    produces_temporal boolean DEFAULT false NOT NULL,
    notes text,
    registered_at timestamp with time zone DEFAULT now()
);


--
-- Name: source_runtime_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.source_runtime_config (
    source text NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    fetch_enabled boolean DEFAULT false NOT NULL,
    project_enabled boolean DEFAULT false NOT NULL,
    extract_enabled boolean DEFAULT false NOT NULL,
    query text DEFAULT ''::text NOT NULL,
    lookback_days integer DEFAULT 7 NOT NULL,
    fetch_limit integer DEFAULT 10000 NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    notes text DEFAULT ''::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT source_runtime_config_fetch_limit_check CHECK ((fetch_limit >= 0)),
    CONSTRAINT source_runtime_config_lookback_days_check CHECK ((lookback_days >= 0))
);


--
-- Name: subreddits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subreddits (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    display_name character varying(200),
    subscribers integer,
    description text,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    audience_prior character varying(16) DEFAULT 'unknown'::character varying NOT NULL,
    is_watchlist boolean DEFAULT true NOT NULL,
    last_fetched_at timestamp with time zone,
    last_seen_post_utc timestamp with time zone,
    CONSTRAINT subreddits_audience_prior_check CHECK (((audience_prior)::text = ANY (ARRAY[('B2B'::character varying)::text, ('B2C'::character varying)::text, ('mixed'::character varying)::text, ('unknown'::character varying)::text])))
);


--
-- Name: subreddits_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.subreddits_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: subreddits_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.subreddits_id_seq OWNED BY public.subreddits.id;


--
-- Name: system_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.system_settings (
    key text NOT NULL,
    value text DEFAULT ''::text NOT NULL,
    is_secret boolean DEFAULT false NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by text
);


--
-- Name: v_cluster_velocity; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_cluster_velocity AS
 WITH run_anchor AS (
         SELECT analysis_runs.id AS cluster_run_id,
                CASE
                    WHEN (analysis_runs.window_end IS NULL) THEN (date_trunc('week'::text, (CURRENT_DATE)::timestamp with time zone))::date
                    WHEN (((analysis_runs.status)::text = 'live'::text) AND ((date_trunc('week'::text, analysis_runs.window_end))::date >= (date_trunc('week'::text, (CURRENT_DATE)::timestamp with time zone))::date)) THEN (date_trunc('week'::text, (CURRENT_DATE)::timestamp with time zone))::date
                    ELSE (date_trunc('week'::text, analysis_runs.window_end))::date
                END AS anchor_week
           FROM public.analysis_runs
        ), per_cluster AS (
         SELECT pvw.cluster_run_id,
            pvw.cluster_id,
            sum(pvw.pain_count) FILTER (WHERE ((pvw.week_start >= (ra.anchor_week - '28 days'::interval)) AND (pvw.week_start < ra.anchor_week))) AS last_4w,
            sum(pvw.pain_count) FILTER (WHERE ((pvw.week_start >= (ra.anchor_week - '56 days'::interval)) AND (pvw.week_start < (ra.anchor_week - '28 days'::interval)))) AS prior_4w,
            regr_slope((pvw.pain_count)::double precision, ((EXTRACT(epoch FROM pvw.week_start) / 604800.0))::double precision) AS slope_per_week,
            count(*) AS weeks_with_data,
            sum(pvw.pain_count) AS total_pain
           FROM (public.pain_volume_weekly pvw
             JOIN run_anchor ra ON ((ra.cluster_run_id = pvw.cluster_run_id)))
          GROUP BY pvw.cluster_run_id, pvw.cluster_id
        ), corpus_ratio AS (
         SELECT pvw.cluster_run_id,
            sum(pvw.pain_count) FILTER (WHERE ((pvw.week_start >= (ra.anchor_week - '28 days'::interval)) AND (pvw.week_start < ra.anchor_week))) AS corpus_last_4w,
            sum(pvw.pain_count) FILTER (WHERE ((pvw.week_start >= (ra.anchor_week - '56 days'::interval)) AND (pvw.week_start < (ra.anchor_week - '28 days'::interval)))) AS corpus_prior_4w
           FROM (public.pain_volume_weekly pvw
             JOIN run_anchor ra ON ((ra.cluster_run_id = pvw.cluster_run_id)))
          GROUP BY pvw.cluster_run_id
        )
 SELECT pc.cluster_run_id,
    pc.cluster_id,
    pc.last_4w AS pain_count_last_4w,
    pc.prior_4w AS pain_count_prior_4w,
    pc.weeks_with_data,
    pc.total_pain,
    round((pc.slope_per_week)::numeric, 3) AS slope_per_week,
        CASE
            WHEN ((COALESCE(pc.prior_4w, (0)::bigint) < 3) OR (COALESCE(pc.last_4w, (0)::bigint) < 3)) THEN NULL::numeric
            ELSE round((((pc.last_4w)::double precision / (pc.prior_4w)::double precision))::numeric, 3)
        END AS velocity_4w_ratio,
        CASE
            WHEN ((COALESCE(pc.prior_4w, (0)::bigint) < 3) OR (COALESCE(pc.last_4w, (0)::bigint) < 3)) THEN NULL::numeric
            WHEN ((COALESCE(cr.corpus_prior_4w, (0)::bigint) = 0) OR (cr.corpus_last_4w IS NULL)) THEN NULL::numeric
            ELSE round(((((pc.last_4w)::double precision / (pc.prior_4w)::double precision) / NULLIF(((cr.corpus_last_4w)::double precision / (NULLIF(cr.corpus_prior_4w, 0))::double precision), (0)::double precision)))::numeric, 3)
        END AS velocity_4w_ratio_normalized
   FROM (per_cluster pc
     LEFT JOIN corpus_ratio cr ON ((cr.cluster_run_id = pc.cluster_run_id)));


--
-- Name: VIEW v_cluster_velocity; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_cluster_velocity IS 'Per-cluster temporal signals anchored by the run data window. A live run only
   uses the current week while its window_end reaches the current week; stale live
   snapshots use their own window_end so Recent trend does not decay to NULL.';


--
-- Name: viability_scores; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.viability_scores (
    viability_run_id integer NOT NULL,
    cluster_id integer NOT NULL,
    is_real_pain boolean NOT NULL,
    is_software_solvable boolean NOT NULL,
    q_specificity smallint,
    q_noise_flag character varying(32),
    v_buildability smallint,
    v_icp_clarity smallint,
    v_moat_potential smallint,
    v_rationale text,
    sample_pain_ids integer[],
    llm_input_tokens integer,
    llm_output_tokens integer,
    raw_response jsonb,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT viability_scores_q_specificity_check CHECK (((q_specificity >= 1) AND (q_specificity <= 5))),
    CONSTRAINT viability_scores_v_buildability_check CHECK (((v_buildability >= 1) AND (v_buildability <= 5))),
    CONSTRAINT viability_scores_v_icp_clarity_check CHECK (((v_icp_clarity >= 1) AND (v_icp_clarity <= 5))),
    CONSTRAINT viability_scores_v_moat_potential_check CHECK (((v_moat_potential >= 1) AND (v_moat_potential <= 5)))
);


--
-- Name: v_market_opportunities_pregate; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_market_opportunities_pregate AS
 WITH base AS (
         SELECT pc.id AS cluster_id,
            pc.run_id AS cluster_run_id,
            pc.label,
            pc.member_count,
            pc.coherence,
            pc.is_candidate,
            ca.classifier_version AS audience_classifier_version,
            ca.audience_type,
            ca.confidence AS audience_confidence,
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
           FROM ((((public.pain_clusters pc
             JOIN public.cluster_audience ca ON (((ca.cluster_id = pc.id) AND ((ca.classifier_version)::text = COALESCE(NULLIF(current_setting('llm_analyse.audience_classifier_version'::text, true), ''::text), 'v1'::text)))))
             JOIN public.cluster_quality_metrics cqm ON (((cqm.cluster_id = pc.id) AND (cqm.cluster_run_id = pc.run_id))))
             LEFT JOIN public.viability_scores vs ON (((vs.cluster_id = pc.id) AND (vs.viability_run_id = (NULLIF(current_setting('llm_analyse.viability_run_id'::text, true), ''::text))::integer))))
             LEFT JOIN public.v_cluster_velocity vcv ON (((vcv.cluster_id = pc.id) AND (vcv.cluster_run_id = pc.run_id))))
          WHERE ((pc.run_id = (NULLIF(current_setting('llm_analyse.cluster_run_id'::text, true), ''::text))::integer) AND pc.is_candidate AND (vs.is_real_pain IS TRUE) AND (vs.is_software_solvable IS TRUE))
        ), cluster_tool_density AS (
         SELECT pcm.cluster_id,
            ((count(*) FILTER (WHERE (array_length(po.mentioned_tools, 1) > 0)))::double precision / (NULLIF(count(*), 0))::double precision) AS tool_density,
            count(*) FILTER (WHERE (array_length(po.mentioned_tools, 1) > 0)) AS members_with_tools
           FROM ((public.pain_cluster_members pcm
             JOIN public.pain_objects po ON ((po.id = pcm.pain_id)))
             JOIN public.pain_clusters pc ON ((pc.id = pcm.cluster_id)))
          WHERE (pc.run_id = (NULLIF(current_setting('llm_analyse.cluster_run_id'::text, true), ''::text))::integer)
          GROUP BY pcm.cluster_id
        ), joined AS (
         SELECT b_1.cluster_id,
            b_1.cluster_run_id,
            b_1.label,
            b_1.member_count,
            b_1.coherence,
            b_1.is_candidate,
            b_1.audience_classifier_version,
            b_1.audience_type,
            b_1.audience_confidence,
            b_1.icp_hint,
            b_1.thread_diversity,
            b_1.author_diversity,
            b_1.subreddit_count,
            b_1.specificity_mean,
            b_1.workaround_ratio,
            b_1.separation,
            b_1.temporal_spread_days,
            b_1.source_diversity,
            b_1.viability_run_id,
            b_1.is_real_pain,
            b_1.is_software_solvable,
            b_1.q_specificity,
            b_1.q_noise_flag,
            b_1.v_buildability,
            b_1.v_icp_clarity,
            b_1.v_moat_potential,
            b_1.v_rationale,
            b_1.pain_count_last_4w,
            b_1.pain_count_prior_4w,
            b_1.weeks_with_data,
            b_1.total_pain,
            b_1.slope_per_week,
            b_1.velocity_4w_ratio,
            b_1.velocity_4w_ratio_normalized,
            COALESCE(td.tool_density, (0)::double precision) AS tool_density,
            COALESCE(td.members_with_tools, (0)::bigint) AS members_with_tools
           FROM (base b_1
             LEFT JOIN cluster_tool_density td ON ((td.cluster_id = b_1.cluster_id)))
        ), pct AS (
         SELECT j.cluster_id,
            j.cluster_run_id,
            j.label,
            j.member_count,
            j.coherence,
            j.is_candidate,
            j.audience_classifier_version,
            j.audience_type,
            j.audience_confidence,
            j.icp_hint,
            j.thread_diversity,
            j.author_diversity,
            j.subreddit_count,
            j.specificity_mean,
            j.workaround_ratio,
            j.separation,
            j.temporal_spread_days,
            j.source_diversity,
            j.viability_run_id,
            j.is_real_pain,
            j.is_software_solvable,
            j.q_specificity,
            j.q_noise_flag,
            j.v_buildability,
            j.v_icp_clarity,
            j.v_moat_potential,
            j.v_rationale,
            j.pain_count_last_4w,
            j.pain_count_prior_4w,
            j.weeks_with_data,
            j.total_pain,
            j.slope_per_week,
            j.velocity_4w_ratio,
            j.velocity_4w_ratio_normalized,
            j.tool_density,
            j.members_with_tools,
            percent_rank() OVER (ORDER BY j.thread_diversity NULLS FIRST) AS p_thread,
            percent_rank() OVER (ORDER BY j.author_diversity NULLS FIRST) AS p_author,
            percent_rank() OVER (ORDER BY j.specificity_mean NULLS FIRST) AS p_spec,
            percent_rank() OVER (ORDER BY j.separation NULLS FIRST) AS p_sep,
            percent_rank() OVER (ORDER BY j.temporal_spread_days NULLS FIRST) AS p_temp,
            percent_rank() OVER (ORDER BY j.slope_per_week NULLS FIRST) AS p_slope,
            percent_rank() OVER (ORDER BY j.pain_count_prior_4w NULLS FIRST) AS p_prior,
            percent_rank() OVER (ORDER BY j.workaround_ratio NULLS FIRST) AS p_workaround,
            ((1.0)::double precision - percent_rank() OVER (ORDER BY j.tool_density NULLS FIRST)) AS p_tool_inv
           FROM joined j
        ), axes AS (
         SELECT p.cluster_id,
            p.cluster_run_id,
            p.label,
            p.member_count,
            p.coherence,
            p.is_candidate,
            p.audience_classifier_version,
            p.audience_type,
            p.audience_confidence,
            p.icp_hint,
            p.thread_diversity,
            p.author_diversity,
            p.subreddit_count,
            p.specificity_mean,
            p.workaround_ratio,
            p.separation,
            p.temporal_spread_days,
            p.source_diversity,
            p.viability_run_id,
            p.is_real_pain,
            p.is_software_solvable,
            p.q_specificity,
            p.q_noise_flag,
            p.v_buildability,
            p.v_icp_clarity,
            p.v_moat_potential,
            p.v_rationale,
            p.pain_count_last_4w,
            p.pain_count_prior_4w,
            p.weeks_with_data,
            p.total_pain,
            p.slope_per_week,
            p.velocity_4w_ratio,
            p.velocity_4w_ratio_normalized,
            p.tool_density,
            p.members_with_tools,
            p.p_thread,
            p.p_author,
            p.p_spec,
            p.p_sep,
            p.p_temp,
            p.p_slope,
            p.p_prior,
            p.p_workaround,
            p.p_tool_inv,
            ((((((0.25)::double precision * p.p_thread) + ((0.20)::double precision * p.p_author)) + ((0.25)::double precision * p.p_spec)) + ((0.15)::double precision * p.p_sep)) + ((0.15)::double precision * p.p_temp)) AS q_axis,
            (((0.6)::double precision * p.p_slope) + ((0.4)::double precision * p.p_prior)) AS m_axis,
            (((0.5)::double precision * p.p_workaround) + ((0.5)::double precision * p.p_tool_inv)) AS s_axis,
            (p.members_with_tools < 20) AS low_confidence_s
           FROM pct p
        ), balanced AS (
         SELECT a.cluster_id,
            a.cluster_run_id,
            a.label,
            a.member_count,
            a.coherence,
            a.is_candidate,
            a.audience_classifier_version,
            a.audience_type,
            a.audience_confidence,
            a.icp_hint,
            a.thread_diversity,
            a.author_diversity,
            a.subreddit_count,
            a.specificity_mean,
            a.workaround_ratio,
            a.separation,
            a.temporal_spread_days,
            a.source_diversity,
            a.viability_run_id,
            a.is_real_pain,
            a.is_software_solvable,
            a.q_specificity,
            a.q_noise_flag,
            a.v_buildability,
            a.v_icp_clarity,
            a.v_moat_potential,
            a.v_rationale,
            a.pain_count_last_4w,
            a.pain_count_prior_4w,
            a.weeks_with_data,
            a.total_pain,
            a.slope_per_week,
            a.velocity_4w_ratio,
            a.velocity_4w_ratio_normalized,
            a.tool_density,
            a.members_with_tools,
            a.p_thread,
            a.p_author,
            a.p_spec,
            a.p_sep,
            a.p_temp,
            a.p_slope,
            a.p_prior,
            a.p_workaround,
            a.p_tool_inv,
            a.q_axis,
            a.m_axis,
            a.s_axis,
            a.low_confidence_s,
                CASE
                    WHEN ((a.q_axis < (0.05)::double precision) OR (a.m_axis < (0.05)::double precision) OR (a.s_axis < (0.05)::double precision)) THEN NULL::double precision
                    ELSE ((3.0)::double precision / ((((1.0)::double precision / a.q_axis) + ((1.0)::double precision / a.m_axis)) + ((1.0)::double precision / a.s_axis)))
                END AS balance_score
           FROM axes a
        ), aud_p25 AS (
         SELECT balanced.audience_type,
            percentile_cont((0.25)::double precision) WITHIN GROUP (ORDER BY balanced.q_axis) AS q_p25,
            percentile_cont((0.25)::double precision) WITHIN GROUP (ORDER BY balanced.s_axis) AS s_p25
           FROM balanced
          GROUP BY balanced.audience_type
        )
 SELECT b.cluster_id,
    b.cluster_run_id,
    b.label,
    b.member_count,
    b.coherence,
    b.is_candidate,
    b.audience_classifier_version,
    b.audience_type,
    b.audience_confidence,
    b.icp_hint,
    b.thread_diversity,
    b.author_diversity,
    b.subreddit_count,
    b.specificity_mean,
    b.workaround_ratio,
    b.separation,
    b.temporal_spread_days,
    b.source_diversity,
    b.viability_run_id,
    b.is_real_pain,
    b.is_software_solvable,
    b.q_specificity,
    b.q_noise_flag,
    b.v_buildability,
    b.v_icp_clarity,
    b.v_moat_potential,
    b.v_rationale,
    b.pain_count_last_4w,
    b.pain_count_prior_4w,
    b.weeks_with_data,
    b.total_pain,
    b.slope_per_week,
    b.velocity_4w_ratio,
    b.velocity_4w_ratio_normalized,
    b.tool_density,
    b.members_with_tools,
    b.p_thread,
    b.p_author,
    b.p_spec,
    b.p_sep,
    b.p_temp,
    b.p_slope,
    b.p_prior,
    b.p_workaround,
    b.p_tool_inv,
    b.q_axis,
    b.m_axis,
    b.s_axis,
    b.low_confidence_s,
    b.balance_score,
    ((b.q_axis >= p25.q_p25) AND (b.s_axis >= p25.s_p25)) AS is_eligible
   FROM (balanced b
     JOIN aud_p25 p25 ON ((NOT ((p25.audience_type)::text IS DISTINCT FROM (b.audience_type)::text))));


--
-- Name: VIEW v_market_opportunities_pregate; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_market_opportunities_pregate IS 'Internal Path C surface: gated v_market_opportunities columns + tool_density,
   within-run axis-input percentiles, Q/M/S axes, ε-floored harmonic balance_score,
   low_confidence_s, and within-audience is_eligible (p25 on Q and S). Feeds
   fn_compute_pareto_layers and v_market_opportunities_ranked.';


--
-- Name: v_market_opportunities_ranked; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_market_opportunities_ranked AS
 WITH pg AS MATERIALIZED (
         SELECT v_market_opportunities_pregate.cluster_id,
            v_market_opportunities_pregate.cluster_run_id,
            v_market_opportunities_pregate.label,
            v_market_opportunities_pregate.member_count,
            v_market_opportunities_pregate.coherence,
            v_market_opportunities_pregate.is_candidate,
            v_market_opportunities_pregate.audience_classifier_version,
            v_market_opportunities_pregate.audience_type,
            v_market_opportunities_pregate.audience_confidence,
            v_market_opportunities_pregate.icp_hint,
            v_market_opportunities_pregate.thread_diversity,
            v_market_opportunities_pregate.author_diversity,
            v_market_opportunities_pregate.subreddit_count,
            v_market_opportunities_pregate.specificity_mean,
            v_market_opportunities_pregate.workaround_ratio,
            v_market_opportunities_pregate.separation,
            v_market_opportunities_pregate.temporal_spread_days,
            v_market_opportunities_pregate.source_diversity,
            v_market_opportunities_pregate.viability_run_id,
            v_market_opportunities_pregate.is_real_pain,
            v_market_opportunities_pregate.is_software_solvable,
            v_market_opportunities_pregate.q_specificity,
            v_market_opportunities_pregate.q_noise_flag,
            v_market_opportunities_pregate.v_buildability,
            v_market_opportunities_pregate.v_icp_clarity,
            v_market_opportunities_pregate.v_moat_potential,
            v_market_opportunities_pregate.v_rationale,
            v_market_opportunities_pregate.pain_count_last_4w,
            v_market_opportunities_pregate.pain_count_prior_4w,
            v_market_opportunities_pregate.weeks_with_data,
            v_market_opportunities_pregate.total_pain,
            v_market_opportunities_pregate.slope_per_week,
            v_market_opportunities_pregate.velocity_4w_ratio,
            v_market_opportunities_pregate.velocity_4w_ratio_normalized,
            v_market_opportunities_pregate.tool_density,
            v_market_opportunities_pregate.members_with_tools,
            v_market_opportunities_pregate.p_thread,
            v_market_opportunities_pregate.p_author,
            v_market_opportunities_pregate.p_spec,
            v_market_opportunities_pregate.p_sep,
            v_market_opportunities_pregate.p_temp,
            v_market_opportunities_pregate.p_slope,
            v_market_opportunities_pregate.p_prior,
            v_market_opportunities_pregate.p_workaround,
            v_market_opportunities_pregate.p_tool_inv,
            v_market_opportunities_pregate.q_axis,
            v_market_opportunities_pregate.m_axis,
            v_market_opportunities_pregate.s_axis,
            v_market_opportunities_pregate.low_confidence_s,
            v_market_opportunities_pregate.balance_score,
            v_market_opportunities_pregate.is_eligible
           FROM public.v_market_opportunities_pregate
        ), layers AS MATERIALIZED (
         SELECT f.cluster_id,
            f.pareto_layer
           FROM (( SELECT DISTINCT pg_1.audience_type
                   FROM pg pg_1
                  WHERE (pg_1.audience_type IS NOT NULL)) a
             CROSS JOIN LATERAL public.fn_compute_pareto_layers((NULLIF(current_setting('llm_analyse.cluster_run_id'::text, true), ''::text))::integer, (a.audience_type)::text) f(cluster_id, pareto_layer))
        )
 SELECT pg.cluster_id,
    pg.cluster_run_id,
    pg.label,
    pg.member_count,
    pg.coherence,
    pg.is_candidate,
    pg.audience_classifier_version,
    pg.audience_type,
    pg.audience_confidence,
    pg.icp_hint,
    pg.thread_diversity,
    pg.author_diversity,
    pg.subreddit_count,
    pg.specificity_mean,
    pg.workaround_ratio,
    pg.separation,
    pg.temporal_spread_days,
    pg.source_diversity,
    pg.viability_run_id,
    pg.is_real_pain,
    pg.is_software_solvable,
    pg.q_specificity,
    pg.q_noise_flag,
    pg.v_buildability,
    pg.v_icp_clarity,
    pg.v_moat_potential,
    pg.v_rationale,
    pg.pain_count_last_4w,
    pg.pain_count_prior_4w,
    pg.weeks_with_data,
    pg.total_pain,
    pg.slope_per_week,
    pg.velocity_4w_ratio,
    pg.velocity_4w_ratio_normalized,
    pg.tool_density,
    pg.members_with_tools,
    pg.p_thread,
    pg.p_author,
    pg.p_spec,
    pg.p_sep,
    pg.p_temp,
    pg.p_slope,
    pg.p_prior,
    pg.p_workaround,
    pg.p_tool_inv,
    pg.q_axis,
    pg.m_axis,
    pg.s_axis,
    pg.low_confidence_s,
    pg.balance_score,
    pg.is_eligible,
    l.pareto_layer
   FROM (pg
     LEFT JOIN layers l ON ((l.cluster_id = pg.cluster_id)));


--
-- Name: VIEW v_market_opportunities_ranked; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_market_opportunities_ranked IS 'Path C ranking surface for dashboard v2. v_market_opportunities_pregate plus
   pareto_layer (1 = optimal frontier, NULL = ineligible / balance-NULL / beyond
   layer 5). Default API sort: pareto_layer ASC NULLS LAST, balance_score DESC
   NULLS LAST. Same three GUCs as v_market_opportunities. LD-#36 / #37 / #38.';


--
-- Name: v_market_opportunities; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_market_opportunities AS
 SELECT cluster_id,
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
   FROM public.v_market_opportunities_ranked;


--
-- Name: VIEW v_market_opportunities; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_market_opportunities IS 'Backward-compat shim (LD-#31): legacy 34-column projection of
   v_market_opportunities_ranked. New code reads v_market_opportunities_ranked.';


--
-- Name: v_market_opportunities_competition_pregate; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_market_opportunities_competition_pregate AS
 WITH active_runs AS (
         SELECT DISTINCT ON (competition_runs.cluster_run_id, competition_runs.viability_run_id, competition_runs.audience_classifier_version, competition_runs.audience_type) competition_runs.id,
            competition_runs.cluster_run_id,
            competition_runs.viability_run_id,
            competition_runs.audience_classifier_version,
            competition_runs.audience_type,
            competition_runs.scope,
            competition_runs.limit_count,
            competition_runs.source_policy,
            competition_runs.status,
            competition_runs.is_active,
            competition_runs.requested_by,
            competition_runs.error_log,
            competition_runs.started_at,
            competition_runs.finished_at,
            competition_runs.created_at,
            competition_runs.updated_at
           FROM public.competition_runs
          WHERE ((competition_runs.status = 'succeeded'::text) AND (competition_runs.is_active IS TRUE))
          ORDER BY competition_runs.cluster_run_id, competition_runs.viability_run_id, competition_runs.audience_classifier_version, competition_runs.audience_type, competition_runs.id DESC
        ), joined AS (
         SELECT ranked.cluster_id,
            ranked.cluster_run_id,
            ranked.label,
            ranked.member_count,
            ranked.coherence,
            ranked.is_candidate,
            ranked.audience_classifier_version,
            ranked.audience_type,
            ranked.audience_confidence,
            ranked.icp_hint,
            ranked.thread_diversity,
            ranked.author_diversity,
            ranked.subreddit_count,
            ranked.specificity_mean,
            ranked.workaround_ratio,
            ranked.separation,
            ranked.temporal_spread_days,
            ranked.source_diversity,
            ranked.viability_run_id,
            ranked.is_real_pain,
            ranked.is_software_solvable,
            ranked.q_specificity,
            ranked.q_noise_flag,
            ranked.v_buildability,
            ranked.v_icp_clarity,
            ranked.v_moat_potential,
            ranked.v_rationale,
            ranked.pain_count_last_4w,
            ranked.pain_count_prior_4w,
            ranked.weeks_with_data,
            ranked.total_pain,
            ranked.slope_per_week,
            ranked.velocity_4w_ratio,
            ranked.velocity_4w_ratio_normalized,
            ranked.tool_density,
            ranked.members_with_tools,
            ranked.p_thread,
            ranked.p_author,
            ranked.p_spec,
            ranked.p_sep,
            ranked.p_temp,
            ranked.p_slope,
            ranked.p_prior,
            ranked.p_workaround,
            ranked.p_tool_inv,
            ranked.q_axis,
            ranked.m_axis,
            ranked.s_axis,
            ranked.low_confidence_s,
            ranked.balance_score,
            ranked.is_eligible,
            ranked.pareto_layer,
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
                    WHEN (cca.competition_adjusted_s_axis IS NOT NULL) THEN 'competition_adjusted'::text
                    ELSE 'missing'::text
                END AS s_axis_source,
            cca.competition_adjusted_s_axis AS effective_s_axis
           FROM ((public.v_market_opportunities_ranked ranked
             LEFT JOIN active_runs cr ON (((cr.cluster_run_id = ranked.cluster_run_id) AND (cr.viability_run_id = ranked.viability_run_id) AND (cr.audience_classifier_version = (ranked.audience_classifier_version)::text) AND (NOT (cr.audience_type IS DISTINCT FROM (ranked.audience_type)::text)))))
             LEFT JOIN public.cluster_competition_assessments cca ON (((cca.competition_run_id = cr.id) AND (cca.cluster_id = ranked.cluster_id))))
        )
 SELECT cluster_id,
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
    velocity_4w_ratio_normalized,
    tool_density,
    members_with_tools,
    p_thread,
    p_author,
    p_spec,
    p_sep,
    p_temp,
    p_slope,
    p_prior,
    p_workaround,
    p_tool_inv,
    q_axis,
    m_axis,
    s_axis,
    low_confidence_s,
    balance_score,
    is_eligible,
    pareto_layer,
    competition_run_id,
    competition_status,
    market_structure,
    competition_confidence,
    external_supply_gap,
    competition_intensity,
    competition_adjusted_s_axis,
    solution_gap_evidence,
    verified_direct_count,
    direct_competitor_count,
    competition_rationale,
    has_competition_assessment,
    s_axis_source,
    effective_s_axis,
        CASE
            WHEN (has_competition_assessment IS NOT TRUE) THEN NULL::double precision
            WHEN (competition_adjusted_s_axis IS NULL) THEN NULL::double precision
            WHEN ((q_axis < (0.05)::double precision) OR (m_axis < (0.05)::double precision) OR (effective_s_axis < (0.05)::double precision)) THEN NULL::double precision
            ELSE ((3.0)::double precision / ((((1.0)::double precision / q_axis) + ((1.0)::double precision / m_axis)) + ((1.0)::double precision / effective_s_axis)))
        END AS competition_adjusted_balance_score
   FROM joined;


--
-- Name: v_market_opportunities_competition_ranked; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_market_opportunities_competition_ranked AS
 WITH pg AS MATERIALIZED (
         SELECT v_market_opportunities_competition_pregate.cluster_id,
            v_market_opportunities_competition_pregate.cluster_run_id,
            v_market_opportunities_competition_pregate.label,
            v_market_opportunities_competition_pregate.member_count,
            v_market_opportunities_competition_pregate.coherence,
            v_market_opportunities_competition_pregate.is_candidate,
            v_market_opportunities_competition_pregate.audience_classifier_version,
            v_market_opportunities_competition_pregate.audience_type,
            v_market_opportunities_competition_pregate.audience_confidence,
            v_market_opportunities_competition_pregate.icp_hint,
            v_market_opportunities_competition_pregate.thread_diversity,
            v_market_opportunities_competition_pregate.author_diversity,
            v_market_opportunities_competition_pregate.subreddit_count,
            v_market_opportunities_competition_pregate.specificity_mean,
            v_market_opportunities_competition_pregate.workaround_ratio,
            v_market_opportunities_competition_pregate.separation,
            v_market_opportunities_competition_pregate.temporal_spread_days,
            v_market_opportunities_competition_pregate.source_diversity,
            v_market_opportunities_competition_pregate.viability_run_id,
            v_market_opportunities_competition_pregate.is_real_pain,
            v_market_opportunities_competition_pregate.is_software_solvable,
            v_market_opportunities_competition_pregate.q_specificity,
            v_market_opportunities_competition_pregate.q_noise_flag,
            v_market_opportunities_competition_pregate.v_buildability,
            v_market_opportunities_competition_pregate.v_icp_clarity,
            v_market_opportunities_competition_pregate.v_moat_potential,
            v_market_opportunities_competition_pregate.v_rationale,
            v_market_opportunities_competition_pregate.pain_count_last_4w,
            v_market_opportunities_competition_pregate.pain_count_prior_4w,
            v_market_opportunities_competition_pregate.weeks_with_data,
            v_market_opportunities_competition_pregate.total_pain,
            v_market_opportunities_competition_pregate.slope_per_week,
            v_market_opportunities_competition_pregate.velocity_4w_ratio,
            v_market_opportunities_competition_pregate.velocity_4w_ratio_normalized,
            v_market_opportunities_competition_pregate.tool_density,
            v_market_opportunities_competition_pregate.members_with_tools,
            v_market_opportunities_competition_pregate.p_thread,
            v_market_opportunities_competition_pregate.p_author,
            v_market_opportunities_competition_pregate.p_spec,
            v_market_opportunities_competition_pregate.p_sep,
            v_market_opportunities_competition_pregate.p_temp,
            v_market_opportunities_competition_pregate.p_slope,
            v_market_opportunities_competition_pregate.p_prior,
            v_market_opportunities_competition_pregate.p_workaround,
            v_market_opportunities_competition_pregate.p_tool_inv,
            v_market_opportunities_competition_pregate.q_axis,
            v_market_opportunities_competition_pregate.m_axis,
            v_market_opportunities_competition_pregate.s_axis,
            v_market_opportunities_competition_pregate.low_confidence_s,
            v_market_opportunities_competition_pregate.balance_score,
            v_market_opportunities_competition_pregate.is_eligible,
            v_market_opportunities_competition_pregate.pareto_layer,
            v_market_opportunities_competition_pregate.competition_run_id,
            v_market_opportunities_competition_pregate.competition_status,
            v_market_opportunities_competition_pregate.market_structure,
            v_market_opportunities_competition_pregate.competition_confidence,
            v_market_opportunities_competition_pregate.external_supply_gap,
            v_market_opportunities_competition_pregate.competition_intensity,
            v_market_opportunities_competition_pregate.competition_adjusted_s_axis,
            v_market_opportunities_competition_pregate.solution_gap_evidence,
            v_market_opportunities_competition_pregate.verified_direct_count,
            v_market_opportunities_competition_pregate.direct_competitor_count,
            v_market_opportunities_competition_pregate.competition_rationale,
            v_market_opportunities_competition_pregate.has_competition_assessment,
            v_market_opportunities_competition_pregate.s_axis_source,
            v_market_opportunities_competition_pregate.effective_s_axis,
            v_market_opportunities_competition_pregate.competition_adjusted_balance_score
           FROM public.v_market_opportunities_competition_pregate
        ), layers AS MATERIALIZED (
         SELECT f.cluster_id,
            f.pareto_layer
           FROM (( SELECT DISTINCT pg_1.cluster_run_id,
                    pg_1.viability_run_id,
                    pg_1.audience_classifier_version,
                    pg_1.audience_type
                   FROM pg pg_1
                  WHERE ((pg_1.competition_run_id IS NOT NULL) AND (pg_1.audience_type IS NOT NULL))) a
             CROSS JOIN LATERAL public.fn_compute_competition_pareto_layers(a.cluster_run_id, a.viability_run_id, (a.audience_classifier_version)::text, (a.audience_type)::text) f(cluster_id, pareto_layer))
        )
 SELECT pg.cluster_id,
    pg.cluster_run_id,
    pg.label,
    pg.member_count,
    pg.coherence,
    pg.is_candidate,
    pg.audience_classifier_version,
    pg.audience_type,
    pg.audience_confidence,
    pg.icp_hint,
    pg.thread_diversity,
    pg.author_diversity,
    pg.subreddit_count,
    pg.specificity_mean,
    pg.workaround_ratio,
    pg.separation,
    pg.temporal_spread_days,
    pg.source_diversity,
    pg.viability_run_id,
    pg.is_real_pain,
    pg.is_software_solvable,
    pg.q_specificity,
    pg.q_noise_flag,
    pg.v_buildability,
    pg.v_icp_clarity,
    pg.v_moat_potential,
    pg.v_rationale,
    pg.pain_count_last_4w,
    pg.pain_count_prior_4w,
    pg.weeks_with_data,
    pg.total_pain,
    pg.slope_per_week,
    pg.velocity_4w_ratio,
    pg.velocity_4w_ratio_normalized,
    pg.tool_density,
    pg.members_with_tools,
    pg.p_thread,
    pg.p_author,
    pg.p_spec,
    pg.p_sep,
    pg.p_temp,
    pg.p_slope,
    pg.p_prior,
    pg.p_workaround,
    pg.p_tool_inv,
    pg.q_axis,
    pg.m_axis,
    pg.s_axis,
    pg.low_confidence_s,
    pg.balance_score,
    pg.is_eligible,
    pg.pareto_layer,
    pg.competition_run_id,
    pg.competition_status,
    pg.market_structure,
    pg.competition_confidence,
    pg.external_supply_gap,
    pg.competition_intensity,
    pg.competition_adjusted_s_axis,
    pg.solution_gap_evidence,
    pg.verified_direct_count,
    pg.direct_competitor_count,
    pg.competition_rationale,
    pg.has_competition_assessment,
    pg.s_axis_source,
    pg.effective_s_axis,
    pg.competition_adjusted_balance_score,
    l.pareto_layer AS competition_adjusted_pareto_layer
   FROM (pg
     LEFT JOIN layers l ON ((l.cluster_id = pg.cluster_id)));


--
-- Name: viability_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.viability_runs (
    id integer NOT NULL,
    cluster_run_id integer NOT NULL,
    started_at timestamp with time zone DEFAULT now(),
    finished_at timestamp with time zone,
    llm_model character varying(64) NOT NULL,
    prompt_version character varying(32) DEFAULT 'v1'::character varying NOT NULL,
    params jsonb,
    n_clusters integer,
    n_succeeded integer,
    n_failed integer,
    notes text
);


--
-- Name: viability_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.viability_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: viability_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.viability_runs_id_seq OWNED BY public.viability_runs.id;


--
-- Name: analysis_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_runs ALTER COLUMN id SET DEFAULT nextval('public.analysis_runs_id_seq'::regclass);


--
-- Name: candidate_promotion_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_promotion_log ALTER COLUMN id SET DEFAULT nextval('public.candidate_promotion_log_id_seq'::regclass);


--
-- Name: cluster_audience id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_audience ALTER COLUMN id SET DEFAULT nextval('public.cluster_audience_id_seq'::regclass);


--
-- Name: cluster_competition id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_competition ALTER COLUMN id SET DEFAULT nextval('public.cluster_competition_id_seq'::regclass);


--
-- Name: cluster_competitors id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_competitors ALTER COLUMN id SET DEFAULT nextval('public.cluster_competitors_id_seq'::regclass);


--
-- Name: comments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments ALTER COLUMN id SET DEFAULT nextval('public.comments_id_seq'::regclass);


--
-- Name: competition_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competition_runs ALTER COLUMN id SET DEFAULT nextval('public.competition_runs_id_seq'::regclass);


--
-- Name: dashboard_users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dashboard_users ALTER COLUMN id SET DEFAULT nextval('public.dashboard_users_id_seq'::regclass);


--
-- Name: emerging_opportunities id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.emerging_opportunities ALTER COLUMN id SET DEFAULT nextval('public.emerging_opportunities_id_seq'::regclass);


--
-- Name: emerging_opportunity_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.emerging_opportunity_runs ALTER COLUMN id SET DEFAULT nextval('public.emerging_opportunity_runs_id_seq'::regclass);


--
-- Name: pain_clusters id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_clusters ALTER COLUMN id SET DEFAULT nextval('public.pain_clusters_id_seq'::regclass);


--
-- Name: pain_objects id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_objects ALTER COLUMN id SET DEFAULT nextval('public.pain_objects_id_seq'::regclass);


--
-- Name: pipeline_job_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_job_events ALTER COLUMN id SET DEFAULT nextval('public.pipeline_job_events_id_seq'::regclass);


--
-- Name: pipeline_job_steps id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_job_steps ALTER COLUMN id SET DEFAULT nextval('public.pipeline_job_steps_id_seq'::regclass);


--
-- Name: pipeline_jobs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_jobs ALTER COLUMN id SET DEFAULT nextval('public.pipeline_jobs_id_seq'::regclass);


--
-- Name: posts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posts ALTER COLUMN id SET DEFAULT nextval('public.posts_id_seq'::regclass);


--
-- Name: reddit_backfill_chunks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reddit_backfill_chunks ALTER COLUMN id SET DEFAULT nextval('public.reddit_backfill_chunks_id_seq'::regclass);


--
-- Name: reddit_backfill_jobs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reddit_backfill_jobs ALTER COLUMN id SET DEFAULT nextval('public.reddit_backfill_jobs_id_seq'::regclass);


--
-- Name: report_jobs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_jobs ALTER COLUMN id SET DEFAULT nextval('public.report_jobs_id_seq'::regclass);


--
-- Name: report_stage_outputs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_stage_outputs ALTER COLUMN id SET DEFAULT nextval('public.report_stage_outputs_id_seq'::regclass);


--
-- Name: reports id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reports ALTER COLUMN id SET DEFAULT nextval('public.reports_id_seq'::regclass);


--
-- Name: signal_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_events ALTER COLUMN id SET DEFAULT nextval('public.signal_events_id_seq'::regclass);


--
-- Name: social_source_venues id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_source_venues ALTER COLUMN id SET DEFAULT nextval('public.social_source_venues_id_seq'::regclass);


--
-- Name: socialcrawl_items id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.socialcrawl_items ALTER COLUMN id SET DEFAULT nextval('public.socialcrawl_items_id_seq'::regclass);


--
-- Name: source_backfill_chunks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_backfill_chunks ALTER COLUMN id SET DEFAULT nextval('public.source_backfill_chunks_id_seq'::regclass);


--
-- Name: source_backfill_jobs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_backfill_jobs ALTER COLUMN id SET DEFAULT nextval('public.source_backfill_jobs_id_seq'::regclass);


--
-- Name: subreddits id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subreddits ALTER COLUMN id SET DEFAULT nextval('public.subreddits_id_seq'::regclass);


--
-- Name: viability_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.viability_runs ALTER COLUMN id SET DEFAULT nextval('public.viability_runs_id_seq'::regclass);


--
-- Name: analysis_runs analysis_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_runs
    ADD CONSTRAINT analysis_runs_pkey PRIMARY KEY (id);


--
-- Name: app_search_cache app_search_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_search_cache
    ADD CONSTRAINT app_search_cache_pkey PRIMARY KEY (keyword);


--
-- Name: app_store_apps app_store_apps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_store_apps
    ADD CONSTRAINT app_store_apps_pkey PRIMARY KEY (app_id);


--
-- Name: candidate_promotion_log candidate_promotion_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_promotion_log
    ADD CONSTRAINT candidate_promotion_log_pkey PRIMARY KEY (id);


--
-- Name: cluster_audience cluster_audience_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_audience
    ADD CONSTRAINT cluster_audience_pkey PRIMARY KEY (id);


--
-- Name: cluster_competition_assessments cluster_competition_assessments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_competition_assessments
    ADD CONSTRAINT cluster_competition_assessments_pkey PRIMARY KEY (competition_run_id, cluster_id);


--
-- Name: cluster_competition cluster_competition_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_competition
    ADD CONSTRAINT cluster_competition_pkey PRIMARY KEY (id);


--
-- Name: cluster_competitors cluster_competitors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_competitors
    ADD CONSTRAINT cluster_competitors_pkey PRIMARY KEY (id);


--
-- Name: cluster_domain_groups cluster_domain_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_domain_groups
    ADD CONSTRAINT cluster_domain_groups_pkey PRIMARY KEY (competition_run_id, domain_id);


--
-- Name: cluster_domain_members cluster_domain_members_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_domain_members
    ADD CONSTRAINT cluster_domain_members_pkey PRIMARY KEY (competition_run_id, cluster_id);


--
-- Name: cluster_quality_metrics cluster_quality_metrics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_quality_metrics
    ADD CONSTRAINT cluster_quality_metrics_pkey PRIMARY KEY (cluster_run_id, cluster_id);


--
-- Name: comments comments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT comments_pkey PRIMARY KEY (id);


--
-- Name: comments comments_reddit_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT comments_reddit_id_key UNIQUE (reddit_id);


--
-- Name: competition_runs competition_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competition_runs
    ADD CONSTRAINT competition_runs_pkey PRIMARY KEY (id);


--
-- Name: corpus_burst_snapshot corpus_burst_snapshot_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.corpus_burst_snapshot
    ADD CONSTRAINT corpus_burst_snapshot_pkey PRIMARY KEY (cluster_run_id);


--
-- Name: dashboard_market_opportunities dashboard_market_opportunities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dashboard_market_opportunities
    ADD CONSTRAINT dashboard_market_opportunities_pkey PRIMARY KEY (cluster_run_id, viability_run_id, audience_classifier_version, cluster_id);


--
-- Name: dashboard_market_refresh_state dashboard_market_refresh_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dashboard_market_refresh_state
    ADD CONSTRAINT dashboard_market_refresh_state_pkey PRIMARY KEY (cluster_run_id, viability_run_id, audience_classifier_version);


--
-- Name: dashboard_users dashboard_users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dashboard_users
    ADD CONSTRAINT dashboard_users_pkey PRIMARY KEY (id);


--
-- Name: dashboard_users dashboard_users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dashboard_users
    ADD CONSTRAINT dashboard_users_username_key UNIQUE (username);


--
-- Name: emerging_opportunities emerging_opportunities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.emerging_opportunities
    ADD CONSTRAINT emerging_opportunities_pkey PRIMARY KEY (id);


--
-- Name: emerging_opportunity_members emerging_opportunity_members_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.emerging_opportunity_members
    ADD CONSTRAINT emerging_opportunity_members_pkey PRIMARY KEY (emerging_opportunity_id, pain_id);


--
-- Name: emerging_opportunity_runs emerging_opportunity_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.emerging_opportunity_runs
    ADD CONSTRAINT emerging_opportunity_runs_pkey PRIMARY KEY (id);


--
-- Name: hackernews_items hackernews_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hackernews_items
    ADD CONSTRAINT hackernews_items_pkey PRIMARY KEY (hn_id);


--
-- Name: pain_cluster_members pain_cluster_members_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_cluster_members
    ADD CONSTRAINT pain_cluster_members_pkey PRIMARY KEY (pain_id, cluster_id);


--
-- Name: pain_clusters pain_clusters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_clusters
    ADD CONSTRAINT pain_clusters_pkey PRIMARY KEY (id);


--
-- Name: pain_extraction_attempts pain_extraction_attempts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_extraction_attempts
    ADD CONSTRAINT pain_extraction_attempts_pkey PRIMARY KEY (signal_event_id);


--
-- Name: pain_objects pain_objects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_objects
    ADD CONSTRAINT pain_objects_pkey PRIMARY KEY (id);


--
-- Name: pain_volume_daily_corpus pain_volume_daily_corpus_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_volume_daily_corpus
    ADD CONSTRAINT pain_volume_daily_corpus_pkey PRIMARY KEY (cluster_run_id, day_start);


--
-- Name: pain_volume_daily pain_volume_daily_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_volume_daily
    ADD CONSTRAINT pain_volume_daily_pkey PRIMARY KEY (cluster_run_id, cluster_id, day_start);


--
-- Name: pain_volume_daily_subreddit pain_volume_daily_subreddit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_volume_daily_subreddit
    ADD CONSTRAINT pain_volume_daily_subreddit_pkey PRIMARY KEY (cluster_run_id, cluster_id, day_start, subreddit_name);


--
-- Name: pain_volume_weekly pain_volume_weekly_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_volume_weekly
    ADD CONSTRAINT pain_volume_weekly_pkey PRIMARY KEY (cluster_run_id, cluster_id, week_start, audience_type, function_tag);


--
-- Name: pipeline_job_events pipeline_job_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_job_events
    ADD CONSTRAINT pipeline_job_events_pkey PRIMARY KEY (id);


--
-- Name: pipeline_job_steps pipeline_job_steps_job_id_stage_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_job_steps
    ADD CONSTRAINT pipeline_job_steps_job_id_stage_name_key UNIQUE (job_id, stage_name);


--
-- Name: pipeline_job_steps pipeline_job_steps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_job_steps
    ADD CONSTRAINT pipeline_job_steps_pkey PRIMARY KEY (id);


--
-- Name: pipeline_jobs pipeline_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_jobs
    ADD CONSTRAINT pipeline_jobs_pkey PRIMARY KEY (id);


--
-- Name: pipeline_worker_heartbeats pipeline_worker_heartbeats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_worker_heartbeats
    ADD CONSTRAINT pipeline_worker_heartbeats_pkey PRIMARY KEY (worker_id);


--
-- Name: posts posts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posts
    ADD CONSTRAINT posts_pkey PRIMARY KEY (id);


--
-- Name: posts posts_reddit_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posts
    ADD CONSTRAINT posts_reddit_id_key UNIQUE (reddit_id);


--
-- Name: processed_posts processed_posts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processed_posts
    ADD CONSTRAINT processed_posts_pkey PRIMARY KEY (post_id);


--
-- Name: processed_signal_events processed_signal_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processed_signal_events
    ADD CONSTRAINT processed_signal_events_pkey PRIMARY KEY (signal_event_id);


--
-- Name: reddit_backfill_chunks reddit_backfill_chunks_job_id_subreddit_name_window_start_w_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reddit_backfill_chunks
    ADD CONSTRAINT reddit_backfill_chunks_job_id_subreddit_name_window_start_w_key UNIQUE (job_id, subreddit_name, window_start, window_end);


--
-- Name: reddit_backfill_chunks reddit_backfill_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reddit_backfill_chunks
    ADD CONSTRAINT reddit_backfill_chunks_pkey PRIMARY KEY (id);


--
-- Name: reddit_backfill_jobs reddit_backfill_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reddit_backfill_jobs
    ADD CONSTRAINT reddit_backfill_jobs_pkey PRIMARY KEY (id);


--
-- Name: reddit_subreddit_sources reddit_subreddit_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reddit_subreddit_sources
    ADD CONSTRAINT reddit_subreddit_sources_pkey PRIMARY KEY (subreddit_name);


--
-- Name: report_jobs report_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_jobs
    ADD CONSTRAINT report_jobs_pkey PRIMARY KEY (id);


--
-- Name: report_stage_outputs report_stage_outputs_job_id_stage_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_stage_outputs
    ADD CONSTRAINT report_stage_outputs_job_id_stage_name_key UNIQUE (job_id, stage_name);


--
-- Name: report_stage_outputs report_stage_outputs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_stage_outputs
    ADD CONSTRAINT report_stage_outputs_pkey PRIMARY KEY (id);


--
-- Name: reports reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reports
    ADD CONSTRAINT reports_pkey PRIMARY KEY (id);


--
-- Name: signal_events signal_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_events
    ADD CONSTRAINT signal_events_pkey PRIMARY KEY (id);


--
-- Name: social_source_venues social_source_venues_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_source_venues
    ADD CONSTRAINT social_source_venues_pkey PRIMARY KEY (id);


--
-- Name: social_source_venues social_source_venues_source_venue_type_url_or_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_source_venues
    ADD CONSTRAINT social_source_venues_source_venue_type_url_or_id_key UNIQUE (source, venue_type, url_or_id);


--
-- Name: socialcrawl_items socialcrawl_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.socialcrawl_items
    ADD CONSTRAINT socialcrawl_items_pkey PRIMARY KEY (id);


--
-- Name: source_backfill_chunks source_backfill_chunks_job_id_source_target_type_target_id__key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_backfill_chunks
    ADD CONSTRAINT source_backfill_chunks_job_id_source_target_type_target_id__key UNIQUE (job_id, source, target_type, target_id, query, window_start, window_end);


--
-- Name: source_backfill_chunks source_backfill_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_backfill_chunks
    ADD CONSTRAINT source_backfill_chunks_pkey PRIMARY KEY (id);


--
-- Name: source_backfill_jobs source_backfill_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_backfill_jobs
    ADD CONSTRAINT source_backfill_jobs_pkey PRIMARY KEY (id);


--
-- Name: source_capabilities source_capabilities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_capabilities
    ADD CONSTRAINT source_capabilities_pkey PRIMARY KEY (source);


--
-- Name: source_runtime_config source_runtime_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_runtime_config
    ADD CONSTRAINT source_runtime_config_pkey PRIMARY KEY (source);


--
-- Name: subreddits subreddits_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subreddits
    ADD CONSTRAINT subreddits_name_key UNIQUE (name);


--
-- Name: subreddits subreddits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subreddits
    ADD CONSTRAINT subreddits_pkey PRIMARY KEY (id);


--
-- Name: system_settings system_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_settings
    ADD CONSTRAINT system_settings_pkey PRIMARY KEY (key);


--
-- Name: cluster_audience uq_cluster_audience_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_audience
    ADD CONSTRAINT uq_cluster_audience_version UNIQUE (cluster_id, classifier_version);


--
-- Name: cluster_competition uq_cluster_run_app; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_competition
    ADD CONSTRAINT uq_cluster_run_app UNIQUE (cluster_id, analysis_run_id, app_id);


--
-- Name: pain_objects uq_pain_post_statement; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_objects
    ADD CONSTRAINT uq_pain_post_statement UNIQUE (post_id, pain_statement);


--
-- Name: reports uq_reports_job_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reports
    ADD CONSTRAINT uq_reports_job_id UNIQUE (job_id);


--
-- Name: signal_events uq_signal_event_provenance; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_events
    ADD CONSTRAINT uq_signal_event_provenance UNIQUE (source, source_table, source_row_id);


--
-- Name: viability_runs viability_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.viability_runs
    ADD CONSTRAINT viability_runs_pkey PRIMARY KEY (id);


--
-- Name: viability_scores viability_scores_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.viability_scores
    ADD CONSTRAINT viability_scores_pkey PRIMARY KEY (viability_run_id, cluster_id);


--
-- Name: idx_app_store_apps_embedding; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_app_store_apps_embedding ON public.app_store_apps USING ivfflat (embedding public.vector_cosine_ops) WITH (lists='100');


--
-- Name: idx_comments_created_utc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_comments_created_utc ON public.comments USING btree (created_utc);


--
-- Name: idx_comments_post_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_comments_post_id ON public.comments USING btree (post_id);


--
-- Name: idx_comments_reddit_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_comments_reddit_id ON public.comments USING btree (reddit_id);


--
-- Name: idx_posts_created_utc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_posts_created_utc ON public.posts USING btree (created_utc);


--
-- Name: idx_posts_reddit_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_posts_reddit_id ON public.posts USING btree (reddit_id);


--
-- Name: idx_posts_score; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_posts_score ON public.posts USING btree (score);


--
-- Name: idx_posts_subreddit_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_posts_subreddit_id ON public.posts USING btree (subreddit_id);


--
-- Name: idx_report_jobs_cluster; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_report_jobs_cluster ON public.report_jobs USING btree (cluster_id);


--
-- Name: idx_report_jobs_cluster_context; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_report_jobs_cluster_context ON public.report_jobs USING btree (cluster_id, analysis_run_id, viability_run_id, audience_classifier_version, created_at DESC, id DESC);


--
-- Name: idx_report_jobs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_report_jobs_status ON public.report_jobs USING btree (status);


--
-- Name: idx_reports_cluster; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reports_cluster ON public.reports USING btree (cluster_id);


--
-- Name: idx_reports_verdict; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reports_verdict ON public.reports USING btree (verdict);


--
-- Name: ix_analysis_runs_live_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_analysis_runs_live_at ON public.analysis_runs USING btree (live_at DESC, id DESC) WHERE (live_at IS NOT NULL);


--
-- Name: ix_cca_cluster_context; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cca_cluster_context ON public.cluster_competition_assessments USING btree (cluster_run_id, viability_run_id, audience_classifier_version, cluster_id);


--
-- Name: ix_cluster_audience_cluster; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_audience_cluster ON public.cluster_audience USING btree (cluster_id);


--
-- Name: ix_cluster_audience_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_audience_type ON public.cluster_audience USING btree (audience_type);


--
-- Name: ix_cluster_competitors_cluster; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_competitors_cluster ON public.cluster_competitors USING btree (competition_run_id, cluster_id, category, strength_score DESC);


--
-- Name: ix_cluster_domain_groups_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_domain_groups_run ON public.cluster_domain_groups USING btree (competition_run_id, domain_id);


--
-- Name: ix_cluster_domain_members_domain; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_domain_members_domain ON public.cluster_domain_members USING btree (competition_run_id, domain_id);


--
-- Name: ix_competition_runs_context_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_competition_runs_context_active ON public.competition_runs USING btree (cluster_run_id, viability_run_id, audience_classifier_version, audience_type, is_active, id DESC);


--
-- Name: ix_cqm_specificity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cqm_specificity ON public.cluster_quality_metrics USING btree (specificity_mean);


--
-- Name: ix_cqm_thread_diversity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cqm_thread_diversity ON public.cluster_quality_metrics USING btree (thread_diversity);


--
-- Name: ix_dashboard_market_competition_rank; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dashboard_market_competition_rank ON public.dashboard_market_opportunities USING btree (cluster_run_id, viability_run_id, audience_classifier_version, audience_type, competition_adjusted_pareto_layer, competition_adjusted_balance_score DESC NULLS LAST, pareto_layer, cluster_id);


--
-- Name: ix_dashboard_market_opportunities_burst; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dashboard_market_opportunities_burst ON public.dashboard_market_opportunities USING btree (cluster_run_id, viability_run_id, audience_classifier_version, audience_type, burst_score DESC NULLS LAST, cluster_id);


--
-- Name: ix_dashboard_market_opportunities_eligible; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dashboard_market_opportunities_eligible ON public.dashboard_market_opportunities USING btree (cluster_run_id, viability_run_id, audience_classifier_version, audience_type, is_eligible);


--
-- Name: ix_dashboard_market_opportunities_list; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dashboard_market_opportunities_list ON public.dashboard_market_opportunities USING btree (cluster_run_id, viability_run_id, audience_classifier_version, audience_type, pareto_layer, balance_score DESC NULLS LAST, cluster_id);


--
-- Name: ix_dashboard_market_window_pain; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dashboard_market_window_pain ON public.dashboard_market_opportunities USING btree (cluster_run_id, viability_run_id, audience_classifier_version, audience_type, window_pain_count DESC NULLS LAST, cluster_id);


--
-- Name: ix_dashboard_users_username; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dashboard_users_username ON public.dashboard_users USING btree (username);


--
-- Name: ix_emerging_members_pain; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_emerging_members_pain ON public.emerging_opportunity_members USING btree (pain_id);


--
-- Name: ix_emerging_opportunities_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_emerging_opportunities_active ON public.emerging_opportunities USING btree (cluster_run_id, emerging_run_id DESC, burst_score DESC, coherence DESC, pain_count DESC);


--
-- Name: ix_emerging_runs_context; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_emerging_runs_context ON public.emerging_opportunity_runs USING btree (cluster_run_id, status, id DESC);


--
-- Name: ix_hn_items_fetched_from; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_hn_items_fetched_from ON public.hackernews_items USING btree (fetched_from);


--
-- Name: ix_hn_items_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_hn_items_parent ON public.hackernews_items USING btree (parent_hn_id);


--
-- Name: ix_hn_items_root; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_hn_items_root ON public.hackernews_items USING btree (root_hn_id);


--
-- Name: ix_hn_items_type_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_hn_items_type_time ON public.hackernews_items USING btree (item_type, time_utc DESC);


--
-- Name: ix_lineage_child; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lineage_child ON public.cluster_lineage USING btree (child_run_id, child_cluster);


--
-- Name: ix_lineage_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lineage_parent ON public.cluster_lineage USING btree (parent_run_id, parent_cluster);


--
-- Name: ix_pain_clusters_candidate; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pain_clusters_candidate ON public.pain_clusters USING btree (run_id, is_candidate) WHERE is_candidate;


--
-- Name: ix_pain_clusters_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pain_clusters_run ON public.pain_clusters USING btree (run_id);


--
-- Name: ix_pain_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pain_created ON public.pain_objects USING btree (source_created);


--
-- Name: ix_pain_post_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pain_post_id ON public.pain_objects USING btree (post_id);


--
-- Name: ix_pain_prompt_version; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pain_prompt_version ON public.pain_objects USING btree (prompt_version);


--
-- Name: ix_pain_signal_event; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pain_signal_event ON public.pain_objects USING btree (signal_event_id);


--
-- Name: ix_pain_subreddit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pain_subreddit ON public.pain_objects USING btree (subreddit_name);


--
-- Name: ix_pcm_cluster; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pcm_cluster ON public.pain_cluster_members USING btree (cluster_id);


--
-- Name: ix_pcm_cluster_pain; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pcm_cluster_pain ON public.pain_cluster_members USING btree (cluster_id, pain_id);


--
-- Name: ix_pea_failed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pea_failed ON public.pain_extraction_attempts USING btree (failed) WHERE failed;


--
-- Name: ix_pipeline_job_events_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_job_events_job ON public.pipeline_job_events USING btree (job_id, created_at, id);


--
-- Name: ix_pipeline_job_events_recent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_job_events_recent ON public.pipeline_job_events USING btree (created_at DESC, id DESC);


--
-- Name: ix_pipeline_job_steps_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_job_steps_job ON public.pipeline_job_steps USING btree (job_id, stage_order);


--
-- Name: ix_pipeline_job_steps_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_job_steps_status ON public.pipeline_job_steps USING btree (status, created_at DESC);


--
-- Name: ix_pipeline_jobs_claim; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_jobs_claim ON public.pipeline_jobs USING btree (status, run_after, id) WHERE (status = 'queued'::text);


--
-- Name: ix_pipeline_jobs_recent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_jobs_recent ON public.pipeline_jobs USING btree (job_type, created_at DESC);


--
-- Name: ix_processed_signal_events_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processed_signal_events_source ON public.processed_signal_events USING btree (source, processed_at DESC);


--
-- Name: ix_promotion_recent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_promotion_recent ON public.candidate_promotion_log USING btree (promoted_at DESC);


--
-- Name: ix_promotion_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_promotion_run ON public.candidate_promotion_log USING btree (cluster_run_id);


--
-- Name: ix_pvd_corpus_run_day; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pvd_corpus_run_day ON public.pain_volume_daily_corpus USING btree (cluster_run_id, day_start);


--
-- Name: ix_pvd_run_day; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pvd_run_day ON public.pain_volume_daily USING btree (cluster_run_id, day_start, cluster_id);


--
-- Name: ix_pvds_run_day_subreddit_cluster; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pvds_run_day_subreddit_cluster ON public.pain_volume_daily_subreddit USING btree (cluster_run_id, day_start, subreddit_name, cluster_id);


--
-- Name: ix_pvds_run_subreddit_day; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pvds_run_subreddit_day ON public.pain_volume_daily_subreddit USING btree (cluster_run_id, subreddit_name, day_start);


--
-- Name: ix_pvw_run_week_cluster; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pvw_run_week_cluster ON public.pain_volume_weekly USING btree (cluster_run_id, week_start, cluster_id);


--
-- Name: ix_pvw_week; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pvw_week ON public.pain_volume_weekly USING btree (week_start);


--
-- Name: ix_reddit_backfill_chunks_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_reddit_backfill_chunks_status ON public.reddit_backfill_chunks USING btree (status, job_id, id);


--
-- Name: ix_reddit_backfill_chunks_subreddit_window; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_reddit_backfill_chunks_subreddit_window ON public.reddit_backfill_chunks USING btree (subreddit_name, window_start, window_end);


--
-- Name: ix_reddit_backfill_jobs_status_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_reddit_backfill_jobs_status_created ON public.reddit_backfill_jobs USING btree (status, created_at DESC);


--
-- Name: ix_reddit_subreddit_sources_status_priority; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_reddit_subreddit_sources_status_priority ON public.reddit_subreddit_sources USING btree (status, priority, subreddit_name);


--
-- Name: ix_reddit_subreddit_sources_updated_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_reddit_subreddit_sources_updated_at ON public.reddit_subreddit_sources USING btree (updated_at DESC);


--
-- Name: ix_signal_events_event_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_signal_events_event_time ON public.signal_events USING btree (event_time);


--
-- Name: ix_signal_events_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_signal_events_parent ON public.signal_events USING btree (parent_event_id);


--
-- Name: ix_signal_events_parent_source_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_signal_events_parent_source_time ON public.signal_events USING btree (source, event_time DESC) WHERE (parent_event_id IS NULL);


--
-- Name: ix_signal_events_source_parent_null; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_signal_events_source_parent_null ON public.signal_events USING btree (source) WHERE (parent_event_id IS NULL);


--
-- Name: ix_signal_events_source_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_signal_events_source_type ON public.signal_events USING btree (source, event_type);


--
-- Name: ix_signal_events_surface; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_signal_events_surface ON public.signal_events USING btree (surface);


--
-- Name: ix_social_source_venues_source_status_priority; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_social_source_venues_source_status_priority ON public.social_source_venues USING btree (source, status, priority, name);


--
-- Name: ix_social_source_venues_updated_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_social_source_venues_updated_at ON public.social_source_venues USING btree (updated_at DESC);


--
-- Name: ix_socialcrawl_items_provider_endpoint; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_socialcrawl_items_provider_endpoint ON public.socialcrawl_items USING btree (provider, provider_endpoint);


--
-- Name: ix_socialcrawl_items_root; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_socialcrawl_items_root ON public.socialcrawl_items USING btree (source, root_source_item_id);


--
-- Name: ix_socialcrawl_items_source_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_socialcrawl_items_source_time ON public.socialcrawl_items USING btree (source, item_kind, event_time DESC);


--
-- Name: ix_source_backfill_chunks_source_window; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_source_backfill_chunks_source_window ON public.source_backfill_chunks USING btree (source, window_start, window_end);


--
-- Name: ix_source_backfill_chunks_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_source_backfill_chunks_status ON public.source_backfill_chunks USING btree (status, job_id, id);


--
-- Name: ix_source_backfill_jobs_status_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_source_backfill_jobs_status_created ON public.source_backfill_jobs USING btree (status, created_at DESC);


--
-- Name: ix_subreddits_audience_prior; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_subreddits_audience_prior ON public.subreddits USING btree (audience_prior);


--
-- Name: ix_system_settings_updated_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_system_settings_updated_at ON public.system_settings USING btree (updated_at DESC);


--
-- Name: ix_vs_cluster; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_vs_cluster ON public.viability_scores USING btree (cluster_id);


--
-- Name: ix_vs_real_pain; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_vs_real_pain ON public.viability_scores USING btree (is_real_pain) WHERE is_real_pain;


--
-- Name: ix_vs_saas; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_vs_saas ON public.viability_scores USING btree (is_software_solvable) WHERE is_software_solvable;


--
-- Name: pain_clusters_centroid_ivfflat; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX pain_clusters_centroid_ivfflat ON public.pain_clusters USING ivfflat (centroid public.vector_cosine_ops) WITH (lists='100');


--
-- Name: uq_lineage_edge; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_lineage_edge ON public.cluster_lineage USING btree (COALESCE(parent_run_id, 0), COALESCE(parent_cluster, 0), child_run_id, COALESCE(child_cluster, 0));


--
-- Name: uq_pain_signal_event_statement_prompt; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_pain_signal_event_statement_prompt ON public.pain_objects USING btree (signal_event_id, pain_statement, prompt_version) WHERE (signal_event_id IS NOT NULL);


--
-- Name: uq_socialcrawl_items_source_kind_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_socialcrawl_items_source_kind_id ON public.socialcrawl_items USING btree (source, item_kind, source_item_id);


--
-- Name: clustering_runs clustering_runs_insert; Type: RULE; Schema: public; Owner: -
--

CREATE RULE clustering_runs_insert AS
    ON INSERT TO public.clustering_runs DO INSTEAD  INSERT INTO public.analysis_runs (label, params, pain_filter, is_legacy, cluster_count, noise_count, total_points, started_at, finished_at, candidate_thresholds, window_start, window_end, resolution)
  VALUES (new.label, COALESCE(new.params, '{}'::jsonb), new.pain_filter, COALESCE(new.is_legacy, false), new.cluster_count, new.noise_count, new.total_points, COALESCE(new.started_at, now()), new.finished_at, new.candidate_thresholds, COALESCE(new.window_start, now()), COALESCE(new.window_end, now()), COALESCE(new.resolution, 'v2'::character varying))
  RETURNING analysis_runs.id,
    analysis_runs.label,
    analysis_runs.params,
    analysis_runs.pain_filter,
    analysis_runs.is_legacy,
    analysis_runs.cluster_count,
    analysis_runs.noise_count,
    analysis_runs.total_points,
    analysis_runs.started_at,
    analysis_runs.finished_at,
    analysis_runs.candidate_thresholds,
    analysis_runs.window_start,
    analysis_runs.window_end,
    analysis_runs.resolution,
    analysis_runs.created_at;


--
-- Name: clustering_runs clustering_runs_update; Type: RULE; Schema: public; Owner: -
--

CREATE RULE clustering_runs_update AS
    ON UPDATE TO public.clustering_runs DO INSTEAD  UPDATE public.analysis_runs SET label = new.label, params = new.params, pain_filter = new.pain_filter, is_legacy = new.is_legacy, cluster_count = new.cluster_count, noise_count = new.noise_count, total_points = new.total_points, finished_at = new.finished_at, candidate_thresholds = new.candidate_thresholds
  WHERE (analysis_runs.id = old.id);


--
-- Name: dashboard_users trg_dashboard_user_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_dashboard_user_updated_at BEFORE UPDATE ON public.dashboard_users FOR EACH ROW EXECUTE FUNCTION public.touch_dashboard_user_updated_at();


--
-- Name: pipeline_job_steps trg_pipeline_job_steps_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_pipeline_job_steps_updated_at BEFORE UPDATE ON public.pipeline_job_steps FOR EACH ROW EXECUTE FUNCTION public.touch_pipeline_updated_at();


--
-- Name: pipeline_jobs trg_pipeline_jobs_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_pipeline_jobs_updated_at BEFORE UPDATE ON public.pipeline_jobs FOR EACH ROW EXECUTE FUNCTION public.touch_pipeline_updated_at();


--
-- Name: pipeline_worker_heartbeats trg_pipeline_worker_heartbeats_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_pipeline_worker_heartbeats_updated_at BEFORE UPDATE ON public.pipeline_worker_heartbeats FOR EACH ROW EXECUTE FUNCTION public.touch_pipeline_updated_at();


--
-- Name: reddit_backfill_chunks trg_reddit_backfill_chunks_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_reddit_backfill_chunks_updated_at BEFORE UPDATE ON public.reddit_backfill_chunks FOR EACH ROW EXECUTE FUNCTION public.touch_reddit_backfill_chunks_updated_at();


--
-- Name: reddit_subreddit_sources trg_reddit_subreddit_sources_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_reddit_subreddit_sources_updated_at BEFORE UPDATE ON public.reddit_subreddit_sources FOR EACH ROW EXECUTE FUNCTION public.touch_reddit_subreddit_sources_updated_at();


--
-- Name: social_source_venues trg_social_source_venues_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_social_source_venues_updated_at BEFORE UPDATE ON public.social_source_venues FOR EACH ROW EXECUTE FUNCTION public.touch_social_source_venues_updated_at();


--
-- Name: source_backfill_chunks trg_source_backfill_chunks_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_source_backfill_chunks_updated_at BEFORE UPDATE ON public.source_backfill_chunks FOR EACH ROW EXECUTE FUNCTION public.touch_source_backfill_chunks_updated_at();


--
-- Name: system_settings trg_system_settings_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_system_settings_updated_at BEFORE UPDATE ON public.system_settings FOR EACH ROW EXECUTE FUNCTION public.touch_system_settings_updated_at();


--
-- Name: candidate_promotion_log candidate_promotion_log_cluster_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_promotion_log
    ADD CONSTRAINT candidate_promotion_log_cluster_id_fkey FOREIGN KEY (cluster_id) REFERENCES public.pain_clusters(id) ON DELETE CASCADE;


--
-- Name: candidate_promotion_log candidate_promotion_log_cluster_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_promotion_log
    ADD CONSTRAINT candidate_promotion_log_cluster_run_id_fkey FOREIGN KEY (cluster_run_id) REFERENCES public.analysis_runs(id) ON DELETE CASCADE;


--
-- Name: cluster_audience cluster_audience_cluster_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_audience
    ADD CONSTRAINT cluster_audience_cluster_id_fkey FOREIGN KEY (cluster_id) REFERENCES public.pain_clusters(id) ON DELETE CASCADE;


--
-- Name: cluster_competition cluster_competition_analysis_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_competition
    ADD CONSTRAINT cluster_competition_analysis_run_id_fkey FOREIGN KEY (analysis_run_id) REFERENCES public.analysis_runs(id);


--
-- Name: cluster_competition cluster_competition_app_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_competition
    ADD CONSTRAINT cluster_competition_app_id_fkey FOREIGN KEY (app_id) REFERENCES public.app_store_apps(app_id);


--
-- Name: cluster_competition_assessments cluster_competition_assessments_cluster_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_competition_assessments
    ADD CONSTRAINT cluster_competition_assessments_cluster_id_fkey FOREIGN KEY (cluster_id) REFERENCES public.pain_clusters(id) ON DELETE CASCADE;


--
-- Name: cluster_competition_assessments cluster_competition_assessments_cluster_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_competition_assessments
    ADD CONSTRAINT cluster_competition_assessments_cluster_run_id_fkey FOREIGN KEY (cluster_run_id) REFERENCES public.analysis_runs(id) ON DELETE CASCADE;


--
-- Name: cluster_competition_assessments cluster_competition_assessments_competition_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_competition_assessments
    ADD CONSTRAINT cluster_competition_assessments_competition_run_id_fkey FOREIGN KEY (competition_run_id) REFERENCES public.competition_runs(id) ON DELETE CASCADE;


--
-- Name: cluster_competition_assessments cluster_competition_assessments_viability_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_competition_assessments
    ADD CONSTRAINT cluster_competition_assessments_viability_run_id_fkey FOREIGN KEY (viability_run_id) REFERENCES public.viability_runs(id) ON DELETE CASCADE;


--
-- Name: cluster_competition cluster_competition_cluster_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_competition
    ADD CONSTRAINT cluster_competition_cluster_id_fkey FOREIGN KEY (cluster_id) REFERENCES public.pain_clusters(id);


--
-- Name: cluster_competitors cluster_competitors_cluster_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_competitors
    ADD CONSTRAINT cluster_competitors_cluster_id_fkey FOREIGN KEY (cluster_id) REFERENCES public.pain_clusters(id) ON DELETE CASCADE;


--
-- Name: cluster_competitors cluster_competitors_competition_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_competitors
    ADD CONSTRAINT cluster_competitors_competition_run_id_fkey FOREIGN KEY (competition_run_id) REFERENCES public.competition_runs(id) ON DELETE CASCADE;


--
-- Name: cluster_domain_groups cluster_domain_groups_competition_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_domain_groups
    ADD CONSTRAINT cluster_domain_groups_competition_run_id_fkey FOREIGN KEY (competition_run_id) REFERENCES public.competition_runs(id) ON DELETE CASCADE;


--
-- Name: cluster_domain_groups cluster_domain_groups_representative_cluster_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_domain_groups
    ADD CONSTRAINT cluster_domain_groups_representative_cluster_id_fkey FOREIGN KEY (representative_cluster_id) REFERENCES public.pain_clusters(id) ON DELETE CASCADE;


--
-- Name: cluster_domain_members cluster_domain_members_cluster_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_domain_members
    ADD CONSTRAINT cluster_domain_members_cluster_id_fkey FOREIGN KEY (cluster_id) REFERENCES public.pain_clusters(id) ON DELETE CASCADE;


--
-- Name: cluster_domain_members cluster_domain_members_competition_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_domain_members
    ADD CONSTRAINT cluster_domain_members_competition_run_id_fkey FOREIGN KEY (competition_run_id) REFERENCES public.competition_runs(id) ON DELETE CASCADE;


--
-- Name: cluster_lineage cluster_lineage_child_cluster_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_lineage
    ADD CONSTRAINT cluster_lineage_child_cluster_fkey FOREIGN KEY (child_cluster) REFERENCES public.pain_clusters(id) ON DELETE CASCADE;


--
-- Name: cluster_lineage cluster_lineage_child_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_lineage
    ADD CONSTRAINT cluster_lineage_child_run_id_fkey FOREIGN KEY (child_run_id) REFERENCES public.analysis_runs(id) ON DELETE CASCADE;


--
-- Name: cluster_lineage cluster_lineage_parent_cluster_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_lineage
    ADD CONSTRAINT cluster_lineage_parent_cluster_fkey FOREIGN KEY (parent_cluster) REFERENCES public.pain_clusters(id) ON DELETE CASCADE;


--
-- Name: cluster_lineage cluster_lineage_parent_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_lineage
    ADD CONSTRAINT cluster_lineage_parent_run_id_fkey FOREIGN KEY (parent_run_id) REFERENCES public.analysis_runs(id) ON DELETE CASCADE;


--
-- Name: cluster_quality_metrics cluster_quality_metrics_cluster_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_quality_metrics
    ADD CONSTRAINT cluster_quality_metrics_cluster_id_fkey FOREIGN KEY (cluster_id) REFERENCES public.pain_clusters(id) ON DELETE CASCADE;


--
-- Name: cluster_quality_metrics cluster_quality_metrics_cluster_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_quality_metrics
    ADD CONSTRAINT cluster_quality_metrics_cluster_run_id_fkey FOREIGN KEY (cluster_run_id) REFERENCES public.analysis_runs(id) ON DELETE CASCADE;


--
-- Name: comments comments_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT comments_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(id);


--
-- Name: competition_runs competition_runs_cluster_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competition_runs
    ADD CONSTRAINT competition_runs_cluster_run_id_fkey FOREIGN KEY (cluster_run_id) REFERENCES public.analysis_runs(id) ON DELETE CASCADE;


--
-- Name: competition_runs competition_runs_viability_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competition_runs
    ADD CONSTRAINT competition_runs_viability_run_id_fkey FOREIGN KEY (viability_run_id) REFERENCES public.viability_runs(id) ON DELETE CASCADE;


--
-- Name: corpus_burst_snapshot corpus_burst_snapshot_cluster_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.corpus_burst_snapshot
    ADD CONSTRAINT corpus_burst_snapshot_cluster_run_id_fkey FOREIGN KEY (cluster_run_id) REFERENCES public.analysis_runs(id) ON DELETE CASCADE;


--
-- Name: emerging_opportunities emerging_opportunities_cluster_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.emerging_opportunities
    ADD CONSTRAINT emerging_opportunities_cluster_run_id_fkey FOREIGN KEY (cluster_run_id) REFERENCES public.analysis_runs(id) ON DELETE CASCADE;


--
-- Name: emerging_opportunities emerging_opportunities_emerging_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.emerging_opportunities
    ADD CONSTRAINT emerging_opportunities_emerging_run_id_fkey FOREIGN KEY (emerging_run_id) REFERENCES public.emerging_opportunity_runs(id) ON DELETE CASCADE;


--
-- Name: emerging_opportunity_members emerging_opportunity_members_emerging_opportunity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.emerging_opportunity_members
    ADD CONSTRAINT emerging_opportunity_members_emerging_opportunity_id_fkey FOREIGN KEY (emerging_opportunity_id) REFERENCES public.emerging_opportunities(id) ON DELETE CASCADE;


--
-- Name: emerging_opportunity_members emerging_opportunity_members_pain_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.emerging_opportunity_members
    ADD CONSTRAINT emerging_opportunity_members_pain_id_fkey FOREIGN KEY (pain_id) REFERENCES public.pain_objects(id) ON DELETE CASCADE;


--
-- Name: emerging_opportunity_runs emerging_opportunity_runs_cluster_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.emerging_opportunity_runs
    ADD CONSTRAINT emerging_opportunity_runs_cluster_run_id_fkey FOREIGN KEY (cluster_run_id) REFERENCES public.analysis_runs(id) ON DELETE CASCADE;


--
-- Name: pain_cluster_members pain_cluster_members_cluster_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_cluster_members
    ADD CONSTRAINT pain_cluster_members_cluster_id_fkey FOREIGN KEY (cluster_id) REFERENCES public.pain_clusters(id) ON DELETE CASCADE;


--
-- Name: pain_cluster_members pain_cluster_members_pain_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_cluster_members
    ADD CONSTRAINT pain_cluster_members_pain_id_fkey FOREIGN KEY (pain_id) REFERENCES public.pain_objects(id) ON DELETE CASCADE;


--
-- Name: pain_clusters pain_clusters_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_clusters
    ADD CONSTRAINT pain_clusters_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.analysis_runs(id);


--
-- Name: pain_extraction_attempts pain_extraction_attempts_signal_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_extraction_attempts
    ADD CONSTRAINT pain_extraction_attempts_signal_event_id_fkey FOREIGN KEY (signal_event_id) REFERENCES public.signal_events(id) ON DELETE CASCADE;


--
-- Name: pain_objects pain_objects_comment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_objects
    ADD CONSTRAINT pain_objects_comment_id_fkey FOREIGN KEY (comment_id) REFERENCES public.comments(id) ON DELETE SET NULL;


--
-- Name: pain_objects pain_objects_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_objects
    ADD CONSTRAINT pain_objects_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(id) ON DELETE CASCADE;


--
-- Name: pain_objects pain_objects_signal_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_objects
    ADD CONSTRAINT pain_objects_signal_event_id_fkey FOREIGN KEY (signal_event_id) REFERENCES public.signal_events(id);


--
-- Name: pain_volume_daily pain_volume_daily_cluster_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_volume_daily
    ADD CONSTRAINT pain_volume_daily_cluster_id_fkey FOREIGN KEY (cluster_id) REFERENCES public.pain_clusters(id) ON DELETE CASCADE;


--
-- Name: pain_volume_daily pain_volume_daily_cluster_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_volume_daily
    ADD CONSTRAINT pain_volume_daily_cluster_run_id_fkey FOREIGN KEY (cluster_run_id) REFERENCES public.analysis_runs(id) ON DELETE CASCADE;


--
-- Name: pain_volume_daily_corpus pain_volume_daily_corpus_cluster_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_volume_daily_corpus
    ADD CONSTRAINT pain_volume_daily_corpus_cluster_run_id_fkey FOREIGN KEY (cluster_run_id) REFERENCES public.analysis_runs(id) ON DELETE CASCADE;


--
-- Name: pain_volume_daily_subreddit pain_volume_daily_subreddit_cluster_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_volume_daily_subreddit
    ADD CONSTRAINT pain_volume_daily_subreddit_cluster_id_fkey FOREIGN KEY (cluster_id) REFERENCES public.pain_clusters(id) ON DELETE CASCADE;


--
-- Name: pain_volume_daily_subreddit pain_volume_daily_subreddit_cluster_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_volume_daily_subreddit
    ADD CONSTRAINT pain_volume_daily_subreddit_cluster_run_id_fkey FOREIGN KEY (cluster_run_id) REFERENCES public.analysis_runs(id) ON DELETE CASCADE;


--
-- Name: pain_volume_weekly pain_volume_weekly_cluster_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_volume_weekly
    ADD CONSTRAINT pain_volume_weekly_cluster_id_fkey FOREIGN KEY (cluster_id) REFERENCES public.pain_clusters(id) ON DELETE CASCADE;


--
-- Name: pain_volume_weekly pain_volume_weekly_cluster_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pain_volume_weekly
    ADD CONSTRAINT pain_volume_weekly_cluster_run_id_fkey FOREIGN KEY (cluster_run_id) REFERENCES public.analysis_runs(id) ON DELETE CASCADE;


--
-- Name: pipeline_job_events pipeline_job_events_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_job_events
    ADD CONSTRAINT pipeline_job_events_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.pipeline_jobs(id) ON DELETE CASCADE;


--
-- Name: pipeline_job_steps pipeline_job_steps_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_job_steps
    ADD CONSTRAINT pipeline_job_steps_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.pipeline_jobs(id) ON DELETE CASCADE;


--
-- Name: pipeline_worker_heartbeats pipeline_worker_heartbeats_current_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_worker_heartbeats
    ADD CONSTRAINT pipeline_worker_heartbeats_current_job_id_fkey FOREIGN KEY (current_job_id) REFERENCES public.pipeline_jobs(id) ON DELETE SET NULL;


--
-- Name: posts posts_subreddit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posts
    ADD CONSTRAINT posts_subreddit_id_fkey FOREIGN KEY (subreddit_id) REFERENCES public.subreddits(id);


--
-- Name: processed_posts processed_posts_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processed_posts
    ADD CONSTRAINT processed_posts_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(id);


--
-- Name: processed_signal_events processed_signal_events_signal_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processed_signal_events
    ADD CONSTRAINT processed_signal_events_signal_event_id_fkey FOREIGN KEY (signal_event_id) REFERENCES public.signal_events(id) ON DELETE CASCADE;


--
-- Name: reddit_backfill_chunks reddit_backfill_chunks_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reddit_backfill_chunks
    ADD CONSTRAINT reddit_backfill_chunks_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.reddit_backfill_jobs(id) ON DELETE CASCADE;


--
-- Name: report_stage_outputs report_stage_outputs_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_stage_outputs
    ADD CONSTRAINT report_stage_outputs_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.report_jobs(id) ON DELETE CASCADE;


--
-- Name: reports reports_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reports
    ADD CONSTRAINT reports_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.report_jobs(id) ON DELETE CASCADE;


--
-- Name: signal_events signal_events_parent_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_events
    ADD CONSTRAINT signal_events_parent_event_id_fkey FOREIGN KEY (parent_event_id) REFERENCES public.signal_events(id) ON DELETE SET NULL;


--
-- Name: source_backfill_chunks source_backfill_chunks_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_backfill_chunks
    ADD CONSTRAINT source_backfill_chunks_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.source_backfill_jobs(id) ON DELETE CASCADE;


--
-- Name: viability_runs viability_runs_cluster_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.viability_runs
    ADD CONSTRAINT viability_runs_cluster_run_id_fkey FOREIGN KEY (cluster_run_id) REFERENCES public.analysis_runs(id) ON DELETE CASCADE;


--
-- Name: viability_scores viability_scores_cluster_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.viability_scores
    ADD CONSTRAINT viability_scores_cluster_id_fkey FOREIGN KEY (cluster_id) REFERENCES public.pain_clusters(id) ON DELETE CASCADE;


--
-- Name: viability_scores viability_scores_viability_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.viability_scores
    ADD CONSTRAINT viability_scores_viability_run_id_fkey FOREIGN KEY (viability_run_id) REFERENCES public.viability_runs(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--


