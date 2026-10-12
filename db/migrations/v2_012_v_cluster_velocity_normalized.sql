-- v2_012: extend v_cluster_velocity with velocity_4w_ratio_normalized.
--
-- The normalized ratio divides each cluster's raw 4w/prior-4w by the corpus-wide
-- 4w/prior-4w ratio for the same run. Reads:
--   1.0 = cluster moving with the corpus
--   > 1.0 = cluster growing faster than the corpus (or declining slower)
--   < 1.0 = cluster declining faster than the corpus (or growing slower)
--
-- Removes the systematic downward bias that the raw ratio inherits when the
-- corpus has a volume swing (e.g., the March 2026 surge that puts a 35K-week
-- numerator over a 101K-week denominator at the corpus level, dragging every
-- cluster's raw ratio toward 0.35 by default).
--
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
        regr_slope(pain_count,
                   EXTRACT(EPOCH FROM week_start) / 604800.0) AS slope_per_week,
        COUNT(*)         AS weeks_with_data,
        SUM(pain_count)  AS total_pain
    FROM pain_volume_weekly
    GROUP BY cluster_run_id, cluster_id
),
corpus_ratio AS (
    SELECT
        cluster_run_id,
        SUM(pain_count) FILTER (
            WHERE week_start >= CURRENT_DATE - INTERVAL '4 weeks'
        ) AS corpus_last_4w,
        SUM(pain_count) FILTER (
            WHERE week_start >= CURRENT_DATE - INTERVAL '8 weeks'
              AND week_start <  CURRENT_DATE - INTERVAL '4 weeks'
        ) AS corpus_prior_4w
    FROM pain_volume_weekly
    GROUP BY cluster_run_id
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
  'Per-cluster temporal signals: 4w/prior-4w ratio (raw + corpus-normalized,
   NULL when sparse), linreg slope across all weeks with data,
   raw last_4w / prior_4w / weeks_with_data / total_pain.';
