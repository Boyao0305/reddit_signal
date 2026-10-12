-- Phase 2 Component A: cluster quality metrics + fused quality+viability LLM judgment
-- Idempotent. Creates cluster_quality_metrics, viability_runs, viability_scores.

BEGIN;

CREATE TABLE IF NOT EXISTS cluster_quality_metrics (
    cluster_run_id        INT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    cluster_id            INT NOT NULL REFERENCES pain_clusters(id) ON DELETE CASCADE,
    source_diversity      DOUBLE PRECISION,
    thread_diversity      DOUBLE PRECISION,
    author_diversity      DOUBLE PRECISION,
    subreddit_count       INT,
    specificity_mean      DOUBLE PRECISION,
    workaround_ratio      DOUBLE PRECISION,
    separation            DOUBLE PRECISION,
    nearest_cluster_id    INT,
    temporal_spread_days  INT,
    computed_at           TIMESTAMPTZ DEFAULT NOW(),
    metrics_version       VARCHAR(16) NOT NULL DEFAULT 'v1',
    PRIMARY KEY (cluster_run_id, cluster_id)
);
CREATE INDEX IF NOT EXISTS ix_cqm_thread_diversity ON cluster_quality_metrics (thread_diversity);
CREATE INDEX IF NOT EXISTS ix_cqm_specificity     ON cluster_quality_metrics (specificity_mean);

ALTER TABLE cluster_quality_metrics OWNER TO llmanalyse;

CREATE TABLE IF NOT EXISTS viability_runs (
    id              SERIAL PRIMARY KEY,
    cluster_run_id  INT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    llm_model       VARCHAR(64) NOT NULL,
    prompt_version  VARCHAR(32) NOT NULL DEFAULT 'v1',
    params          JSONB,
    n_clusters      INT,
    n_succeeded     INT,
    n_failed        INT,
    notes           TEXT
);
ALTER TABLE viability_runs OWNER TO llmanalyse;
ALTER SEQUENCE viability_runs_id_seq OWNER TO llmanalyse;

CREATE TABLE IF NOT EXISTS viability_scores (
    viability_run_id      INT NOT NULL REFERENCES viability_runs(id) ON DELETE CASCADE,
    cluster_id            INT NOT NULL REFERENCES pain_clusters(id) ON DELETE CASCADE,
    is_real_pain          BOOLEAN NOT NULL,
    is_software_solvable  BOOLEAN NOT NULL,
    q_specificity         SMALLINT CHECK (q_specificity      BETWEEN 1 AND 5),
    q_noise_flag          VARCHAR(32),
    v_buildability        SMALLINT CHECK (v_buildability     BETWEEN 1 AND 5),
    v_icp_clarity         SMALLINT CHECK (v_icp_clarity      BETWEEN 1 AND 5),
    v_moat_potential      SMALLINT CHECK (v_moat_potential   BETWEEN 1 AND 5),
    v_rationale           TEXT,
    sample_pain_ids       INT[],
    llm_input_tokens      INT,
    llm_output_tokens     INT,
    raw_response          JSONB,
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (viability_run_id, cluster_id)
);
CREATE INDEX IF NOT EXISTS ix_vs_cluster   ON viability_scores (cluster_id);
CREATE INDEX IF NOT EXISTS ix_vs_real_pain ON viability_scores (is_real_pain) WHERE is_real_pain;
CREATE INDEX IF NOT EXISTS ix_vs_saas      ON viability_scores (is_software_solvable) WHERE is_software_solvable;
ALTER TABLE viability_scores OWNER TO llmanalyse;

COMMIT;
