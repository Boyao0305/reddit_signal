-- v2 migration 001 — Layer 2: signal_events + source_capabilities
-- Idempotent. Safe to re-run.

BEGIN;

CREATE TABLE IF NOT EXISTS signal_events (
    id                BIGSERIAL PRIMARY KEY,

    source            VARCHAR(32) NOT NULL,
    source_table      VARCHAR(64) NOT NULL,
    source_row_id     VARCHAR(64) NOT NULL,

    event_type        VARCHAR(32) NOT NULL,
    event_time        TIMESTAMP WITH TIME ZONE NOT NULL,

    surface           VARCHAR(200),
    author            VARCHAR(200),

    parent_event_id   BIGINT REFERENCES signal_events(id) ON DELETE SET NULL,

    title             TEXT,
    text              TEXT,

    engagement_score  DOUBLE PRECISION,

    raw_meta          JSONB,

    created_at        TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    CONSTRAINT uq_signal_event_provenance
        UNIQUE (source, source_table, source_row_id)
);

CREATE INDEX IF NOT EXISTS ix_signal_events_source_type
    ON signal_events (source, event_type);
CREATE INDEX IF NOT EXISTS ix_signal_events_event_time
    ON signal_events (event_time);
CREATE INDEX IF NOT EXISTS ix_signal_events_parent
    ON signal_events (parent_event_id);
CREATE INDEX IF NOT EXISTS ix_signal_events_surface
    ON signal_events (surface);
CREATE INDEX IF NOT EXISTS ix_signal_events_source_parent_null
    ON signal_events (source) WHERE parent_event_id IS NULL;

CREATE TABLE IF NOT EXISTS source_capabilities (
    source                  VARCHAR(32) PRIMARY KEY,
    produces_pain           BOOLEAN NOT NULL DEFAULT FALSE,
    produces_competitor     BOOLEAN NOT NULL DEFAULT FALSE,
    produces_traction       BOOLEAN NOT NULL DEFAULT FALSE,
    produces_temporal       BOOLEAN NOT NULL DEFAULT FALSE,
    notes                   TEXT,
    registered_at           TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

INSERT INTO source_capabilities (source, produces_pain, produces_temporal, notes)
VALUES ('reddit', TRUE, TRUE, 'Reddit via Arctic Shift; produces post + comment events')
ON CONFLICT (source) DO NOTHING;

COMMIT;
