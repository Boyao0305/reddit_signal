BEGIN;

-- SocialCrawl /facebook/group/posts accepts a numeric group_id. It rejects
-- common vanity group URLs such as /groups/saasfoundersclub/ as invalid
-- Facebook URL patterns, so keep those venues visible but paused until a
-- numeric group id is supplied.
UPDATE social_source_venues
SET status = 'paused',
    notes = CONCAT_WS(
        E'\n',
        NULLIF(notes, ''),
        'Paused by v2_039: SocialCrawl group posts requires a numeric Facebook group_id; vanity group URL rejected by upstream.'
    ),
    updated_at = NOW()
WHERE source = 'facebook'
  AND venue_type = 'facebook_group'
  AND url_or_id IN (
      'https://www.facebook.com/groups/saasfoundersclub/',
      'https://www.facebook.com/groups/saasfoundersnetwork/',
      'https://www.facebook.com/groups/SaaSgrowthhacking/',
      'https://www.facebook.com/groups/foundersspace/',
      'https://www.facebook.com/groups/cemgroup/'
  )
  AND status = 'active';

COMMIT;
