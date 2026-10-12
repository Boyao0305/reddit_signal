-- v2 migration 041 - expand numeric Facebook seeds, add more X communities,
-- and seed a richer YouTube query pool.
--
-- Constraints:
-- 1. Facebook group venues must use numeric ids because SocialCrawl rejects
--    vanity /groups/<slug>/ URLs for /facebook/group/posts.
-- 2. X continues to use managed community venues.
-- 3. YouTube currently uses source_runtime_config.query, so we seed multiple
--    newline-delimited search queries instead of introducing a new venue table.

BEGIN;

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
        'Scaling SaaS Founders',
        '2044902829088090',
        'candidate',
        15,
        7,
        15,
        'RECENT_ACTIVITY',
        'v2_041 seed: numeric Facebook group id for SaaS founders and operators.'
    ),
    (
        'facebook',
        'facebook_group',
        'Small Business Owners USA',
        '211270969394435',
        'candidate',
        25,
        7,
        15,
        'RECENT_ACTIVITY',
        'v2_041 seed: numeric Facebook group id for small business operator pain.'
    ),
    (
        'facebook',
        'facebook_group',
        'AI Automation for US-based Business Owners',
        '559198672415446',
        'candidate',
        35,
        7,
        15,
        'RECENT_ACTIVITY',
        'v2_041 seed: numeric Facebook group id for AI automation and SMB workflows.'
    ),
    (
        'facebook',
        'facebook_group',
        'The No Code App Entrepreneur',
        '1165236046981865',
        'candidate',
        45,
        7,
        15,
        'RECENT_ACTIVITY',
        'v2_041 seed: numeric Facebook group id for no-code founder and automation pain.'
    ),
    (
        'facebook',
        'facebook_group',
        'Customer Success Jobs',
        '1998012103811981',
        'candidate',
        55,
        7,
        15,
        'RECENT_ACTIVITY',
        'v2_041 seed: numeric Facebook group id for customer success and retention discussions. Candidate only due to hiring noise risk.'
    ),
    (
        'twitter',
        'twitter_community',
        'Build in Public',
        'https://x.com/i/communities/1539305026511941632',
        'candidate',
        110,
        7,
        30,
        '',
        'v2_041 seed: build-in-public founders and operator pain.'
    ),
    (
        'twitter',
        'twitter_community',
        'Marketing & Growth',
        'https://x.com/i/communities/1604888013352939520',
        'candidate',
        120,
        7,
        30,
        '',
        'v2_041 seed: growth and acquisition pain for SaaS/startups.'
    ),
    (
        'twitter',
        'twitter_community',
        'AI Founders',
        'https://x.com/i/communities/1712901466984323496',
        'candidate',
        130,
        7,
        30,
        '',
        'v2_041 seed: AI founder/operator pain and workflow tooling.'
    ),
    (
        'twitter',
        'twitter_community',
        'Customer Success & Support',
        'https://x.com/i/communities/1772996341972181112',
        'candidate',
        140,
        7,
        30,
        '',
        'v2_041 seed: customer success, retention, and support operations.'
    ),
    (
        'twitter',
        'twitter_community',
        'No-Code Builders',
        'https://x.com/i/communities/1688925993895868794',
        'candidate',
        150,
        7,
        30,
        '',
        'v2_041 seed: no-code builders and workflow automation pain.'
    )
ON CONFLICT (source, venue_type, url_or_id) DO NOTHING;

UPDATE source_runtime_config
SET
    query = CASE
        WHEN source = 'youtube' AND (
            query = '' OR
            query = 'small business software problems'
        ) THEN
            'customer success churn onboarding SaaS' || E'\n' ||
            'small business workflow automation problems' || E'\n' ||
            'CRM setup pain founders' || E'\n' ||
            'support ops ticketing spreadsheets' || E'\n' ||
            'no-code automation founders'
        ELSE query
    END,
    fetch_limit = CASE
        WHEN source = 'youtube' AND fetch_limit IN (0, 100, 200) THEN 250
        ELSE fetch_limit
    END,
    notes = CASE
        WHEN source = 'youtube' THEN
            'Enabled source. Query is a newline-delimited YouTube search pool for SaaS, support ops, workflow automation, CRM, and no-code pain.'
        ELSE notes
    END,
    updated_at = NOW()
WHERE source = 'youtube';

COMMIT;
