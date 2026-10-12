-- v2_010_cluster_lineage.sql
-- Cluster lineage: member-overlap (Jaccard) edges across re-cluster runs.
--
-- 'born':     parent_run_id NULL, parent_cluster NULL, child non-null
-- 'died':     child_cluster NULL, parent non-null
-- 'continued', 'split', 'merged': both non-null
--
-- Idempotent — safe to re-run.

CREATE TABLE IF NOT EXISTS cluster_lineage (
    parent_run_id   INT  REFERENCES analysis_runs(id)  ON DELETE CASCADE,
    parent_cluster  INT  REFERENCES pain_clusters(id)  ON DELETE CASCADE,
    child_run_id    INT  NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    child_cluster   INT  REFERENCES pain_clusters(id)  ON DELETE CASCADE,
    overlap_jaccard FLOAT,
    shared_members  INT,
    relation        VARCHAR(20) NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Unique edge: treat NULL as 0 for the COALESCE so the partial index is
-- consistent and upserts work correctly.
CREATE UNIQUE INDEX IF NOT EXISTS uq_lineage_edge ON cluster_lineage (
    COALESCE(parent_run_id, 0),
    COALESCE(parent_cluster, 0),
    child_run_id,
    COALESCE(child_cluster, 0)
);

CREATE INDEX IF NOT EXISTS ix_lineage_child  ON cluster_lineage (child_run_id,  child_cluster);
CREATE INDEX IF NOT EXISTS ix_lineage_parent ON cluster_lineage (parent_run_id, parent_cluster);

ALTER TABLE cluster_lineage OWNER TO llmanalyse;
