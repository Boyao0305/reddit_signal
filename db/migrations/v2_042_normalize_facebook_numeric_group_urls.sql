-- v2 migration 042 - normalize Facebook numeric group ids to URL display.
--
-- SocialCrawl accepts either a bare numeric group id or a
-- facebook.com/groups/<numeric_id>/ URL. Keep the stored management value as a
-- URL so old and new Facebook venue rows render consistently in the dashboard.

BEGIN;

UPDATE social_source_venues
SET
    url_or_id = 'https://www.facebook.com/groups/' || url_or_id || '/',
    updated_at = NOW()
WHERE source = 'facebook'
  AND venue_type = 'facebook_group'
  AND url_or_id ~ '^[0-9]+$'
  AND NOT EXISTS (
      SELECT 1
      FROM social_source_venues existing
      WHERE existing.source = social_source_venues.source
        AND existing.venue_type = social_source_venues.venue_type
        AND existing.url_or_id = 'https://www.facebook.com/groups/' || social_source_venues.url_or_id || '/'
  );

COMMIT;
