-- v2_014: add status column to analysis_runs to distinguish live vs historical vs legacy runs.
--
-- Why: the dashboard needs to know which run is "the live one" so it can warn the user
-- when they're looking at a historical snapshot where velocity numbers no longer reflect
-- recent activity (because daily centroid assignment has moved on to a newer run).
-- Without this column the frontend would have to infer "live = most recent id," which is
-- fragile if a backtest or admin task ever creates a non-active run with a recent
-- `window_end`.
--
-- Backfill rule (applied here, deterministically):
--   - is_legacy = TRUE                                        -> status = 'legacy'
--   - is_legacy = FALSE, most-recent id by ORDER BY id DESC   -> status = 'live'
--   - is_legacy = FALSE, all others                           -> status = 'historical'
--
-- Going forward, the monthly orchestrator is responsible for atomically transitioning the
-- prior 'live' run to 'historical' when it creates and finalises a new run. That logic
-- does NOT exist yet — it is captured as a future task in
-- `05_potential_implementations/next_tasks/cluster_run_continuation.md`.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS + a DO block for the constraint + UPDATEs that
-- check current status.

ALTER TABLE analysis_runs
    ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'historical';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'analysis_runs_status_check'
          AND conrelid = 'analysis_runs'::regclass
    ) THEN
        ALTER TABLE analysis_runs
            ADD CONSTRAINT analysis_runs_status_check
            CHECK (status IN ('live', 'historical', 'legacy'));
    END IF;
END $$;

-- Backfill: legacy rows
UPDATE analysis_runs
SET status = 'legacy'
WHERE is_legacy = TRUE AND status <> 'legacy';

-- Backfill: mark most-recent non-legacy run as 'live'
WITH most_recent AS (
    SELECT id
    FROM analysis_runs
    WHERE is_legacy = FALSE
    ORDER BY id DESC
    LIMIT 1
)
UPDATE analysis_runs
SET status = 'live'
WHERE id IN (SELECT id FROM most_recent)
  AND status <> 'live';

-- Defensive: ensure only one live row exists. If a rerun finds multiple, demote the
-- older ones to 'historical'. (Shouldn't happen on first apply.)
UPDATE analysis_runs
SET status = 'historical'
WHERE is_legacy = FALSE
  AND status = 'live'
  AND id NOT IN (
      SELECT id
      FROM analysis_runs
      WHERE is_legacy = FALSE
      ORDER BY id DESC
      LIMIT 1
  );

COMMENT ON COLUMN analysis_runs.status IS
    'Lifecycle state: live = current canonical run; historical = past non-legacy snapshot; legacy = v1.
     Orchestrator transitions previous live to historical when creating a new run.
     See 05_potential_implementations/next_tasks/cluster_run_continuation.md for the broader story.';

-- Verification (manual, after applying):
--   SELECT status, COUNT(*) FROM analysis_runs GROUP BY status;
--   -- expected: exactly one 'live' (currently id=10), N 'legacy' (matching is_legacy=TRUE
--   -- count), remainder 'historical'.
