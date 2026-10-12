-- migrate: no-transaction
-- Extraction now selects unprocessed parent events newest-first within a time window
-- (core/extraction_policy.py). This partial index serves
--   WHERE source = $1 AND parent_event_id IS NULL AND event_time >= $2 AND event_time < $3
--   ORDER BY event_time DESC
-- without scanning the ~60M child rows. Built CONCURRENTLY so ingestion is not blocked.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_signal_events_parent_source_time
    ON signal_events (source, event_time DESC)
    WHERE parent_event_id IS NULL;
