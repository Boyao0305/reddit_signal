# Phase 2C — Dashboard v2 design specification

> **v1.0 (2026-05-12) — implemented.** Notes: Data layer + API + frontend shipped in one session. 740 viable B2C clusters surface end-to-end. Migration v2_013 was tightened from `COALESCE`-to-true to `IS TRUE` semantics mid-build (see Decision #33). Sparkline gained an always-visible partial-week boundary marker to handle null-current-week (see failure log 2026-05-12). 11 minor spec-wording gaps surfaced in `03_handoffs/2026-05-12_implementation.md` for the next orchestrator pass.

> **Status:** v1.0 design — ready for implementation.
> **Created:** 2026-05-12.
> **Implementation owner:** Claude Code. All three migration SQL files have been authored by the Claude orchestrator (this session) and are in `migrations/`; Claude Code applies them via `psql` and verifies the acceptance queries in §8. Python and frontend code are Claude Code's to write from scratch.
> **Companion files:** `01_design/system_design.md` (architecture), `01_design/database_schema.md` (schema), `06_teamwork_protocol.md` (working pattern), `05_potential_implementations/signal_generation/*` (deferred designs).
> **Acceptance criteria:** §9 below. Implementation order: §10.

This file is self-contained for the implementer. It carries the full API contract, all SQL, the file tree, and the must-not-violate constraints. Other vault files do not need to be read to do the work.

---

## 1. Goal

Build a fresh React+Vite dashboard at `src/dashboard_2/` for inspecting and ranking the ~740 viable B2C cluster opportunities from `analysis_runs.id=10` / `viability_run_id=4`. The dashboard reads exclusively from a new `v_market_opportunities` SQL VIEW plus a few small helper endpoints; the underlying tables are not queried directly. It surfaces three-signal velocity (current intensity + normalized recency ratio + long-term slope) and supports a user-tunable composite ranking score whose weights are visible, session-local, and never feed any gate. The legacy v1 dashboard at `src/dashboard/` stays frozen and untouched.

## 2. Out of scope

- Authentication / multi-user state.
- Persisting weights, filters, or selections across sessions or page reloads.
- Population view / leaderboards (deferred until usage reveals demand — see open question #6 in `04_open_questions.md`).
- L5 competition data integration. The slider UI is structured so adding a "competition" group later is mechanical, but no current implementation work touches L5.
- Backtest harness / learned model integration (`05_potential_implementations/signal_generation/backtest_harness.md`).
- Nonlinear composite scoring (`05_potential_implementations/signal_generation/nonlinear_composite.md`).
- Mobile responsive layout. Desktop-first; mobile is acceptable to break.
- CSV / JSON export from the UI (acceptable follow-up, not in scope).
- Re-clustering, manual labeling, or any write-back to the DB from the dashboard.

## 3. Hard constraints (must not violate)

These trace to locked decisions and existing principles. Violating any of these is grounds for rejecting an implementation.

1. **No reference to v1-deprecated columns.** `opportunity_score`, `market_gap`, `competition_density`, `golden_quadrant`, `practicality_score`, `intensity`, `prevalence`, `velocity`, `hhi`, `cr4`, `weighted_rating`, `complaint_ratio`, `active_ratio`, `total_installs` — none of these appear in any v2 SQL, API endpoint, type definition, or component. They will be dropped in a later Phase 2.x cleanup.
2. **No canonical composite score in the DB or API.** The composite is computed in the frontend from percentile ranks plus session-local weights. Nothing persists. The system never says "this cluster scores 0.83" without showing the weights that produced 0.83.
3. **No gating by composite.** Hard gates remain `pc.is_candidate ∧ vs.is_real_pain ∧ vs.is_software_solvable` (enforced in `v_market_opportunities`'s WHERE clause). The composite affects display ordering only.
4. **Sliders default velocity dimensions to 0 weight.** Velocity is not a gate; making it influence the score requires the user to opt in. Quality and viability dimensions default to 1.0.
5. **The current week and any partial weeks are always visually distinguished** (dashed line segment in sparkline, dashed segment in detail chart, "partial" annotation on tooltips). Never rendered identically to full weeks.
6. **All three velocity display signals are visible in every cluster row.** These are: `pain_count_last_4w` (absolute count, never NULL), `velocity_4w_ratio_normalized` (recent trend vs corpus, NULL when sparse), and `slope_per_week` (long-term trajectory). The frontend may not collapse them into a single display value. Note these are **display** obligations, not scoring obligations — only `velocity_4w_ratio_normalized` and `slope_per_week` appear in `/score-config`'s `dimensions` array, because they are percentile-rankable across clusters. `pain_count_last_4w` is an absolute count and is intentionally absent from the score computation (it would let large-volume clusters dominate the score regardless of quality).
7. **The ranking surface is `v_market_opportunities` only.** The `/clusters` endpoint's primary SELECT goes through the view, never directly against `pain_clusters` / `cluster_quality_metrics` / `viability_scores` / `cluster_audience`. Purpose-specific endpoints are allowed to query other tables — these are enumerated here so the constraint is verifiable rather than aspirational:
   - `pain_volume_weekly` — sparkline series joined into `/clusters` (15-week window), full series for `/clusters/{id}/velocity`, corpus-wide totals for `/corpus-velocity`.
   - `pain_cluster_members` ⋈ `pain_objects` — sample quotes for `/clusters/{id}/members`.
   - `analysis_runs` and `viability_runs` — run enumeration for `/runs`.
   - `cluster_audience` and `viability_scores` — detail-only fields for `/clusters/{id}` (audience reasoning, sample_pain_ids, surface_distribution, LLM token counts) that aren't carried by `v_market_opportunities`.
   The `/clusters` ranking endpoint is the strict scope of the "via the view" rule; everything else reads from purpose-specific tables.
8. **Source code never goes in the vault.** Illustrative snippets in this spec are clearly fenced and labelled. The implementer writes the actual code into `src/dashboard_2/`.
9. **Historical runs are explicitly marked as snapshots in the UI.** `v_cluster_velocity` is anchored to `CURRENT_DATE`, which is correct only for the live run. After the monthly re-cluster fires (first scheduled 2026-06-01), prior runs' velocity numbers stop meaningfully reflecting recent activity because daily centroid assignment shifts to the new run. When the selected `cluster_run_id` has `status != 'live'`, the UI MUST display a visible banner stating "Historical snapshot — velocity reflects period ending `<window_end>`. Switch to live run for current data." with a link that switches the selection to the live run. Enforced by §8.24. The proper fix (per-run-anchored velocity windows, cross-run cluster lineage display, viability inheritance) is a deferred design — see `05_potential_implementations/next_tasks/cluster_run_continuation.md`.

---

## 4. Data layer

Four DB changes in this order.

### 4.1 Backfill `share_global` and `share_field` on existing `pain_volume_weekly` rows

**Authored by:** Claude orchestrator. **Applied by:** Claude Code.
**Location:** `migrations/v2_011_share_global_backfill.sql` (already written).
**How to apply:** `psql -d reddit_raw -v run_id=10 -f migrations/v2_011_share_global_backfill.sql`.
**Idempotent:** yes (WHERE clause scoped to the run; rerunning produces the same result).

```sql
-- share_global / share_field backfill for pain_volume_weekly
-- Computes per-week denominators (global + per-audience) and updates the columns.
-- Idempotent: safe to rerun. Scoped to one run_id at a time via the parameter.

WITH week_audience_totals AS (
  SELECT cluster_run_id, week_start, audience_type, SUM(pain_count) AS aud_total
  FROM pain_volume_weekly
  WHERE cluster_run_id = :run_id
  GROUP BY cluster_run_id, week_start, audience_type
),
week_global_totals AS (
  SELECT cluster_run_id, week_start, SUM(pain_count) AS global_total
  FROM pain_volume_weekly
  WHERE cluster_run_id = :run_id
  GROUP BY cluster_run_id, week_start
)
UPDATE pain_volume_weekly pvw
SET share_global = pvw.pain_count::float / NULLIF(wg.global_total, 0),
    share_field  = pvw.pain_count::float / NULLIF(wt.aud_total, 0)
FROM week_global_totals wg, week_audience_totals wt
WHERE pvw.cluster_run_id = :run_id
  AND wg.cluster_run_id  = pvw.cluster_run_id AND wg.week_start = pvw.week_start
  AND wt.cluster_run_id  = pvw.cluster_run_id AND wt.week_start = pvw.week_start
  AND wt.audience_type   = pvw.audience_type;
```

**Acceptance for this step:** `SELECT COUNT(*) FROM pain_volume_weekly WHERE cluster_run_id=10 AND share_global IS NULL` returns 0. Spot-check: for any (cluster, week) row, `share_global * sum(pain_count for that week) ≈ pain_count for that row` within float rounding.

### 4.2 Update `src/temporal/aggregate_weekly.py` to maintain shares on daily upserts

**Ownership:** Claude Code.
**Why:** the current `INCREMENTAL_SQL` only upserts `pain_count`. Without this change, the daily cron silently NULLs out the share columns for the current week each run.
**Approach:** add a "compute shares" pass that runs after both `REBUILD_SQL` and `INCREMENTAL_SQL`. The pass is the same SQL as §4.1, scoped by run_id. It's fast — the full UPDATE over 32K rows runs in well under a second on this DB.

Required code shape (illustrative — adapt to project conventions):

```python
RECOMPUTE_SHARES_SQL = """
WITH week_audience_totals AS (
    SELECT cluster_run_id, week_start, audience_type, SUM(pain_count) AS aud_total
    FROM pain_volume_weekly
    WHERE cluster_run_id = %(run_id)s
    GROUP BY cluster_run_id, week_start, audience_type
),
week_global_totals AS (
    SELECT cluster_run_id, week_start, SUM(pain_count) AS global_total
    FROM pain_volume_weekly
    WHERE cluster_run_id = %(run_id)s
    GROUP BY cluster_run_id, week_start
)
UPDATE pain_volume_weekly pvw
SET share_global = pvw.pain_count::float / NULLIF(wg.global_total, 0),
    share_field  = pvw.pain_count::float / NULLIF(wt.aud_total, 0)
FROM week_global_totals wg, week_audience_totals wt
WHERE pvw.cluster_run_id = %(run_id)s
  AND wg.cluster_run_id  = pvw.cluster_run_id AND wg.week_start = pvw.week_start
  AND wt.cluster_run_id  = pvw.cluster_run_id AND wt.week_start = pvw.week_start
  AND wt.audience_type   = pvw.audience_type;
"""

def aggregate(run_id: int, mode: str) -> dict:
    sql = REBUILD_SQL if mode == "rebuild" else INCREMENTAL_SQL
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            if mode == "rebuild":
                cur.execute("DELETE FROM pain_volume_weekly WHERE cluster_run_id = %s", (run_id,))
            cur.execute(sql, {"run_id": run_id})
            written = cur.rowcount
            # NEW: recompute shares for all weeks in this run
            cur.execute(RECOMPUTE_SHARES_SQL, {"run_id": run_id})
            shares_updated = cur.rowcount
        conn.commit()
    finally:
        db.put_conn(conn)
    return {"mode": mode, "rows_written": written, "shares_updated": shares_updated}
```

Note: the share recompute runs across **all weeks in the run**, not just the current week, because daily centroid assignment can backfill a prior week if a new pain has an older `source_created` timestamp. Cheap full UPDATE is correct and safe.

**Acceptance:** running `python -m src.temporal.aggregate_weekly --run-id 10 --mode incremental` twice in a row leaves `share_global IS NULL` count at 0. Running `--mode rebuild` produces same.

### 4.3 Extend `v_cluster_velocity` with `velocity_4w_ratio_normalized`

**Authored by:** Claude orchestrator. **Applied by:** Claude Code.
**Location:** `migrations/v2_012_v_cluster_velocity_normalized.sql` (already written).
**How to apply:** `psql -d reddit_raw -f migrations/v2_012_v_cluster_velocity_normalized.sql`.
**Idempotent:** yes (`DROP VIEW IF EXISTS` + `CREATE VIEW`).

Full new DDL:

```sql
CREATE OR REPLACE VIEW v_cluster_velocity AS
WITH per_cluster AS (
    SELECT
        cluster_run_id,
        cluster_id,
        SUM(pain_count) FILTER (WHERE week_start >= CURRENT_DATE - INTERVAL '28 days')                                                         AS last_4w,
        SUM(pain_count) FILTER (WHERE week_start >= CURRENT_DATE - INTERVAL '56 days'
                                  AND week_start <  CURRENT_DATE - INTERVAL '28 days')                                                          AS prior_4w,
        regr_slope(pain_count::float8, (EXTRACT(epoch FROM week_start) / 604800.0)::float8)                                                     AS slope_per_week,
        COUNT(*)                                                                                                                                AS weeks_with_data,
        SUM(pain_count)                                                                                                                         AS total_pain
    FROM pain_volume_weekly
    GROUP BY cluster_run_id, cluster_id
),
corpus_ratio AS (
    SELECT
        cluster_run_id,
        SUM(pain_count) FILTER (WHERE week_start >= CURRENT_DATE - INTERVAL '28 days')                                                          AS corpus_last_4w,
        SUM(pain_count) FILTER (WHERE week_start >= CURRENT_DATE - INTERVAL '56 days'
                                  AND week_start <  CURRENT_DATE - INTERVAL '28 days')                                                          AS corpus_prior_4w
    FROM pain_volume_weekly
    GROUP BY cluster_run_id
)
SELECT
    pc.cluster_run_id,
    pc.cluster_id,
    pc.last_4w                                                  AS pain_count_last_4w,
    pc.prior_4w                                                 AS pain_count_prior_4w,
    pc.weeks_with_data,
    pc.total_pain,
    ROUND(pc.slope_per_week::numeric, 3)                        AS slope_per_week,
    CASE
        WHEN COALESCE(pc.prior_4w, 0) < 3 OR COALESCE(pc.last_4w, 0) < 3 THEN NULL::numeric
        ELSE ROUND((pc.last_4w::float8 / pc.prior_4w)::numeric, 3)
    END                                                         AS velocity_4w_ratio,
    CASE
        WHEN COALESCE(pc.prior_4w, 0) < 3 OR COALESCE(pc.last_4w, 0) < 3 THEN NULL::numeric
        WHEN COALESCE(cr.corpus_prior_4w, 0) = 0 OR cr.corpus_last_4w IS NULL THEN NULL::numeric
        ELSE ROUND(
            ((pc.last_4w::float8 / pc.prior_4w)
             / NULLIF(cr.corpus_last_4w::float8 / NULLIF(cr.corpus_prior_4w, 0), 0))::numeric,
            3
        )
    END                                                         AS velocity_4w_ratio_normalized
FROM per_cluster pc
LEFT JOIN corpus_ratio cr ON cr.cluster_run_id = pc.cluster_run_id;
```

**Acceptance:** `SELECT velocity_4w_ratio, velocity_4w_ratio_normalized FROM v_cluster_velocity WHERE cluster_run_id=10 LIMIT 5` returns sensible values; the normalized ratio for ≥80% of viable B2C is non-null. Spot-check: corpus_last_4w / corpus_prior_4w currently ≈ 0.35, so a raw ratio of 0.35 should normalize to ≈ 1.00.

### 4.4 Create `v_market_opportunities` VIEW

**Authored by:** Claude orchestrator. **Applied by:** Claude Code.
**Location:** `migrations/v2_013_v_market_opportunities.sql` (already written).
**How to apply:** `psql -d reddit_raw -f migrations/v2_013_v_market_opportunities.sql`.
**Idempotent:** yes (`DROP VIEW IF EXISTS` + `CREATE VIEW`).
**Note:** depends on `v_cluster_velocity` (§4.3), so apply v2_012 first.

Full new DDL:

```sql
CREATE OR REPLACE VIEW v_market_opportunities AS
SELECT
    pc.id                AS cluster_id,
    pc.run_id            AS cluster_run_id,
    pc.label,
    pc.member_count,
    pc.coherence,
    pc.is_candidate,

    ca.classifier_version AS audience_classifier_version,
    ca.audience_type,
    ca.confidence         AS audience_confidence,
    ca.icp_hint,

    cqm.thread_diversity,
    cqm.author_diversity,
    cqm.subreddit_count,
    cqm.specificity_mean,
    cqm.workaround_ratio,
    cqm.separation,
    cqm.temporal_spread_days,
    cqm.source_diversity,

    vs.viability_run_id,
    vs.is_real_pain,
    vs.is_software_solvable,
    vs.q_specificity,
    vs.q_noise_flag,
    vs.v_buildability,
    vs.v_icp_clarity,
    vs.v_moat_potential,
    vs.v_rationale,

    vcv.pain_count_last_4w,
    vcv.pain_count_prior_4w,
    vcv.weeks_with_data,
    vcv.total_pain,
    vcv.slope_per_week,
    vcv.velocity_4w_ratio,
    vcv.velocity_4w_ratio_normalized

FROM pain_clusters pc
JOIN cluster_audience ca
    ON ca.cluster_id = pc.id
   AND ca.classifier_version = COALESCE(
           NULLIF(current_setting('llm_analyse.audience_classifier_version', true), ''),
           'v1')
JOIN cluster_quality_metrics cqm
    ON cqm.cluster_id     = pc.id
   AND cqm.cluster_run_id = pc.run_id
LEFT JOIN viability_scores vs
    ON vs.cluster_id       = pc.id
   AND vs.viability_run_id = NULLIF(current_setting('llm_analyse.viability_run_id', true), '')::int
LEFT JOIN v_cluster_velocity vcv
    ON vcv.cluster_id     = pc.id
   AND vcv.cluster_run_id = pc.run_id
WHERE pc.run_id = NULLIF(current_setting('llm_analyse.cluster_run_id', true), '')::int
  AND pc.is_candidate
  AND COALESCE(vs.is_real_pain,         true)
  AND COALESCE(vs.is_software_solvable, true);
```

**Behavior on missing GUCs:** if `llm_analyse.cluster_run_id` isn't set, the WHERE clause `pc.run_id = NULL` evaluates to false for every row and the view returns empty. This is intentional — querying without parameterization should fail loudly, not silently default to the full set.

**API responsibility:** the API sets these GUCs per request via `SET LOCAL` inside the transaction (so they don't leak across pool connections):

```python
cur.execute("SET LOCAL llm_analyse.cluster_run_id = %s", (cluster_run_id,))
cur.execute("SET LOCAL llm_analyse.viability_run_id = %s", (viability_run_id,))
cur.execute("SET LOCAL llm_analyse.audience_classifier_version = %s", (classifier_version,))
cur.execute("SELECT * FROM v_market_opportunities WHERE ...")
```

**Acceptance:** with GUCs set to `cluster_run_id=10, viability_run_id=4, audience_classifier_version='v1'`, `SELECT COUNT(*) FROM v_market_opportunities WHERE audience_type='B2C'` returns 740.

### 4.5 Add `status` column to `analysis_runs`

**Authored by:** Claude orchestrator. **Applied by:** Claude Code.
**Location:** `migrations/v2_014_analysis_runs_status.sql` (already written).
**How to apply:** `psql -d reddit_raw -f migrations/v2_014_analysis_runs_status.sql`.
**Idempotent:** yes (`ADD COLUMN IF NOT EXISTS`, a `DO` block for the CHECK constraint, and `UPDATE`s gated on current `status`).
**Why:** the dashboard needs to distinguish the live run from historical snapshots so it can warn the user when velocity numbers are stale (see constraint §3.9). Without this column the frontend would have to infer "live = most recent id," which is fragile if a backtest or admin task ever creates a non-active run with a recent `window_end`.

Behavior:
- New column `analysis_runs.status VARCHAR(16) NOT NULL DEFAULT 'historical'` with `CHECK (status IN ('live', 'historical', 'legacy'))`.
- Backfill rule on apply (deterministic):
  - `is_legacy = TRUE` rows → `'legacy'`.
  - Most-recent `is_legacy = FALSE` row by id → `'live'`.
  - All other non-legacy rows → `'historical'` (the column default).
  - Defensive: if multiple rows have `status = 'live'` (shouldn't happen on first apply), all but the most-recent are demoted to `'historical'`.
- Orchestrator responsibility, NOT implemented here: when the monthly orchestrator creates a new clustering run, it transitions the prior `'live'` row to `'historical'` and marks the new run `'live'` in the same transaction that finalises the cluster set. This belongs in `src/clustering/` orchestration and is captured in `05_potential_implementations/next_tasks/cluster_run_continuation.md`.

**Acceptance:** see §8.23 below.

---

## 5. API layer

### 5.1 New file: `src/dashboard_2/api.py`

FastAPI app, served via uvicorn. Reference v1's `src/dashboard/api.py` for the `SimpleConnectionPool` + CORS patterns — those are mature and worth keeping. Do not reuse v1's endpoint shapes; they bind to deprecated columns.

Suggested module layout:
- `src/dashboard_2/api.py` — FastAPI app, endpoints
- `src/dashboard_2/db.py` — connection pool, GUC-aware query helper
- `src/dashboard_2/scoring.py` — percentile-rank precompute (used by `/clusters` endpoint)
- `src/dashboard_2/requirements.txt` — fastapi, psycopg2-binary, uvicorn

Connection pool envvar: `DATABASE_URL`, falling back to `postgresql://llmanalyse:llmanalyse_dev@localhost/reddit_raw` for local dev. Same shape as v1.

### 5.2 Endpoint contract

All endpoints are GET. All responses are JSON. Errors return standard FastAPI HTTPException with status code.

#### `GET /api/v2/runs`

List available cluster runs and viability runs.

Request: no params.

Response:
```json
{
  "cluster_runs": [
    {
      "id": 10,
      "window_start": "2026-02-05",
      "window_end":   "2026-05-06",
      "cluster_count": 2594,
      "is_legacy": false,
      "status": "live"
    }
  ],
  "viability_runs": [
    {"id": 4, "cluster_run_id": 10, "n_clusters": 1070, "llm_model": "minimax-m2.7"}
  ],
  "defaults": {
    "cluster_run_id": 10,
    "viability_run_id": 4,
    "audience_classifier_version": "v1"
  }
}
```

`defaults.cluster_run_id` is whichever row has `status = 'live'` (there must be exactly one). `defaults.viability_run_id` is the most-recent viability run for that cluster run. The frontend uses these on initial load. If the user later selects a run with `status != 'live'`, the UI shows the snapshot banner per §3.9.

#### `GET /api/v2/clusters`

The main table endpoint. Returns ALL qualifying clusters in one response (≤1000 rows expected — the 740 viable B2C plus headroom). Client does sorting/filtering/pagination in memory; the slider weights re-rank live without server roundtrips.

Request params:
- `cluster_run_id` (int, default from `/runs`)
- `viability_run_id` (int, default from `/runs`)
- `audience_classifier_version` (string, default `'v1'`)
- `audience_type` (optional, default `'B2C'`; pass `'all'` to skip the filter)

Response:
```json
{
  "cluster_run_id": 10,
  "viability_run_id": 4,
  "audience_classifier_version": "v1",
  "total": 740,
  "clusters": [
    {
      "cluster_id": 16800,
      "label": "Parenting: Parents struggle with children's social-emotional needs",
      "audience_type": "B2C",
      "audience_confidence": 0.85,
      "icp_hint": "Parents of children ages 5-12 ...",
      "member_count": 802,
      "pain_count_last_4w": 99,
      "v_rationale": "Parents seek tools for emotional regulation ...",
      "q_noise_flag": null,
      "dimensions": {
        "coherence":                    { "value": 0.62,  "pct": 0.71 },
        "thread_diversity":             { "value": 0.48,  "pct": 0.52 },
        "author_diversity":             { "value": 0.71,  "pct": 0.65 },
        "subreddit_count":              { "value": 12,    "pct": 0.82 },
        "specificity_mean":             { "value": 0.41,  "pct": 0.55 },
        "workaround_ratio":             { "value": 0.18,  "pct": 0.62 },
        "separation":                   { "value": 0.18,  "pct": 0.41 },
        "temporal_spread_days":         { "value": 98,    "pct": 0.74 },
        "q_specificity":                { "value": 4,     "pct": 0.71 },
        "v_buildability":               { "value": 3,     "pct": 0.50 },
        "v_icp_clarity":                { "value": 4,     "pct": 0.62 },
        "v_moat_potential":             { "value": 3,     "pct": 0.55 },
        "slope_per_week":               { "value": -4.10, "pct": 0.21 },
        "velocity_4w_ratio_normalized": { "value": 0.87,  "pct": 0.42 }
      },
      "sparkline": [12, 29, 35, 29, 67, 91, 120, 112, 121, 50, 43, 47, 41, 11, null],
      "sparkline_weeks": ["2026-02-02","2026-02-09","2026-02-16","2026-02-23","2026-03-02","2026-03-09","2026-03-16","2026-03-23","2026-03-30","2026-04-06","2026-04-13","2026-04-20","2026-04-27","2026-05-04","2026-05-11"],
      "sparkline_partial_from_index": 13
    }
  ]
}
```

Per-row details:
- `dimensions` carries 14 dimensions, each with `value` (raw) and `pct` (within-run percentile rank in [0, 1]). The percentile is computed across all rows in the response (i.e., across the audience-filtered viable set, not all 2,594 clusters). Missing values: `value: null, pct: null`.
- `sparkline` is `pain_count` for the 15 most recent weeks. **The window is fixed per request:** 15 consecutive weeks ending at `date_trunc('week', NOW())` (current week inclusive). The API query selects `WHERE week_start >= date_trunc('week', NOW()) - INTERVAL '14 weeks' AND week_start <= date_trunc('week', NOW())`. Weeks with no rows in `pain_volume_weekly` are rendered as `null` (not `0`) so the frontend can distinguish "no data" from "0 pains observed."
- `sparkline_partial_from_index` is the index from which weeks are considered partial (current week + any later) — the frontend renders segments at or after this index dashed. Computed server-side: any week_start in the future or within the current week.

Implementation notes for the API:
1. SET LOCAL the three GUCs in a transaction.
2. SELECT from `v_market_opportunities` filtered by `audience_type`.
3. Compute percentile ranks in Python: for each dimension, rank the values and divide by N. NULL values stay NULL. Use `scipy.stats.rankdata` or a numpy + argsort one-liner.
4. JOIN against `pain_volume_weekly` for the sparkline series (one extra query, batched).

Response size ballpark: 740 rows × ~2KB each = ~1.5MB. Pre-gzip; ~300KB compressed over the wire. Acceptable for an initial load.

#### `GET /api/v2/clusters/{cluster_id}`

Detail-only context fields that are NOT part of the `/clusters` row response. The frontend already has the row-level data in memory by the time the detail panel opens; this endpoint exists to surface fields too verbose to ship inside every row.

Request params: `cluster_run_id`, `viability_run_id`, `audience_classifier_version` (same as `/clusters`).

Response:
```json
{
  "cluster_id": 16800,
  "audience": {
    "reasoning":            "Full LLM rationale text for the B2C verdict (multi-paragraph)...",
    "sample_pain_ids":      [1234, 1235, 1236],
    "surface_distribution": {"r/Parenting": 412, "r/Mommit": 198}
  },
  "viability": {
    "sample_pain_ids":   [4321, 4322, 4323],
    "llm_input_tokens":  8420,
    "llm_output_tokens": 312
  }
}
```

Any field may be `null` if the underlying row is absent — e.g., `surface_distribution` is a nullable JSONB on `cluster_audience`; the whole `viability` block is `null` if no `viability_scores` row exists for the `(cluster_id, viability_run_id)` pair. The frontend renders missing fields as "—" or hides the corresponding section. Do not duplicate any column that's already in the `/clusters` row payload.

#### `GET /api/v2/clusters/{cluster_id}/velocity`

Full weekly time-series for the detail panel chart.

Request params: `cluster_run_id` (required).

Response:
```json
{
  "cluster_id": 16800,
  "cluster_run_id": 10,
  "median_pain_count": 47,
  "weeks": [
    {
      "week_start": "2026-02-02",
      "pain_count": 12,
      "share_global": 0.00147,
      "share_field":  0.00193,
      "is_partial": false
    },
    /* 15 entries total, last one is_partial: true */
  ],
  "windows": {
    "last_4w_start":  "2026-04-14",
    "prior_4w_start": "2026-03-17",
    "prior_4w_end":   "2026-04-14"
  }
}
```

`is_partial` is true for weeks where `week_start >= current_week_start` (i.e., the current week and any subsequent partial data).

#### `GET /api/v2/clusters/{cluster_id}/members`

Sample pain quotes for the detail panel. Paginated.

Request params: `page` (default 1), `page_size` (default 20, max 100).

Response:
```json
{
  "cluster_id": 16800,
  "total": 802,
  "page": 1,
  "page_size": 20,
  "pages": 41,
  "members": [
    {
      "pain_id": 123,
      "pain_statement": "I can't get my 8-year-old to ...",
      "raw_quote": "Original post text ...",
      "subreddit_name": "Parenting",
      "source_author": "anon_user_abc",
      "source_created": "2026-04-22T14:30:00Z",
      "specificity": 0.62,
      "sentiment_score": -0.4,
      "is_workaround": false,
      "mentioned_tools": ["calm-app", "headspace"]
    }
  ]
}
```

ORDER BY `community_score DESC NULLS LAST, source_created DESC` for stable, useful ordering — high-engagement posts first.

#### `GET /api/v2/corpus-velocity`

Corpus-wide weekly totals, used as the faint secondary-axis overlay in the cluster detail chart.

Request params: `cluster_run_id` (required).

Response:
```json
{
  "cluster_run_id": 10,
  "weeks": [
    {"week_start": "2026-02-02", "total_pain": 8176,  "is_partial": false},
    /* ... */
    {"week_start": "2026-05-11", "total_pain": 266,   "is_partial": true}
  ]
}
```

Cache this in-memory for ≤60 seconds per `cluster_run_id` — it doesn't change between requests in a single session.

#### `GET /api/v2/score-config`

Static metadata about the scoring dimensions. Defines the slider UI on the frontend.

Request: no params.

Response:
```json
{
  "groups": [
    {"key": "quality",   "label": "Quality"},
    {"key": "viability", "label": "Viability (LLM)"},
    {"key": "velocity",  "label": "Velocity"}
  ],
  "dimensions": [
    {"key": "coherence",                    "label": "Coherence",            "group": "quality",   "default_weight": 1.0, "missing_policy": "skip"},
    {"key": "thread_diversity",             "label": "Thread diversity",     "group": "quality",   "default_weight": 1.0, "missing_policy": "skip"},
    {"key": "author_diversity",             "label": "Author diversity",     "group": "quality",   "default_weight": 1.0, "missing_policy": "skip"},
    {"key": "subreddit_count",              "label": "Subreddit breadth",    "group": "quality",   "default_weight": 1.0, "missing_policy": "skip"},
    {"key": "specificity_mean",             "label": "Specificity",          "group": "quality",   "default_weight": 1.0, "missing_policy": "skip"},
    {"key": "workaround_ratio",             "label": "Workaround signal",    "group": "quality",   "default_weight": 1.0, "missing_policy": "skip"},
    {"key": "separation",                   "label": "Cluster separation",   "group": "quality",   "default_weight": 1.0, "missing_policy": "skip"},
    {"key": "temporal_spread_days",         "label": "Temporal spread",      "group": "quality",   "default_weight": 1.0, "missing_policy": "skip"},
    {"key": "q_specificity",                "label": "LLM specificity",      "group": "viability", "default_weight": 1.0, "missing_policy": "skip"},
    {"key": "v_buildability",               "label": "Buildability",         "group": "viability", "default_weight": 1.0, "missing_policy": "skip"},
    {"key": "v_icp_clarity",                "label": "ICP clarity",          "group": "viability", "default_weight": 1.0, "missing_policy": "skip"},
    {"key": "v_moat_potential",             "label": "Moat potential",       "group": "viability", "default_weight": 1.0, "missing_policy": "skip"},
    {"key": "slope_per_week",               "label": "Long-term slope",      "group": "velocity",  "default_weight": 0.0, "missing_policy": "skip"},
    {"key": "velocity_4w_ratio_normalized", "label": "Recent trend",         "group": "velocity",  "default_weight": 0.0, "missing_policy": "skip"}
  ]
}
```

The frontend's slider panel reads this and renders one slider per dimension, grouped. `default_weight` is the initial slider position. `missing_policy: "skip"` means clusters with NULL values for that dimension exclude it from their score computation (denominator renormalizes — see §6.4).

### 5.3 Implementation notes

- **CORS:** allow `http://localhost:5173`, `http://localhost:5174` (Vite dev ports) per v1 pattern. Production deployment notes deferred.
- **Static file serving:** in production, mount the Vite build output at the root. Reference v1 `api.py` lines for the mount pattern.
- **Pool size:** start with minconn=1, maxconn=5 (v1 setting). Increase if request latency suggests pool starvation.
- **GUC isolation:** always use `SET LOCAL` inside a transaction. Pool connections are reused, so `SET` (without LOCAL) would leak settings across requests.
- **Error handling:** invalid `cluster_run_id` returns 404, not 200-with-empty-clusters. The frontend should never get "everything's empty" without a clear error.
- **Logging:** log each request's GUC values for debugging. Don't log full responses.
- **Dimensions source of truth.** The 14 dimensions appear in two responses (`/clusters` per-row `dimensions` map, `/score-config` `dimensions` array). Both endpoints MUST serialize from a single Python list defined once in `src/dashboard_2/scoring.py` — e.g., `DIMENSIONS = [("coherence", "Coherence", "quality", 1.0, "skip"), ...]`. If the two lists drift, the frontend composite score silently breaks because it computes against keys that don't exist on cluster rows. The single source of truth is enforced by a unit test — see §8.21.

---

## 6. Frontend layer

### 6.1 File tree

```
src/dashboard_2/
└── frontend/
    ├── index.html
    ├── package.json
    ├── tsconfig.json
    ├── tsconfig.node.json
    ├── vite.config.ts
    ├── tailwind.config.js
    ├── postcss.config.js
    ├── .gitignore
    └── src/
        ├── main.tsx
        ├── App.tsx
        ├── api.ts
        ├── types.ts
        ├── index.css
        ├── components/
        │   ├── RunSelector.tsx
        │   ├── WeightsPanel.tsx
        │   ├── FilterBar.tsx
        │   ├── ClusterTable.tsx
        │   ├── ClusterRow.tsx
        │   ├── Sparkline.tsx
        │   ├── TrendChip.tsx
        │   ├── ScoreCell.tsx
        │   ├── ClusterDetailPanel.tsx
        │   ├── VelocityChart.tsx
        │   └── MembersList.tsx
        ├── hooks/
        │   ├── useClusters.ts
        │   ├── useWeights.ts
        │   ├── useDetail.ts
        │   └── useScoreConfig.ts
        └── lib/
            ├── score.ts
            ├── format.ts
            └── colors.ts
```

### 6.2 Stack

Same baseline as v1 (proven to work in this environment):
- React 18.3.x
- Vite 5.x or 8.x (latest available)
- TypeScript 5.4.x
- Tailwind 3.4.x
- Recharts 2.12.x (used in the detail-panel chart only — sparklines are inline SVG, no library)

`package.json` derived from v1's, dropping `echarts` and `echarts-for-react` (only Recharts in v2).

### 6.3 Component breakdown

#### `App.tsx`
Top-level layout. Holds the selected `cluster_run_id` / `viability_run_id`, current weights state, current filter state, currently selected cluster for the detail panel.

Renders:
- Header with `RunSelector`
- Sidebar with `WeightsPanel` (collapsible)
- Main column with `FilterBar` over `ClusterTable`
- `ClusterDetailPanel` overlay when a cluster is selected

#### `RunSelector.tsx`
Dropdowns for cluster run and viability run. Pulls options from `/api/v2/runs`. On change, refetches clusters.

When the currently-selected cluster run has `status != 'live'`, the selector renders a snapshot banner adjacent to (or directly below) the dropdown:
> ⚠ Historical snapshot — velocity reflects period ending `<window_end>`. [Switch to live run]

The "Switch to live run" link sets the cluster run to whichever row has `status === 'live'`. Banner styling: warning-tone background (e.g., amber/var(--color-background-warning) or Tailwind `bg-amber-50` + `text-amber-900`), single line, dismissible only by switching runs. Required by hard constraint §3.9; verified by §8.24.

#### `WeightsPanel.tsx`
Renders one slider per dimension, grouped by `group` from `/api/v2/score-config`. Default values from `default_weight`. Slider range: `[0, 1]`, step `0.1`. Each slider shows: dimension label, current weight value, a tiny ↻ to reset to default. Group headers are collapsible. At the bottom, a "Reset all to defaults" button.

The composite computation (see §6.4) runs in JS whenever weights change — no API roundtrip.

#### `FilterBar.tsx`
Text search (matches cluster label, substring, case-insensitive). Min-`member_count` numeric input. Min-`pain_count_last_4w` numeric input. Audience selector (default B2C, plus 'all').

#### `ClusterTable.tsx`
Sortable, virtualized table. Columns:
- Label (truncated, full on hover)
- Audience badge (B2C / B2B / mixed / unclear) — color-coded
- `member_count` (right-aligned)
- `pain_count_last_4w` (right-aligned)
- Sparkline (`Sparkline` component)
- Normalized ratio chip (`TrendChip` with `kind='ratio'`)
- Slope chip (`TrendChip` with `kind='slope'`)
- Score (`ScoreCell` — composite computed in JS, with hover breakdown)
- v_rationale truncated to ~80 chars

Sorting: click column header to sort. Default sort: score descending. Sort by sparkline column = sort by slope.

Virtualization: use `react-window` or roll a simple "render only visible rows" hook. 740 rows × 11 columns = ~8K cells without virtualization, which works but is sluggish on slider drag (every drag re-renders the whole table). With virtualization, only ~30 visible rows re-render, smooth.

Add `react-window` to package.json.

#### `ClusterRow.tsx`
One row of the table. Pure component, memoized — re-renders only when its own data, score, or selection state changes.

#### `Sparkline.tsx`
Inline SVG, 80×24. Props: `data: (number | null)[]` (15 elements), `partialFromIndex: number`, `color: string`.

Algorithm:
- Find min/max of non-null values for y-scale
- x-step = 80 / (data.length - 1)
- Build polyline points
- Segment from index 0 to `partialFromIndex` rendered solid
- Segment from `partialFromIndex` to end rendered dashed (`stroke-dasharray="3,2"`, opacity 0.6)
- A single point marker at `partialFromIndex` shows the boundary

Skip null points by breaking the polyline (use multiple `<polyline>` elements if needed).

#### `TrendChip.tsx`
Small colored pill showing a trend direction + numeric value. Props: `value: number | null`, `kind: 'ratio' | 'slope'`.

For ratio:
- value ≥ 1.10 → teal background, ↗ icon
- value ≤ 0.90 → amber background, ↘ icon
- otherwise (or null) → gray, → icon (or "—" if null)

For slope (units = pains/week):
- value > 2 → teal ↗
- value < -2 → amber ↘
- otherwise → gray →

Numeric formatting: ratio to 2 decimals, slope to 1 decimal. NULLs render as "—".

#### `ScoreCell.tsx`
Renders the composite score as a percent (e.g., `73%`). On hover, opens a small floating panel showing each dimension's contribution: `<dimension label> × <weight> × <pct> = <product>`. The hover panel makes "where does this score come from" inspectable per Constraint #2.

#### `ClusterDetailPanel.tsx`
Slide-in panel from the right (width ~50% of viewport). On open, fires three fetches in parallel: `/clusters/{id}` (for fields not in the row), `/clusters/{id}/velocity`, `/clusters/{id}/members?page=1`. Renders:

- Header: label, audience badge, run context
- Metric strip: member_count, pain_count_last_4w, normalized ratio (with raw ratio in small gray below labelled "before normalization"), slope
- `VelocityChart` — full-size temporal chart
- Dimensions breakdown (a denser version of the row's columns)
- `v_rationale` full text
- `MembersList` — paginated sample pains

#### `VelocityChart.tsx`
Recharts line chart, ~600 × 220. Series:
- Primary (cluster `pain_count` or `share_global` depending on toggle)
- Secondary (faint, corpus total — different y-axis when in "raw" mode)
- Median horizontal dotted line

Toggles (segmented control above the chart):
- Raw `pain_count` (default)
- `share_global` (cluster pain / corpus pain for that week)
- Log scale (applies to the y-axis of whatever's currently selected)

Partial weeks rendered as dashed line segments (use Recharts' `strokeDasharray` on a per-segment basis, or split into two `Line` components and overlay).

4w / prior-4w windows lightly shaded in the background. Hovering the normalized-ratio chip in the parent panel lights up these regions and shows the arithmetic.

#### `MembersList.tsx`
Paginated list of pain quotes. Each row: `pain_statement`, the `raw_quote` (truncated, expandable), subreddit name, source_created date, sentiment badge, workaround flag if true, mentioned tools as tags. Sort: as the API returns.

### 6.4 Composite score computation (`lib/score.ts`)

The single most-load-bearing piece of frontend logic. Pure function, easy to test.

```typescript
type DimensionPoint = { value: number | null; pct: number | null };
type Weights = Record<string, number>;
type Cluster = { dimensions: Record<string, DimensionPoint>; /* ... */ };

export function computeScore(cluster: Cluster, weights: Weights): number | null {
  let weightedSum = 0;
  let totalWeight = 0;
  for (const key in weights) {
    const w = weights[key];
    if (w === 0) continue;
    const point = cluster.dimensions[key];
    if (!point || point.pct === null) continue;
    weightedSum += w * point.pct;
    totalWeight += w;
  }
  return totalWeight > 0 ? weightedSum / totalWeight : null;
}
```

Properties:
- Output range: `[0, 1]` because all `pct` values are in `[0, 1]` and the weighted average preserves that range.
- Missing dimensions skip; denominator renormalizes. A cluster missing `velocity_4w_ratio_normalized` gets scored on the present dimensions, no penalty.
- If all weights are zero or all present dimensions have null `pct`, returns null. The UI shows "—" and sorts such rows last.

### 6.5 The weights panel UX

Default state on first load (from `score-config`):
- Quality dimensions: all 1.0
- Viability dimensions: all 1.0
- Velocity dimensions: all 0.0

So the initial score = mean percentile over the 12 non-velocity dimensions. To bias toward trending clusters, the user pulls a velocity slider up.

Per-slider hover tooltip: short description of what the dimension means. (1-2 sentences each. Examples: "Coherence — how tightly the pain statements in this cluster cluster semantically." "Thread diversity — fraction of distinct source posts; low means echo-chamber.")

Weights persist in component state only. Page refresh resets to defaults. This is intentional (Constraint #2 — no canonical weighting).

### 6.6 Sparkline rendering detail

The constraint that the last 2 segments be dashed needs care. Approach:

```typescript
function buildPathSegments(data: (number|null)[], partialFromIndex: number, width=80, height=24) {
  // returns two paths: solid (indices 0..partialFromIndex) and dashed (partialFromIndex..end)
  const validPoints = data.map((v, i) => ({ i, v }))
                          .filter(p => p.v !== null) as {i: number, v: number}[];
  if (validPoints.length < 2) return { solid: '', dashed: '' };
  const min = Math.min(...validPoints.map(p => p.v));
  const max = Math.max(...validPoints.map(p => p.v));
  const range = max - min || 1;
  const xStep = width / (data.length - 1);
  const toPoint = (p: {i:number,v:number}) => `${p.i * xStep},${height - ((p.v - min) / range) * height}`;
  const solidPts = validPoints.filter(p => p.i <= partialFromIndex).map(toPoint).join(' ');
  const dashedPts = validPoints.filter(p => p.i >= partialFromIndex).map(toPoint).join(' ');
  return { solid: solidPts, dashed: dashedPts };
}
```

Render as two `<polyline>` elements inside one `<svg viewBox="0 0 80 24">`. The dashed one gets `stroke-dasharray="3,2"` and reduced opacity.

### 6.7 Cluster detail panel detail

Layout (top to bottom):
1. Sticky header with label, audience badge, close button.
2. Run context strip (small text): "Cluster run 10 · viability run 4 · classifier v1".
3. Four metric tiles: member_count, pain_count_last_4w, normalized ratio (with raw ratio underneath in smaller, secondary text labelled "before normalization"), slope.
4. `VelocityChart`.
5. Dimensions breakdown (all 14 dimensions in a 2- or 3-column grid, each showing value + percentile + tiny bar).
6. `v_rationale` full text.
7. `MembersList` (paginated).

The chart's 4w/prior-4w window highlights are a normal background shade by default. When the user hovers the normalized ratio tile, the highlights brighten (or border becomes solid) and a tooltip appears explaining the math:
> Last 4w sum: 99 · Prior 4w sum: 326 · Raw ratio: 0.30 · Corpus ratio: 0.35 · Normalized: 0.87

This makes the metric self-explaining without requiring the user to read docs.

---

## 7. File summary — who creates what

| File | Owner | Action |
|---|---|---|
| `migrations/v2_011_share_global_backfill.sql` | Claude orchestrator | Authored. Claude Code applies via psql with `-v run_id=10`. |
| `migrations/v2_012_v_cluster_velocity_normalized.sql` | Claude orchestrator | Authored. Claude Code applies via psql. |
| `migrations/v2_013_v_market_opportunities.sql` | Claude orchestrator | Authored. Claude Code applies via psql. |
| `migrations/v2_014_analysis_runs_status.sql` | Claude orchestrator | Authored. Claude Code applies via psql. Adds `status` column + deterministic backfill. |
| `src/temporal/aggregate_weekly.py` | Claude Code | Edit: add `RECOMPUTE_SHARES_SQL` + call it from `aggregate()`. |
| `src/dashboard_2/api.py` | Claude Code | New. |
| `src/dashboard_2/db.py` | Claude Code | New. |
| `src/dashboard_2/scoring.py` | Claude Code | New. |
| `src/dashboard_2/requirements.txt` | Claude Code | New. |
| `src/dashboard_2/frontend/**` | Claude Code | New (mirror v1 file tree, fresh components per §6). |

Existing files NOT modified:
- `src/dashboard/**` — v1, frozen.
- `01_design/system_design.md` — already updated this session to reference dashboard v2 as "in design."
- `01_design/database_schema.md` — needs a refresh later for current row counts and new views (noted in handoff; not in this implementation's scope).

---

## 8. Acceptance criteria

Implementation is "done" when ALL of these hold.

**Data layer:**
1. `SELECT COUNT(*) FROM pain_volume_weekly WHERE cluster_run_id=10 AND share_global IS NULL` → 0.
2. Running `python -m src.temporal.aggregate_weekly --run-id 10 --mode incremental` produces `shares_updated > 0` in the return and leaves share_global non-null.
3. `SELECT velocity_4w_ratio_normalized FROM v_cluster_velocity WHERE cluster_run_id=10 AND velocity_4w_ratio_normalized IS NOT NULL` returns ≥ 80% of viable B2C row count (≈ 590 of 740).
4. Inside a transaction (because `SET LOCAL` is a no-op outside one), with the three GUCs set:
   ```sql
   BEGIN;
   SET LOCAL llm_analyse.cluster_run_id              = 10;
   SET LOCAL llm_analyse.viability_run_id            = 4;
   SET LOCAL llm_analyse.audience_classifier_version = 'v1';
   SELECT COUNT(*) FROM v_market_opportunities WHERE audience_type = 'B2C';
   COMMIT;
   ```
   Returns 740.

**API layer:**
5. `uvicorn src.dashboard_2.api:app` starts cleanly.
6. `GET /api/v2/runs` returns the expected structure with `defaults.cluster_run_id=10, defaults.viability_run_id=4`.
7. `GET /api/v2/clusters` (with defaults) returns `total=740` and 740 entries in `clusters`. Each entry has all 14 dimensions, a 15-length sparkline, and `sparkline_partial_from_index = 14` (always — the current week is the last index in the window because the window is anchored at `date_trunc('week', NOW())`).
8. `GET /api/v2/clusters/16800/velocity` returns 15 weeks with `share_global` non-null on the historical weeks.
9. `GET /api/v2/clusters/16800/members?page=1&page_size=20` returns 20 members ordered by community_score desc.
10. `GET /api/v2/corpus-velocity` returns 15 weeks summing to ≈ 250K total pain.

**Frontend layer:**
11. `npm run build` succeeds in `src/dashboard_2/frontend/`.
12. `npm run dev` serves the dashboard on port 5173 (or next available); navigating to `/` shows the cluster table with all 740 rows visible.
13. Sparklines render with the last 1–2 segments dashed.
14. Moving any slider in the weights panel re-sorts the table in under 200ms (with virtualization).
15. Setting all sliders to 0 results in score column showing "—" for all rows.
16. Clicking a row opens the detail panel; the velocity chart renders, the three view toggles work (raw, share_global, log), the 4w/4w windows highlight on hover of the normalized ratio tile.
17. The detail panel's members list loads and paginates.

**Hard constraints check:**
18. No v1-deprecated column appears in any source file (grep for `opportunity_score`, `market_gap`, `competition_density`, `golden_quadrant`, `practicality_score` — all 0 hits in v2 code).
19. Weights state is in React state only — no `localStorage`, no API write, no DB persistence.
20. Velocity dimensions default to 0 weight; quality and viability default to 1.0.

**Tests:**
21. **`lib/score.ts` unit tests pass**, covering at minimum:
    - Equal weights with no missing dimensions → score equals the arithmetic mean of percentile ranks.
    - One dimension has `pct: null` → that dimension is excluded; remaining weights renormalize; output stays in `[0, 1]`.
    - All weights set to `0` → returns `null` (not `0`, not `NaN`).
    - Empty `dimensions` map → returns `null`.
    - A cluster with `velocity_4w_ratio_normalized` null but `slope_per_week` present (the two scorable velocity dimensions; one missing) scores on `slope_per_week` plus all quality + viability dimensions; the missing dimension is excluded from both numerator and denominator. (`pain_count_last_4w` is intentionally absent from `/score-config` per §3.6 — it's a row display signal, not a scoring dimension.)
    - The dimensions-source-of-truth invariant (§5.3): a test asserts every key returned by `/api/v2/score-config` also appears in every cluster's `dimensions` map from `/api/v2/clusters` (and vice versa). Catches drift between the two responses.
22. **API smoke test passes**: an integration test (e.g., `tests/test_dashboard_v2_api.py`) starts the FastAPI app, calls `/api/v2/clusters?cluster_run_id=10&viability_run_id=4`, and asserts `response.json()["total"] == 740` AND every cluster has all 14 dimensions populated in its `dimensions` map. This verifies the GUC + view + filter chain end-to-end and the dimensions-source-of-truth invariant in one shot.

**Run lifecycle:**
23. **v2_014 applied and backfilled correctly.** `SELECT status, COUNT(*) FROM analysis_runs GROUP BY status` returns exactly one `'live'` row (currently id=10), a count of `'legacy'` rows matching `is_legacy=TRUE` count, and the remainder as `'historical'`. `/api/v2/runs` response carries `status` per cluster run; `defaults.cluster_run_id` equals the id of the `'live'` row.
24. **Snapshot banner renders when `status != 'live'`.** Manual UI check: select a `'historical'` or `'legacy'` cluster run from the selector. A visible warning-tone banner appears reading "Historical snapshot — velocity reflects period ending `<window_end>`" with a "Switch to live run" link. Clicking the link returns the selector to the `'live'` run and dismisses the banner. Selecting the `'live'` run shows no banner.

---

## 9. Implementation order (recommended)

For minimum risk and maximum incremental verifiability:

0. **Backup `pain_volume_weekly` before applying v2_011** — the backfill is an `UPDATE` over 31,995 rows. Per `02_principles.md` ("backup before destructive operations"): `pg_dump -t pain_volume_weekly reddit_raw > /tmp/pvw_pre_v2_011_$(date +%s).sql`. Keep the dump until §8.1 verifies non-null `share_global`; then it can be discarded. Strictly the share columns are recomputable from `pain_count` alone, but losing them silently and then running a daily cron over a broken state would be a confusing failure mode to debug — the dump is cheap insurance.
1. **Apply v2_011 backfill** — `psql -d reddit_raw -v run_id=10 -f migrations/v2_011_share_global_backfill.sql`. Verify §8.1.
2. **Apply v2_012 v_cluster_velocity update** — small SQL, easy to verify in psql. Acceptance §8.3.
3. **Apply v2_013 v_market_opportunities** — depends on step 2. Acceptance §8.4.
4. **Apply v2_014 analysis_runs.status** — `psql -d reddit_raw -f migrations/v2_014_analysis_runs_status.sql`. Acceptance §8.23.
5. **§4.2 aggregate_weekly.py edit** — defensive; ensures the daily cron doesn't re-NULL share_global on subsequent runs. Acceptance §8.2.
6. **§5 API** — endpoint by endpoint. Test each with curl before moving to the frontend. Acceptance §8.5–§8.10.
7. **§6 frontend skeleton** — App.tsx + ClusterTable.tsx + Sparkline.tsx + TrendChip.tsx + RunSelector with snapshot banner (per §3.9), hard-coded weights. Acceptance §8.11–§8.13, §8.24.
8. **§6 weights + composite score** — WeightsPanel + ScoreCell + score.ts. Acceptance §8.14–§8.15.
9. **§6 detail panel** — ClusterDetailPanel + VelocityChart + MembersList. Acceptance §8.16–§8.17.
10. **Final pass** — virtualization tuning, hover tooltips, color refinements. Acceptance §8.18–§8.20.

Each step ends with a verification query or curl call. Don't move to step N+1 until step N's acceptance passes.

---

## 10. Items deferred to implementation-time decisions

These are intentionally left to Claude Code's judgment during build, not pinned in this spec:

- Choice between `react-window` and `@tanstack/react-virtual` for table virtualization (both work; pick whichever is currently better maintained at implementation time).
- Specific Tailwind color tokens for trend chips and audience badges — design taste, get reasonable defaults working then iterate.
- The exact pixel dimensions of the detail panel (~600px wide is a starting suggestion).
- Test framework (vitest is the natural fit for Vite; pytest for the FastAPI backend).
- Whether to add a `react-query` / `swr` layer for API state. v1 used plain `fetch` + `useState`; v2 may benefit from caching invalidation patterns but it's not required.

The snapshot banner from §3.9 is the *minimum* mitigation for historical-run staleness. The fuller fix (per-run-anchored velocity windows, cross-run lineage display, switching the daily centroid assigner across runs, possible viability inheritance) is captured as a deferred problem definition in `05_potential_implementations/next_tasks/cluster_run_continuation.md`.

Surface anything material in the implementation handoff so it can be folded back into the spec.

---

## 11. Implementation retrospective (to be appended after completion)

> _Placeholder for the one-line retrospective per `_conventions.md` §02_components convention. To be added by Claude in the session that ratifies Claude Code's handoff._
