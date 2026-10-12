

> Things noticed but deferred. Items move out of this file when they are answered
> (into 01_locked_decisions.md or 02_principles.md) or when they become a component plan.

---

## L5 competition: market structure typology
HHI and CR4 collapse "monopolistic mature", "fragmented mature", and "emerging greenfield" into one number. The typology design needs its own pass before vendor decision (G2 Content Subscription / Apify / Bright Data / hybrid stack of Crunchbase + GitHub + AlternativeTo + Capterra). Blocks the entire L5 layer.

## Cluster lineage at scale
Current Jaccard threshold (0.4 for any relation, 0.6 for "continued") was set by intuition. With ≥6 monthly runs, edge cases appear: clusters that drift gradually (no single 0.4 overlap but topic preserved), clusters that bifurcate evenly (50/50 splits), clusters that absorb noise. Revisit after run_id=15 or so. Possible addition: LLM arbitration for ambiguous overlaps (40-60% Jaccard).

## Pain extraction backfill for older 18 months
L1 has 18 months of Reddit data; pain extraction only ran on the last 90 days. Older months would unlock 6-12 months of velocity per cluster (vs current ~13 weeks). Cost estimate: $30-80 on Gemini Flash Lite. Defer until run_id=12 or so when there's clear demand from the velocity view.

## Subreddit list curation
Currently keeping all 210 subs, leaning B2B-heavy. After 2-3 monthly runs we'll have empirical B2C-yield-per-subreddit data. Question: prune low-yield subs, expand into adjacent ones, or both?

## Daily-assignment threshold drift
Current threshold is 0.70, calibrated once on run_id=10 distributions. Drift expected over time as cluster shapes change. Decide: monthly recalibration in the orchestrator, or keep fixed and revisit quarterly?

## Composite ranking — empirical reconsideration
The hard "no" on composite scores stands. But once the dashboard exists with N filterable columns, observe whether users (= the founder) actually filter or just sort by member_count. If filtering is rare, a composite may earn its place. Revisit only after 2-3 months of dashboard usage.

## Multi-source ingestion priority
Phase 3+ list: HN, ProductHunt, Twitter complaint mining, App Store reviews, GitHub Issues. Each adapter is ~3-5 days of work. Question: do all of them serve the B2C funnel, or do some belong to the B2B fork? Sequence depends on which audience we're scaling first.

## Email alerting on cron failures
First unattended monthly orchestrator runs the 1st of next month. Currently no alerting. Decide: simple `mailx` on non-zero exit, or a Hermes cronjob that polls and DMs on Telegram?

## Operational runbook
B-ops.5 is deferred until after the first observed monthly run, so the runbook documents real failure modes. Triggers: write the runbook within 48 hours of the first monthly orchestrator completion (or first failure).

## Fate of legacy `cluster_competition` table
Holds Play Store v1 results, ignored by Phase 2+ code. Drop after L5 redesign lands, or keep read-only for historical reference?

## v1 deprecated columns on `pain_clusters`
14 v1 columns (`opportunity_score`, `intensity`, `prevalence`, `velocity`, `competition_density`, `market_gap`, `hhi`, `cr4`, `weighted_rating`, `complaint_ratio`, `active_ratio`, `total_installs`, `golden_quadrant`, `practicality_score`, `practicality_reasoning`, `practicality_tags`). Marked deprecated; should be dropped once `v_market_opportunities` is verified in production.

## Disk monitoring as a first-class signal
The +87 GB miss caught us off guard. Should there be a daily check that alerts when DB size grows >X% week-over-week, or when free disk falls below 30%?

## Run-10 sparsity on 4w-ratio columns is window-aging, not ETL break (2026-06-08)
`pain_volume_weekly` row counts cliff at 2026-05-04 → 2026-05-11 for run 10, and `velocity_4w_ratio_normalized` covers only 27/740 viable B2C clusters (3.6 %). Initially misread on the `from-colourpinke` branch as a permanent ETL failure starting 2026-05-11. **Actual cause:** run 10's `window_end` is 2026-05-06; the daily centroid-assignment cron only assigns ~28 % of new pains (LD-#23), so post-window coverage is naturally sparse. The 2026-06-01 monthly recluster (`b_ops_3_monthly.sh`'s first untested fire — see Decision #28) should rebuild over a fresh 90-day window and these columns repopulate. **Trigger to close this OQ:** after the first monthly recluster lands, re-check coverage on the new run; if it's back to the 80 %+ range that run 10 had at design-time, close. If it's still <50 %, escalate as a real ETL issue and trace the join in `aggregate_weekly.py`. Failure-log entry recorded 2026-06-08.

## Multi-audience scaling of `fn_compute_pareto_layers` (2026-06-08)
`v_market_opportunities_ranked`'s perf fix (Phase 3B v1.0) materializes the expensive `pregate` CTE once, but `fn_compute_pareto_layers` is invoked per audience via `CROSS JOIN LATERAL` and each invocation re-scans `v_market_opportunities_pregate` internally (it cannot read the calling query's materialized CTE). Run 10's gated opportunity set is 100 % B2C (1 audience) → 1 function call → 1 internal pregate scan, totalling ~12.6s per query (acceptable). **Future-run risk:** a run with N audiences in the gated set produces (1 + N) pregate evaluations; if a multi-audience run lands and dashboard load regresses past ~20s, refactor `fn_compute_pareto_layers` to accept the cluster axes as a passed-in array argument (so it reads from the caller's already-materialized data) instead of re-querying the pregate view. **Trigger to act:** dashboard-load timings >20s on the next monthly recluster (or any run where `SELECT DISTINCT audience_type FROM v_market_opportunities_pregate` returns >1 row). Spec §3.5 explicitly defers this until it shows up empirically.

## fs-MCP read-only mode (deferred from phase3a v1.0)
The MCP Config UI spec (`02_components/phase3a_mcp_config_ui.md` §3.9) originally mandated a per-profile read-only toggle for the filesystem MCP. During implementation we found `@modelcontextprotocol/server-filesystem` has no `--readonly` flag — it always exposes `write_file`, `edit_file`, `create_directory`, `move_file`. The `readOnlyHint` annotations in its tool list are client-display hints, not server enforcement. Two paths to revisit when the toggle becomes load-bearing: (a) wrap the upstream binary with a stdio-level filter that blocks write tools by name; (b) PR the upstream package to accept a `--readonly` flag. Trigger to fire: when a new profile points fs at a directory we don't want any agent writing to (e.g. an external customer's repo).
