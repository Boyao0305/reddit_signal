-- Step 4: Pain objects schema
-- Run after the base schema (subreddits, posts, comments) is already created.
-- Idempotent: safe to run multiple times.

-- Enable pgvector for future embedding support
CREATE EXTENSION IF NOT EXISTS vector;

-- Pain signals extracted from Reddit posts/comments
CREATE TABLE IF NOT EXISTS pain_objects (
    id               SERIAL PRIMARY KEY,
    post_id          INT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    comment_id       INT REFERENCES comments(id) ON DELETE SET NULL,
    subreddit_name   VARCHAR(100) NOT NULL,
    pain_statement   TEXT NOT NULL,
    raw_quote        TEXT NOT NULL,
    mentioned_tools  TEXT[],
    is_workaround    BOOLEAN NOT NULL DEFAULT FALSE,
    source_author    VARCHAR(100),
    sentiment_score  FLOAT,
    specificity      FLOAT,
    community_score  FLOAT,
    source_created   TIMESTAMP WITH TIME ZONE,
    embedding        VECTOR(384),
    -- cluster_id column dropped in migration v2_006 (membership now via pain_cluster_members)
    created_at       TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Deduplication key: one pain statement per post (hash-based)
    CONSTRAINT uq_pain_post_statement UNIQUE (post_id, pain_statement)
);

CREATE INDEX IF NOT EXISTS ix_pain_subreddit   ON pain_objects (subreddit_name);
-- ix_pain_cluster dropped with cluster_id column in migration v2_006
CREATE INDEX IF NOT EXISTS ix_pain_created     ON pain_objects (source_created);
CREATE INDEX IF NOT EXISTS ix_pain_post_id     ON pain_objects (post_id);

-- Pain clusters table (shared by old destructive flow and new run-based flow)
CREATE TABLE IF NOT EXISTS pain_clusters (
    id                  SERIAL PRIMARY KEY,
    run_id              INT,   -- NULL for legacy clusters, FK added by migrate_schema()
    label               TEXT,
    member_count        INT NOT NULL,
    intensity           FLOAT,
    prevalence          FLOAT,
    coherence           FLOAT,
    competitive_density FLOAT,
    opportunity_score   FLOAT,
    centroid            VECTOR(384),
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Analysis runs: each --analyze invocation creates one row
CREATE TABLE IF NOT EXISTS analysis_runs (
    id              SERIAL PRIMARY KEY,
    window_start    TIMESTAMP WITH TIME ZONE NOT NULL,
    window_end      TIMESTAMP WITH TIME ZONE NOT NULL,
    resolution      VARCHAR(20) NOT NULL DEFAULT 'adhoc',
    params          JSONB,
    cluster_count   INT,
    noise_count     INT,
    total_points    INT,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Junction table: which pain objects belong to which cluster in a given run
CREATE TABLE IF NOT EXISTS pain_cluster_members (
    pain_id     INT NOT NULL REFERENCES pain_objects(id) ON DELETE CASCADE,
    cluster_id  INT NOT NULL REFERENCES pain_clusters(id) ON DELETE CASCADE,
    PRIMARY KEY (pain_id, cluster_id)
);

CREATE INDEX IF NOT EXISTS ix_pcm_cluster ON pain_cluster_members (cluster_id);
