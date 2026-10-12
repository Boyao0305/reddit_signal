-- v2_029: Reddit subreddit source management, backfill tracking, and
-- date/subreddit scoped cluster filtering support.

CREATE TABLE IF NOT EXISTS reddit_subreddit_sources (
    subreddit_name TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'active', 'paused', 'rejected')),
    priority INTEGER NOT NULL DEFAULT 100,
    daily_lookback_days INTEGER NOT NULL DEFAULT 7 CHECK (daily_lookback_days >= 0),
    backfill_months INTEGER NOT NULL DEFAULT 18 CHECK (backfill_months >= 0),
    backfill_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    tags TEXT[] NOT NULL DEFAULT '{}',
    notes TEXT NOT NULL DEFAULT '',
    last_fetch_at TIMESTAMPTZ,
    last_backfill_at TIMESTAMPTZ,
    last_evaluated_at TIMESTAMPTZ,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_reddit_subreddit_sources_status_priority
    ON reddit_subreddit_sources (status, priority, subreddit_name);

CREATE INDEX IF NOT EXISTS ix_reddit_subreddit_sources_updated_at
    ON reddit_subreddit_sources (updated_at DESC);

CREATE OR REPLACE FUNCTION touch_reddit_subreddit_sources_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_reddit_subreddit_sources_updated_at
    ON reddit_subreddit_sources;
CREATE TRIGGER trg_reddit_subreddit_sources_updated_at
BEFORE UPDATE ON reddit_subreddit_sources
FOR EACH ROW
EXECUTE FUNCTION touch_reddit_subreddit_sources_updated_at();

CREATE TABLE IF NOT EXISTS reddit_backfill_jobs (
    id BIGSERIAL PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    requested_by TEXT,
    subreddits TEXT[] NOT NULL DEFAULT '{}',
    months INTEGER NOT NULL DEFAULT 18 CHECK (months >= 0),
    chunk_days INTEGER NOT NULL DEFAULT 7 CHECK (chunk_days > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error TEXT,
    total_chunks INTEGER NOT NULL DEFAULT 0,
    succeeded_chunks INTEGER NOT NULL DEFAULT 0,
    failed_chunks INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_reddit_backfill_jobs_status_created
    ON reddit_backfill_jobs (status, created_at DESC);

CREATE TABLE IF NOT EXISTS reddit_backfill_chunks (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES reddit_backfill_jobs(id) ON DELETE CASCADE,
    subreddit_name TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'skipped')),
    posts_count INTEGER NOT NULL DEFAULT 0,
    comments_count INTEGER NOT NULL DEFAULT 0,
    projected_count INTEGER NOT NULL DEFAULT 0,
    extracted_count INTEGER NOT NULL DEFAULT 0,
    assigned_count INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (job_id, subreddit_name, window_start, window_end)
);

CREATE INDEX IF NOT EXISTS ix_reddit_backfill_chunks_status
    ON reddit_backfill_chunks (status, job_id, id);

CREATE INDEX IF NOT EXISTS ix_reddit_backfill_chunks_subreddit_window
    ON reddit_backfill_chunks (subreddit_name, window_start, window_end);

CREATE OR REPLACE FUNCTION touch_reddit_backfill_chunks_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_reddit_backfill_chunks_updated_at
    ON reddit_backfill_chunks;
CREATE TRIGGER trg_reddit_backfill_chunks_updated_at
BEFORE UPDATE ON reddit_backfill_chunks
FOR EACH ROW
EXECUTE FUNCTION touch_reddit_backfill_chunks_updated_at();

CREATE TABLE IF NOT EXISTS pain_volume_daily_subreddit (
    cluster_run_id INT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    cluster_id INT NOT NULL REFERENCES pain_clusters(id) ON DELETE CASCADE,
    day_start DATE NOT NULL,
    subreddit_name TEXT NOT NULL,
    pain_count INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (cluster_run_id, cluster_id, day_start, subreddit_name)
);

CREATE INDEX IF NOT EXISTS ix_pvds_run_day_subreddit_cluster
    ON pain_volume_daily_subreddit (
        cluster_run_id,
        day_start,
        subreddit_name,
        cluster_id
    );

CREATE INDEX IF NOT EXISTS ix_pvds_run_subreddit_day
    ON pain_volume_daily_subreddit (
        cluster_run_id,
        subreddit_name,
        day_start
    );

ALTER TABLE dashboard_market_opportunities
    ADD COLUMN IF NOT EXISTS window_pain_count INT,
    ADD COLUMN IF NOT EXISTS window_days_active INT,
    ADD COLUMN IF NOT EXISTS window_subreddit_count INT,
    ADD COLUMN IF NOT EXISTS window_filter JSONB;

CREATE INDEX IF NOT EXISTS ix_dashboard_market_window_pain
    ON dashboard_market_opportunities (
        cluster_run_id,
        viability_run_id,
        audience_classifier_version,
        audience_type,
        window_pain_count DESC NULLS LAST,
        cluster_id
    );

INSERT INTO reddit_subreddit_sources (
    subreddit_name,
    status,
    priority,
    daily_lookback_days,
    backfill_months,
    notes
)
SELECT
    s.name,
    CASE WHEN COALESCE(s.is_watchlist, TRUE) THEN 'active' ELSE 'paused' END,
    100,
    7,
    18,
    'Seeded from existing subreddits table.'
FROM subreddits s
ON CONFLICT (subreddit_name) DO NOTHING;

INSERT INTO reddit_subreddit_sources (
    subreddit_name,
    status,
    priority,
    daily_lookback_days,
    backfill_months,
    notes
)
SELECT name, 'active', priority, 7, 18, 'Seeded default subreddit.'
FROM (
    VALUES
        ('smallbusiness', 10),
        ('sysadmin', 20),
        ('msp', 30),
        ('SaaS', 40),
        ('Entrepreneur', 50),
        ('sales', 60)
) AS defaults(name, priority)
ON CONFLICT (subreddit_name) DO NOTHING;
