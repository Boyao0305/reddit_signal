-- v2 migration 002 — pain_objects: add signal_event_id, prompt_version; relax NOT NULLs
-- Idempotent. NON-DESTRUCTIVE: does NOT drop cluster_id (that happens later in Phase 1.18).

BEGIN;

ALTER TABLE pain_objects
    ADD COLUMN IF NOT EXISTS signal_event_id BIGINT REFERENCES signal_events(id),
    ADD COLUMN IF NOT EXISTS prompt_version  VARCHAR(32) NOT NULL DEFAULT 'v1';

-- Allow non-Reddit sources later
ALTER TABLE pain_objects ALTER COLUMN post_id        DROP NOT NULL;
ALTER TABLE pain_objects ALTER COLUMN subreddit_name DROP NOT NULL;

CREATE INDEX IF NOT EXISTS ix_pain_signal_event   ON pain_objects(signal_event_id);
CREATE INDEX IF NOT EXISTS ix_pain_prompt_version ON pain_objects(prompt_version);

COMMIT;
