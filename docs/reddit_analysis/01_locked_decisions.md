

> Append-only ledger. Every entry is a design choice approved by the user.
> Never edit past entries. To reverse a decision, add a new entry that supersedes the old one.

Format: `## NN. <decision> (YYYY-MM-DD)` followed by a one-line rationale.

---

## 1. Three-layer architecture: raw → projected → derived (2026-04-29)
Sources mirrored as L1 raw tables (sacred, never deleted). L2 `signal_events` is a normalized projection. L3+ are derived and rebuildable. Regen cost determines preservation policy per layer.

## 2. signal_events does not store raw payload (2026-04-29)
Links to L1 via `(source_table, source_row_id)`. ~70% disk savings vs duplicating raw JSON.

## 3. Adapter pattern via folder, not flag (2026-04-29)
New source = one new file in `src/sources/`. No core changes, no feature flags.

## 6. L3/L4 split: extraction separate from clustering (2026-04-30)
Pain is extracted once per `(signal_event, prompt_version)`. Clustering is rerun on whatever subset/window we choose. `pain_objects.cluster_id` was dropped; `pain_cluster_members` is the only join path.

## 7. No L6 viability layer; quality+viability fused into L4 (2026-04-30)
Single LLM call per candidate cluster emits both halves of the judgment. Saves cost; keeps related signals co-located.

## 8. Hard binary gates only: `is_real_pain ∧ is_software_solvable` (2026-04-30)
The 1-5 dimensions (buildability, ICP, moat, specificity) are stored but never collapsed into a gate. They are sort/filter inputs, not approval criteria.

## 9. Per-cluster B2B/B2C audience classification, not per-subreddit (2026-04-30)
Subreddits like r/Entrepreneur carry both audiences. The subreddit's `audience_prior` is a weak input to the per-cluster LLM verdict, not the verdict itself.

## 10. Audience classifier is a separate LLM step, not fused with viability (2026-05-04)
Cheap Gemini Flash Lite filter on all clusters; expensive MiniMax M2.7 viability only on the B2C survivors.

## 11. Storage: cluster_quality_metrics is a separate table (2026-04-30)
Tier-1 deterministic metrics live in their own table, recomputable from raw data. `pain_clusters` stays canonical and small.

## 12. Storage: viability_scores stores both quality and viability halves (2026-04-30)
One row per `(viability_run_id, cluster_id)`. Reflects the fused LLM call.

## 13. Specificity heuristic: keep both spaCy and LLM (2026-04-30)
spaCy `pain_objects.specificity` for the cheap deterministic gate; LLM `q_specificity` for survivors. Cross-check correlation later.

## 14. Final ranking is a SQL VIEW with session GUCs (2026-04-30)
Tunable weights without recomputation. Not a 7th materialized layer.

## 15. clustering_runs is a writable VIEW over analysis_runs (2026-05-03)
Existing FKs preserved. v1 runs (1-7) tagged `is_legacy=TRUE`.

## 16. Per-run threshold calibration via p10 of distributions (2026-05-06)
Each monthly re-cluster recalibrates tier-1 gates from observed distributions. No reused thresholds across runs without checking drift. No manual eyeballing.

## 17. Re-cluster monthly, aggregate weekly, assign daily (2026-05-04)
HDBSCAN needs ≥3-4 weeks of fresh data to surface new clusters. Daily re-cluster would just churn IDs.

## 18. 90-day rolling window for re-cluster (2026-05-04)
Balances recency against statistical significance.

## 19. 18-month L1 lookback (2026-05-04)
Lets us validate cluster lineage across 3+ monthly runs. L1 storage is cheap.

## 20. Pain extraction limited to last 90 days initially (2026-05-05)
Matches the re-cluster window. Older months can be backfilled later if signal demands it.

## 21. Keep all 210 subreddits initially (2026-05-04)
Prune later based on empirical B2C-yield-per-subreddit, not prior assumptions.

## 22. Cluster lineage via Jaccard member overlap, not centroid similarity (2026-05-07)
Member overlap is exact ground truth and naturally handles splits/merges. Centroid cosine would need threshold tuning and miss multi-parent cases.

## 23. Centroid-assignment threshold for daily new pains: 0.70 (2026-05-07)
Calibrated from TP-vs-noise distributions. Smoke-tested at 28% real-world assignment rate. Recalibrated each monthly orchestrator pass.

## 24. Failed-extraction retry: pain_extraction_attempts side table (2026-05-04)
DLQ pattern with `attempts`, `last_error`, `failed` columns.

## 25. System cron, not Hermes cronjob (2026-05-04)
Pipeline is data ops; Hermes cronjob reserved for future LLM-agent alerting.

## 26. Component D (competition) deferred until typology design done (2026-04-29)
Market structure is not unidimensional. Vendor decision (G2/Apify/Bright Data/hybrid) is premature without it.


## 28. Cron deployed before operational runbook written (2026-05-07)
Risk accepted: the runbook will document real failure modes from the first observed monthly run rather than speculative ones.

## 29. Exploratory composite is allowed; fixed gating composite is not (2026-05-12)
The dashboard exposes user-tunable slider weights producing a session-local composite score over within-run percentile-transformed dimensions. Three guardrails: the score never enters `is_candidate` or any gate; the weights are visible at all times; the score is session-local, not persisted. This softens #14 ("no canonical composite") without lifting the prohibition on hidden composite scoring or gating. The dashboard itself is the experiment that informs whether a stable composite is even possible. See `02_components/phase2c_dashboard_v2.md` for the implementation and `05_potential_implementations/signal_generation/nonlinear_composite.md` for the deferred follow-up.

## 30. Velocity is surfaced as three signals, not one ratio (2026-05-12)
The dashboard renders `pain_count_last_4w` (current intensity, always computable), `velocity_4w_ratio_normalized` (recent trend vs. corpus, NULL when sparse), and `slope_per_week` (longer-term shape). The three are not redundant: disagreement between them — e.g. high `last_4w` with negative slope and `norm_ratio < 1` — is itself informative ("still busy but the momentum is gone"). Collapsing to a single number erases this. Velocity remains outside `is_candidate`.

## 31. Dashboard v2 reads exclusively from `v_market_opportunities` (2026-05-12)
The dashboard's primary endpoint queries the view, not the underlying `pain_clusters` / `cluster_quality_metrics` / `viability_scores` / `cluster_audience` tables. Two narrow exceptions are explicitly documented in the spec: `pain_volume_weekly` for the per-cluster time-series chart, and `pain_objects` (via `pain_cluster_members`) for sample-quote drill-in. This keeps the API surface narrow and makes future schema refactors safer.

## 32. Velocity-ratio normalization formula: cluster_4w_ratio / corpus_4w_ratio (2026-05-12)
`velocity_4w_ratio_normalized = (cluster_last_4w / cluster_prior_4w) / (corpus_last_4w / corpus_prior_4w)`, computed per cluster_run_id. Reads 1.0 = corpus pace, > 1.0 = growing faster than corpus, < 1.0 = declining faster. Removes the systematic downward bias the raw ratio inherits when the corpus has a volume swing (e.g., March 2026 surge currently pulls every cluster's raw ratio toward the corpus-wide 0.35). Implemented as a column on `v_cluster_velocity`.

## 33. Viability gate in `v_market_opportunities` uses `IS TRUE`, not `COALESCE`-to-true (2026-05-12)
First draft of `migrations/v2_013` used `COALESCE(vs.is_real_pain, true) AND COALESCE(vs.is_software_solvable, true)`, letting B2C candidates with no row in `viability_scores` pass the gate (vacuously). This produced 741 viable B2C against the design-session count of 740. Tightened to `vs.is_real_pain IS TRUE AND vs.is_software_solvable IS TRUE`. Net semantics: "shown in dashboard" ⇔ "evaluated AND passed both binary gates." Once a viability run scores a previously-missing cluster, it appears automatically on the next request if both flags are true. Aligns with the spirit of Decision #8 ("hard binary gates only"): missing-row is not implicitly true.

## 34. `pain_clusters.member_count` is a synced-from-pcm derived column, refreshed nightly (2026-05-18)
Two writers (initial HDBSCAN in `clustering.py`, daily centroid assignments in `assign_to_centroid.py`) insert rows into `pain_cluster_members`, but only the first updates `pain_clusters.member_count`. By 2026-05-18 the canonical column had drifted 2.4× from the join-table truth (270,502 vs 650,672 on run 10). Two valid resolutions: (A) keep the column and reconcile nightly, (B) drop the column and live-compute. Chose (A) for minimal blast radius and consistency with how `cluster_quality_metrics` are treated (derived, recomputable, refreshed on a known cadence per Decision #11). Implementation: Phase 4.5 of `b_ops_1_daily.sh` runs an idempotent `UPDATE pain_clusters SET member_count = (SELECT COUNT(*) FROM pain_cluster_members WHERE cluster_id = pc.id)` after the centroid-assignment phase, before the weekly aggregation. `IS DISTINCT FROM` guard skips no-op writes; cost is one GROUP BY against pcm with covering index `ix_pcm_cluster`. The `cluster_quality_metrics` per-row divisions (source/thread/author diversity, workaround_ratio) intentionally remain pinned to the clustering-time member_count — they get refreshed at each monthly re-cluster, not nightly. See `03_failure_log.md` 2026-05-18 entry for the discovery context.

## 35. Hermes is exposed to external coding agents via the `hermes-bridge` MCP server (2026-05-23)
The openclaw-mcp gateway publishes a fourth named server, `hermes`, on top of the existing `pg`/`fs`/`guide` triad. It exposes one tool, `ask_hermes(prompt, reset)`, letting Claude Desktop (system design) and Claude Code (implementation) hold a multi-turn conversation with the Hermes agent already wired into the user's Telegram session. Three design choices that go with this: (a) **long-poll** with bounded 5s waits returning either the final answer or `[HERMES_WORKING] <activity>`, because Claude Desktop's hardcoded ~60s tool-call timeout is not configurable and Anthropic clients explicitly ignore MCP progress notifications for timeout purposes; (b) **native-session mode** — each `ask_hermes` call attaches to the user's actual Telegram session id, so Hermes answers Claude with the full chat context and every Claude↔Hermes turn persists into that transcript, instead of spawning isolated stranger-Hermes processes; (c) **Telegram mirror** of both prompts (`🤖 [CLAUDE]:`) and replies (`🦊 [HERMES → CLAUDE]:`), with an `[I am Claude]` identity-marker check so impersonation/curl-debug calls surface as `⚠️ [CLAUDE? — MARKER MISSING]`. Trade-off accepted: two AIAgent instances may briefly run against the same session if Claude calls while the user texts — SQLite serializes writes, but message ordering can interleave. Acceptable for the solo-pilot phase; revisit if it gets messy. Server code at `~/openclaw-mcp/hermes-bridge/server.py`, full doc at `08_mcp_tools/hermes_bridge.md`.
## 36. Path C ranking — server-computed Pareto layer + balance score allowed for display, NOT for gating (2026-06-08)
Softens but does not lift Decisions #14 and #29. The dashboard's Path C ranking surface (`v_market_opportunities_ranked`) computes per-row `pareto_layer ∈ {1..5, NULL}` and `balance_score ∈ [0,1] ∪ NULL` server-side and returns them in the API payload. Allowed because: (a) the inputs (Q/M/S axes) are also returned alongside, and the row tooltip surfaces the harmonic-mean formula and the per-axis values — no opaque "the system thinks this is 0.83" without the user being able to see inside; (b) the user retains a session-local Explore score driven by Q/M/S sliders for live re-ranking; (c) `is_candidate` and the hard gates (LD-#8) are untouched — `pareto_layer` and `balance_score` are display-and-default-sort signals only, never consulted by gates. The default sort `pareto_layer ASC NULLS LAST, balance_score DESC NULLS LAST` shows the Pareto frontier first, then well-balanced runners-up; the user can switch to Score, Members, Pain 4w, etc. at any time. See `02_components/phase3b_path_c_ranking.md` for the implementation and `_mcp_tools/claude_code_delegation.md` for the build's delegation discipline. Supersedes nothing; refines #29.

## 37. `is_eligible` is a default display filter, NOT a hard gate (2026-06-08)
Path C computes `is_eligible = (q_axis >= p25(q_within_audience)) AND (s_axis >= p25(s_within_audience))`. This filter hides bottom-Q1-on-quality-OR-supply clusters from the default cluster list. **It is explicitly not a gate**: (a) `is_candidate` is unaffected; (b) ineligible rows are returned by `v_market_opportunities_ranked` with `pareto_layer = NULL`; (c) the dashboard exposes a "Show ineligible" toggle that surfaces the full set, sorted to the bottom by `NULLS LAST`. Decision #8's hard-binary-gates rule is preserved: only `is_real_pain` and `is_software_solvable` are gates; `is_eligible` is UX. Within-audience percentile is intentional — B2B vs B2C distributions differ (LD-#9). Supersedes nothing.

## 38. Path C harmonic-mean balance score uses ε=0.05 zero-floor (2026-06-08)
The harmonic mean `3 / (1/Q + 1/M + 1/S)` explodes to a misleadingly-high number when one axis approaches zero (e.g., Q=0.001, M=0.5, S=0.5 → balance ≈ 0.003 — fine — but a few floating-point artifacts upstream can produce arbitrary-looking outputs in the [0, 0.05] tail). More importantly, a cluster that's effectively zero on any one axis is structurally not Pareto-optimal — declaring its balance "high" because the other two compensate would mislead the user. Rule: if any of `q_axis`, `m_axis`, `s_axis` is below ε=0.05, set `balance_score = NULL` AND `pareto_layer = NULL`; the row is excluded from layer 1, shown only when "Show ineligible" is toggled on. ε=0.05 chosen as 5% of the [0,1] axis range — small enough to admit clusters that are merely weak-but-real on an axis, large enough to exclude floating-point noise. Implemented in the `fn_compute_pareto_layers` function and the view's CTE. Supersedes nothing.

## 39. Competition research is batched by domain group, not per-cluster (2026-06-26)
Top-N Explore clusters are grouped into ~12 semantic domains by pgvector centroid cosine-similarity clustering before calling external research. One research call per domain replaces N per-cluster calls — a ~76% call-volume reduction at baseline limit=50. The clustering is pure-algorithm (embedding similarity), runs zero external LLM calls, and is persisted to `cluster_domain_groups` / `cluster_domain_members` for auditability. Each cluster within a domain shares the domain's competitor pool but adjusts its supply-gap score independently via its own `mentioned_tools` overlap. Domain research failures auto-fallback to per-cluster. The old per-cluster path remains callable and tested; domain batching is the default from v2_040 forward. See `02_components/phase4a_competition_domain_cost.md`.

## 40. SocialCrawl Prism is the authoritative source for competitor satisfaction gap (2026-06-26)
The competition layer now cross-validates domain-level competitor pools via SocialCrawl's Prism endpoints — `reputation` (cross-source weighted score: Trustpilot + App Store + Google Play + Google Business + web, 30 credits/call) and `demand-signals` (fused demand index from app review velocity, web mention slope, Reddit velocity, and commerce review levels, 30 credits/call). Verified `satisfaction_gap` and `confidence = verified` override the research-layer LLM estimates before scoring. Verification is per-competitor, shared across all clusters in the domain (deduped by name). Budget cap: top-30 competitors by strength_score, ~1,800 credits/run, Growth tier (20,000 credits) covers ~11 monthly runs. Controllable via `COMPETITION_SOCIALCRAWL_ENABLED` runtime setting and `--no-socialcrawl` CLI flag. SocialCrawl is used for reputation/demand **verification only**; competitor **discovery** (who exists) remains the domain-research layer's responsibility. Sonar-pro's role is thus scoped down to discovery; SocialCrawl provides the quality evidence. See `02_components/phase4a_competition_domain_cost.md`. Supersedes nothing.
