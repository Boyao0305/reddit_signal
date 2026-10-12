
> The objective and current scope of openclaw.
> Stable across sessions. Update only when the goal or active phase shifts.

## What openclaw is

A pipeline that surfaces ranked SaaS opportunities by mining real user pain points from Reddit (and eventually other sources), clustering them, and scoring them on multiple independent dimensions for downstream filtering.

## What the final output looks like

A filterable list of market opportunities, each with:
- A cluster of real pain statements with provenance back to source posts.
- An audience tag (B2C / B2B / mixed).
- Independent quality dimensions (cluster cohesion, source diversity, specificity).
- Independent viability dimensions (buildability, ICP clarity, moat potential).
- A temporal signal (4-week velocity, slope, raw weekly counts).
- Eventually: a competition signal (saturation, market structure typology).

The output is a set of filterable columns, not a single composite score. Users sort and filter according to what they care about; the system does not collapse dimensions.

## Audience and use case

The user is a founder doing pre-seed-stage opportunity discovery for B2C SaaS. The pipeline is a hypothesis generator, not a recommendation engine. Final judgment about "should I build this" remains a human decision informed by the columns.

## Current phase

Phase 2-B complete: temporal layer is live, daily ingest and monthly re-cluster are scheduled in cron. run_id=10 is the current baseline (740 viable B2C clusters).

## Scope boundaries (what we are NOT doing yet)

- B2B clusters are classified and stored but not in the active funnel.
- Multi-source ingestion (HN, ProductHunt, Twitter) is deferred to Phase 3.
- Competition density (L5) is deferred until the market-structure typology is designed.
- Dashboard v2 is deferred until competition design is settled.
- Cluster lineage tracking exists for run-to-run continuity but is not yet exposed in any UI.

## What "done" looks like for the current phase

- Daily cron ingests new Reddit data, extracts pain, assigns to existing centroids, upserts the current week's volume.
- Monthly cron re-clusters on rolling 90-day window, runs audience + quality + viability LLM steps, computes lineage to the prior run.
- The first unattended monthly run (target: 1st of next month) completes without intervention.
- An operational runbook documents the real failure modes observed in that first run.

## Budget envelope

LLM costs at steady state are dominated by monthly re-cluster (~$5/month) and daily Gemini Flash Lite extraction (~$0.30/day). All routing through OpenRouter. No per-month hard cap; cost is acceptable as long as it stays under ~$30/month before competition layer adds variable spend.