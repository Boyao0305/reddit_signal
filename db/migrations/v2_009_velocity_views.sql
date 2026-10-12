-- B-prep.7b: velocity view with both ratio and slope (linear regression)
-- Plus raw last_4w_count for size context.
-- Idempotent.

DROP VIEW IF EXISTS v_cluster_velocity;
CREATE VIEW v_cluster_velocity AS
WITH per_cluster AS (
    SELECT
        cluster_run_id,
        cluster_id,
        SUM(pain_count) FILTER (
            WHERE week_start >= CURRENT_DATE - INTERVAL '4 weeks'
        ) AS last_4w,
        SUM(pain_count) FILTER (
            WHERE week_start >= CURRENT_DATE - INTERVAL '8 weeks'
              AND week_start <  CURRENT_DATE - INTERVAL '4 weeks'
        ) AS prior_4w,
        -- Linear regression of pain_count on week index across the entire window.
        -- regr_slope returns coefficient = pains added per week.
        regr_slope(pain_count,
                   EXTRACT(EPOCH FROM week_start) / 604800.0) AS slope_per_week,
        COUNT(*)         AS weeks_with_data,
        SUM(pain_count)  AS total_pain
    FROM pain_volume_weekly
    GROUP BY cluster_run_id, cluster_id
)
SELECT
    cluster_run_id,
    cluster_id,
    last_4w   AS pain_count_last_4w,
    prior_4w  AS pain_count_prior_4w,
    weeks_with_data,
    total_pain,
    ROUND(slope_per_week::numeric, 3) AS slope_per_week,
    CASE
        WHEN COALESCE(prior_4w,0) < 3 OR COALESCE(last_4w,0) < 3 THEN NULL
        ELSE ROUND((last_4w::float / prior_4w)::numeric, 3)
    END AS velocity_4w_ratio
FROM per_cluster;

ALTER VIEW v_cluster_velocity OWNER TO llmanalyse;

COMMENT ON VIEW v_cluster_velocity IS
  'Per-cluster temporal signals: 4w/prior-4w ratio (NULL when sparse),
   linreg slope across all weeks with data, raw last_4w_count for size.';
