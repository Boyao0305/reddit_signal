-- v2 migration 025 — runtime configuration for source adapters.
--
-- This table controls which source adapters participate in local/worker
-- fetch, projection, and extraction loops. New sources are disabled by default;
-- reddit is seeded to preserve the current production behavior.

CREATE TABLE IF NOT EXISTS source_runtime_config (
    source          TEXT PRIMARY KEY,
    enabled         BOOLEAN NOT NULL DEFAULT FALSE,
    fetch_enabled   BOOLEAN NOT NULL DEFAULT FALSE,
    project_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    extract_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    query           TEXT NOT NULL DEFAULT '',
    lookback_days   INTEGER NOT NULL DEFAULT 7 CHECK (lookback_days >= 0),
    fetch_limit     INTEGER NOT NULL DEFAULT 10000 CHECK (fetch_limit >= 0),
    priority        INTEGER NOT NULL DEFAULT 100,
    notes           TEXT NOT NULL DEFAULT '',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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
    'reddit',
    TRUE,
    TRUE,
    TRUE,
    TRUE,
    '',
    7,
    10000,
    10,
    'Default source; preserves existing daily pipeline behavior.'
)
ON CONFLICT (source) DO NOTHING;

-- Canonical multi-source de-duplication. The older reddit-only
-- uq_pain_post_statement remains in place for backward compatibility.
CREATE UNIQUE INDEX IF NOT EXISTS uq_pain_signal_event_statement_prompt
    ON pain_objects (signal_event_id, pain_statement, prompt_version)
    WHERE signal_event_id IS NOT NULL;
