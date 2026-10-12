-- v2 migration 031 - SocialCrawl-backed source adapters.
-- Idempotent. Adds a shared L1 table and seeds disabled runtime config rows.

BEGIN;

CREATE TABLE IF NOT EXISTS socialcrawl_items (
    id                    BIGSERIAL PRIMARY KEY,
    source                VARCHAR(32) NOT NULL,
    item_kind             VARCHAR(32) NOT NULL,
    source_item_id        VARCHAR(56) NOT NULL,
    raw_source_item_id    TEXT,
    parent_source_item_id VARCHAR(56),
    root_source_item_id   VARCHAR(56),
    event_time            TIMESTAMPTZ NOT NULL,
    surface               TEXT,
    author                TEXT,
    title                 TEXT,
    text                  TEXT,
    url                   TEXT,
    engagement_score      DOUBLE PRECISION,
    query                 TEXT NOT NULL DEFAULT '',
    provider              TEXT NOT NULL DEFAULT 'socialcrawl',
    provider_endpoint     TEXT NOT NULL DEFAULT '',
    raw_json              JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_socialcrawl_items_source_kind_id
    ON socialcrawl_items (source, item_kind, source_item_id);
CREATE INDEX IF NOT EXISTS ix_socialcrawl_items_source_time
    ON socialcrawl_items (source, item_kind, event_time DESC);
CREATE INDEX IF NOT EXISTS ix_socialcrawl_items_root
    ON socialcrawl_items (source, root_source_item_id);
CREATE INDEX IF NOT EXISTS ix_socialcrawl_items_provider_endpoint
    ON socialcrawl_items (provider, provider_endpoint);

CREATE TABLE IF NOT EXISTS processed_signal_events (
    signal_event_id BIGINT PRIMARY KEY REFERENCES signal_events(id) ON DELETE CASCADE,
    source          TEXT NOT NULL,
    processed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_processed_signal_events_source
    ON processed_signal_events (source, processed_at DESC);

INSERT INTO system_settings (key, value, is_secret, description, updated_by)
VALUES (
    'SOCIALCRAWL_API_KEY',
    '',
    TRUE,
    'Used by SocialCrawl-backed GitHub, YouTube, TikTok, Instagram, and X/Twitter source adapters.',
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
    ('github', TRUE, TRUE, TRUE, 'GitHub issues and PR discussions via SocialCrawl'),
    ('youtube', TRUE, TRUE, TRUE, 'YouTube video search and comments via SocialCrawl'),
    ('tiktok', TRUE, TRUE, TRUE, 'TikTok video search and comments via SocialCrawl'),
    ('instagram', TRUE, TRUE, TRUE, 'Instagram hashtag posts and comments via SocialCrawl'),
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
        'github',
        FALSE,
        FALSE,
        FALSE,
        FALSE,
        '"feature request" is' || chr(58) || 'issue comments' || chr(58) || '>5',
        7,
        200,
        30,
        'Disabled by default. Starter query finds active GitHub issue discussions; refine with a repo qualifier when needed.'
    ),
    (
        'youtube',
        FALSE,
        FALSE,
        FALSE,
        FALSE,
        'small business software problems',
        7,
        200,
        40,
        'Disabled by default. Starter query is a YouTube search phrase for software/workflow pain.'
    ),
    (
        'tiktok',
        FALSE,
        FALSE,
        FALSE,
        FALSE,
        'small business software problems',
        7,
        200,
        50,
        'Disabled by default. Starter query is a TikTok keyword search phrase for software/workflow pain.'
    ),
    (
        'instagram',
        FALSE,
        FALSE,
        FALSE,
        FALSE,
        'productivitytools',
        7,
        200,
        60,
        'Disabled by default. Starter query is an Instagram hashtag, with or without #.'
    ),
    (
        'twitter',
        FALSE,
        FALSE,
        FALSE,
        FALSE,
        'founders complaining about CRM support tools spreadsheets workflow automation',
        7,
        100,
        70,
        'Disabled by default. Starter query is passed to SocialCrawl X/Twitter ai-search.'
    )
ON CONFLICT (source) DO NOTHING;

COMMIT;
