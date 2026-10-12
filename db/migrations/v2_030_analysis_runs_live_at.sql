-- v2_030: record when an analysis run became the production live run.
--
-- This lets the Dashboard filter by "entered live" date. Existing historical
-- rows cannot recover their exact promotion timestamp, so the backfill uses the
-- best available lifecycle timestamp and is documented as approximate.

BEGIN;

ALTER TABLE analysis_runs
    ADD COLUMN IF NOT EXISTS live_at TIMESTAMP WITH TIME ZONE;

UPDATE analysis_runs
SET live_at = COALESCE(finished_at, started_at, created_at, window_end)
WHERE live_at IS NULL
  AND COALESCE(status, 'historical') IN ('live', 'historical')
  AND COALESCE(is_legacy, FALSE) = FALSE;

CREATE INDEX IF NOT EXISTS ix_analysis_runs_live_at
    ON analysis_runs(live_at DESC, id DESC)
    WHERE live_at IS NOT NULL;

COMMENT ON COLUMN analysis_runs.live_at IS
    'Timestamp when the run became the production live run. Backfilled historical values are approximate from finished_at/started_at/created_at/window_end.';

COMMIT;
