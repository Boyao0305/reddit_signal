-- Phase 1 Step 6 — DESTRUCTIVE: drop pain_objects.cluster_id
--
-- Pre-conditions verified BEFORE this migration:
--   - All 426,713 pain_objects have signal_event_id populated (Step 4 done)
--   - 7 v1 runs already have entries in pain_cluster_members (208,487 rows)
--   - Code paths in clustering.py / ranking.py / labeling.py have been
--     refactored to use pain_cluster_members exclusively (this commit)
--
-- This migration:
--   1. Backfills any pain_cluster_members entries that are missing for the
--      7,913 pain_objects with cluster_id IS NOT NULL (idempotent INSERT).
--   2. Drops pain_objects.cluster_id and its index.
--
-- ROLLBACK PLAN: pg_restore from
--   /var/backups/reddit_raw/reddit_raw_pre_v2_20260503_0053.dump

BEGIN;

-- 1. Backfill any rows where cluster_id is set but pain_cluster_members lacks the entry.
INSERT INTO pain_cluster_members (pain_id, cluster_id)
SELECT po.id, po.cluster_id
FROM pain_objects po
WHERE po.cluster_id IS NOT NULL
ON CONFLICT (pain_id, cluster_id) DO NOTHING;

-- 2. Drop the column + its index.
DROP INDEX IF EXISTS ix_pain_cluster;
ALTER TABLE pain_objects DROP COLUMN IF EXISTS cluster_id;

-- Validation: pain_cluster_members coverage of legacy clusters
DO $$
DECLARE n_pcm INT; n_legacy_clusters INT;
BEGIN
    SELECT COUNT(*) INTO n_pcm FROM pain_cluster_members;
    SELECT COUNT(*) INTO n_legacy_clusters FROM pain_clusters
      WHERE run_id IN (SELECT id FROM analysis_runs WHERE is_legacy = TRUE);
    RAISE NOTICE 'pain_cluster_members: % rows; legacy clusters: %',
                 n_pcm, n_legacy_clusters;
END $$;

COMMIT;
