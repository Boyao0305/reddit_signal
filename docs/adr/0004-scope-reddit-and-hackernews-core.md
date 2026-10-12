# 0004 — Scope: Reddit + Hacker News core pipeline

Date: 2026-10-10 · Status: accepted

## Context

The repo carries modules beyond the core ingest → extract → cluster → judge → rank pipeline. Several
depend on paid or dead services, or are superseded.

## Decision

Keep: Reddit (Arctic Shift) and Hacker News sources, pain extraction, clustering, temporal aggregates,
audience classification, quality metrics, viability scoring, emerging opportunities, ranking views.

Drop from the codebase (history remains in the private archive repo):

| Module | Reason |
|---|---|
| `report_pipeline` | depends on an Anthropic relay on Zeabur that no longer exists |
| `mcp_config_ui` | unrelated utility |
| `competition_density` | v1, superseded by `competition_layer` |
| `competition_layer` | paid Perplexity Sonar + SocialCrawl; results not shown on Seedcast |
| SocialCrawl adapters (Facebook, YouTube, Twitter, TikTok, Instagram) | paid; no data since July 2026 |

Viability scoring stays: its `is_real_pain ∧ is_software_solvable` flags are the hard filter of the
ranked surface.

## Consequences

- Tables written by dropped modules stay in the database untouched (migrations are append-only); a later
  migration may drop them once confirmed unused.
- The Path C views keep working: the competition-adjusted view falls back to the base ranked view when no
  competition data exists.

## Alternatives rejected

- **Keep everything behind feature flags.** Ships code paths nobody can run without paid keys.
