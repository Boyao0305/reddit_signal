-- v2_046: Make source backfill jobs fetch-only by default.
--
-- Historical fetch no longer performs projection in the same job by default.
-- Project-only jobs are queued explicitly after a fetch job completes.

ALTER TABLE source_backfill_jobs
    ALTER COLUMN mode SET DEFAULT 'fetch';

ALTER TABLE source_backfill_chunks
    ALTER COLUMN mode SET DEFAULT 'fetch';
