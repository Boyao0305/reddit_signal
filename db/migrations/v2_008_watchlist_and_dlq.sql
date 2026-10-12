-- v2_008_watchlist_and_dlq.sql
-- Phase 2 Component B-prep.1: watchlist columns on subreddits + pain extraction DLQ
-- Idempotent.

BEGIN;

-- 1. Watchlist + ingest-state columns on subreddits
ALTER TABLE subreddits
  ADD COLUMN IF NOT EXISTS is_watchlist        BOOLEAN     NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS last_fetched_at     TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS last_seen_post_utc  TIMESTAMPTZ;

-- All existing 210 subs default to is_watchlist=TRUE via the default;
-- explicit re-affirm in case the column already existed with a different default.
UPDATE subreddits SET is_watchlist = TRUE WHERE is_watchlist IS NULL;

-- 2. Pain extraction DLQ side table
CREATE TABLE IF NOT EXISTS pain_extraction_attempts (
    signal_event_id   BIGINT      PRIMARY KEY REFERENCES signal_events(id) ON DELETE CASCADE,
    attempts          INT         NOT NULL DEFAULT 0,
    last_error        TEXT,
    last_attempt_at   TIMESTAMPTZ DEFAULT NOW(),
    failed            BOOLEAN     NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS ix_pea_failed
  ON pain_extraction_attempts (failed) WHERE failed;

-- 3. Ownership (skill pitfall #2: psql -U postgres creates objects owned by postgres)
ALTER TABLE pain_extraction_attempts OWNER TO llmanalyse;

COMMIT;
