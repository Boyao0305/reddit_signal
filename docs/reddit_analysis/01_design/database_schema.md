# Database Schema — LLM Analyse v2 (Phase 2 revision)

> **Status:** Reflects live schema as of 2026-05-04 + Phase 2 additions (proposed)
> **Database:** `reddit_raw` (PostgreSQL with pgvector)
> **Owner:** `llmanalyse`
> **Companion:** `00_system_design_v2.md` (this directory)
> **Convention:** All migrations idempotent (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`).

This file documents (a) **the current live schema** as it actually exists on disk after `v2-phase1`, and (b) **the additions Phase 2 will introduce**. The legacy reference doc `../02_database_schema.md` predates the live migrations and is superseded.

---

## Layer Map (revised)

| Layer | Tables | Versioned by | Status |
|-------|--------|--------------|--------|
| **L1 — Raw** | `subreddits`, `posts`, `comments`, `app_store_apps`, `app_search_cache`, `processed_posts` | none — sacred | live |
| **L2 — Projected** | `signal_events`, `source_capabilities` | none — drop & rebuild | live (4.0M rows) |
| **L3 — Extracted** | `pain_objects` | `prompt_version` column | live (426K rows) |
| **L4 — Clustered + Judged** | `analysis_runs` (alias view: `clustering_runs`), `pain_clusters`, `pain_cluster_members`, `cluster_audience`, `cluster_quality_metrics` *(NEW Phase 2)*, `viability_scores` *(NEW Phase 2 — fused quality+viability)*, `pain_volume_weekly`, `candidate_promotion_log` | `analysis_runs.id` (FK) | partial — quality/viability/velocity Phase 2 |
| **L5 — Competition** | `cluster_competition` (legacy v1 only — deprecated, will be redesigned) | `analysis_run_id` | DEFERRED — see system design §6 |
| **L6 — Viability** | *(merged into L4)* | n/a | ELIMINATED — replaced by fused L4 LLM judgment |
| **Output** | `v_market_opportunities`, `v_market_opportunities_ranked`, `v_market_opportunities_pregate`, `v_cluster_velocity`, fn `fn_compute_pareto_layers` (VIEWs + function, Phase 2 + 3B) | parameterized by GUCs | live |

---

## L1 — Raw (live, unchanged)

Existing Reddit + Play Store tables. **Do not modify.** Schema for `subreddits`, `posts`, `comments`, `app_store_apps`, `app_search_cache`, `processed_posts` matches what was documented in `../02_database_schema.md` §L1.

---

## L2 — Projected (live)

```sql
CREATE TABLE signal_events (
    id               BIGSERIAL PRIMARY KEY,
    source           VARCHAR(32)  NOT NULL,
    source_table     VARCHAR(64)  NOT NULL,
    source_row_id    VARCHAR(64)  NOT NULL,
    event_type       VARCHAR(32)  NOT NULL,
    event_time       TIMESTAMPTZ  NOT NULL,
    surface          VARCHAR(200),
    author           VARCHAR(200),
    parent_event_id  BIGINT       REFERENCES signal_events(id) ON DELETE SET NULL,
    title            TEXT,
    text             TEXT,
    engagement_score DOUBLE PRECISION,
    raw_meta         JSONB,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_signal_event_provenance UNIQUE (source, source_table, source_row_id)
);
CREATE INDEX ix_signal_events_event_time          ON signal_events (event_time);
CREATE INDEX ix_signal_events_parent              ON signal_events (parent_event_id);
CREATE INDEX ix_signal_events_source_parent_null  ON signal_events (source) WHERE parent_event_id IS NULL;
CREATE INDEX ix_signal_events_source_type         ON signal_events (source, event_type);
CREATE INDEX ix_signal_events_surface             ON signal_events (surface);

CREATE TABLE source_capabilities (
    source              VARCHAR(32) PRIMARY KEY,
    produces_pain       BOOLEAN NOT NULL DEFAULT FALSE,
    produces_competitor BOOLEAN NOT NULL DEFAULT FALSE,
    produces_traction   BOOLEAN NOT NULL DEFAULT FALSE,
    produces_temporal   BOOLEAN NOT NULL DEFAULT FALSE,
    notes               TEXT,
    registered_at       TIMESTAMPTZ DEFAULT NOW()
);
```

Span: `2025-10-09 → 2026-05-01`, 4,022,490 rows (only `source='reddit'` so far).

---

## L3 — Extracted (live)

```sql
CREATE TABLE pain_objects (
    id              SERIAL PRIMARY KEY,
    post_id         INT REFERENCES posts(id)    ON DELETE CASCADE,        -- legacy, nullable
    comment_id      INT REFERENCES comments(id) ON DELETE SET NULL,       -- legacy, nullable
    subreddit_name  VARCHAR(100),
    pain_statement  TEXT NOT NULL,
    raw_quote       TEXT NOT NULL,
    mentioned_tools TEXT[],
    is_workaround   BOOLEAN NOT NULL DEFAULT FALSE,
    source_author   VARCHAR(100),
    sentiment_score DOUBLE PRECISION,
    specificity     DOUBLE PRECISION,                -- spaCy heuristic [0,1]
    community_score DOUBLE PRECISION,
    source_created  TIMESTAMPTZ,
    embedding       VECTOR(384),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    signal_event_id BIGINT       REFERENCES signal_events(id),            -- v2 join path
    prompt_version  VARCHAR(32) NOT NULL DEFAULT 'v1',
    CONSTRAINT uq_pain_post_statement UNIQUE (post_id, pain_statement)
);
CREATE INDEX ix_pain_signal_event   ON pain_objects (signal_event_id);
CREATE INDEX ix_pain_prompt_version ON pain_objects (prompt_version);
CREATE INDEX ix_pain_subreddit      ON pain_objects (subreddit_name);
CREATE INDEX ix_pain_post_id        ON pain_objects (post_id);
CREATE INDEX ix_pain_created        ON pain_objects (source_created);
```

> **Note:** `pain_objects.cluster_id` was dropped in Phase 1 step 6. `pain_cluster_members` is the sole join path to clusters.

---

## L4 — Clustered + Judged

### Existing tables (live)

#### `analysis_runs`

```sql
CREATE TABLE analysis_runs (
    id                   SERIAL PRIMARY KEY,
    window_start         TIMESTAMPTZ NOT NULL,
    window_end           TIMESTAMPTZ NOT NULL,
    resolution           VARCHAR(20) NOT NULL DEFAULT 'adhoc',
    params               JSONB,
    cluster_count        INT,
    noise_count          INT,
    total_points         INT,
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    label                TEXT,
    pain_filter          JSONB,
    is_legacy            BOOLEAN NOT NULL DEFAULT FALSE,
    started_at           TIMESTAMPTZ,
    finished_at          TIMESTAMPTZ,
    candidate_thresholds JSONB                       -- versioned gate config
);

-- writable VIEW alias kept for backward compatibility
-- clustering_runs is a 1:1 view over analysis_runs
```

#### `pain_clusters`

```sql
CREATE TABLE pain_clusters (
    id                     SERIAL PRIMARY KEY,
    run_id                 INT REFERENCES analysis_runs(id),
    label                  TEXT,
    member_count           INT NOT NULL,
    coherence              DOUBLE PRECISION,
    centroid               VECTOR(384),
    is_candidate           BOOLEAN NOT NULL DEFAULT FALSE,
    volume_4w              INT,
    volume_prior_4w        INT,
    created_at             TIMESTAMPTZ DEFAULT NOW(),

    -- ⚠ v1 legacy columns (DEPRECATED — do not reference in Phase 2+)
    intensity              DOUBLE PRECISION,
    prevalence             DOUBLE PRECISION,
    competitive_density    DOUBLE PRECISION,
    opportunity_score      DOUBLE PRECISION,
    velocity               DOUBLE PRECISION,
    competition_density    DOUBLE PRECISION,
    market_gap             DOUBLE PRECISION,
    hhi                    DOUBLE PRECISION,
    cr4                    DOUBLE PRECISION,
    weighted_rating        DOUBLE PRECISION,
    complaint_ratio        DOUBLE PRECISION,
    active_ratio           DOUBLE PRECISION,
    total_installs         BIGINT,
    golden_quadrant        VARCHAR(20),
    practicality_score     DOUBLE PRECISION,
    practicality_reasoning TEXT,
    practicality_tags      TEXT[]
);
CREATE INDEX ix_pain_clusters_run        ON pain_clusters (run_id);
CREATE INDEX ix_pain_clusters_candidate  ON pain_clusters (run_id, is_candidate) WHERE is_candidate;
```

> **Cleanup task:** v1 legacy columns to be dropped in a Phase 2.x migration after `v_market_opportunities` is in production.

#### `pain_cluster_members`

```sql
CREATE TABLE pain_cluster_members (
    pain_id    INT NOT NULL REFERENCES pain_objects(id)  ON DELETE CASCADE,
    cluster_id INT NOT NULL REFERENCES pain_clusters(id) ON DELETE CASCADE,
    PRIMARY KEY (pain_id, cluster_id)
);
CREATE INDEX ix_pcm_cluster ON pain_cluster_members (cluster_id);
```

#### `cluster_audience`

```sql
CREATE TABLE cluster_audience (
    id                   SERIAL PRIMARY KEY,
    cluster_id           INT NOT NULL REFERENCES pain_clusters(id) ON DELETE CASCADE,
    classifier_version   VARCHAR(32) NOT NULL DEFAULT 'v1',
    audience_type        VARCHAR(16) NOT NULL
        CHECK (audience_type IN ('B2B','B2C','mixed','unclear')),
    confidence           DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    icp_hint             TEXT,
    reasoning            TEXT,
    sample_pain_ids      INT[],
    surface_distribution JSONB,
    llm_model            VARCHAR(64),
    llm_input_tokens     INT,
    llm_output_tokens    INT,
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_cluster_audience_version UNIQUE (cluster_id, classifier_version)
);
CREATE INDEX ix_cluster_audience_cluster ON cluster_audience (cluster_id);
CREATE INDEX ix_cluster_audience_type    ON cluster_audience (audience_type);
```

#### `pain_volume_weekly` (live but currently empty)

```sql
CREATE TABLE pain_volume_weekly (
    cluster_run_id INT  NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    cluster_id     INT  NOT NULL REFERENCES pain_clusters(id) ON DELETE CASCADE,
    week_start     DATE NOT NULL,
    audience_type  VARCHAR(16) NOT NULL DEFAULT 'unknown',
    function_tag   VARCHAR(64) NOT NULL DEFAULT 'unknown',
    pain_count     INT  NOT NULL,
    share_global   DOUBLE PRECISION,
    share_field    DOUBLE PRECISION,
    PRIMARY KEY (cluster_run_id, cluster_id, week_start, audience_type, function_tag)
);
CREATE INDEX ix_pvw_week ON pain_volume_weekly (week_start);
```

#### `candidate_promotion_log`

```sql
CREATE TABLE candidate_promotion_log (
    id                 SERIAL PRIMARY KEY,
    cluster_run_id     INT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    cluster_id         INT NOT NULL REFERENCES pain_clusters(id) ON DELETE CASCADE,
    promoted_at        TIMESTAMPTZ DEFAULT NOW(),
    metrics_snapshot   JSONB NOT NULL,
    threshold_snapshot JSONB NOT NULL,
    direction          VARCHAR(16) NOT NULL CHECK (direction IN ('promoted','demoted'))
);
CREATE INDEX ix_promotion_run    ON candidate_promotion_log (cluster_run_id);
CREATE INDEX ix_promotion_recent ON candidate_promotion_log (promoted_at DESC);
```

---

### NEW Phase 2 — `cluster_quality_metrics`

Tier-1 deterministic quality signals. Recomputable from raw data; one row per `(cluster_run_id, cluster_id)`.

```sql
CREATE TABLE IF NOT EXISTS cluster_quality_metrics (
    cluster_run_id        INT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    cluster_id            INT NOT NULL REFERENCES pain_clusters(id) ON DELETE CASCADE,
    -- diversity (denominator = member_count)
    source_diversity      DOUBLE PRECISION,        -- distinct signal_event_id / member_count
    thread_diversity      DOUBLE PRECISION,        -- distinct post_id / member_count
    author_diversity      DOUBLE PRECISION,        -- distinct source_author / member_count
    subreddit_count       INT,                     -- distinct subreddit count
    -- content character
    specificity_mean      DOUBLE PRECISION,        -- mean pain_objects.specificity
    workaround_ratio      DOUBLE PRECISION,        -- count(is_workaround)/member_count
    -- structural
    separation            DOUBLE PRECISION,        -- min cosine distance to nearest centroid (same run)
    nearest_cluster_id    INT,                     -- which cluster set the separation
    temporal_spread_days  INT,                     -- max(event_time)-min(event_time) in days
    -- bookkeeping
    computed_at           TIMESTAMPTZ DEFAULT NOW(),
    metrics_version       VARCHAR(16) NOT NULL DEFAULT 'v1',
    PRIMARY KEY (cluster_run_id, cluster_id)
);
CREATE INDEX IF NOT EXISTS ix_cqm_thread_diversity ON cluster_quality_metrics (thread_diversity);
CREATE INDEX IF NOT EXISTS ix_cqm_specificity     ON cluster_quality_metrics (specificity_mean);
```

`coherence` and `member_count` are intentionally **not** copied here — they already live on `pain_clusters` and the VIEW joins both.

---

### NEW Phase 2 — `viability_scores` (fused quality + viability)

One row per `(viability_run_id, cluster_id)`. Stores both halves of the fused LLM call. Per user decision, no composite score; downstream consumers filter on individual columns.

```sql
CREATE TABLE IF NOT EXISTS viability_runs (
    id              SERIAL PRIMARY KEY,
    cluster_run_id  INT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    llm_model       VARCHAR(64) NOT NULL,
    prompt_version  VARCHAR(32) NOT NULL DEFAULT 'v1',
    params          JSONB,                          -- sampling config, temperature, etc.
    n_clusters      INT,
    n_succeeded     INT,
    n_failed        INT,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS viability_scores (
    viability_run_id      INT NOT NULL REFERENCES viability_runs(id) ON DELETE CASCADE,
    cluster_id            INT NOT NULL REFERENCES pain_clusters(id) ON DELETE CASCADE,

    -- quality half
    is_real_pain          BOOLEAN NOT NULL,
    is_software_solvable  BOOLEAN NOT NULL,
    q_specificity         SMALLINT CHECK (q_specificity      BETWEEN 1 AND 5),
    q_noise_flag          VARCHAR(32),               -- null | 'venting' | 'off_topic_drift'
                                                     --      | 'duplicate_threads' | 'too_generic'

    -- viability half
    v_buildability        SMALLINT CHECK (v_buildability     BETWEEN 1 AND 5),
    v_icp_clarity         SMALLINT CHECK (v_icp_clarity      BETWEEN 1 AND 5),
    v_moat_potential      SMALLINT CHECK (v_moat_potential   BETWEEN 1 AND 5),
    v_rationale           TEXT,

    -- bookkeeping
    sample_pain_ids       INT[],
    llm_input_tokens      INT,
    llm_output_tokens     INT,
    raw_response          JSONB,
    created_at            TIMESTAMPTZ DEFAULT NOW(),

    PRIMARY KEY (viability_run_id, cluster_id)
);
CREATE INDEX IF NOT EXISTS ix_vs_cluster      ON viability_scores (cluster_id);
CREATE INDEX IF NOT EXISTS ix_vs_real_pain    ON viability_scores (is_real_pain) WHERE is_real_pain;
CREATE INDEX IF NOT EXISTS ix_vs_saas         ON viability_scores (is_software_solvable) WHERE is_software_solvable;
```

> The legacy `cluster_competition` table (Play Store v1 results) is retained read-only and ignored by Phase 2 code. It will be dropped or replaced when L5 is redesigned.

---

## Output VIEWs (NEW Phase 2)

### `v_cluster_velocity`

Helper view computing 4w/prior-4w ratios from `pain_volume_weekly`. Definition deferred to implementation; sketch:

```sql
CREATE OR REPLACE VIEW v_cluster_velocity AS
WITH windowed AS (
    SELECT
        cluster_run_id,
        cluster_id,
        SUM(pain_count) FILTER (WHERE week_start >= CURRENT_DATE - INTERVAL '4 weeks') AS last_4w,
        SUM(pain_count) FILTER (WHERE week_start >= CURRENT_DATE - INTERVAL '8 weeks'
                                 AND  week_start <  CURRENT_DATE - INTERVAL '4 weeks') AS prior_4w
    FROM pain_volume_weekly
    GROUP BY cluster_run_id, cluster_id
)
SELECT
    cluster_run_id,
    cluster_id,
    last_4w  AS pain_count_last_4w,
    prior_4w AS pain_count_prior_4w,
    CASE
        WHEN COALESCE(prior_4w, 0) < 3 OR COALESCE(last_4w, 0) < 3 THEN NULL
        ELSE last_4w::float / prior_4w
    END AS velocity_4w_ratio
FROM windowed;
```

### `v_market_opportunities`

Backward-compat shim since 2026-06-08 (Phase 3B): a 34-column projection of `v_market_opportunities_ranked` matching the v2_013 view shape. New code reads `v_market_opportunities_ranked`; legacy probes (test fixtures, ad-hoc psql, the count-of-740 invariant) keep working unchanged. Hard filter unchanged from v2_013 + LD-#33: `pc.is_candidate ∧ vs.is_real_pain IS TRUE ∧ vs.is_software_solvable IS TRUE`. See system design §4 / §5.

### `v_market_opportunities_pregate` (NEW Phase 3B)

Internal CTE-style view. Replicates the gated join, then adds: `tool_density` (from `pain_cluster_members ⋈ pain_objects`), within-run percent-rank percentiles for the axis inputs, the three Q/M/S axes, the ε=0.05-floored harmonic balance score, the `low_confidence_s` flag (cluster has <20 members with non-empty `mentioned_tools`), and the within-audience eligibility partition (Q & S ≥ p25). Feeds `fn_compute_pareto_layers` and `v_market_opportunities_ranked`. Same three GUCs as the legacy view.

### `v_market_opportunities_ranked` (NEW Phase 3B)

The unified ranking surface read by Dashboard v2 since 2026-06-08. `v_market_opportunities_pregate` joined to `fn_compute_pareto_layers` (per audience, ≤5 layers). Adds 7 fields on top of the legacy projection: `pareto_layer ∈ {1..5, NULL}`, `balance_score ∈ [0,1] ∪ NULL`, `q_axis`, `m_axis`, `s_axis ∈ [0,1]`, `is_eligible BOOL`, `low_confidence_s BOOL`. Default API sort: `pareto_layer ASC NULLS LAST, balance_score DESC NULLS LAST`. Uses `WITH pg AS MATERIALIZED (…)` to pin the expensive pregate scan to one materialization (PG12+ optimization fence; reddit_raw is PG16). See LD-#36 / #37 / #38 and system design §4 / §5.

### `fn_compute_pareto_layers(p_run_id INT, p_audience TEXT)` (NEW Phase 3B)

`plpgsql` STABLE function. Reads `v_market_opportunities_pregate` (parameterized by the calling session's GUCs), loads the eligible candidate set into parallel arrays, and iteratively peels the Pareto frontier on `(q_axis, m_axis, s_axis)` up to 5 layers. Returns `(cluster_id, pareto_layer)` tuples; ineligible / balance-NULL clusters and clusters beyond layer 5 are not returned (`pareto_layer` resolves to `NULL` via the `LEFT JOIN` in the ranked view).

---

## Migration order (Phase 2 → Phase 3B)

Phase 2:
1. `cluster_quality_metrics` — create table, populate from a single SQL+numpy job per run.
2. `viability_runs` + `viability_scores` — create tables; populate via fused LLM scorer (Component A).
3. `pain_volume_weekly` — already exists; populate via aggregation job (Component B).
4. `v_cluster_velocity` — create view.
5. `v_market_opportunities` — create view (initial, since superseded by the shim form below).

Phase 3B (2026-06-08, `migrations/v2_015_path_c_ranking.sql`, idempotent):
6. `fn_compute_pareto_layers` — create plpgsql function.
7. `v_market_opportunities_pregate` — create view.
8. `v_market_opportunities_ranked` — create view (with `MATERIALIZED` CTE fence).
9. `v_market_opportunities` — `DROP CASCADE` + recreate as a 34-column projection of `_ranked` (backward-compat shim).
10. *(later, separate task)* drop deprecated columns from `pain_clusters`; drop or redesign `cluster_competition`.

All migrations idempotent (`CREATE OR REPLACE …`, `CREATE TABLE IF NOT EXISTS`). Phase 1 baseline (`v2.0-phase1` tag, run_id=9) is the fixed point — no re-clustering required for Phase 2 or Phase 3B.

---

## CHANGELOG

- **2026-06-08** — Phase 3B (Path C ranking) shipped. §Layer Map Output row updated to list the new pregate / ranked views + function. New §Output subsections for `v_market_opportunities_pregate`, `v_market_opportunities_ranked`, and `fn_compute_pareto_layers`. `v_market_opportunities` recharacterized as a backward-compat 34-column shim. Migration order extended to steps 6–9 covering the 2026-06-08 deployment.
