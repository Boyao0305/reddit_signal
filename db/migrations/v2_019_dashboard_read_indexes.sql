-- v2_019: dashboard read-path indexes.
--
-- Intentionally no BEGIN/COMMIT: CREATE INDEX CONCURRENTLY cannot run inside
-- a transaction block. This keeps production writes available while indexes
-- are built.

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_pvw_run_week_cluster
    ON pain_volume_weekly (cluster_run_id, week_start, cluster_id);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_pcm_cluster_pain
    ON pain_cluster_members (cluster_id, pain_id);

ANALYZE pain_volume_weekly;
ANALYZE pain_cluster_members;
