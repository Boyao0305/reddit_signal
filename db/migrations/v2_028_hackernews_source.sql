-- v2 migration 028 — Hacker News source adapter support.
-- Idempotent. Adds the HN L1 table and seeds a disabled runtime config row.

BEGIN;

CREATE TABLE IF NOT EXISTS hackernews_items (
    hn_id          BIGINT PRIMARY KEY,
    item_type      TEXT NOT NULL,
    by_author      TEXT,
    time_utc       TIMESTAMPTZ,
    title          TEXT,
    text           TEXT,
    url            TEXT,
    score          INTEGER,
    descendants    INTEGER,
    parent_hn_id   BIGINT,
    root_hn_id     BIGINT,
    depth          INTEGER NOT NULL DEFAULT 0,
    fetched_from   TEXT NOT NULL DEFAULT '',
    kids           BIGINT[] NOT NULL DEFAULT '{}',
    parts          BIGINT[] NOT NULL DEFAULT '{}',
    deleted        BOOLEAN NOT NULL DEFAULT FALSE,
    dead           BOOLEAN NOT NULL DEFAULT FALSE,
    raw_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_hn_items_type_time
    ON hackernews_items (item_type, time_utc DESC);
CREATE INDEX IF NOT EXISTS ix_hn_items_root
    ON hackernews_items (root_hn_id);
CREATE INDEX IF NOT EXISTS ix_hn_items_parent
    ON hackernews_items (parent_hn_id);
CREATE INDEX IF NOT EXISTS ix_hn_items_fetched_from
    ON hackernews_items (fetched_from);

CREATE TABLE IF NOT EXISTS processed_signal_events (
    signal_event_id BIGINT PRIMARY KEY REFERENCES signal_events(id) ON DELETE CASCADE,
    source          TEXT NOT NULL,
    processed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_processed_signal_events_source
    ON processed_signal_events (source, processed_at DESC);

INSERT INTO source_capabilities (
    source,
    produces_pain,
    produces_traction,
    produces_temporal,
    notes
)
VALUES (
    'hackernews',
    TRUE,
    TRUE,
    TRUE,
    'Hacker News via official Firebase API; produces story/job/poll + comment events'
)
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
VALUES (
    'hackernews',
    FALSE,
    FALSE,
    FALSE,
    FALSE,
    'new,ask,show',
    7,
    1000,
    20,
    'Disabled by default. Query accepts HN story lists such as new, ask, show, top, best, job.'
)
ON CONFLICT (source) DO NOTHING;

COMMIT;
