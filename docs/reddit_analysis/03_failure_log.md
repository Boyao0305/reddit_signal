

> Short cautionary entries about things that broke.
> Append-only. Each entry: date, what happened, what we learned.

---

## 2026-05-02 — Disk estimate off by 10x on 18-month L1 backfill
Plan estimated +10 GB for the 18-month Reddit backfill across 210 subs. Actual was +87 GB (DB went from 14 GB to 101 GB). Disk filled to 100%, Postgres briefly entered crash recovery, the backfill process died.
**Lesson:** Numerical projections in unfamiliar regimes are usually order-of-magnitude wrong. Validate small first (e.g., 5 subs) before launching all 210. Always run `df -h` checks during long fills.

## 2026-05-06 — UMAP OOM at 7.1 GB RSS on 7.6 GB RAM
Re-clustering 426K embeddings hit 7.1 GB peak resident memory on a 7.6 GB box; would have OOM-killed without intervention. Provisioned 4 GB of swap mid-run.
**Lesson:** UMAP+HDBSCAN at 384-d × 100K+ rows is memory-hungry. Always provision swap before re-cluster. For 800K+ rows, plan 16 GB+ swap, persisted in `/etc/fstab`.

## 2026-04-30 — MiniMax M2.7 JSON parse failures with default max_tokens
Initial smoke test had 2/5 LLM calls fail JSON parsing because `max_tokens=900` cut off the reasoning model mid-output. Reasoning models emit thinking tokens before the structured output, so the visible JSON gets truncated.
**Lesson:** Reasoning models (M2.7, o1-style) need `max_tokens=4000+` and a balanced-brace JSON extractor that tolerates pre-output reasoning tokens. Don't trust the default.

## 2026-05-07 — `analysis_runs.status` column doesn't exist
Daily ingest script's Phase 4 query filtered by `WHERE status='complete'`. The column was assumed from the name but never existed in the schema.
**Lesson:** Hermes (and any LLM agent) generates SQL that references plausible-but-nonexistent columns when extending an existing schema. Always run the query against the real DB before committing the script. Use `cluster_count IS NOT NULL` as the "complete" proxy for analysis_runs.

## 2026-05-03 — Stale schema reference broke init_db()
`src/data_ingestion/schema.sql` still had a CREATE INDEX referencing `pain_objects.cluster_id` after that column was dropped in v2_006. `init_db()` failed when re-applied.
**Lesson:** When a column is dropped, grep the entire repo for references — not just the migration files. Schema files duplicated across modules are a recurring trap.

## 2026-05-04 — Foreground tool timeout killed psql mid-transaction
A 60-second foreground timeout killed a long UPDATE backfill mid-transaction. Nothing committed, but autovacuum kicked in and slowed the next run.
**Lesson:** Anything that touches more than ~100K rows runs in the background with `notify_on_complete=true`. Foreground is for queries that finish in seconds.

## 2026-05-04 — `sudo -u postgres psql` creates objects owned by postgres
New tables created via the postgres superuser are owned by `postgres`, not by the `llmanalyse` application role. App connections then fail with permission denied.
**Lesson:** Every migration ends with `ALTER TABLE x OWNER TO llmanalyse;`. The `openclaw-v2-postgres-migration` skill captures this — load it before any DDL.

## 2026-05-05 — Pain extraction throughput drops as backlog accumulates
LLM calls/hour dropped from 94K to 45K over 6 hours during pain extraction. Initial diagnosis suggested rate limiting; actual cause was the `WHERE NOT EXISTS (pain_objects po WHERE po.post_id=p.id)` lookup slowing as the pain_objects table grew. Each batch had to scan more "already-processed" rows to find 200 fresh ones.
**Lesson:** SQL fetch time on `unprocessed` queries grows non-linearly with already-processed volume. For long-running ingestions, monitor SQL time vs LLM time as separate metrics. Add a covering index if the lookup dominates.

## 2026-05-06 — `--limit` flag applied to wrong phase in smoke test
`b_ops_1_daily.sh --limit 100` was assumed to limit Phase 3 (projection), but only Phase 2 (extraction) honored it. Phase 3 still projected the full 254K-row backlog. Smoke test ran for 15 minutes instead of seconds.
**Lesson:** "Limit" flags in multi-phase scripts often only apply to one phase. Document which phase each flag affects in the script's docstring.

## 2026-04-29 — Initial v1 cluster shape was misleading
Run 9 had 578 LOW-quality clusters (38% of 1542). The deterministic gate (member≥30 ∧ coherence≥0.4 ∧ B2C ∧ aud_conf≥0.6) let through echo chambers (one viral thread sampled 50 times) and generic-venting clusters with high cosine coherence but no SaaS signal.
**Lesson:** Coherence + member_count alone is not sufficient quality signal. Need diversity metrics (thread, author, source) and specificity to catch the failure modes that look statistically OK.

## 2026-05-12 — `v_market_opportunities` count was 741, not 740, due to COALESCE on viability flags
First draft of `migrations/v2_013` used `COALESCE(vs.is_real_pain, true) AND COALESCE(vs.is_software_solvable, true)`, which let one B2C candidate (cluster 16780) with no row in `viability_scores` pass the gate vacuously. Spec §8.4 expected 740, got 741. Caught only because the design-session prediction (740) was concrete and the verification query was run before moving on.
**Lesson:** "Hard binary gate" in design language is unambiguous; in SQL, `COALESCE(x, true)` and `x IS TRUE` differ on missing rows. When implementing a gate, prefer `IS TRUE` / `IS FALSE` so a NULL upstream row never silently passes. See Decision #33.

## 2026-05-12 — Sparkline dashed segment invisible when current week is null
Frontend `Sparkline` component used `partialFromIndex=14` (last index of a 15-element array) to switch from solid → dashed polyline. In live data the current week's `pain_count` is null (no `pain_volume_weekly` row exists for the partial week yet), so the dashed polyline collapsed to one point and rendered as nothing. Result: 23 of 24 visible rows had no visual indicator of the partial-week boundary, silently violating Constraint §3.5.
**Lesson:** Visual hard-constraints like "always distinguish the current week" need a fallback path for the null-data case. Fixed by always rendering a faint dashed *vertical boundary marker* between the last completed week and the partial week, regardless of whether the partial week has a value. Caught only by DOM-inspecting the rendered SVG via `browser_console` — visual-only inspection missed it.

## 2026-05-18 — `pain_clusters.member_count` drifted 2.4× from `pain_cluster_members`
Run 10 canonical sum was 270,502 vs actual pcm rows 650,672 (+380,170 delta, 2,474 of 2,594 clusters affected). Cluster 15470 read 754 on the dashboard vs 8,538 actual members; cluster 16877 read 56 vs 8,211. Two writers — `src/pain_extraction/clustering.py` (initial HDBSCAN) and `src/clustering/assign_to_centroid.py` (daily centroid pass) — both write rows to `pain_cluster_members` but only the first updates `pain_clusters.member_count`. A larger backfill in week of 2026-05-04 added ~8K rows to top clusters via the assigner path without updating the canonical column. `cluster_quality_metrics` divides by member_count to compute `source_diversity` / `thread_diversity` / `author_diversity` / `workaround_ratio`, so those metrics are pinned to the clustering-time member_count snapshot — internally consistent but stale. Decision #34 makes member_count a synced-from-pcm derived column refreshed nightly as Phase 4.5 of `b_ops_1_daily.sh`. The frozen quality metrics get refreshed at the next monthly re-cluster (2026-06-01).
**Lesson:** When a denormalized count column has multiple writers, pick one of: (a) every writer updates it, or (b) treat it as a derived column synced on a known cadence. Splitting the writers without a sync path produces silent drift that only surfaces when someone asks "is this number current?" Caught by querying `count(*)` from the join table in this session — not by any test or invariant. Add a daily reconciliation invariant for any denormalized count.

## 2026-06-08 — Misdiagnosed `velocity_4w_ratio_normalized` as a permanent ETL break

**What happened.** The `from-colourpinke` branch annotated `velocity_4w_ratio_normalized` as `status='deprecated'` with `deprecation_reason: "pain_volume_weekly ETL 断层（2026-05-11 起），run_id=10 数据集覆盖率仅 3.6%"` ("ETL break starting 2026-05-11; coverage only 3.6%"). At face value it looked like a recurring data-quality issue worth a long-lived deprecation flag — and indeed the live `pain_volume_weekly` row counts cliff at 2026-05-04 → 2026-05-11 (1,256 → 415 cluster-week rows for run 10), and the live coverage of `velocity_4w_ratio_normalized` on viable B2C is exactly 27/740 = 3.6 %.

**The actual cause.** Run 10 was clustered with `window_end = 2026-05-06`. The daily centroid-assignment cron only assigns ~28% of new pains to existing centroids (LD-#23) — the other ~72% wait for the next monthly recluster to surface as new shapes. So *of course* the per-cluster weekly aggregates after 2026-05-06 are sparse: only the small fraction that does assign produces rows. The cliff at 2026-05-04 → 2026-05-11 isn't an ETL break — it's exactly the run-10 boundary, with the natural daily-assignment decay on the windward side. The 2026-06-01 monthly recluster (the first untested fire of `b_ops_3_monthly.sh`) will rebuild over a fresh 90-day window and the velocity columns will repopulate automatically.

**Lesson.** When a metric goes sparse mid-run window, the first hypothesis is "is this the run window aging out?" — not "is the ETL broken?" The `analysis_runs.window_end` of the live run, the daily-assignment rate (LD-#23, 28%), and the cliff date are the three signals to check before concluding "ETL break." The misdiagnosis was understandable from outside the project's locked-decision context — the architecture documents are where this gets caught. Recorded by AEO (default-Hermes orchestrator session, 2026-06-08) while reviewing `from-colourpinke` for merge.

**Action taken.** In `02_components/phase3b_path_c_ranking.md`, the deprecation_reason in `scoring.py:DIMENSION_META` is rewritten to *"run-10 90-day window ends 2026-05-06; coverage on the 4w-ratio columns is naturally 3.6% and 14%. The next monthly recluster will move the window forward and coverage recovers automatically. Treat this as a per-run calibration window, not a permanent deprecation."* The weight stays pinned to 0.0 for run 10 (correct — the column shouldn't drive a score with that little data), but the framing is window-specific, not architectural.
