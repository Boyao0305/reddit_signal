-- v2_024: Per-stage execution tracking for daily/monthly pipeline jobs.
--
-- Records each major phase inside b_ops_1_daily.sh / b_ops_3_monthly.sh so
-- operators can see which stage failed and why, without reading full logs.

CREATE TABLE IF NOT EXISTS pipeline_job_steps (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES pipeline_jobs(id) ON DELETE CASCADE,
    stage_name TEXT NOT NULL,
    stage_order INT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'skipped')),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    exit_code INTEGER,
    error TEXT,
    log_tail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (job_id, stage_name)
);

CREATE INDEX IF NOT EXISTS ix_pipeline_job_steps_job
    ON pipeline_job_steps (job_id, stage_order);

CREATE INDEX IF NOT EXISTS ix_pipeline_job_steps_status
    ON pipeline_job_steps (status, created_at DESC);

DROP TRIGGER IF EXISTS trg_pipeline_job_steps_updated_at ON pipeline_job_steps;
CREATE TRIGGER trg_pipeline_job_steps_updated_at
BEFORE UPDATE ON pipeline_job_steps
FOR EACH ROW
EXECUTE FUNCTION touch_pipeline_updated_at();
