-- v2 migration 043 - source-wide historical backfill queue and direct social venue expansion.
--
-- Facebook seeds in this migration are numeric group URLs only. SocialCrawl
-- /facebook/group/posts rejects vanity group URLs, so do not insert nonnumeric
-- facebook.com/groups/<slug>/ values here.

BEGIN;

CREATE TABLE IF NOT EXISTS source_backfill_jobs (
    id BIGSERIAL PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    sources TEXT[] NOT NULL DEFAULT '{}',
    scope TEXT NOT NULL DEFAULT 'active'
        CHECK (scope IN ('active', 'candidate', 'all', 'selected', 'query')),
    mode TEXT NOT NULL DEFAULT 'fetch-project'
        CHECK (mode IN ('fetch', 'project', 'fetch-project', 'extract')),
    date_from TIMESTAMPTZ NOT NULL,
    date_to TIMESTAMPTZ NOT NULL,
    chunk_days INTEGER NOT NULL DEFAULT 7 CHECK (chunk_days > 0 AND chunk_days <= 31),
    requested_by TEXT,
    params JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error TEXT,
    total_chunks INTEGER NOT NULL DEFAULT 0,
    queued_chunks INTEGER NOT NULL DEFAULT 0,
    running_chunks INTEGER NOT NULL DEFAULT 0,
    succeeded_chunks INTEGER NOT NULL DEFAULT 0,
    failed_chunks INTEGER NOT NULL DEFAULT 0,
    skipped_chunks INTEGER NOT NULL DEFAULT 0,
    empty_chunks INTEGER NOT NULL DEFAULT 0,
    rows_fetched INTEGER NOT NULL DEFAULT 0,
    rows_projected INTEGER NOT NULL DEFAULT 0,
    rows_extracted INTEGER NOT NULL DEFAULT 0,
    CHECK (date_to > date_from)
);

CREATE INDEX IF NOT EXISTS ix_source_backfill_jobs_status_created
    ON source_backfill_jobs (status, created_at DESC);

CREATE TABLE IF NOT EXISTS source_backfill_chunks (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES source_backfill_jobs(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT 'source',
    target_id TEXT NOT NULL DEFAULT '',
    target_label TEXT NOT NULL DEFAULT '',
    query TEXT NOT NULL DEFAULT '',
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    coverage_mode TEXT NOT NULL DEFAULT 'best_effort_window'
        CHECK (coverage_mode IN ('full_window', 'best_effort_window', 'recent_only', 'unsupported')),
    coverage_note TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'fetch-project'
        CHECK (mode IN ('fetch', 'project', 'fetch-project', 'extract')),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'skipped', 'empty')),
    rows_fetched INTEGER NOT NULL DEFAULT 0,
    rows_projected INTEGER NOT NULL DEFAULT 0,
    rows_extracted INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (window_end > window_start),
    UNIQUE (job_id, source, target_type, target_id, query, window_start, window_end)
);

CREATE INDEX IF NOT EXISTS ix_source_backfill_chunks_status
    ON source_backfill_chunks (status, job_id, id);

CREATE INDEX IF NOT EXISTS ix_source_backfill_chunks_source_window
    ON source_backfill_chunks (source, window_start, window_end);

CREATE OR REPLACE FUNCTION touch_source_backfill_chunks_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_source_backfill_chunks_updated_at
    ON source_backfill_chunks;
CREATE TRIGGER trg_source_backfill_chunks_updated_at
BEFORE UPDATE ON source_backfill_chunks
FOR EACH ROW
EXECUTE FUNCTION touch_source_backfill_chunks_updated_at();

UPDATE social_source_venues
SET status = 'paused',
    notes = CONCAT_WS(
        E'\n',
        NULLIF(notes, ''),
        'Paused by v2_043: pending relevance/name verification before Facebook expansion.'
    ),
    metrics = COALESCE(metrics, '{}'::jsonb) || '{"quality_status":"pending_review"}'::jsonb
WHERE source = 'facebook'
  AND venue_type = 'facebook_group'
  AND url_or_id = 'https://www.facebook.com/groups/1998012103811981/'
  AND status = 'active';

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
    ('facebook', 'facebook_group', 'SaaS Marketing Group', 'https://www.facebook.com/groups/4098505490210812/', 'candidate', 90, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: SaaS and growth discussion candidate.'),
    ('facebook', 'facebook_group', 'Small Business Owners and Entrepreneurs', 'https://www.facebook.com/groups/626660935015474/', 'candidate', 95, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: SMB operator pain candidate.'),
    ('facebook', 'facebook_group', 'Small Business Owners', 'https://www.facebook.com/groups/2447572192031896/', 'candidate', 100, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: SMB operator pain candidate.'),
    ('facebook', 'facebook_group', 'USA Small Business Owners Community', 'https://www.facebook.com/groups/1380135855880231/', 'candidate', 105, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: SMB operator pain candidate.'),
    ('facebook', 'facebook_group', 'Business Owners, Entrepreneurs & Startups', 'https://www.facebook.com/groups/253853437316337/', 'candidate', 110, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: startup and business owner discussions.'),
    ('facebook', 'facebook_group', 'Entrepreneurs and Business Owners of New Jersey', 'https://www.facebook.com/groups/629539353792944/', 'candidate', 115, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: regional SMB pain candidate.'),
    ('facebook', 'facebook_group', 'Chicago Small Business Owners & Entrepreneurs', 'https://www.facebook.com/groups/537984549703356/', 'candidate', 120, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: regional SMB pain candidate.'),
    ('facebook', 'facebook_group', 'Small Business Networking and Referrals Chicagoland', 'https://www.facebook.com/groups/3446201918834296/', 'candidate', 125, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: regional SMB referral and ops pain.'),
    ('facebook', 'facebook_group', 'Round Rock Small Business Networking Group', 'https://www.facebook.com/groups/267282410511867/', 'candidate', 130, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: regional SMB pain candidate.'),
    ('facebook', 'facebook_group', 'San Antonio Networking Group', 'https://www.facebook.com/groups/835418491174863/', 'candidate', 135, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: regional SMB pain candidate.'),
    ('facebook', 'facebook_group', 'Maine Business Networking', 'https://www.facebook.com/groups/560840802998331/', 'candidate', 140, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: regional SMB pain candidate.'),
    ('facebook', 'facebook_group', 'Los Angeles Small Business Group', 'https://www.facebook.com/groups/42088924145/', 'candidate', 145, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: regional SMB pain candidate.'),
    ('facebook', 'facebook_group', 'Dunedin Small Business Networking', 'https://www.facebook.com/groups/883525981195832/', 'candidate', 150, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: regional SMB pain candidate.'),
    ('facebook', 'facebook_group', 'No Code Community', 'https://www.facebook.com/groups/1636991833121307/', 'candidate', 155, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: no-code builders and workflow pain.'),
    ('facebook', 'facebook_group', 'Stackby: No Code Entrepreneurs and Builders', 'https://www.facebook.com/groups/200732620977669/', 'candidate', 160, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: no-code entrepreneurs and builders.'),
    ('facebook', 'facebook_group', 'Without Code Community', 'https://www.facebook.com/groups/292693001288573/', 'candidate', 165, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: no-code website/app builder pain.'),
    ('facebook', 'facebook_group', 'A I for Business Owners', 'https://www.facebook.com/groups/584765680795689/', 'candidate', 170, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: AI for SMB/business owner discussions.'),
    ('facebook', 'facebook_group', 'Upleveling Your Business with AI', 'https://www.facebook.com/groups/122895862472702/', 'candidate', 175, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: AI adoption and SMB workflow pain.'),
    ('facebook', 'facebook_group', 'AI Hub Community', 'https://www.facebook.com/groups/24312842445046541/', 'candidate', 180, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: AI tools and workflow discussion.'),
    ('facebook', 'facebook_group', 'AI / Small Business Automation', 'https://www.facebook.com/groups/180837768789806/', 'candidate', 185, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: AI automation for SMB candidate.'),
    ('facebook', 'facebook_group', 'AI community / automation learning', 'https://www.facebook.com/groups/698593531630485/', 'candidate', 190, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: AI automation learning and tool pain.'),
    ('facebook', 'facebook_group', 'AI tools for entrepreneurs', 'https://www.facebook.com/groups/475565033219356/', 'candidate', 195, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: AI tools for entrepreneurs candidate.'),
    ('facebook', 'facebook_group', 'AI for small business owners', 'https://www.facebook.com/groups/619950578606875/', 'candidate', 200, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: AI adoption by small business owners.'),
    ('facebook', 'facebook_group', 'AI for Main Street candidate', 'https://www.facebook.com/groups/1397063724172373/', 'candidate', 205, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: AI for local business candidate.'),
    ('facebook', 'facebook_group', 'AI Software for local business candidate', 'https://www.facebook.com/groups/469820494480580/', 'candidate', 210, 7, 15, 'RECENT_ACTIVITY', 'v2_043 numeric seed: local business AI/CRM candidate.'),
    ('twitter', 'twitter_community', 'Build in Public', 'https://x.com/i/communities/1493446837214187523', 'candidate', 160, 7, 30, '', 'v2_043 seed: build-in-public founders.'),
    ('twitter', 'twitter_community', 'Indie Hackers', 'https://x.com/i/communities/1852284626317754858', 'candidate', 165, 7, 30, '', 'v2_043 seed: indie founders and SaaS builders.'),
    ('twitter', 'twitter_community', 'Build in Public 3.0', 'https://x.com/i/communities/1814594147195478202', 'candidate', 170, 7, 30, '', 'v2_043 seed: build-in-public founder discussions.'),
    ('twitter', 'twitter_community', 'BuildinPublic', 'https://x.com/i/communities/1495042358068477955', 'candidate', 175, 7, 30, '', 'v2_043 seed: build-in-public candidate.'),
    ('twitter', 'twitter_community', 'Tech Twitter', 'https://x.com/i/communities/1472105760389668865', 'candidate', 180, 7, 30, '', 'v2_043 seed: tech operator and founder discussion.'),
    ('twitter', 'twitter_community', 'build in community', 'https://x.com/i/communities/1521855238277414913', 'candidate', 185, 7, 30, '', 'v2_043 seed: founder building community.'),
    ('twitter', 'twitter_community', 'Product Hunt PH community', 'https://x.com/i/communities/1511695156881158148', 'candidate', 190, 7, 30, '', 'v2_043 seed: product launch and feedback.'),
    ('twitter', 'twitter_community', 'Product Hunt launch users', 'https://x.com/i/communities/1511240768618381316', 'candidate', 195, 7, 30, '', 'v2_043 seed: product launch support candidate.'),
    ('twitter', 'twitter_community', 'Cold Email & Sales', 'https://x.com/i/communities/1493822076280475651', 'candidate', 200, 7, 30, '', 'v2_043 seed: sales and outbound operator pain.'),
    ('twitter', 'twitter_community', 'Cold Outreach & Lead Gen', 'https://x.com/i/communities/1501203242641932292', 'candidate', 205, 7, 30, '', 'v2_043 seed: sales/outreach workflow pain.'),
    ('twitter', 'twitter_community', 'Business Owners & Entrepreneur', 'https://x.com/i/communities/1598630196323688448', 'candidate', 210, 7, 30, '', 'v2_043 seed: SMB and entrepreneur discussions.'),
    ('twitter', 'twitter_community', 'Marketing Twitter', 'https://x.com/i/communities/1473332064552378368', 'candidate', 215, 7, 30, '', 'v2_043 seed: marketing operator pain.'),
    ('twitter', 'twitter_community', 'B2B SaaS Growth', 'https://x.com/i/communities/1509818787159326726', 'candidate', 220, 7, 30, '', 'v2_043 seed: B2B SaaS growth candidate.'),
    ('twitter', 'twitter_community', 'The First Thousand', 'https://x.com/i/communities/1926186499399139650', 'candidate', 225, 7, 30, '', 'v2_043 seed: early customer acquisition and launch pain.'),
    ('twitter', 'twitter_community', 'Solopreneurs', 'https://x.com/i/communities/1500364132390150144', 'candidate', 230, 7, 30, '', 'v2_043 seed: solopreneur operator pain.'),
    ('twitter', 'twitter_community', 'Startup Club', 'https://x.com/i/communities/1900346497369333789', 'candidate', 235, 7, 30, '', 'v2_043 seed: startup founder candidate.'),
    ('twitter', 'twitter_community', 'Building on Shopify', 'https://x.com/i/communities/1521673414716444673', 'candidate', 240, 7, 30, '', 'v2_043 seed: ecommerce operator workflows.'),
    ('twitter', 'twitter_community', 'AI Automation Agencies', 'https://x.com/i/communities/1682653840775188480', 'candidate', 245, 7, 30, '', 'v2_043 seed: AI automation services and SMB demand.'),
    ('twitter', 'twitter_community', 'AI Agents', 'https://x.com/i/communities/1670204079619055616', 'candidate', 250, 7, 30, '', 'v2_043 seed: AI agents and automation.'),
    ('twitter', 'twitter_community', 'AI Agent Builders', 'https://x.com/i/communities/1918880733730603290', 'candidate', 255, 7, 30, '', 'v2_043 seed: AI agent builders.'),
    ('twitter', 'twitter_community', 'AI Engineering', 'https://x.com/i/communities/1929211810798043448', 'candidate', 260, 7, 30, '', 'v2_043 seed: AI engineering operator pain.'),
    ('twitter', 'twitter_community', 'Product Management', 'https://x.com/i/communities/1670313412323348481', 'candidate', 265, 7, 30, '', 'v2_043 seed: product management pain.'),
    ('twitter', 'twitter_community', 'Notion', 'https://x.com/i/communities/1453875227058974754', 'candidate', 270, 7, 30, '', 'v2_043 seed: workflow and knowledge management pain.'),
    ('twitter', 'twitter_community', 'SEO', 'https://x.com/i/communities/1506779096784769028', 'candidate', 275, 7, 30, '', 'v2_043 seed: marketing/SEO pain.'),
    ('twitter', 'twitter_community', 'Productivity', 'https://x.com/i/communities/1471492460329123840', 'candidate', 280, 7, 30, '', 'v2_043 seed: productivity and workflow discussions.'),
    ('twitter', 'twitter_community', 'Fail in Public', 'https://x.com/i/communities/1892177256048447870', 'candidate', 285, 7, 30, '', 'v2_043 seed: founder failure and pain signals.'),
    ('twitter', 'twitter_community', 'Ship in Public', 'https://x.com/i/communities/1999500623708868668', 'candidate', 290, 7, 30, '', 'v2_043 seed: shipping/building in public.'),
    ('twitter', 'twitter_community', 'Build in Public small group', 'https://x.com/i/communities/1944464523269955845', 'candidate', 295, 7, 30, '', 'v2_043 seed: build-in-public candidate.'),
    ('twitter', 'twitter_community', 'Builders.to', 'https://x.com/i/communities/1943895831322439993', 'candidate', 300, 7, 30, '', 'v2_043 seed: builders community candidate.'),
    ('twitter', 'twitter_community', 'Vibe Marketing', 'https://x.com/i/communities/1931201559251329449', 'candidate', 305, 7, 30, '', 'v2_043 seed: marketing candidate.'),
    ('twitter', 'twitter_community', 'Build In Public - SaaS', 'https://x.com/i/communities/1828843041869713562', 'candidate', 310, 7, 30, '', 'v2_043 seed: SaaS build-in-public candidate.'),
    ('twitter', 'twitter_community', 'e/acc', 'https://x.com/i/communities/1670821987235553282', 'candidate', 315, 7, 30, '', 'v2_043 seed: technology and AI acceleration discussions.'),
    ('twitter', 'twitter_community', 'AI-Native Designers', 'https://x.com/i/communities/1493639156739854336', 'candidate', 320, 7, 30, '', 'v2_043 seed: AI product/design workflows.'),
    ('twitter', 'twitter_community', 'YouTube Creators', 'https://x.com/i/communities/1471963429040799746', 'candidate', 325, 7, 30, '', 'v2_043 seed: creator operator pain.'),
    ('twitter', 'twitter_community', 'YouTube Automation', 'https://x.com/i/communities/1670833591264362496', 'candidate', 330, 7, 30, '', 'v2_043 seed: creator automation workflows.')
ON CONFLICT (source, venue_type, url_or_id) DO NOTHING;

DO $$
DECLARE
    bad_count INTEGER;
BEGIN
    SELECT COUNT(*)
    INTO bad_count
    FROM social_source_venues
    WHERE source = 'facebook'
      AND venue_type = 'facebook_group'
      AND notes LIKE 'v2_043 numeric seed:%'
      AND url_or_id !~ '^https://www\.facebook\.com/groups/[0-9]+/$';

    IF bad_count > 0 THEN
        RAISE EXCEPTION 'v2_043 Facebook seed contains nonnumeric group URL rows: %', bad_count;
    END IF;
END $$;

COMMIT;
