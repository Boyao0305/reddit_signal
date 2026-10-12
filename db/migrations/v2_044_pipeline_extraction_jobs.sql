-- v2_044: Allow manual extraction jobs in the existing EC2 pipeline queue.
--
-- Historical source backfills write raw rows and project them to signal_events.
-- Operators can then enqueue a bounded extraction job from the dashboard; EC2
-- runs the existing src.pain_extraction ingest path so Zeabur never executes the
-- LLM/embedding workload directly.

ALTER TABLE pipeline_jobs
    DROP CONSTRAINT IF EXISTS pipeline_jobs_job_type_check;

ALTER TABLE pipeline_jobs
    ADD CONSTRAINT pipeline_jobs_job_type_check
    CHECK (job_type IN ('daily', 'monthly', 'extraction'));

ALTER TABLE pipeline_jobs
    ADD COLUMN IF NOT EXISTS params JSONB NOT NULL DEFAULT '{}'::jsonb;
