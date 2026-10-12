-- v2 migration 003 (REVISED) — extend analysis_runs in place, alias as clustering_runs
-- Decision: existing pain_clusters.run_id FK → analysis_runs has 7 legacy runs and 12,298 clusters.
-- Rather than create a parallel table and migrate FKs, we extend analysis_runs with the v2
-- columns and create a VIEW `clustering_runs` so new v2 code can use the planned name.
-- This preserves all existing FKs (cluster_competition.analysis_run_id, pain_clusters.run_id).

BEGIN;

-- Extend analysis_runs with v2 columns
ALTER TABLE analysis_runs
    ADD COLUMN IF NOT EXISTS label                TEXT,
    ADD COLUMN IF NOT EXISTS pain_filter          JSONB,
    ADD COLUMN IF NOT EXISTS is_legacy            BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS started_at           TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS finished_at          TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS candidate_thresholds JSONB;

-- Backfill started_at from existing created_at for legacy rows
UPDATE analysis_runs SET started_at = created_at WHERE started_at IS NULL;

-- Tag the 7 existing rows as legacy (they pre-date v2)
UPDATE analysis_runs SET is_legacy = TRUE, label = COALESCE(label, 'v1-' || resolution)
WHERE is_legacy = FALSE;  -- only NEW v2 runs will leave is_legacy=FALSE

-- Create a writable VIEW `clustering_runs` so v2 code can use the planned name
DROP VIEW IF EXISTS clustering_runs;
CREATE VIEW clustering_runs AS
SELECT
    id,
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
    -- v1 fields kept for back-compat readers
    window_start, window_end, resolution, created_at
FROM analysis_runs;

-- Allow inserts through the view via INSTEAD OF rule
CREATE OR REPLACE RULE clustering_runs_insert AS
    ON INSERT TO clustering_runs DO INSTEAD
    INSERT INTO analysis_runs (
        label, params, pain_filter, is_legacy,
        cluster_count, noise_count, total_points,
        started_at, finished_at, candidate_thresholds,
        window_start, window_end, resolution
    ) VALUES (
        NEW.label,
        COALESCE(NEW.params, '{}'::jsonb),
        NEW.pain_filter,
        COALESCE(NEW.is_legacy, FALSE),
        NEW.cluster_count, NEW.noise_count, NEW.total_points,
        COALESCE(NEW.started_at, NOW()),
        NEW.finished_at,
        NEW.candidate_thresholds,
        -- For v2 runs that don't care about windows, default to NOW():
        COALESCE(NEW.window_start, NOW()),
        COALESCE(NEW.window_end,   NOW()),
        COALESCE(NEW.resolution, 'v2')
    ) RETURNING
        id, label, params, pain_filter, is_legacy,
        cluster_count, noise_count, total_points,
        started_at, finished_at, candidate_thresholds,
        window_start, window_end, resolution, created_at;

CREATE OR REPLACE RULE clustering_runs_update AS
    ON UPDATE TO clustering_runs DO INSTEAD
    UPDATE analysis_runs SET
        label = NEW.label,
        params = NEW.params,
        pain_filter = NEW.pain_filter,
        is_legacy = NEW.is_legacy,
        cluster_count = NEW.cluster_count,
        noise_count = NEW.noise_count,
        total_points = NEW.total_points,
        finished_at = NEW.finished_at,
        candidate_thresholds = NEW.candidate_thresholds
    WHERE id = OLD.id;

-- Extend pain_clusters with v2 fields
ALTER TABLE pain_clusters
    ADD COLUMN IF NOT EXISTS is_candidate    BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS volume_4w       INT,
    ADD COLUMN IF NOT EXISTS volume_prior_4w INT;

CREATE INDEX IF NOT EXISTS ix_pain_clusters_run ON pain_clusters(run_id);
CREATE INDEX IF NOT EXISTS ix_pain_clusters_candidate
    ON pain_clusters(run_id, is_candidate) WHERE is_candidate;

COMMIT;
