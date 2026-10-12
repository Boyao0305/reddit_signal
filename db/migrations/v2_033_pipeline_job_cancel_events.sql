-- v2_033: Pipeline job cancellation and detailed progress events.
--
-- Adds cooperative cancellation states and an append-only event stream so the
-- dashboard can show what long-running jobs are doing between coarse stages.

ALTER TABLE pipeline_jobs
    DROP CONSTRAINT IF EXISTS pipeline_jobs_status_check;

ALTER TABLE pipeline_jobs
    ADD CONSTRAINT pipeline_jobs_status_check
    CHECK (status IN (
        'queued',
        'running',
        'cancel_requested',
        'cancelled',
        'succeeded',
        'failed'
    ));

ALTER TABLE pipeline_jobs
    ADD COLUMN IF NOT EXISTS cancel_requested_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS cancel_requested_by TEXT;

ALTER TABLE pipeline_job_steps
    DROP CONSTRAINT IF EXISTS pipeline_job_steps_status_check;

ALTER TABLE pipeline_job_steps
    ADD CONSTRAINT pipeline_job_steps_status_check
    CHECK (status IN (
        'pending',
        'running',
        'succeeded',
        'failed',
        'skipped',
        'cancelled'
    ));

CREATE TABLE IF NOT EXISTS pipeline_job_events (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES pipeline_jobs(id) ON DELETE CASCADE,
    stage_name TEXT,
    event_type TEXT NOT NULL DEFAULT 'log',
    message TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_pipeline_job_events_job
    ON pipeline_job_events (job_id, created_at ASC, id ASC);

CREATE INDEX IF NOT EXISTS ix_pipeline_job_events_recent
    ON pipeline_job_events (created_at DESC, id DESC);
