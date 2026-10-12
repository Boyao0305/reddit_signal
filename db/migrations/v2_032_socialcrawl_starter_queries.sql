-- v2 migration 032 - Fill starter queries for SocialCrawl-backed sources.
-- This is intentionally conservative: it only fills blank query fields.

BEGIN;

UPDATE source_runtime_config
SET
    query = CASE source
        WHEN 'github' THEN '"feature request" is' || chr(58) || 'issue comments' || chr(58) || '>5'
        WHEN 'youtube' THEN 'small business software problems'
        WHEN 'tiktok' THEN 'small business software problems'
        WHEN 'instagram' THEN 'productivitytools'
        WHEN 'twitter' THEN 'founders complaining about CRM support tools spreadsheets workflow automation'
        ELSE query
    END,
    notes = CASE source
        WHEN 'github' THEN 'Disabled by default. Starter query finds active GitHub issue discussions; refine with a repo qualifier when needed.'
        WHEN 'youtube' THEN 'Disabled by default. Starter query is a YouTube search phrase for software/workflow pain.'
        WHEN 'tiktok' THEN 'Disabled by default. Starter query is a TikTok keyword search phrase for software/workflow pain.'
        WHEN 'instagram' THEN 'Disabled by default. Starter query is an Instagram hashtag, with or without #.'
        WHEN 'twitter' THEN 'Disabled by default. Starter query is passed to SocialCrawl X/Twitter ai-search.'
        ELSE notes
    END,
    updated_at = NOW()
WHERE source IN ('github', 'youtube', 'tiktok', 'instagram', 'twitter')
  AND query = '';

COMMIT;
