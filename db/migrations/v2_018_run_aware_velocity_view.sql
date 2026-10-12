-- v2_018: make v_cluster_velocity use the run's own time anchor.
--
-- Live runs use the current week. Historical/building/failed snapshots use
-- analysis_runs.window_end, so recent-trend metrics do not drift as wall-clock
-- time moves on after a run was created.

CREATE OR REPLACE VIEW v_cluster_velocity AS
WITH run_anchor AS (
    SELECT
        id AS cluster_run_id,
        CASE
            WHEN status = 'live' OR window_end IS NULL
                THEN date_trunc('week', CURRENT_DATE)::date
            ELSE date_trunc('week', window_end)::date
        END AS anchor_week
    FROM analysis_runs
),
per_cluster AS (
    SELECT
        pvw.cluster_run_id,
        pvw.cluster_id,
        SUM(pvw.pain_count) FILTER (
            WHERE pvw.week_start >= ra.anchor_week - INTERVAL '4 weeks'
              AND pvw.week_start <  ra.anchor_week
        ) AS last_4w,
        SUM(pvw.pain_count) FILTER (
            WHERE pvw.week_start >= ra.anchor_week - INTERVAL '8 weeks'
              AND pvw.week_start <  ra.anchor_week - INTERVAL '4 weeks'
        ) AS prior_4w,
        regr_slope(
            pvw.pain_count,
            EXTRACT(EPOCH FROM pvw.week_start) / 604800.0
        ) AS slope_per_week,
        COUNT(*)              AS weeks_with_data,
        SUM(pvw.pain_count)   AS total_pain
    FROM pain_volume_weekly pvw
    JOIN run_anchor ra ON ra.cluster_run_id = pvw.cluster_run_id
    GROUP BY pvw.cluster_run_id, pvw.cluster_id
),
corpus_ratio AS (
    SELECT
        pvw.cluster_run_id,
        SUM(pvw.pain_count) FILTER (
            WHERE pvw.week_start >= ra.anchor_week - INTERVAL '4 weeks'
              AND pvw.week_start <  ra.anchor_week
        ) AS corpus_last_4w,
        SUM(pvw.pain_count) FILTER (
            WHERE pvw.week_start >= ra.anchor_week - INTERVAL '8 weeks'
              AND pvw.week_start <  ra.anchor_week - INTERVAL '4 weeks'
        ) AS corpus_prior_4w
    FROM pain_volume_weekly pvw
    JOIN run_anchor ra ON ra.cluster_run_id = pvw.cluster_run_id
    GROUP BY pvw.cluster_run_id
)
SELECT
    pc.cluster_run_id,
    pc.cluster_id,
    pc.last_4w                                                          AS pain_count_last_4w,
    pc.prior_4w                                                         AS pain_count_prior_4w,
    pc.weeks_with_data,
    pc.total_pain,
    ROUND(pc.slope_per_week::numeric, 3)                                AS slope_per_week,
    CASE
        WHEN COALESCE(pc.prior_4w, 0) < 3 OR COALESCE(pc.last_4w, 0) < 3
            THEN NULL
        ELSE ROUND((pc.last_4w::float / pc.prior_4w)::numeric, 3)
    END                                                                 AS velocity_4w_ratio,
    CASE
        WHEN COALESCE(pc.prior_4w, 0) < 3 OR COALESCE(pc.last_4w, 0) < 3
            THEN NULL
        WHEN COALESCE(cr.corpus_prior_4w, 0) = 0 OR cr.corpus_last_4w IS NULL
            THEN NULL
        ELSE ROUND(
            ((pc.last_4w::float / pc.prior_4w)
             / NULLIF(cr.corpus_last_4w::float / NULLIF(cr.corpus_prior_4w, 0), 0))::numeric,
            3
        )
    END                                                                 AS velocity_4w_ratio_normalized
FROM per_cluster pc
LEFT JOIN corpus_ratio cr ON cr.cluster_run_id = pc.cluster_run_id;

ALTER VIEW v_cluster_velocity OWNER TO llmanalyse;

COMMENT ON VIEW v_cluster_velocity IS
  'Per-cluster temporal signals anchored by analysis_runs status/window_end:
   live runs use the current week; historical snapshots use their own window_end.
   4w windows exclude the anchor week because it is partial for live runs and can
   be partial for rolling snapshots.';
