-- v2 migration 038 - managed SocialCrawl venue pools.
--
-- Seed default Facebook groups and X/Twitter communities so SocialCrawl-backed
-- sources can run without hand-configuring a first batch of venues.

BEGIN;

CREATE TABLE IF NOT EXISTS social_source_venues (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL CHECK (source IN ('facebook', 'twitter')),
    venue_type TEXT NOT NULL,
    name TEXT NOT NULL,
    url_or_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'active', 'paused', 'rejected')),
    priority INTEGER NOT NULL DEFAULT 100
        CHECK (priority >= 0 AND priority <= 10000),
    daily_lookback_days INTEGER NOT NULL DEFAULT 7
        CHECK (daily_lookback_days >= 0 AND daily_lookback_days <= 3650),
    fetch_limit INTEGER NOT NULL DEFAULT 20
        CHECK (fetch_limit >= 0 AND fetch_limit <= 100000),
    sort_mode TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_fetch_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source, venue_type, url_or_id)
);

CREATE INDEX IF NOT EXISTS ix_social_source_venues_source_status_priority
    ON social_source_venues (source, status, priority, name);

CREATE INDEX IF NOT EXISTS ix_social_source_venues_updated_at
    ON social_source_venues (updated_at DESC);

CREATE OR REPLACE FUNCTION touch_social_source_venues_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_social_source_venues_updated_at
    ON social_source_venues;
CREATE TRIGGER trg_social_source_venues_updated_at
BEFORE UPDATE ON social_source_venues
FOR EACH ROW
EXECUTE FUNCTION touch_social_source_venues_updated_at();

INSERT INTO social_source_venues (
    source,
    venue_type,
    name,
    url_or_id,
    status,
    priority,
    daily_lookback_days,
    fetch_limit,
    sort_mode,
    notes
)
VALUES
    (
        'facebook',
        'facebook_group',
        'SaaS Founders Club',
        'https://www.facebook.com/groups/saasfoundersclub/',
        'active',
        10,
        7,
        20,
        'RECENT_ACTIVITY',
        'Default seed: SaaS founder discussion group.'
    ),
    (
        'facebook',
        'facebook_group',
        'SaaS Founders Network',
        'https://www.facebook.com/groups/saasfoundersnetwork/',
        'active',
        20,
        7,
        20,
        'RECENT_ACTIVITY',
        'Default seed: SaaS founder discussion group.'
    ),
    (
        'facebook',
        'facebook_group',
        'SaaS Growth Hacks',
        'https://www.facebook.com/groups/SaaSgrowthhacking/',
        'active',
        30,
        7,
        20,
        'RECENT_ACTIVITY',
        'Default seed: SaaS growth discussion group.'
    ),
    (
        'facebook',
        'facebook_group',
        'SaaS',
        'https://www.facebook.com/groups/6204556817/',
        'active',
        40,
        7,
        20,
        'RECENT_ACTIVITY',
        'Default seed: broad SaaS discussion group.'
    ),
    (
        'facebook',
        'facebook_group',
        'Small Business Owners',
        'https://www.facebook.com/groups/2815042615255352/',
        'active',
        50,
        7,
        20,
        'RECENT_ACTIVITY',
        'Default seed: small business owner discussion group.'
    ),
    (
        'facebook',
        'facebook_group',
        'Founders Space',
        'https://www.facebook.com/groups/foundersspace/',
        'active',
        60,
        7,
        20,
        'RECENT_ACTIVITY',
        'Default seed: startup founder discussion group.'
    ),
    (
        'facebook',
        'facebook_group',
        'Customer Experience Management',
        'https://www.facebook.com/groups/cemgroup/',
        'active',
        70,
        7,
        20,
        'RECENT_ACTIVITY',
        'Default seed: customer experience and support operations group.'
    ),
    (
        'facebook',
        'facebook_group',
        'Business AI and Automation Community',
        'https://www.facebook.com/groups/1752388055008755/',
        'active',
        80,
        7,
        20,
        'RECENT_ACTIVITY',
        'Default seed: business automation discussion group.'
    ),
    (
        'twitter',
        'twitter_community',
        'Startup Community',
        'https://x.com/i/communities/1471580197908586507',
        'active',
        10,
        7,
        30,
        '',
        'Default seed: startup founder X community.'
    ),
    (
        'twitter',
        'twitter_community',
        'Indiemakers and SaaS founders',
        'https://x.com/i/communities/1492982239960969222',
        'active',
        20,
        7,
        30,
        '',
        'Default seed: indie maker and SaaS founder X community.'
    ),
    (
        'twitter',
        'twitter_community',
        'Startup founders and friends',
        'https://x.com/i/communities/1498737511241158657',
        'active',
        30,
        7,
        30,
        '',
        'Default seed: startup founder X community.'
    ),
    (
        'twitter',
        'twitter_community',
        'Tech Founders',
        'https://x.com/i/communities/1824090181827457380',
        'active',
        40,
        7,
        30,
        '',
        'Default seed: tech founder X community.'
    ),
    (
        'twitter',
        'twitter_community',
        'Bootstrapped Startup Founders',
        'https://x.com/i/communities/1725580569633829064',
        'active',
        50,
        7,
        30,
        '',
        'Default seed: bootstrapped founder X community.'
    ),
    (
        'twitter',
        'twitter_community',
        'Small Business Owners USA',
        'https://x.com/i/communities/1669371460874371073',
        'active',
        60,
        7,
        30,
        '',
        'Default seed: small business owner X community.'
    ),
    (
        'twitter',
        'twitter_community',
        'Solopreneurs and SMB Owners',
        'https://x.com/i/communities/1776363404396995055',
        'active',
        70,
        7,
        30,
        '',
        'Default seed: solopreneur and SMB owner X community.'
    ),
    (
        'twitter',
        'twitter_community',
        'CRM and Workflow Automation',
        'https://x.com/i/communities/1798290720509268264',
        'active',
        80,
        7,
        30,
        '',
        'Default seed: CRM and workflow automation X community.'
    ),
    (
        'twitter',
        'twitter_community',
        'Product and Project Management',
        'https://x.com/i/communities/1510231149167235076',
        'active',
        90,
        7,
        30,
        '',
        'Default seed: product and project management X community.'
    ),
    (
        'twitter',
        'twitter_community',
        'AI MVP Builders',
        'https://x.com/i/communities/1891501175075491943',
        'active',
        100,
        7,
        30,
        '',
        'Default seed: AI MVP builder X community.'
    )
ON CONFLICT (source, venue_type, url_or_id) DO NOTHING;

UPDATE source_capabilities
SET notes = CASE source
    WHEN 'facebook' THEN 'Facebook public group posts and comments via SocialCrawl'
    WHEN 'twitter' THEN 'X/Twitter community tweets via SocialCrawl'
    ELSE notes
END
WHERE source IN ('facebook', 'twitter');

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
        TRUE,
        TRUE,
        TRUE,
        TRUE,
        '',
        7,
        160,
        30,
        'Enabled source. Daily fetches use managed active Facebook groups from social_source_venues.'
    ),
    (
        'twitter',
        TRUE,
        TRUE,
        TRUE,
        TRUE,
        '',
        7,
        300,
        70,
        'Enabled source. Daily fetches use managed active X/Twitter communities from social_source_venues.'
    )
ON CONFLICT (source) DO NOTHING;

UPDATE source_runtime_config
SET
    enabled = TRUE,
    fetch_enabled = TRUE,
    project_enabled = TRUE,
    extract_enabled = TRUE,
    query = CASE
        WHEN source = 'twitter'
         AND query = 'founders complaining about CRM support tools spreadsheets workflow automation'
            THEN ''
        ELSE query
    END,
    fetch_limit = CASE
        WHEN source = 'facebook' AND fetch_limit IN (0, 100) THEN 160
        WHEN source = 'twitter' AND fetch_limit IN (0, 100) THEN 300
        ELSE fetch_limit
    END,
    notes = CASE source
        WHEN 'facebook' THEN 'Enabled source. Daily fetches use managed active Facebook groups from social_source_venues.'
        WHEN 'twitter' THEN 'Enabled source. Daily fetches use managed active X/Twitter communities from social_source_venues.'
        ELSE notes
    END,
    updated_at = NOW()
WHERE source IN ('facebook', 'twitter');

COMMIT;
