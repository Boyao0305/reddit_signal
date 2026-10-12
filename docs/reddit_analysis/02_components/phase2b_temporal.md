# Temporal Layer + Operational Pipeline — Plan (Phase 2 Component B + B-ops)

> **Status:** Plan — pending user approval before execution
> **Date:** 2026-05-04
> **Companion:** `00_system_design_v2.md`, `01_database_schema_v2.md`
> **Predecessor state:** Component A complete (commit `fa3916d`, run_id=9, 691 candidates, 511 viable)
>
> **2026-05-07 update:** B-prep.7 + B-prep.8 + B-ops.1 + B-ops.2 + B-ops.3 implemented and cron-installed. See `added_components/02_b_ops_implementation_log.md` for results, cost breakdown, and bugs found. Three plan deviations (calibration approach, lineage timing, monthly numbering) noted in §Plan deviations at end.

---

## 1. Mental model — daily/monthly split

The architecture separates **cluster shape** (expensive, monthly) from **cluster membership** (cheap, daily):

```
DAILY                                     MONTHLY (1st of month)
──────                                    ─────────────────────
fetch new posts/comments                  re-cluster on rolling 90d window
project → signal_events                     → new run_id (e.g. 11, 12, …)
extract pain → pain_objects                 → audience classifier
embed                                       → cluster_quality_metrics
assign each new pain to NEAREST             → candidate_gate (tier-1)
existing centroid (cosine sim)              → quality_viability_llm (tier-2)
   if sim < threshold → leave as            → mark new run as "current"
   noise (picked up at next                 → pain_volume_weekly recomputes
   monthly re-cluster)                        against the new clustering
upsert pain_volume_weekly current week
```

Sparklines stay continuous day-to-day. At month boundary the run_id flips; old run rows are kept (not deleted) so future cluster-lineage work (Phase 2.x) can stitch trajectories across runs.

### Decisions locked

| Decision                     | Value                                                       | Rationale                                                                                                |     |
| ---------------------------- | ----------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- | --- |
| Re-cluster cadence           | **Monthly** (1st of month)                                  | HDBSCAN needs ≥3-4w of fresh data to surface new clusters; weekly = ID churn without insight             |     |
| Pain subset for re-cluster   | **Rolling 90 days** by `signal_events.event_time`           | New pains surface fast; long-tail handled by cluster lineage in Phase 2.x                                |     |
| Aggregation cadence          | **Daily upsert into `pain_volume_weekly`** for current week | Cheap; sparkline is always current                                                                       |     |
| Historical lookback          | **18 months** (~2024-11 → today)                            | Lets us validate cluster lineage across re-clusters; 3 monthly re-runs of overlap                        |     |
| Subreddit curation           | **Defer** — keep all 210 as-is                              | Will compute B2C-yield-per-subreddit from existing run_id=9 data later; subreddit *expansion* is Phase 3 |     |
| Cluster-assignment threshold | **TBD via calibration** (see B-ops.2)                       | Eyeball pair-similarity at multiple cutoffs; pick where sim matches "should be same cluster" intuition   |     |
| Cron infrastructure          | **System cron**                                             | Simpler/more reliable for data pipelines; Hermes cronjob reserved for future LLM-agent alerting layer    |     |
| Failed-extraction retry      | **`pain_extraction_attempts` side table**                   | DLQ pattern: `failed_at`, `retry_count`, `last_error`                                                    |     |

---

## 2. Phase B-prep — bootstrap data foundation (one-time)

| # | Task | File / target | Cost | Wall-clock | Blocks on |
|---|---|---|---|---|---|
| **B-prep.1** | **Watchlist + ingest-state schema** — add `subreddits.is_watchlist BOOL DEFAULT TRUE`, `subreddits.last_fetched_at TIMESTAMPTZ`, `subreddits.last_seen_post_utc TIMESTAMPTZ`. Seed `is_watchlist=TRUE` for current 210. Add table `pain_extraction_attempts (signal_event_id BIGINT PK, attempts INT, last_error TEXT, last_attempt_at TIMESTAMPTZ, failed BOOL)`. | `migrations/v2_008_watchlist_and_dlq.sql` | trivial | 5 min | — |
| **B-prep.2** | **18-month historical fetch** — extend arctic-shift fetcher lookback from current Oct-2025 floor back to ~2024-11-01 for all watchlist subs. Background, idempotent (uses existing `posts.reddit_id` UNIQUE). Update `subreddits.last_seen_post_utc` per run. | run existing `src/data_ingestion` with extended date range | free | several hours | B-prep.1 |
| **B-prep.3** | **Project new L1 → signal_events** — rerun `RedditAdapter.project()`. Idempotent via `uq_signal_event_provenance`. | existing | minutes | minutes | B-prep.2 |
| **B-prep.4** | **Pain extraction backfill** — run `src.pain_extraction` over new signal_events with `prompt_version='v1'`, write-once. Failed events go to `pain_extraction_attempts`. | existing + small DLQ wrapper | ~$3-8 Gemini Flash Lite | ~12-24h bg | B-prep.3 |
| **B-prep.5** | **Embed new pain_objects** — sentence-transformers `all-MiniLM-L6-v2`, idempotent (skip rows with `embedding IS NOT NULL`). | existing | free | ~30 min | B-prep.4 |
| **B-prep.6** | **First 90d re-cluster** → `run_id=10` baseline. Runs the full Component A pipeline: cluster_all → audience classifier → quality_metrics → candidate_gate (existing thresholds) → quality_viability_llm. | existing | ~$5 LLM | ~2h | B-prep.5 |
| **B-prep.7a** | **Weekly aggregator** — `src/temporal/aggregate_weekly.py` populating `pain_volume_weekly` for `run_id=10` from `signal_events.event_time`. Idempotent ON CONFLICT. | new | trivial | seconds | B-prep.6 |
| **B-prep.7b** | **Velocity view** — `v_cluster_velocity` (4w/prior-4w ratio, NULL when either window has <3 pains). | new SQL | trivial | seconds | B-prep.7a |
| **B-prep.7c** | **Quick visual sanity check** — pick 5 high-velocity and 5 flat clusters; eyeball sparklines. | manual | — | 15 min | B-prep.7b |

**Total Phase B-prep cost:** ~$8-13 LLM, ~24-36h wall-clock (mostly background ingestion + extraction).

---

## 3. Phase B-ops — scheduled pipeline (recurring)

| #               | Task                                                                                                                                                                                                                                                                                                                                                                                                             | File / target                                                          | Cadence                   | Blocks on     |
| --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- | ------------------------- | ------------- |
| **B-ops.1**     | **Daily ingestion script** — `scripts/daily_ingest.sh`: loop watchlist subs, incremental fetch since `subreddits.last_seen_post_utc` → project → extract (idempotent) → embed → call B-ops.2 → call B-ops.3. Single shell wrapper, idempotent at every step, exits non-zero on any failure.                                                                                                                      | new                                                                    | daily                     | B-prep.7      |
| **B-ops.2**     | **Cluster assignment module** — `src/clustering/assign_to_centroid.py`. For each new pain_object embedded since last run: find nearest centroid in **current run_id** via pgvector `<=>` (cosine distance); insert into `pain_cluster_members` only if `(1 - distance) ≥ THRESHOLD`. Unassigned ones stay as noise (re-evaluated next monthly re-cluster).                                                       | new                                                                    | daily (called by B-ops.1) | B-prep.6      |
| **B-ops.2-cal** | **Threshold calibration** — sample 500 random (new_pain, nearest_centroid) pairs across the cosine-similarity range. Group by similarity bucket (0.3-0.4, 0.4-0.5, 0.5-0.6, 0.6-0.7, 0.7+). For each bucket, manually inspect 10 pairs (pain_statement vs cluster.label + 3 sample members). Pick the lowest threshold where ≥80% of pairs read as "yes, same cluster." Document the calibration in this folder. | calibration notebook + writeup                                         | once before B-ops.5       | B-ops.2       |
| **B-ops.3**     | **Daily aggregation step** — invoked by `daily_ingest.sh` after B-ops.2: upsert this-week's row in `pain_volume_weekly` for the current run_id. Single SQL UPSERT; idempotent.                                                                                                                                                                                                                                   | extension of `src/temporal/aggregate_weekly.py` (`--mode incremental`) | daily (called by B-ops.1) | B-prep.7a     |
| **B-ops.4**     | **Monthly re-cluster orchestrator** — `scripts/monthly_recluster.sh`: provision swap (4G if not present) → run `cluster_all` over rolling 90d window → audience classifier → quality_metrics → candidate_gate → quality_viability_llm → mark new run as "current" (config flag or env var read by B-ops.2) → trigger full `pain_volume_weekly` rebuild for the new run.                                          | new                                                                    | monthly (1st 03:00)       | B-prep.7      |
| **B-ops.5**     | **Operational runbook** — `llm_analyse_project_2/implementation_2/03_ops_runbook.md`: command sequence, expected runtimes, log locations, failure modes (Reddit rate-limit, OpenRouter outage, disk-full, OOM during UMAP, LLM JSON-parse failure), how to add/remove subreddits, how to roll back to a prior run_id, alert thresholds (e.g. <10 new pains/day = ingestion broken).                              | doc                                                                    | once                      | B-ops.1-4     |
| **B-ops.6**     | **Cron registration** — system cron entries written; do NOT enable until B-ops.1-4 each pass a manual dry-run on real data, AND B-ops.4 runs end-to-end once with operator watching.                                                                                                                                                                                                                             | `/etc/cron.d/openclaw_v2` (root install)                               | once                      | dry-runs pass |

---

## 4. Calibration sub-task detail (B-ops.2-cal)

Why this matters: too-low threshold → drift (noise gets force-assigned to whatever's nearest); too-high → most new pains end up unassigned and only surface at month-end.

Procedure:
1. After B-ops.2 module exists, run it in **dry-run mode** over the last week's pain_objects against `run_id=10`. Output: list of `(pain_id, nearest_cluster_id, similarity)` to a CSV.
2. Stratified sample: 50 pairs per similarity bucket (0.3-0.4, 0.4-0.5, 0.5-0.6, 0.6-0.7, 0.7+). 250 pairs total.
3. For each pair, render: `pain_statement` text, target cluster's `label`, and 3 random sample members of that cluster.
4. Manual binary judgment: "Should this pain belong to this cluster?" Rate yes/no/maybe.
5. Compute precision per bucket. Pick threshold where precision ≥ 80%.
6. Document the calibration table in `02_temporal_plan_calibration.md` (this folder, future).

Default starting guess pending calibration: **0.5** cosine similarity. Will likely move to 0.55-0.60 based on observed HDBSCAN coherence median (~0.52 on B2C candidates).

---

## 5. Suggested execution order

```
B-prep.1 (5 min)
   ↓
B-prep.2 (background, several hours)  ←── monitor disk; expect +6-10 GB
   ↓
B-prep.3 (minutes)
   ↓
B-prep.4 (background, ~12-24h)  ←── ~$3-8 Gemini Flash Lite
   ↓
B-prep.5 (~30 min)
   ↓
B-prep.6 (~2h, ~$5 LLM)  ──→  run_id=10 baseline established
   ↓
B-prep.7a + 7b + 7c (half day total)  ──→  Component B functionally complete
   ↓
B-ops.2 module (build, no run yet)
   ↓
B-ops.2-cal (manual calibration, half day)
   ↓
B-ops.1 + B-ops.3 wired together — manual dry-run for 3-5 days
   ↓
B-ops.4 — manual run once, operator watching
   ↓
B-ops.5 (runbook)
   ↓
B-ops.6 (turn on crons)  ──→  steady state
```

Stop-and-review checkpoints (recommended):
- After **B-prep.6**: confirm run_id=10 quality metrics look comparable to run_id=9; spot-check 10 viable verdicts.
- After **B-prep.7c**: review sparklines; is velocity signal real or noise?
- After **B-ops.2-cal**: confirm threshold choice before wiring daily script.
- After **3-5 days of dry-run B-ops.1**: review the daily logs; confirm assignment rate matches expectation (~70-80% of new pains assignable, ~20-30% noise is healthy).
- Before **B-ops.6**: green-light to enable cron.

---

## 6. Schema additions summary

```sql
-- B-prep.1
ALTER TABLE subreddits
  ADD COLUMN IF NOT EXISTS is_watchlist        BOOLEAN     NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS last_fetched_at     TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS last_seen_post_utc  TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS pain_extraction_attempts (
    signal_event_id   BIGINT PRIMARY KEY REFERENCES signal_events(id) ON DELETE CASCADE,
    attempts          INT NOT NULL DEFAULT 0,
    last_error        TEXT,
    last_attempt_at   TIMESTAMPTZ DEFAULT NOW(),
    failed            BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_pea_failed ON pain_extraction_attempts (failed) WHERE failed;

-- B-prep.7b
CREATE OR REPLACE VIEW v_cluster_velocity AS
WITH windowed AS (
    SELECT cluster_run_id, cluster_id,
        SUM(pain_count) FILTER (WHERE week_start >= CURRENT_DATE - INTERVAL '4 weeks') AS last_4w,
        SUM(pain_count) FILTER (WHERE week_start >= CURRENT_DATE - INTERVAL '8 weeks'
                                 AND  week_start <  CURRENT_DATE - INTERVAL '4 weeks') AS prior_4w
    FROM pain_volume_weekly
    GROUP BY cluster_run_id, cluster_id
)
SELECT cluster_run_id, cluster_id,
    last_4w  AS pain_count_last_4w,
    prior_4w AS pain_count_prior_4w,
    CASE WHEN COALESCE(prior_4w,0) < 3 OR COALESCE(last_4w,0) < 3 THEN NULL
         ELSE last_4w::float / prior_4w END AS velocity_4w_ratio
FROM windowed;
```

Existing tables used unchanged: `signal_events`, `pain_objects`, `pain_clusters`, `pain_cluster_members`, `pain_volume_weekly`, `cluster_audience`, `cluster_quality_metrics`, `viability_runs`, `viability_scores`.

---

## 7. Non-goals for Phase 2 Component B

- **Cluster lineage tracking** across re-clusters (mapping run_id=10 cluster X → run_id=11 cluster Y by centroid similarity). Defer to Phase 2.x once we have ≥3 monthly runs of data.
- **Adaptive threshold** for daily assignment (per-cluster, function of coherence). Start with global fixed threshold; revisit after 1-2 months of operation.
- **Sub-daily ingest cadence** (hourly). Daily is enough; we can move faster later if a use case emerges.
- **Multi-source ingestion** (HN, PH, G2). Phase 3+.
- **Subreddit list curation** — keep all 210; revisit using B2C-yield-per-sub data after run_id=10 lands.

---

## 8. Open issues / risks to track

1. **Disk capacity** — current DB ~13 GB; +18mo of historical Reddit could add ~10 GB. Monitor `/var/lib/postgresql` during B-prep.2; have a plan to expand the EBS volume if needed.
2. **OpenRouter/MiniMax outage handling** — `quality_viability_llm.py` already retries 3× with backoff. For monthly orchestrator, add a "resume from existing viability_run_id" path (already supported via `--viability-run-id` arg) so a partial run can finish.
3. **Cluster-ID instability across re-clusters** — until lineage tracking exists, dashboards built on cluster_id will lose continuity at month boundary. Mitigation: dashboard shows cluster *label* primarily, ID secondarily; users navigate by topic, not by ID.
4. **Reddit rate limits** — 18-month backfill of 210 subs is non-trivial; arctic-shift API has its own limits. May need to chunk by sub or by month. Existing fetcher has retry logic; verify before B-prep.2 starts.
5. **VACUUM cadence** — after each large UPDATE/INSERT phase, run `VACUUM ANALYZE` on touched tables. Add to B-ops.1 and B-ops.4 as a final step.

---

## Plan deviations (2026-05-07 implementation)

### 1. Centroid-assignment threshold calibration — distribution-based, not manual eyeball
- **Original plan (B-ops.2-cal):** sample 500 pairs across similarity buckets, manually inspect 10 pairs per bucket, pick lowest threshold where ≥80% pairs read as "same cluster."
- **As built:** `--calibrate` mode samples n=5000 TP pairs (assigned-pain → its own centroid) and n=5000 noise pairs (unassigned-pain → nearest centroid), reports p10/p25/p50/p75/p90 of both, suggests a threshold, and warns if distributions overlap.
- **Why changed:** Distribution comparison is reproducible and re-runnable on every monthly orchestrator pass without human inspection. Manual eyeballing remains available if needed but is no longer in the critical path.
- **Resulting threshold:** **0.70** (TP recall ~70%, noise rejection ~70%; smoke test confirmed 28.1% real-world assignment rate).

### 2. Cluster lineage promoted from "Phase 2.x" to right now (B-prep.8)
- **Original plan:** lineage tracking deferred to Phase 2.x.
- **As built:** Implemented now as `migrations/v2_010_cluster_lineage.sql` + `src/clustering/lineage.py`. Jaccard-based pain-membership overlap with thresholds: ≥0.4 = continued, multi-child = split, multi-parent = merged, isolated = born/died.
- **Why changed:** Cheap to build (~$0.37 subagent cost), and the monthly orchestrator was already doing the work to detect cluster changes — natural fit. First lineage run (run_9 → run_10) produced sensible numbers: 390 continued, 2 merged, 0 split, 2203 born, 1150 died.

### 3. Script numbering — `b_ops_3_monthly.sh` instead of `b_ops_4_monthly.sh`
- **Original plan:** B-ops.4 = monthly re-cluster orchestrator.
- **As built:** Daily aggregation (was B-ops.3) is folded into `b_ops_1_daily.sh` as Phase 5, freeing the `b_ops_3_*` slot. Monthly orchestrator named `b_ops_3_monthly.sh`.
- **Why changed:** Aggregation is one SQL upsert call — splitting it into a separate orchestrator was overengineering.

### 4. Cron deployment — done now, despite plan saying "do NOT enable until B-ops.5 runbook exists"
- **Original plan (B-ops.6):** Don't enable cron until runbook (B-ops.5) exists.
- **As built:** Cron installed today (2026-05-07) after smoke test passed. Runbook deferred until after first observed monthly run (June 1) so it can document real failure modes rather than speculative ones.
- **Risk accepted:** First monthly orchestrator runs unattended on June 1. Will set up email alerts on cron failures before then OR observe the first run live.
