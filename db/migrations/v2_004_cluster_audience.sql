-- v2 migration 004 — cluster_audience (per-cluster B2B/B2C classification)
-- + subreddits.audience_prior weak signal column.

BEGIN;

CREATE TABLE IF NOT EXISTS cluster_audience (
    id                  SERIAL PRIMARY KEY,
    cluster_id          INT NOT NULL REFERENCES pain_clusters(id) ON DELETE CASCADE,
    classifier_version  VARCHAR(32) NOT NULL DEFAULT 'v1',
    audience_type       VARCHAR(16) NOT NULL
        CHECK (audience_type IN ('B2B', 'B2C', 'mixed', 'unclear')),
    confidence          DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    icp_hint            TEXT,
    reasoning           TEXT,
    sample_pain_ids     INT[],
    surface_distribution JSONB,
    llm_model           VARCHAR(64),
    llm_input_tokens    INT,
    llm_output_tokens   INT,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    CONSTRAINT uq_cluster_audience_version UNIQUE (cluster_id, classifier_version)
);

CREATE INDEX IF NOT EXISTS ix_cluster_audience_type    ON cluster_audience(audience_type);
CREATE INDEX IF NOT EXISTS ix_cluster_audience_cluster ON cluster_audience(cluster_id);

ALTER TABLE subreddits
    ADD COLUMN IF NOT EXISTS audience_prior VARCHAR(16) NOT NULL DEFAULT 'unknown';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'subreddits_audience_prior_check'
    ) THEN
        ALTER TABLE subreddits
            ADD CONSTRAINT subreddits_audience_prior_check
            CHECK (audience_prior IN ('B2B', 'B2C', 'mixed', 'unknown'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_subreddits_audience_prior
    ON subreddits(audience_prior);

COMMIT;
