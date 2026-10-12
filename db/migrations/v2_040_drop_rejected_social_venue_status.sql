-- v2 migration 040 - retire the 'rejected' status from social source venues.
--
-- 'rejected' was functionally identical to 'paused' (both just exclude a venue
-- from the active crawl pool) but offered no real removal. It is replaced by a
-- hard DELETE in the admin UI, so the status is dropped from the model. The
-- remaining lifecycle is candidate -> active -> paused, plus delete.

BEGIN;

-- Defensive cleanup: no rows should be 'rejected' on production (v2_039 paused
-- the vanity Facebook groups instead), but purge any stragglers before
-- tightening the CHECK so the constraint cannot fail mid-migration.
DELETE FROM social_source_venues WHERE status = 'rejected';

-- The original CHECK was created inline in v2_038 without an explicit name.
-- PostgreSQL names column CHECK constraints '<table>_<column>_check', so on
-- every environment it is 'social_source_venues_status_check'. Drop it by that
-- fixed name (IF EXISTS guards environments where it was already removed) and
-- re-add with the same name holding the tightened value set. Note that
-- pg_get_constraintdef normalizes `status IN (...)` to `status = ANY(ARRAY[...])`,
-- so matching on the literal IN-syntax is unreliable across PG versions.
ALTER TABLE social_source_venues
    DROP CONSTRAINT IF EXISTS social_source_venues_status_check;

ALTER TABLE social_source_venues
    ADD CONSTRAINT social_source_venues_status_check
    CHECK (status IN ('candidate', 'active', 'paused'));

COMMIT;

