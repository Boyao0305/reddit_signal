-- v2_045: Add cooperative cancellation and richer status counts for source backfill jobs.

ALTER TABLE source_backfill_chunks
    DROP CONSTRAINT IF EXISTS source_backfill_chunks_status_check;

ALTER TABLE source_backfill_chunks
    ADD CONSTRAINT source_backfill_chunks_status_check
    CHECK (status IN (
        'queued',
        'running',
        'succeeded',
        'failed',
        'skipped',
        'empty',
        'cancelled'
    ));

ALTER TABLE source_backfill_jobs
    ADD COLUMN IF NOT EXISTS cancelled_chunks INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cancel_requested_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS cancel_requested_by TEXT;
