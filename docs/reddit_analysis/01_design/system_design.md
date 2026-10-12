# LLM Analyse — System Design

> **Status:** v3.4 — current as of 2026-07-13.
> **Predecessors:** v3.0 (2026-05-12, dashboard v2 in design) → v3.1 (2026-06-08, dashboard v2 + Path C ranking shipped) → v3.2 (2026-06-23, burst sentinel + production L5 competition MVP shipped) → v3.3 (2026-06-27, source backfill fetch/projection split + worker lane separation). v2 archived at `archive/v2_2026-05-04.md`.
> **Companion:** `database_schema.md` (same folder) — schema details live there.
> **Scope:** Architecture and operational state. Decision rationales live in `01_locked_decisions.md`; implementation plans in `02_components/`; deferred designs in `05_potential_implementations/`.

This file is meant to be the first place a new Claude session looks after the conventions and teamwork-protocol files. It answers: what runs, where, when, and which tables hold the output.

---

## 1. Quick reference

### Last live DB baseline snapshot (verified on 2026-05-12)

The counts below are a historical DB snapshot. Feature status in this document is updated through 2026-06-27.

| Item | Value |
|---|---|
| L1 posts | 3,930,630 · `processed_posts` ≈ 3.92M after 2026-05-12 backfill |
| L2 signal_events | 45,623,945 |
| L3 pain_objects | 1,582,843 (write-once per prompt_version) |
| L4 baseline | `analysis_runs.id = 10` · 2,594 clusters · window 2026-02-05 → 2026-05-06 · 649,521 cluster memberships |
| L4 viability | `viability_run_id = 4` · 1,070 scored · **740 viable B2C** (is_real_pain ∧ is_software_solvable) |
| L4 weekly aggregates | `pain_volume_weekly` populated · 15 weeks · 31,995 (cluster, week) rows |
| Branch | `v2-phase1`, HEAD `cd83ee3` |
| Daily cron | Healthy. Latest run notes: `03_handoffs/`. |

### Schedule

- **Daily 04:05 UTC** (`scripts/b_ops_1_daily.sh`): ingest enabled runtime sources → project to `signal_events` → extract pains (Gemini Flash Lite) → score (spaCy specificity + sentiment) → embed → assign each new pain to the nearest existing centroid at threshold 0.70 → update temporal aggregates and dashboard snapshot. Bounded by genuinely new content; historical Reddit-only baseline was ~30 min, ~5–15K new pains, ~28% (≈1.6K) successfully assign to existing centroids.
- **Monthly 05:00 UTC on the 1st** (orchestrator, first untested fire on 2026-06-01): re-cluster the past 90 days with HDBSCAN, recompute `cluster_audience` + `cluster_quality_metrics`, run viability scoring on tier-1 survivors, rebuild `pain_volume_weekly` for the new run, recalibrate thresholds from observed p10 distributions. Lineage to the prior run via Jaccard member overlap.
- **Ad-hoc**: pain-extraction backfill over older signal_events, threshold recalibration, manual single-cluster reruns.

### Production placement

- Web/API remains in the existing Zeabur service on EC2 `54.82.99.49`.
- PostgreSQL runs in the target host's managed `pgvector/pgvector:pg18` container.
- Web/API reaches PostgreSQL through a host-level SSH tunnel; target host workers use the local PostgreSQL NodePort.
- The three active workers are `openclaw` user systemd services under `/opt/openclaw`, with linger enabled; the old EC2 worker services stay disabled.

### Worker lanes

`scripts/ec2_pipeline_worker.py` now supports independent lanes so different task families do not block each other:

| Lane | Consumes | Notes |
|---|---|---|
| `pipeline` | `pipeline_jobs` daily/monthly | Also refreshes dashboard snapshots after daily/monthly success. |
| `source-backfill` | `source_backfill_chunks` | Historical fetch and projection chunks. Same lane remains serial to avoid double-claiming chunks. |
| `extraction` | `pipeline_jobs` extraction | Manual extract + score + embed jobs from Sources > Processing. |
| `snapshot` | dashboard snapshot refresh loop | Optional dedicated lane for hourly snapshot refresh when enabled. |

### Feature status and deferred items

| Component | Status |
|---|---|
| Unified source backfill | **Shipped 2026-06-27** — fetch-only by default; completed fetch jobs can manually queue projection jobs; backfill, daily/monthly, extraction, and optional snapshot refresh run in separate EC2 worker lanes. |
| L5 competition layer | **Shipped MVP 2026-06-23** — `competition_runs`, `cluster_competition_assessments`, `cluster_competitors`; dashboard snapshot uses competition-adjusted S axis when `research_confidence >= 0.50`. Legacy `cluster_competition` remains ignored by Phase 2+ code. |
| Dashboard v2 + Path C ranking | **Shipped 2026-06-08** (`02_components/phase2c_dashboard_v2.md` v1.0 + `phase3b_path_c_ranking.md` v1.0). Reads from `v_market_opportunities_ranked` (default-sorted by `pareto_layer ASC, balance_score DESC`); legacy `v_market_opportunities` retained as a backward-compat 34-column shim over the ranked view. v1 dashboard at `src/dashboard/` is frozen. |
| `share_global` / `share_field` backfill | **Shipped 2026-05-12** (Phase 2C). Per-week sums to 1.0000, 0 NULLs on run 10. |
| Nonlinear composite | Deferred — `05_potential_implementations/signal_generation/nonlinear_composite.md`. |
| Backtest harness | Deferred — `05_potential_implementations/signal_generation/backtest_harness.md`. |
| v1 deprecated columns on `pain_clusters` | Phase 2.x cleanup — after Path C dashboard verified in production. |
| Operational runbook | Triggers within 48h of first monthly orchestrator run. |

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  L1 — RAW (immutable, source-native)                              │
│  posts · comments · subreddits · app_store_apps · processed_posts │
└────────────────┬─────────────────────────────────────────────────┘
                 │ adapter.project()   (src/sources/<name>_adapter.py)
                 ▼
┌──────────────────────────────────────────────────────────────────┐
│  L2 — PROJECTED                                                   │
│  signal_events · source_capabilities                              │
└────────────────┬─────────────────────────────────────────────────┘
                 │ pain_extraction   (LLM, write-once per prompt_version)
                 ▼
┌──────────────────────────────────────────────────────────────────┐
│  L3 — EXTRACTED                                                   │
│  pain_objects (signal_event_id, prompt_version, embedding …)      │
└────────────────┬─────────────────────────────────────────────────┘
                 │ HDBSCAN monthly · audience classifier · quality metrics ·
                 │ viability scorer · daily centroid assignment
                 ▼
┌──────────────────────────────────────────────────────────────────┐
│  L4 — CLUSTERED + JUDGED                                          │
│  analysis_runs · pain_clusters · pain_cluster_members             │
│  cluster_audience · cluster_quality_metrics · viability_scores    │
│  pain_volume_weekly · candidate_promotion_log                     │
└────────────────┬─────────────────────────────────────────────────┘
                 │ SQL VIEWs (parameterized by GUC run_ids)
                 ▼
   v_market_opportunities_pregate
        → fn_compute_pareto_layers (per audience, ≤5 layers)
        → v_market_opportunities_ranked   ← API reads this
        → v_market_opportunities          (backward-compat 34-col shim)
   v_cluster_velocity
                 │
                 ▼
┌──────────────────────────────────────────────────────────────────┐
│  L5 — COMPETITION MVP                                             │
│  competition_runs · assessments · competitors                     │
│  competition-adjusted S axis feeds ranked snapshot when available │
└──────────────────────────────────────────────────────────────────┘
```

**Storage gradient by regen cost.** L1 is sacred (refetch is expensive). L2 is rebuildable from L1 with local SQL / adapters. L3 is rebuildable from L2 with LLM cost. L4 is rebuildable from L3 with compute cost. L5 hangs off L4 market opportunity survivors and is rebuildable from web-grounded competition research.

**L6 viability** was eliminated in the Phase 2 redesign (Decision #7). The LLM judgment lives at L4 in `viability_scores`, fused with quality.

---

## 3. Per-layer details

### L1 — Raw

Reddit (and eventually other sources) mirrored as source-native tables — schema in `database_schema.md`. **Never deleted, never modified.** 210 subreddits active; pruning deferred pending empirical B2C-yield-per-subreddit data after a few monthly runs (Decision #21).

`processed_posts` is the authoritative "we've seen this" marker. Both the daily extractor's "what's new" query and any backfill rely on it, **not** on `pain_objects` existence — this was the root cause of the 2026-05-08 token-burn incident. See `03_failure_log.md`.

### L2 — Projected

`signal_events` is the unified event stream: every L1 row that could carry a pain signal projects to one row here, keyed by `(source, source_table, source_row_id)`. Reddit remains the baseline source, and registered adapters such as Hacker News plus SocialCrawl-backed Facebook / YouTube / Twitter can populate the same L2 surface when enabled in `source_runtime_config`. ~70% disk savings vs. duplicating raw JSON because L2 links to L1, not copies.

Adapter pattern (Decision #3): a new source = one new `src/sources/<name>_adapter.py` implementing `BaseAdapter` and registered through `@register_adapter`. Runtime enablement is data-driven through `source_runtime_config`, so a new adapter is discoverable without hard-coding it into daily orchestration.

Manual source backfill is now split into two phases. Fetch jobs write only L1/source-native data; completed fetch jobs expose a `Queue projection` action that creates a projection-only job to populate `signal_events`. This keeps large historical fetches observable and cancellable without binding them to projection CPU work in the same process. Reddit projection stays per subreddit/window; SocialCrawl-backed sources project per source/window to avoid repeated projection over the same raw table slice.

### L3 — Extracted

`pain_objects` carries the LLM-extracted pain plus computed signals — 384-dim embedding (sentence-transformers), spaCy specificity heuristic, sentiment, mentioned tools, is_workaround flag. Write-once per `(signal_event_id, prompt_version)` per Decision #6.

Failed extractions go to `pain_extraction_attempts` as DLQ after N retries (Decision #24). Cost shape: roughly $30–80 per million signal_events at current Gemini Flash Lite rates.

### L4 — Clustered + Judged

Most of the system's complexity lives here. Five sub-concerns:

**Clustering shape.** Monthly HDBSCAN over 90 days of pains in the run window (Decisions #17, #18), embedded with the same 384-dim model. `pain_cluster_members` is the **only** join path between pains and clusters — `pain_objects.cluster_id` was dropped in Phase 1 step 6 (Decision #6). Centroids stored as 384-dim vectors on `pain_clusters.centroid`.

**Audience.** `cluster_audience` carries one row per `(cluster_id, classifier_version)`: B2B / B2C / mixed / unclear with confidence in [0, 1]. Cheap LLM classifier (Gemini Flash Lite) on a sampled member set. Per-cluster, not per-subreddit, because subreddits like r/Entrepreneur carry both audiences (Decisions #9, #10).

**Tier-1 quality (deterministic).** `cluster_quality_metrics` carries one row per `(cluster_run_id, cluster_id)`: `source_diversity`, `thread_diversity`, `author_diversity`, `subreddit_count`, `specificity_mean`, `workaround_ratio`, `separation` (min cosine to nearest centroid in the same run), `temporal_spread_days`. Recomputable in a single SQL+numpy pass; never on the critical write path. `coherence` and `member_count` stay on `pain_clusters` so the canonical table is self-contained (Decision #11).

**Tier-2 quality + viability (LLM).** `viability_scores` carries the fused output of one MiniMax M2.7 call per tier-1 survivor: `is_real_pain` and `is_software_solvable` (hard binary gates per Decision #8) plus 1–5 scales for `q_specificity`, `v_buildability`, `v_icp_clarity`, `v_moat_potential`, plus textual `v_rationale`. Versioned by `viability_run_id` (Decision #12). The 1–5 dimensions are **never collapsed into a composite gate** — sort/filter inputs only.

**Temporal aggregates.** `pain_volume_weekly` carries one row per `(cluster_run_id, cluster_id, week_start, audience_type, function_tag)` and powers velocity charts / 4w momentum. `pain_volume_daily` carries one row per `(cluster_run_id, cluster_id, day_start)` and powers short-term burst detection. `pain_volume_daily_corpus` carries assigned / orphan corpus-level daily totals; `corpus_burst_snapshot` is the sentinel for month-mid new topics that are not yet assigned to any cluster. Weekly aggregation is populated by `src/temporal/aggregate_weekly.py`; daily aggregation is populated by `src/temporal/aggregate_daily.py` with `--mode rebuild` for monthly runs and `--mode incremental` for the daily pipeline's recent 32-day refresh. Dashboard list endpoints do not compute burst live; `refresh_dashboard_market_opportunities()` stores the precomputed burst fields in `dashboard_market_opportunities`.

**Daily centroid assignment.** New pains from the daily cron get assigned to the closest existing centroid at threshold 0.70 (Decision #23, calibrated from TP-vs-noise distributions on run 10). This avoids HDBSCAN churn between monthly reruns. ~28% of new pains assign; the rest stay unassigned until the next monthly re-cluster surfaces them as new shapes.

### L5 — Competition

Production MVP shipped on 2026-06-23. The legacy `cluster_competition` table still holds v1 Play Store match results and remains ignored by Phase 2+ code; current L5 state is run-scoped and lives in `competition_runs`, `cluster_competition_assessments`, and `cluster_competitors`.

The competition layer replaces the old Reddit-internal S-axis proxy with web-grounded competitor research. `src/competition_layer/research.py` asks the LLM to search broadly across product websites, AlternativeTo, Product Hunt, GitHub, app stores, review sites, and general search results. It distinguishes direct competitors, adjacent tools, and incumbents, then classifies the market as `strong_incumbent`, `crowded_mature`, `fragmented_low_quality`, `adjacent_only`, `emerging_greenfield`, or `unknown`.

`src/competition_layer/scoring.py` turns the research output into:

- `competition_intensity = 0.55 * max_direct_strength + 0.30 * direct_count_pressure + 0.15 * incumbent_pressure`
- `external_supply_gap = 0.65 * (1 - competition_intensity) + 0.35 * solution_gap_evidence`
- `competition_adjusted_s_axis`, only when `market_structure != 'unknown'` and `research_confidence >= 0.50`

Low-confidence or unknown competition research emits no S axis instead of falling back to the old Reddit proxy. This is intentional: the layer prefers visible absence over false precision.

---

## 4. Output VIEWs

### `v_market_opportunities_ranked`

The unified ranking surface read by Dashboard v2 since 2026-06-08 (Phase 3B). Built on top of `v_market_opportunities_pregate` (an internal CTE-style view computing the gated join + tool_density + within-run axis-input percentiles + Q/M/S axes + ε-floored harmonic balance score + low-confidence-S flag + within-audience eligibility), then joined to `fn_compute_pareto_layers(p_run_id, p_audience)` via `CROSS JOIN LATERAL` over the audiences present in the run. Same three GUCs as `v_market_opportunities` parameterize the underlying scan.

Adds 7 fields on top of the legacy projection: `pareto_layer ∈ {1..5, NULL}`, `balance_score ∈ [0,1] ∪ NULL`, `q_axis`, `m_axis`, `s_axis ∈ [0,1]`, `is_eligible BOOL`, `low_confidence_s BOOL`. Default API sort: `pareto_layer ASC NULLS LAST, balance_score DESC NULLS LAST`. The `WITH pg AS MATERIALIZED (…)` fence pins the expensive pregate scan to a single materialization (PG12+, reddit_raw is PG16) — without it the wide query evaluates pregate three times and runs ~23s; with it ~12.6s for B2C run 10. See LD-#36 (composite softening), LD-#37 (eligibility partition is a display filter, not a gate), LD-#38 (harmonic ε=0.05 zero-floor).

### `v_market_opportunities_competition_ranked`

Competition-aware ranking surface introduced by `v2_033_competition_layer.sql`. It wraps `v_market_opportunities_ranked`, joins the latest run-scoped competition assessment, replaces the S axis with `competition_adjusted_s_axis` when available, and computes `competition_adjusted_balance_score` plus `competition_adjusted_pareto_layer`.

The dashboard repository prefers this view and falls back to the base ranked view only when the competition view is unavailable. Rows without reliable competition research keep visible NULL competition-adjusted fields; they are not silently scored with the old Reddit proxy.

### `v_market_opportunities`

Backward-compat shim — a 34-column projection of `v_market_opportunities_ranked` matching the v2_013 view shape. Old probes (test fixtures, ad-hoc psql, the count-of-740 invariant) keep working without touching call sites. New code reads `v_market_opportunities_ranked`. Hard filter unchanged from the v2_013 ratification (LD-#33): `pc.is_candidate ∧ vs.is_real_pain IS TRUE ∧ vs.is_software_solvable IS TRUE`. Output: filterable + sortable columns, no canonical single composite score (Decision #14, softened in §5 below for the dashboard's exploratory composite + Path C's display Pareto layer). **Dashboards consume the ranked view; they do not query the underlying tables directly.**

### `v_cluster_velocity`

Helper view over `pain_volume_weekly`:
- `pain_count_last_4w`, `pain_count_prior_4w`
- `weeks_with_data`, `total_pain`
- `slope_per_week` — linear regression on `(week_index, pain_count)` over the cluster's full history
- `velocity_4w_ratio` — raw `last_4w / prior_4w`, NULL when either window has < 3
- `velocity_4w_ratio_normalized` — the raw ratio divided by the corpus-wide last_4w/prior_4w ratio (1.0 = corpus pace, > 1.0 = growing faster, < 1.0 = declining faster). Removes the systematic downward bias the raw ratio inherits when the corpus has a volume swing.

**Calibration note (2026-06-08):** `velocity_4w_ratio_normalized` is intentionally NOT included in the Q/M/S axes of Path C (`v_market_opportunities_ranked`) for run 10. Run 10's clustering window ends 2026-05-06; only ~28 % of post-window pains assign to existing centroids (LD-#23), so coverage on the 4w-ratio columns is naturally 3.6 % / 14 %. This is a per-run window-aging artifact, not an ETL break — the next monthly recluster moves the window forward and coverage recovers automatically. Path C's M axis uses `slope_per_week` (full history) + `pain_count_prior_4w` (the more populated of the two volume buckets) to stay reliable on run 10. See `03_failure_log.md` 2026-06-08 and `04_open_questions.md` (run-10 sparsity).

---

## 5. Cross-cutting concerns

### Versioning

- `cluster_run_id` (= `analysis_runs.id`) versions cluster shapes and tier-1 quality metrics.
- `viability_run_id` (= `viability_runs.id`) versions tier-2 LLM judgments. Multiple viability runs can exist per cluster run.
- `prompt_version` on `pain_objects` versions L3 extraction. Re-extraction with a new prompt produces new rows; old rows stay.
- `classifier_version` on `cluster_audience` versions audience classification.

All four are stored on the rows and flow through `params` JSONB / GUC settings. New work creates new runs; old runs stay queryable forever.

### Threshold calibration

Per-run, distribution-based (Decision #16). Each monthly re-cluster computes p10 of each tier-1 metric on its own clusters and sets thresholds accordingly. No reuse of thresholds across runs without an explicit drift check. Thresholds stored in `analysis_runs.candidate_thresholds` as JSONB so each run is replayable at its original gate.

Defaults: "cut at p10" — the bottom 10% of each tier-1 distribution. More aggressive cuts need an empirical reason; less aggressive cuts let in known-bad clusters.

### Velocity (three-signal model)

Per the dashboard v2 design pass (this session), velocity is rendered as three complementary signals, not a single ratio:

| Signal | What it answers | Failure mode |
|---|---|---|
| `pain_count_last_4w` | How active is this cluster right now? | None — always computable. |
| `velocity_4w_ratio_normalized` | Growing vs. corpus over last 8 weeks? | NULL when either window has < 3 pains. |
| `slope_per_week` | Long-term trajectory over the cluster's full history. | Flattens hump-shaped clusters. |

Disagreement between them is itself informative — a cluster with high `last_4w`, `norm_ratio < 1`, and negative `slope` is "still busy but the momentum is gone." The dashboard surfaces all three; downstream consumers pick.

Velocity is **not** a hard gate (Decision rationale: a steady or slightly declining pain can still be a viable opportunity if it's underserved). It feeds display ordering and optionally the exploratory composite, never `is_candidate`.

### The exploratory composite (dashboard v2 + Path C)

Decision #14 has been softened in two stages, never lifted. **Stage 1** (LD-#29, 2026-05-12, dashboard v2): a parameterized, transparent, non-gating composite is allowed for display ordering — the original 14-dim slider score over within-run percentile-transformed dimensions, with three guardrails (never enters `is_candidate`; weights visible at all times; session-local). **Stage 2** (LD-#36 / #37 / #38, 2026-06-08, Path C): server-computed Pareto layer + harmonic-mean balance score over three axes (Q / M / S) added to `v_market_opportunities_ranked`, with the same gating discipline plus three layered guardrails — Q/M/S axis values returned alongside the layer + balance (no opaque single number); a session-local Explore score driven by Q/M/S sliders for live re-ranking; `is_eligible` (within-audience Q & S ≥ p25) is a default display filter, NOT a gate (`is_candidate` unaffected; ineligible rows surfaced via "Show ineligible" toggle). Harmonic mean uses ε=0.05 zero-floor: any axis below ε ⇒ `balance_score = NULL` ⇒ excluded from layer 1 (LD-#38). Specs: `02_components/phase2c_dashboard_v2.md` (Stage 1) + `phase3b_path_c_ranking.md` (Stage 2).

### Original Decision #14 framing (preserved for context)

Decision #14 ("final ranking is a SQL VIEW with session GUCs, no canonical composite") is softened, not lifted, for the dashboard. The softening: a **parameterized, transparent, non-gating** composite is allowed for display ordering. The dashboard exposes user-tunable weights via a slider panel; the score is a weighted sum of within-run percentile-transformed dimensions. Three guardrails:

1. The score never enters `is_candidate` or any other gate. Display ordering only.
2. Weights are visible at all times. There is no "the system thinks this is a 0.83 cluster"; there is "at the weights you've set, this cluster ranks 12th."
3. The score is session-local. When weights are tweaked the ordering re-ranks live; the system never stores "the answer."

This satisfies the spirit of #14 ("no opaque single number you can't see inside") while letting the founder rank 740 candidates without sorting through 8 columns by hand. The full spec: `02_components/dashboard_v2_design.md`. The softening itself: see the locked-decision entry recording the change.

---

## 6. Pipeline ownership

Mapping from layer transitions to code locations.

| Transition | Owner |
|---|---|
| L1 fetch / ingest | `src/data_ingestion/` and `src/sources/*_adapter.py` (per-source fetch) |
| L1 → L2 projection | `src/sources/<name>_adapter.py` adapter `project()` |
| L2 → L3 extraction | `src/pain_extraction/` (`main.py` drives; `db.py`, `scoring.py`, `labeling.py` support) |
| L3 → L4 clustering (monthly) | `src/clustering/` |
| L4 audience classification | `src/clustering/` (audience module) |
| L4 quality metrics (tier-1) | `src/clustering/` (deterministic SQL+numpy pass) |
| L4 viability (tier-2 LLM) | `src/viability_scoring/` |
| L4 temporal aggregation | `src/temporal/aggregate_weekly.py`, `src/temporal/aggregate_daily.py` |
| L4 centroid assignment (daily) | `src/clustering/` |
| L4 output VIEWs (DDL) | `migrations/` |
| L5 competition research + scoring | `src/competition_layer/` |
| Daily orchestration | `scripts/b_ops_1_daily.sh` |
| Worker queue ownership | `scripts/ec2_pipeline_worker.py` lanes: `pipeline`, `source-backfill`, `extraction`, optional `snapshot` |
| Dashboard/API | `src/dashboard/` |

Schema migrations live in `migrations/`, all idempotent (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, UPSERT on UNIQUE keys).

---

## 7. Non-goals for the current phase

- A canonical or gating composite "market opportunity score" (the transparent slider-driven exploratory composite for display is OK; see §5).
- Full competition coverage/backfill cadence for every viable cluster. The MVP exists, but not every cluster is guaranteed to have a high-confidence competition assessment.
- Turnkey creation of real external-source credentials and production enablement. Adapters exist, but source runtime config still controls whether they enter daily.
- Re-clustering during dashboard v2 work — run_id=10 baseline stays; new work operates on existing cluster shapes.
- Backtest harness — Phase 4, deferred.
- Nonlinear composite — Phase 4, deferred.

---

## 8. CHANGELOG

- **v3.4 (2026-07-13)** — Moved PostgreSQL to the target host's managed pgvector container and moved the three worker lanes to target-host `openclaw` user systemd services with linger enabled. The existing EC2 Zeabur Web/API and public domain remain in place and reach PostgreSQL through a restricted SSH tunnel.
- **v3.3 (2026-06-27)** — Documented unified source backfill's fetch/projection split and EC2 worker lane separation. Backfill jobs now default to fetch-only; projection is queued explicitly after a fetch job completes; extraction remains a separate manual processing job. Worker ownership is split into `pipeline`, `source-backfill`, `extraction`, and optional `snapshot` lanes so daily/monthly, historical fetch/projection, and extraction no longer share one global queue.
- **v3.2 (2026-06-23)** — Documented shipped short-term burst boundary handling and the production L5 competition MVP. L4 temporal aggregates now include `pain_volume_daily_corpus` / `corpus_burst_snapshot` for orphan-topic sentinel alerts. L5 is no longer deferred: `competition_runs`, `cluster_competition_assessments`, `cluster_competitors`, and `v_market_opportunities_competition_ranked` provide competition-adjusted S axis, balance score, and Pareto layer when web-grounded research confidence is sufficient. L2 source language updated for the multi-source adapter/runtime-config model.
- **v3.1 (2026-06-08)** — Dashboard v2 + Path C ranking shipped (LD-#36 / #37 / #38). Output VIEW section restructured around `v_market_opportunities_ranked` (the new primary surface) with `v_market_opportunities` retained as a backward-compat 34-column shim. Architecture diagram updated to show the pregate → fn_compute_pareto_layers → ranked → shim chain. §1 status table flipped Dashboard v2 + share_global rows to "Shipped." §5 exploratory-composite section now documents the two-stage softening of #14 (Stage 1 = the 14-dim slider, Stage 2 = Path C's Pareto layer + balance score); original prose preserved as "original framing for context." `v_cluster_velocity` section keeps `velocity_4w_ratio_normalized` as a real column (no longer "planned") with a calibration note explaining why Path C excludes it on run 10 (window-aging, not ETL break).
- **v3.0 (2026-05-12)** — fork from v2. Reorganized around current operational state instead of "proposed Phase 2 revision." Removed the Component A/B/C/D implementation breakdown (now lives in `02_components/`). Added: explicit pipeline-ownership map, three-signal velocity model, exploratory-composite softening of Decision #14, forward pointer to dashboard v2 spec. Schema details continue to live in `database_schema.md`. All operational numbers re-verified against the live DB.
- **v2 (2026-05-04)** — Phase 2 revision proposal: fused L4 LLM judgment, eliminated L6, defined `cluster_quality_metrics` / `viability_scores` / `pain_volume_weekly`, added `v_market_opportunities` VIEW. Archived as `archive/v2_2026-05-04.md`.
- **v1** — original six-layer design with separate L4 → L5 → L6 funnel. Archived in `last_version_dont_read/` per the original Phase 2 fork.
