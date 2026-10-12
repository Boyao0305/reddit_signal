# Phase 3B — Path C ranking (Q / M / S axes + Pareto layers + balance score)

> **v1.0 (2026-06-08) — implemented.** Notes: shipped via Claude Code in two spawns (the first SIGTERM'd at 22:41 after writing the migration; resume via `systemd-run --user` finished backend, frontend, tests, and verification at 23:08). All deliverables green: 17 pytest / 14 vitest / tsc clean / vite build clean / live API restarted with full path-C field set. 28 L1 / 51 L2 / 48 L3 / 51 L4 / 48 L5 / 514 NULL on B2C run 10; 410 of 740 are eligible. Spec §4.1 / §5.2.7 column-count correction folded below; multi-audience perf logged to `04_open_questions.md`. Handoff: `03_handoffs/2026-06-08.md`.

> **Status:** v1 spec — design locked, build authorized. Owner of build: Claude Code via delegation.
> **Predecessors:** `phase2c_dashboard_v2.md` (the dashboard reads `v_market_opportunities`); the `from-colourpinke` branch carrying a parallel implementation against a different DB.
> **Why this exists:** colourpinke's branch ships a meaningfully better triage UX (3 axes + Pareto frontier + balance score) than the existing 14-dim weighted score, but its DDL was deployed out-of-band against a separate Postgres on an SSD socket the main server cannot reach, and several of its design choices need ratification (softening of LD-#14/#29, eligibility gate, harmonic edge cases) before landing on `main`. This component re-derives the algorithm from the column shape, locks the gaps, and ships a clean migration + tests on `reddit_raw`.

---

## 1. Goal

One dashboard query produces (a) Q/M/S three-axis values per cluster, (b) Pareto-frontier layer (1 = optimal, 2+ = dominated), (c) a harmonic-mean `balance_score`, (d) an `is_eligible` flag for "shown by default" filtering, (e) a `low_confidence_s` flag where the supply-gap signal is sparse. The dashboard sorts `pareto_layer ASC, balance_score DESC` by default and exposes a Q/M/S slider trio for the user-controlled `explore_score` (session-local, computed in JS from the three persisted axis values).

## 2. Hard constraints (must hold across this build)

- **LD-#8** — only two hard gates allowed in `is_candidate`: `is_real_pain` and `is_software_solvable`. Path C's `is_eligible` is a *display filter*, NOT a gate, NOT consulted by `is_candidate`. The user can toggle it off in the UI to reveal the full set.
- **LD-#14 / LD-#29** — composites are exploratory, transparent, non-gating. Path C softens this further but per the new LD lands in this commit (see §6) — `pareto_layer` and `balance_score` are server-computed and persisted in the view, but: (a) they are surfaced with the formula and inputs in the row tooltip; (b) they never enter `is_candidate`; (c) the user-driven Explore score remains session-local and re-rankable live.
- **LD-#31** — the dashboard reads from a single market-opportunities surface. We satisfy this by *renaming* the existing surface to `v_market_opportunities_ranked` AND keeping `v_market_opportunities` as a backward-compat view that simply selects the legacy columns from the ranked view. New code consumes `_ranked`. Old probes (test fixtures, ad-hoc psql) still work.
- **`02_principles.md` — additive idempotent migrations only.** All new DDL must be `CREATE OR REPLACE VIEW` / `CREATE TABLE IF NOT EXISTS` / `CREATE OR REPLACE FUNCTION`. No destructive DROP-without-IF-EXISTS.

## 3. Algorithm

Operates per-`cluster_run_id` against the rows of the existing `v_market_opportunities` view. All percentile computations are *within-run* (DENSE_RANK over the run's clusters, divided by run-cluster-count), and *within-audience* where the column distributions differ across B2B/B2C/mixed (per LD-#9 audience semantics).

### 3.1 The three axes (each mapped to [0, 1])

**Q — Quality.** Signal quality of the cluster: how coherent, diverse, and specific the underlying pain reports are. Inputs (all already in `v_market_opportunities`):

| Input | Direction | Within-run percentile rank |
|---|---|---|
| `cqm.thread_diversity` | higher = better | p_thread |
| `cqm.author_diversity` | higher = better | p_author |
| `cqm.specificity_mean` | higher = better | p_spec |
| `cqm.separation` | higher = better | p_sep |
| `cqm.temporal_spread_days` | higher = better | p_temp |

`q_axis = 0.25*p_thread + 0.20*p_author + 0.25*p_spec + 0.15*p_sep + 0.15*p_temp`

(Author diversity ranges 0.767–1.000 in run 10 — narrow, hence the slightly lower weight; thread + specificity carry the most signal.)

**M — Momentum.** Cluster-level activity trend. Inputs:

| Input | Direction | Within-run percentile rank |
|---|---|---|
| `vcv.slope_per_week` | higher = better | p_slope |
| `vcv.pain_count_prior_4w` | higher = better (volume signal) | p_prior |

`m_axis = 0.6*p_slope + 0.4*p_prior`

We deliberately exclude `velocity_4w_ratio_normalized` and `pain_count_last_4w` from the M axis on run 10 — coverage is 3.6% and 14% respectively because the 90-day clustering window ends 2026-05-06 (see `03_failure_log.md` 2026-06-08). The 2026-06-01 monthly recluster will move that window forward and these columns regain coverage; future-run calibration MAY add them back. For now the slope (computed over the cluster's full history) and prior-4w (the more populated of the two volume buckets) are the reliable momentum signals.

**S — Supply gap.** "Underservedness" — how few existing tools the pain mentions, how many users describe workarounds. Inputs:

| Input | Direction | Within-run percentile rank |
|---|---|---|
| `cqm.workaround_ratio` | higher = better (more workarounds = more underserved) | p_workaround |
| `tool_density` (computed, see §3.2) | lower = better (fewer mentions = more underserved) | p_tool_inv |

`s_axis = 0.5*p_workaround + 0.5*p_tool_inv`

`low_confidence_s = TRUE` when the cluster has fewer than 20 pain_object members with non-null `mentioned_tools` arrays — too sparse to trust the tool-density estimate.

### 3.2 `tool_density` derivation

Computed in a CTE off `pain_cluster_members` joined to `pain_objects`:

```sql
WITH cluster_tool_density AS (
  SELECT pcm.cluster_id,
         COUNT(*) FILTER (WHERE array_length(po.mentioned_tools, 1) > 0)::float
           / NULLIF(COUNT(*), 0) AS tool_density
  FROM pain_cluster_members pcm
  JOIN pain_objects po ON po.id = pcm.pain_id
  JOIN pain_clusters pc ON pc.id = pcm.cluster_id
  WHERE pc.run_id = <run_id_from_GUC>
  GROUP BY pcm.cluster_id
)
```

`p_tool_inv = 1.0 - percent_rank() OVER (ORDER BY tool_density)`. Clusters where every member mentions a tool → high tool_density → low p_tool_inv → low S. Clusters where no member mentions a tool → tool_density=0 → high p_tool_inv → high S (everyone is suffering, no one has shipped a fix yet).

### 3.3 Eligibility partition

`is_eligible = (q_axis >= p25(q_axis_within_audience)) AND (s_axis >= p25(s_axis_within_audience))`.

Rationale: a cluster that's bottom-quartile on quality (probably noisy) OR bottom-quartile on supply-gap (probably already-served) is filtered from the default view. The user toggles "show ineligible" in the dashboard to see the full set. Eligibility is computed within audience because B2C and B2B distributions differ.

`is_eligible` is **NOT** a hard gate. It does not flow into `is_candidate`. The view returns ineligible rows; the dashboard hides them by default.

### 3.4 Harmonic mean balance score with zero-floor

```
ε = 0.05  (5 % of the [0,1] axis range)

if q_axis < ε OR m_axis < ε OR s_axis < ε:
    balance_score = NULL    -- excluded from L1 by the layering function
else:
    balance_score = 3.0 / (1.0/q_axis + 1.0/m_axis + 1.0/s_axis)
```

Rationale: a cluster that's effectively zero on any one axis is structurally not Pareto-optimal — declaring its balance "high" because the other two are good would mislead. NULL is the honest answer. The ε=0.05 floor handles the divide-by-tiny-positive case where the harmonic mean explodes to a misleadingly-high number.

### 3.5 Pareto layering

Implemented as a SQL function `fn_compute_pareto_layers(cluster_run_id INT, audience TEXT) RETURNS TABLE(cluster_id INT, pareto_layer INT)`.

Within (run_id, audience), a cluster `A` is *dominated by* `B` iff `B.q_axis >= A.q_axis AND B.m_axis >= A.m_axis AND B.s_axis >= A.s_axis AND (B strict on at least one axis)`.

Iterative peel:
- Layer 1 = all eligible clusters with non-NULL balance_score not dominated by any other eligible cluster (in the same audience).
- Remove L1 from the candidate set; Layer 2 = clusters not dominated by anyone left.
- Repeat until empty.
- Ineligible or balance-NULL clusters get `pareto_layer = NULL` (NULL sorts last by `NULLS LAST`).

Cap at 5 layers — anything beyond layer 5 also gets `pareto_layer = NULL`. Triage UX doesn't benefit from labeling layer 47.

Performance note: 740 B2C clusters × pairwise dominance is ~270K comparisons per layer per audience. Materialize as a single function call invoked once when the view is queried (the view sets a GUC, the function reads it). Acceptable for our scale; optimize only if it shows up in dashboard-load timings.

## 4. Schema deliverables

### 4.1 Migration `migrations/v2_015_path_c_ranking.sql`

> **Correction (2026-06-08, post-implementation):** the spec says "legacy 35 columns" in step 4 and §5.2.7; the actual `v2_013` projection is **34 columns** (the velocity-ratio pair `velocity_4w_ratio` + `velocity_4w_ratio_normalized` plus 32 others). The migration's shim and `tests/test_dashboard_v2_path_c.py::LEGACY_COLS` use 34. Original "35" prose retained for intent.

Real DDL, idempotent. Order:

1. `CREATE OR REPLACE FUNCTION fn_compute_pareto_layers(p_run_id INT, p_audience TEXT) RETURNS TABLE(cluster_id INT, pareto_layer INT) ...` — pure SQL, language `plpgsql`, marked STABLE.
2. `CREATE OR REPLACE VIEW v_market_opportunities_pregate AS ...` — internal view: original `v_market_opportunities` columns + tool_density + raw axis-input percentiles.
3. `CREATE OR REPLACE VIEW v_market_opportunities_ranked AS ...` — the surface the API reads. Selects everything from `v_market_opportunities` + the 7 new fields: `pareto_layer`, `balance_score`, `q_axis`, `m_axis`, `s_axis`, `is_eligible`, `low_confidence_s`. GUC parameterization unchanged from `v_market_opportunities`.
4. `CREATE OR REPLACE VIEW v_market_opportunities AS SELECT cluster_id, cluster_run_id, label, member_count, coherence, is_candidate, audience_classifier_version, audience_type, audience_confidence, icp_hint, thread_diversity, author_diversity, subreddit_count, specificity_mean, workaround_ratio, separation, temporal_spread_days, source_diversity, viability_run_id, is_real_pain, is_software_solvable, q_specificity, q_noise_flag, v_buildability, v_icp_clarity, v_moat_potential, v_rationale, pain_count_last_4w, pain_count_prior_4w, weeks_with_data, total_pain, slope_per_week, velocity_4w_ratio, velocity_4w_ratio_normalized FROM v_market_opportunities_ranked;` — backward-compat shim.
5. Smoke-test queries at the end of the migration as comments (not executed) listing the invariants the next section enumerates.

### 4.2 Backend code changes

- **`src/dashboard_2/api.py`** `/api/v2/clusters` — switch `FROM v_market_opportunities` to `FROM v_market_opportunities_ranked`. Default sort `ORDER BY pareto_layer ASC NULLS LAST, balance_score DESC NULLS LAST`. Output payload adds 7 new fields per row (`pareto_layer`, `balance_score`, `q_axis`, `m_axis`, `s_axis`, `is_eligible`, `low_confidence_s`).
- **`src/dashboard_2/db.py`** — keep `DEFAULT_DSN` reading from `DATABASE_URL` env (the existing pattern; do NOT introduce the SSD socket from `from-colourpinke`).
- **`src/dashboard_2/scoring.py`** — fold colourpinke's `DIMENSION_META` `status='deprecated'` annotation but **change the deprecation_reason** to: *"run-10 90-day window ends 2026-05-06; coverage on the 4w-ratio columns is naturally 3.6 % and 14 %. The next monthly recluster (2026-07-01 onward) will move the window forward and coverage recovers automatically. Treat this as a per-run calibration window, not a permanent deprecation. See `03_failure_log.md` 2026-06-08."* Keep weight pinned to `0.0` — the column shouldn't drive the legacy 14-dim score with that little data, but the framing is run-10-specific, not architectural.

### 4.3 Frontend deliverables

- **`src/dashboard_2/frontend/src/types.ts`** — add `Cluster` fields `pareto_layer`, `balance_score`, `q_axis`, `m_axis`, `s_axis`, `is_eligible`, `low_confidence_s` (all nullable). Add `AxisWeights {q, m, s}`, `DEFAULT_AXIS_WEIGHTS = {q:1, m:1, s:1}`, and `computeExploreScore(cluster, axisWeights)` returning `null | number` per the colourpinke implementation (verbatim — it's correct).
- **`src/dashboard_2/frontend/src/components/WeightsPanel.tsx`** — add the "Explore (Q / M / S)" section above the existing 14-dim sliders. Three sliders, each with a percentage-share bar. Reset button. (colourpinke's implementation is fine; carry it forward as-is.)
- **`src/dashboard_2/frontend/src/components/ClusterTable.tsx`** — add columns Layer (Pareto, L1=emerald-600 bold / L2=stone-600 / else=stone-400), Balance, Explore ✦. Default sort `pareto_layer ASC, balance_score DESC`.
- **`src/dashboard_2/frontend/src/components/ClusterRow.tsx`** — **CRITICAL: keep the existing design language.** Use `rounded-[20px] ring-1 ring-stone-200/60 shadow-[0_1px_3px_rgba(28,25,23,0.05),0_4px_14px_rgba(28,25,23,0.04)] px-5 py-2 hover:ring-stone-300/80 hover:shadow-[0_2px_6px_rgba(28,25,23,0.07),0_8px_24px_rgba(28,25,23,0.06)] transition-all`. Do NOT regress to `rounded-lg`, `shadow-sm`, or violet hover ring. Violet is reserved for Explore ✦ cell text + Q/M/S sliders only. Tighten column widths and font sizes to fit new columns; do not strip the card style.
- **`src/dashboard_2/frontend/src/components/ClusterDetailPanel.tsx`** — add 4 metric tiles for Pareto Layer, Balance Score, Q/M/S, Eligible (carry colourpinke's tile content but with **English-only** copy — replace the Chinese tooltip strings with English equivalents that describe the same meaning).
- **`src/dashboard_2/frontend/src/App.tsx`** — pass `axisWeights` state down to `WeightsPanel` + `ClusterTable`.
- **`src/dashboard_2/frontend/src/lib/score.test.ts`** — extend with cases for `computeExploreScore` (all-null axes → null, total=0 → null, mix of null and value).

### 4.4 Filter UI for "show ineligible"

A toggle at the top of the cluster list: "Show ineligible (below-Q25 on Q or S axis)". OFF by default. When ON, ineligible rows appear at the bottom of the layered list (their `pareto_layer` is NULL so they sort last by `NULLS LAST`).

## 5. Tests (acceptance criteria)

### 5.1 Migration apply (idempotent)

```sql
\i migrations/v2_015_path_c_ranking.sql
\i migrations/v2_015_path_c_ranking.sql  -- second apply is a no-op
SET llm_analyse.cluster_run_id='10'; SET llm_analyse.viability_run_id='4'; SET llm_analyse.audience_classifier_version='v1';
SELECT count(*) FROM v_market_opportunities WHERE audience_type='B2C';     -- still 740 (LD-#33)
SELECT count(*) FROM v_market_opportunities_ranked WHERE audience_type='B2C'; -- 740
```

### 5.2 Backend pytest (`tests/test_dashboard_v2_path_c.py`, NEW)

1. Connect via DSN, set GUCs, query `v_market_opportunities_ranked` for run 10 / B2C; assert row count = 740 and every row has all 7 path-C columns.
2. Pareto layer 1 is non-empty (`>= 5` rows) and pairwise non-dominated on (q_axis, m_axis, s_axis).
3. For every row with `is_eligible=TRUE`, `q_axis >= q_axis_p25_within_audience` and `s_axis >= s_axis_p25_within_audience`.
4. For every row with any axis < 0.05, `balance_score IS NULL` and `pareto_layer IS NULL`.
5. Harmonic mean: pick a row with all three axes ≥ 0.5; assert balance_score within ±0.001 of `3 / (1/q + 1/m + 1/s)` computed in Python.
6. `low_confidence_s = TRUE` rows: assert per-row pain-with-tools count `< 20` against `pain_cluster_members` join.
7. The legacy `v_market_opportunities` projection still returns the original 35 columns (no schema break) and the row count matches `v_market_opportunities_ranked`.
8. `/api/v2/clusters?audience=B2C` returns 740 rows, sorted by `pareto_layer ASC NULLS LAST, balance_score DESC NULLS LAST`. First non-NULL-layer row has `pareto_layer == 1`.

### 5.3 Frontend vitest (`src/dashboard_2/frontend/src/lib/score.test.ts` extension)

1. `computeExploreScore({q_axis: 0.5, m_axis: 0.5, s_axis: 0.5}, {q:1, m:1, s:1}) === 0.5`
2. `computeExploreScore({q_axis: null, m_axis: null, s_axis: null}, {q:1, m:1, s:1}) === null`
3. `computeExploreScore({q_axis: 0.5, m_axis: 0.5, s_axis: 0.5}, {q:0, m:0, s:0}) === null`
4. `computeExploreScore({q_axis: 1, m_axis: null, s_axis: 0}, {q:1, m:1, s:1})` ≈ `(1+0+0)/3 = 0.333` (null counted as 0 per total-weights normalization).
5. The default sort key in `ClusterTable.tsx` initial state is `pareto_layer` direction `asc`.

### 5.4 Build / typecheck

```
pytest -q tests/test_dashboard_v2_path_c.py
npx --no-install vitest run --reporter=basic
CI=1 npx --no-install tsc --noEmit
CI=1 NO_COLOR=1 timeout 120 node ./node_modules/vite/bin/vite.js build --logLevel error
```

All must exit 0. (The `vite build` invocation pattern is from the openclaw-coherence-verification skill — Hermes' terminal heuristic refuses `npx vite build` foreground.)

### 5.5 Live API smoke

```
curl -s 'http://127.0.0.1:8765/api/v2/clusters?audience=B2C' | jq '.clusters[0] | keys' | grep -E 'pareto_layer|balance_score|q_axis|m_axis|s_axis|is_eligible|low_confidence_s'
# Expect all 7 keys to be present.
```

## 6. Out of scope for this build

- Anything against the SSD-mounted Postgres on `/media/yuqing/SSD1/...`. We deploy strictly on `localhost:5432/reddit_raw`.
- The 14-dim legacy score's deprecation. The Score column stays; the user can still drive it.
- Any change to `is_candidate`, the binary gates (`is_real_pain`, `is_software_solvable`), or LD-#8/#33 semantics.
- Migration of run-10 data into `pain_volume_weekly` for the missing weeks. The 4w-ratio coverage gap is a calibration artifact, not a data bug — let the next monthly recluster fix it.
- Mobile-access changes to `api.ts` / `vite.config.ts` (the previously-deferred uncommitted set on `main`).

## 7. CHANGELOG

- **v1.0 (2026-06-08) — implemented and deployed to `localhost:5432/reddit_raw`.**
  - **Performance:** initial wide-query timeout (~23s, multi-audience GROUP BY hit 60s statement timeout) traced via `EXPLAIN ANALYZE` to triple-evaluation of `v_market_opportunities_pregate` (9 window functions + a 653K-row tool_density join, ~6.6s per scan). Fix: `WITH pg AS MATERIALIZED (…)` in `v_market_opportunities_ranked` pins pregate to a single materialization (PG12+ optimization fence; reddit_raw is PG16). Result: ~23.5s → ~12.6s for B2C run 10. Idempotent.
  - **Implementation decisions ratified** (per `06_teamwork_protocol.md §4.1` — annotated, not new LDs):
    (a) dual `include_ineligible` filter — server-side query param `?include_ineligible=false` AND client-side `showIneligible` toggle (default OFF; eligible + null-eligibility always shown);
    (b) the existing "Norm. ratio" tile in `ClusterDetailPanel` keeps its original English copy — no Chinese ETL-deprecation annotation ported; the run-10 framing lives in `scoring.py:DIMENSION_META` and surfaces via `/score-config`;
    (c) `DIMENSION_META.deprecated_since = "2026-06-08"` (the date the run-10-window-aging framing was locked, not colourpinke's `2026-06-04`);
    (d) Path C detail tiles render when `pareto_layer !== null || balance_score !== null || is_eligible !== null` (defensive against future runs where eligibility exists without a layer).
  - **Confirmed expected, not a defect:** `low_confidence_s = TRUE` on a layer-1 cluster (top B2C row carries the flag). Per §3.1 the flag is a data-density caveat on S, independent of rank. Surfaced in the detail tile.
  - **Deferred:** multi-audience perf scaling — see `04_open_questions.md` 2026-06-08 entry.
- **v1 (2026-06-08)** — initial spec. Re-derives Path C from colourpinke's column shape; locks LD-#36 (composite softening), LD-#37 (eligibility filter), LD-#38 (harmonic zero-floor); requires English-only copy, design-language preservation in the row card, env-driven DSN, and idempotent additive migration.
