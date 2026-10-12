-- v2_011: backfill share_global / share_field on pain_volume_weekly.
--
-- The columns existed in v2_005 schema but were never populated because
-- the aggregator (src/temporal/aggregate_weekly.py) only writes pain_count.
-- This is the one-shot backfill for run 10. Future runs are kept in sync by
-- the aggregator edit landing in the same component (phase2c_dashboard_v2).
--
-- share_global = pain_count / sum(pain_count) over the same week, across all clusters in the run.
-- share_field  = pain_count / sum(pain_count) over the same week, within the same audience_type.
--
-- Idempotent: WHERE clause scopes to one run; rerunning produces identical values.
-- Replace :run_id at apply time, e.g. `psql -v run_id=10 -f migrations/v2_011_share_global_backfill.sql`.

WITH week_audience_totals AS (
    SELECT cluster_run_id, week_start, audience_type, SUM(pain_count) AS aud_total
    FROM pain_volume_weekly
    WHERE cluster_run_id = :run_id
    GROUP BY cluster_run_id, week_start, audience_type
),
week_global_totals AS (
    SELECT cluster_run_id, week_start, SUM(pain_count) AS global_total
    FROM pain_volume_weekly
    WHERE cluster_run_id = :run_id
    GROUP BY cluster_run_id, week_start
)
UPDATE pain_volume_weekly pvw
SET share_global = pvw.pain_count::float / NULLIF(wg.global_total, 0),
    share_field  = pvw.pain_count::float / NULLIF(wt.aud_total, 0)
FROM week_global_totals wg, week_audience_totals wt
WHERE pvw.cluster_run_id = :run_id
  AND wg.cluster_run_id  = pvw.cluster_run_id AND wg.week_start = pvw.week_start
  AND wt.cluster_run_id  = pvw.cluster_run_id AND wt.week_start = pvw.week_start
  AND wt.audience_type   = pvw.audience_type;

-- Verification query (not part of the migration, run manually after):
--   SELECT COUNT(*) FILTER (WHERE share_global IS NULL) AS still_null,
--          COUNT(*)                                     AS total,
--          ROUND(MIN(share_global)::numeric, 6) AS min_share,
--          ROUND(MAX(share_global)::numeric, 6) AS max_share
--   FROM pain_volume_weekly WHERE cluster_run_id = :run_id;
