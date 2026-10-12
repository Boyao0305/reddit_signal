-- Migration: report_pipeline tables
-- On-demand per-cluster market-research report pipeline (Stage 1-4).
-- Reached through the existing dashboard_2 connection pool (env-driven
-- DATABASE_URL → localhost/reddit_raw). Do NOT introduce any socket DSN here.
-- Run manually:
--   psql "$DATABASE_URL" -f migrations/v2_016_report_pipeline.sql

-- report_jobs: one row per triggered pipeline run.
CREATE TABLE IF NOT EXISTS report_jobs (
    id              BIGSERIAL PRIMARY KEY,
    cluster_id      BIGINT NOT NULL,
    analysis_run_id BIGINT,
    status          TEXT NOT NULL DEFAULT 'queued',   -- queued|running|done|failed
    current_stage   SMALLINT NOT NULL DEFAULT 0,      -- 0..4, progress + resume anchor
    requested_by    TEXT,
    cost_usd        NUMERIC(10,4) DEFAULT 0,
    error_log       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_report_jobs_status  ON report_jobs(status);
CREATE INDEX IF NOT EXISTS idx_report_jobs_cluster ON report_jobs(cluster_id);

-- report_stage_outputs: raw JSON output of each stage, one row per (job, stage).
CREATE TABLE IF NOT EXISTS report_stage_outputs (
    id          BIGSERIAL PRIMARY KEY,
    job_id      BIGINT NOT NULL REFERENCES report_jobs(id) ON DELETE CASCADE,
    stage_name  TEXT NOT NULL,          -- scoping|competitor_research|evaluation|assembly
    model_used  TEXT,
    raw_output  JSONB NOT NULL,
    tokens_in   INTEGER,
    tokens_out  INTEGER,
    cost_usd    NUMERIC(10,4) DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(job_id, stage_name)          -- matches runner ON CONFLICT (job_id, stage_name)
);

-- reports: final assembled deliverable, one row per job (upserted).
CREATE TABLE IF NOT EXISTS reports (
    id                   BIGSERIAL PRIMARY KEY,
    job_id               BIGINT NOT NULL REFERENCES report_jobs(id) ON DELETE CASCADE,
    cluster_id           BIGINT NOT NULL,
    verdict              TEXT CHECK (verdict IN ('go', 'no_go', 'conditional')),
    verdict_reason       TEXT,
    product_logic        TEXT,
    competitors          JSONB,              -- Stage 2 competitor catalogue
    market_structure     TEXT,
    report_md            TEXT NOT NULL,
    why_no_direct        TEXT,
    opportunity_or_trap  TEXT,
    condition_difficulty TEXT,               -- product|technical|business_model|structural
    condition_summary    TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_reports_cluster ON reports(cluster_id);
CREATE INDEX IF NOT EXISTS idx_reports_verdict ON reports(verdict);

-- Idempotent additions for environments where an earlier (incomplete) version of
-- this table already exists. The runner writes these four columns and the API
-- reads them back; without them the Stage-4 INSERT crashes after 3 paid LLM calls.
ALTER TABLE reports ADD COLUMN IF NOT EXISTS why_no_direct        TEXT;
ALTER TABLE reports ADD COLUMN IF NOT EXISTS opportunity_or_trap  TEXT;
ALTER TABLE reports ADD COLUMN IF NOT EXISTS condition_difficulty TEXT;
ALTER TABLE reports ADD COLUMN IF NOT EXISTS condition_summary    TEXT;

-- runner does ON CONFLICT (job_id) DO UPDATE — the upsert needs this unique key.
DO $$
BEGIN
    ALTER TABLE reports ADD CONSTRAINT uq_reports_job_id UNIQUE (job_id);
EXCEPTION
    WHEN duplicate_object THEN NULL;   -- already present, nothing to do
    WHEN duplicate_table  THEN NULL;   -- index name collision, treat as present
END $$;
