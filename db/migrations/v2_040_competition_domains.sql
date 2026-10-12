-- v2_040_competition_domains.sql
-- Domain grouping for competition research: clusters are grouped by centroid
-- similarity so that one LLM research call can cover a whole domain instead of
-- one call per cluster. This is the schema side of the per-domain cost
-- reduction (plan stage 2).

-- One row per (competition run, domain) describing the group and its
-- representative cluster (the one whose label anchors the research prompt).
CREATE TABLE IF NOT EXISTS cluster_domain_groups (
    competition_run_id    BIGINT NOT NULL REFERENCES competition_runs(id) ON DELETE CASCADE,
    domain_id             INT NOT NULL,
    domain_label          TEXT NOT NULL,
    representative_cluster_id INT NOT NULL REFERENCES pain_clusters(id) ON DELETE CASCADE,
    cluster_count         INT NOT NULL DEFAULT 0,
    member_seed_tools     TEXT[] NOT NULL DEFAULT '{}',
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (competition_run_id, domain_id)
);

CREATE INDEX IF NOT EXISTS ix_cluster_domain_groups_run
    ON cluster_domain_groups (competition_run_id, domain_id);

-- Per-cluster membership in a domain group for a given competition run.
CREATE TABLE IF NOT EXISTS cluster_domain_members (
    competition_run_id    BIGINT NOT NULL REFERENCES competition_runs(id) ON DELETE CASCADE,
    domain_id             INT NOT NULL,
    cluster_id            INT NOT NULL REFERENCES pain_clusters(id) ON DELETE CASCADE,
    is_representative     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (competition_run_id, cluster_id)
);

CREATE INDEX IF NOT EXISTS ix_cluster_domain_members_domain
    ON cluster_domain_members (competition_run_id, domain_id);

-- Track which domain a competition assessment was derived from, and whether the
-- research output was shared (domain-level) vs per-cluster. NULL keeps legacy
-- per-cluster rows valid.
ALTER TABLE cluster_competition_assessments
    ADD COLUMN IF NOT EXISTS domain_id INT,
    ADD COLUMN IF NOT EXISTS research_scope TEXT NOT NULL DEFAULT 'per_cluster'
        CHECK (research_scope IN ('per_cluster', 'per_domain'));

-- How many domains the run was split into; 0/NULL for legacy per-cluster runs.
ALTER TABLE competition_runs
    ADD COLUMN IF NOT EXISTS domain_count INT;
