-- v2_017: extend analysis_runs lifecycle states for monthly promotion safety.
--
-- Adds building/failed so monthly reclusters can create a run without exposing
-- it to daily assignment or dashboard defaults until every downstream phase has
-- completed successfully.

BEGIN;

ALTER TABLE analysis_runs
    ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'historical';

ALTER TABLE analysis_runs
    DROP CONSTRAINT IF EXISTS analysis_runs_status_check;

ALTER TABLE analysis_runs
    ADD CONSTRAINT analysis_runs_status_check
    CHECK (status IN ('live', 'historical', 'legacy', 'building', 'failed'));

COMMIT;
