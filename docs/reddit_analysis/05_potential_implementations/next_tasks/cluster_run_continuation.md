# Cluster run continuation — problem definition

> **Status:** Problem definition only. No structural solution proposed yet.
> **Created:** 2026-05-12.
> **Trigger to design:** before the first monthly orchestrator run (currently scheduled 2026-06-01), or earlier if any of the symptoms below become visible during Phase 2C operation.
> **Related:** `02_components/phase2c_dashboard_v2.md` §3.9 / §4.5 (the minimum mitigation: snapshot banner + `analysis_runs.status`); `01_locked_decisions.md` #17, #18, #22.

## What this is about

The system re-clusters monthly. Decision #17 — "Re-cluster monthly, aggregate weekly, assign daily" — means each monthly orchestrator pass produces a new `analysis_runs.id`, runs HDBSCAN over the prior 90 days, and emits a fresh set of clusters. Daily centroid assignment then switches to feeding new pains into the new run's clusters.

This works fine for the single-run case (which is what the project has been so far — only run 10 is active and is the dashboard's exclusive subject). It breaks down in several specific ways as soon as a second non-legacy run exists. The first opportunity for this to happen is 2026-06-01.

Phase 2C's design includes one minimum mitigation: an `analysis_runs.status` column (`live` / `historical` / `legacy`) plus a UI banner that warns the user when they're viewing a non-live run. That keeps the dashboard honest about staleness but does not solve the underlying problems below. This file is the place to design the proper solution when there is appetite for it.

## Symptoms

### Symptom 1: velocity numbers on historical runs go stale silently

`v_cluster_velocity` is anchored to `CURRENT_DATE`, not to each run's `window_end`. After 2026-06-01 a user inspecting run 10 would see `last_4w` and `prior_4w` numbers that no longer reflect actual recent activity — because daily centroid assignment is now feeding run 11, not run 10. The values aren't wrong; they're just measuring something that stopped being meaningful.

Phase 2C's snapshot banner *tells the user* this is happening but doesn't fix the math. A proper fix would either parameterise the velocity anchor per run (`LEAST(CURRENT_DATE, window_end + N days)`) or freeze velocity numbers at the moment a run transitions `live → historical`.

### Symptom 2: cross-run cluster identity is opaque

A cluster in run 10 ("Parenting: social-emotional needs") may correspond to a cluster in run 11 with a different id, similar but not identical members, and a slightly different label. Decision #22 says lineage is computed via Jaccard member overlap on `pain_cluster_members`. But lineage is not surfaced anywhere in the UI. A user who shortlisted cluster 16800 in May has no way in June to ask "where did 16800 go in the new run, and what does it look like now?"

The design pass needs to decide: is lineage a column on `v_market_opportunities` (e.g., `predecessor_cluster_id`, `lineage_jaccard`), a separate join surface, or a UI affordance ("view this cluster's run-10 ancestor")?

### Symptom 3: the exploratory composite breaks across runs

The composite score is computed from within-run percentile ranks. Cluster X's score in run 10 is not comparable to its lineage-descendant's score in run 11 — different denominators, different distributions. Slider settings tuned to "find good clusters in run 10" don't transfer cleanly to run 11; the same numeric weights may produce a quite different ranking shape because the dimensions' distributions have shifted.

Open question: should the dashboard recompute percentiles across both runs when comparing? Show "your shortlist's run-11 equivalents" as a special view? Or accept that scores are run-local and surface that fact in the UI (e.g., "score recomputed in this run's context")?

### Symptom 4: viability scoring may be redoing work

Each run gets its own viability scoring pass (a new `viability_run_id`). If cluster X in run 11 is the obvious descendant of cluster X' in run 10 with `viability_run_id=4`, the LLM call for run 11 is re-doing essentially the same judgment. With ~740 viable B2C clusters per run at ~$2 per pass that's not catastrophic, but compounding monthly it adds up, and the larger cost is conceptual: a cluster's `is_real_pain` verdict shouldn't randomly flip between runs just because the LLM was re-prompted with slightly different sample pains.

Carrying viability forward across runs (when lineage is strong enough) is a cost-plus-stability optimization that hasn't been designed. Open question: at what Jaccard threshold does inheritance kick in, and what refresh policy avoids stale verdicts in pain-spaces that genuinely shifted?

### Symptom 5: orchestrator must atomically transition `status`

Phase 2C's migration v2_014 adds the `status` column and backfills it deterministically. But going forward, the monthly orchestrator becomes responsible for: (a) marking the new run `'live'` in the same transaction that finalises clustering, (b) demoting the prior `'live'` run to `'historical'`, (c) switching the daily centroid assigner over to the new run's centroids. This logic does not exist yet. Currently this works because there is only one non-legacy run; the first monthly orchestrator run on 2026-06-01 will hit this gap.

The atomicity matters: if (a) and (b) happen at different times, the dashboard briefly shows zero `'live'` runs (banner says "no live run" for every selection) or two `'live'` runs (defensive backfill demotes one but which?). A transaction-bounded swap is the safe pattern; concrete implementation goes in `src/clustering/` orchestration.

### Symptom 6: daily centroid assignment scope

When run 11 is created, the daily cron's centroid assigner needs to start using run-11 centroids, not run-10. The transition needs to be: well-defined (no ambiguity about which run is "current" mid-cron), idempotent (a half-completed transition can be safely retried), and observable (logs should make the active centroid set obvious). None of this is currently designed.

A related secondary question: should daily centroid assignment continue to update run-10's `pain_volume_weekly` for some grace period, so its sparklines remain useful while users wind down their attention to it? Or hard cutover at the transaction boundary?

## Why this is being deferred

None of the six symptoms above are blockers for Phase 2C, which ships before 2026-06-01 and includes the snapshot banner that covers the most user-visible failure mode (silent velocity staleness). But all six become real problems in the run-11 era — and at least Symptom 5 is a hard blocker for the orchestrator to function correctly past June 1.

The honest scope is: a design pass before June 1 that addresses Symptoms 5 and 6 at minimum (so the orchestrator works), plus a UX call on Symptoms 1–4 (which are about the dashboard's honesty across runs, not about the pipeline functioning at all).

## Open design questions

These are the questions a design pass would need to answer. They are not answered here — that is the next session's job.

- Should `v_cluster_velocity` parameterise its anchor date per run, or should historical runs serve frozen velocity numbers captured at `live → historical` transition?
- Should cluster lineage become a column on `v_market_opportunities`, a separate VIEW, or a dedicated lineage API endpoint?
- How does the composite score handle cross-run comparison — recompute percentiles per request across multiple runs, or accept run-local scoring and surface that fact in UI?
- Should viability scoring inherit from a strong-Jaccard predecessor? At what threshold? With what refresh policy?
- What's the orchestrator's exact transaction shape for the `live → historical` swap, and how is it tested before the first June 1 fire?
- Daily centroid assignment: hard cutover or grace period?
- Does the dashboard's run selector need a "compare two runs" mode, or is single-run + lineage links sufficient?

## What this file is NOT

This is a problem definition, not a design. The next session that picks this up should:
1. Read this file.
2. Read `02_components/phase2c_dashboard_v2.md` §3.9 and §4.5 for what's already in place.
3. Read `01_locked_decisions.md` #17, #18, #22 for the existing rules about re-clustering cadence, window size, and lineage method.
4. Produce a proper component plan at `02_components/phase2d_cluster_run_continuation.md` (or similar) covering: orchestrator transition, velocity anchoring, lineage display, viability inheritance policy. With acceptance criteria and a migration list.

When that component plan exists, this problem-definition file gets a forward-pointer at the top and stays as the original problem statement.
