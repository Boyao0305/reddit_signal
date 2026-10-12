-- v2 migration 005 (REVISED) — temporal aggregates + candidate_promotion_log
-- FK targets analysis_runs (the underlying table for clustering_runs view)

BEGIN;

CREATE TABLE IF NOT EXISTS pain_volume_weekly (
    cluster_run_id  INT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    cluster_id      INT NOT NULL REFERENCES pain_clusters(id) ON DELETE CASCADE,
    week_start      DATE NOT NULL,
    audience_type   VARCHAR(16) NOT NULL DEFAULT 'unknown',
    function_tag    VARCHAR(64) NOT NULL DEFAULT 'unknown',
    pain_count      INT NOT NULL,
    share_global    DOUBLE PRECISION,
    share_field     DOUBLE PRECISION,

    PRIMARY KEY (cluster_run_id, cluster_id, week_start, audience_type, function_tag)
);

CREATE INDEX IF NOT EXISTS ix_pvw_week ON pain_volume_weekly(week_start);

CREATE TABLE IF NOT EXISTS candidate_promotion_log (
    id              SERIAL PRIMARY KEY,
    cluster_run_id  INT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    cluster_id      INT NOT NULL REFERENCES pain_clusters(id) ON DELETE CASCADE,
    promoted_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metrics_snapshot   JSONB NOT NULL,
    threshold_snapshot JSONB NOT NULL,
    direction       VARCHAR(16) NOT NULL CHECK (direction IN ('promoted', 'demoted'))
);

CREATE INDEX IF NOT EXISTS ix_promotion_run    ON candidate_promotion_log(cluster_run_id);
CREATE INDEX IF NOT EXISTS ix_promotion_recent ON candidate_promotion_log(promoted_at DESC);

COMMIT;
