-- v2 migration 037 - supported source set update.
--
-- Keep runtime source rows aligned with the current product direction:
-- preserve Reddit + Hacker News, add Facebook, keep YouTube/X enabled,
-- and remove GitHub/TikTok/Instagram from runtime configuration.

BEGIN;

INSERT INTO system_settings (key, value, is_secret, description, updated_by)
VALUES (
    'SOCIALCRAWL_API_KEY',
    '',
    TRUE,
    'Used by SocialCrawl-backed Facebook, YouTube, and X/Twitter source adapters.',
    'migration'
)
ON CONFLICT (key) DO UPDATE SET
    is_secret = EXCLUDED.is_secret,
    description = EXCLUDED.description;

INSERT INTO source_capabilities (
    source,
    produces_pain,
    produces_traction,
    produces_temporal,
    notes
)
VALUES
    ('facebook', TRUE, TRUE, TRUE, 'Facebook public page/profile posts and comments via SocialCrawl'),
    ('youtube', TRUE, TRUE, TRUE, 'YouTube video search and comments via SocialCrawl'),
    ('twitter', TRUE, TRUE, TRUE, 'X/Twitter search via SocialCrawl ai-search')
ON CONFLICT (source) DO UPDATE SET
    produces_pain = EXCLUDED.produces_pain,
    produces_traction = EXCLUDED.produces_traction,
    produces_temporal = EXCLUDED.produces_temporal,
    notes = EXCLUDED.notes;

INSERT INTO source_runtime_config (
    source,
    enabled,
    fetch_enabled,
    project_enabled,
    extract_enabled,
    query,
    lookback_days,
    fetch_limit,
    priority,
    notes
)
VALUES
    (
        'facebook',
        FALSE,
        FALSE,
        FALSE,
        FALSE,
        '',
        7,
        100,
        30,
        'Disabled by default. Query should be a public Facebook page/profile URL or pageId for SocialCrawl profile posts.'
    ),
    (
        'youtube',
        TRUE,
        TRUE,
        TRUE,
        TRUE,
        'small business software problems',
        7,
        200,
        40,
        'Enabled source. Starter query is a YouTube search phrase for software/workflow pain.'
    ),
    (
        'twitter',
        TRUE,
        TRUE,
        TRUE,
        TRUE,
        'founders complaining about CRM support tools spreadsheets workflow automation',
        7,
        100,
        70,
        'Enabled source. Starter query is passed to SocialCrawl X/Twitter ai-search.'
    )
ON CONFLICT (source) DO NOTHING;

UPDATE source_runtime_config
SET
    enabled = TRUE,
    fetch_enabled = TRUE,
    project_enabled = TRUE,
    extract_enabled = TRUE,
    query = CASE
        WHEN source = 'youtube' AND query = '' THEN 'small business software problems'
        WHEN source = 'twitter' AND query = '' THEN 'founders complaining about CRM support tools spreadsheets workflow automation'
        ELSE query
    END,
    notes = CASE source
        WHEN 'youtube' THEN 'Enabled source. Starter query is a YouTube search phrase for software/workflow pain.'
        WHEN 'twitter' THEN 'Enabled source. Starter query is passed to SocialCrawl X/Twitter ai-search.'
        ELSE notes
    END,
    updated_at = NOW()
WHERE source IN ('youtube', 'twitter');

DELETE FROM source_runtime_config
WHERE source IN ('github', 'tiktok', 'instagram');

DELETE FROM source_capabilities
WHERE source IN ('github', 'tiktok', 'instagram');

COMMIT;
