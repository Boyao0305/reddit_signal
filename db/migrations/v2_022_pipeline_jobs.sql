-- v2_022: PostgreSQL-backed daily/monthly worker queue.
--
-- Zeabur web/API writes jobs here. The EC2 worker claims jobs with
-- FOR UPDATE SKIP LOCKED and executes the existing daily/monthly scripts.

CREATE TABLE IF NOT EXISTS pipeline_jobs (
    id BIGSERIAL PRIMARY KEY,
    job_type TEXT NOT NULL CHECK (job_type IN ('daily', 'monthly')),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    requested_by TEXT,
    request_source TEXT NOT NULL DEFAULT 'dashboard',
    run_after TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    worker_id TEXT,
    exit_code INTEGER,
    error TEXT,
    log_tail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_pipeline_jobs_claim
    ON pipeline_jobs (status, run_after, id)
    WHERE status = 'queued';

CREATE INDEX IF NOT EXISTS ix_pipeline_jobs_recent
    ON pipeline_jobs (job_type, created_at DESC);

CREATE TABLE IF NOT EXISTS pipeline_worker_heartbeats (
    worker_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL DEFAULT 'ec2-worker',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    current_job_id BIGINT REFERENCES pipeline_jobs(id) ON DELETE SET NULL,
    version TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION touch_pipeline_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_pipeline_jobs_updated_at ON pipeline_jobs;
CREATE TRIGGER trg_pipeline_jobs_updated_at
BEFORE UPDATE ON pipeline_jobs
FOR EACH ROW
EXECUTE FUNCTION touch_pipeline_updated_at();

DROP TRIGGER IF EXISTS trg_pipeline_worker_heartbeats_updated_at ON pipeline_worker_heartbeats;
CREATE TRIGGER trg_pipeline_worker_heartbeats_updated_at
BEFORE UPDATE ON pipeline_worker_heartbeats
FOR EACH ROW
EXECUTE FUNCTION touch_pipeline_updated_at();
