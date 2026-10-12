-- v2_035: Deterministic emerging opportunity layer.
--
-- Stores short-lived micro-clusters built from recent orphan pain_objects:
-- pains with embeddings that are not assigned to any cluster in the active
-- monthly map. This does not alter analysis_runs or monthly promotion.

CREATE TABLE IF NOT EXISTS emerging_opportunity_runs (
    id             BIGSERIAL PRIMARY KEY,
    cluster_run_id INT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    lookback_days  INT NOT NULL DEFAULT 7,
    min_size       INT NOT NULL DEFAULT 8,
    eps            DOUBLE PRECISION NOT NULL DEFAULT 0.18,
    status         TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'failed')),
    candidate_count INT NOT NULL DEFAULT 0,
    opportunity_count INT NOT NULL DEFAULT 0,
    error_log      TEXT,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at    TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_emerging_runs_context
    ON emerging_opportunity_runs (cluster_run_id, status, id DESC);

CREATE TABLE IF NOT EXISTS emerging_opportunities (
    id                 BIGSERIAL PRIMARY KEY,
    emerging_run_id    BIGINT NOT NULL REFERENCES emerging_opportunity_runs(id) ON DELETE CASCADE,
    cluster_run_id     INT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    label              TEXT NOT NULL,
    pain_count         INT NOT NULL,
    author_count       INT NOT NULL,
    subreddit_count    INT NOT NULL,
    coherence          DOUBLE PRECISION NOT NULL DEFAULT 0,
    burst_score        DOUBLE PRECISION NOT NULL DEFAULT 0,
    source_window_start DATE,
    source_window_end   DATE,
    sample_pain_ids    INT[] NOT NULL DEFAULT '{}',
    sample_quotes      JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_emerging_opportunities_active
    ON emerging_opportunities (
        cluster_run_id,
        emerging_run_id DESC,
        burst_score DESC,
        coherence DESC,
        pain_count DESC
    );

CREATE TABLE IF NOT EXISTS emerging_opportunity_members (
    emerging_opportunity_id BIGINT NOT NULL REFERENCES emerging_opportunities(id) ON DELETE CASCADE,
    pain_id                 INT NOT NULL REFERENCES pain_objects(id) ON DELETE CASCADE,
    similarity              DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY (emerging_opportunity_id, pain_id)
);

CREATE INDEX IF NOT EXISTS ix_emerging_members_pain
    ON emerging_opportunity_members (pain_id);
