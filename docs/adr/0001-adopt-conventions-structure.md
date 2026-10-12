# 0001 — Restructure to CONVENTIONS.md, keep the production machinery

Date: 2026-10-10 · Status: accepted

## Context

The repo grew stage by stage (`src/sources`, `src/pain_extraction`, `src/clustering`, …). Each stage owns
its own `db.py`, CLI and environment reads (15 files read env vars, 6 hard-coded fallback DSNs, 10 places
open connections). `src/` itself is the package (`from src.x`), so nothing runs without `PYTHONPATH=.`.
Orchestration lives in Bash (`b_ops_1_daily.sh`, `b_ops_3_monthly.sh`) launched by a worker via
`subprocess`, so wrong-but-successful results pass silently: on 2026-10-01 a monthly run built from 4,120
pains (vs ~165K the month before) was promoted live. The project is about to become open source.

The operational machinery, by contrast, works: a PostgreSQL job queue with lanes, heartbeats and cancel;
runtime settings in `system_settings`; a migration ledger; precomputed dashboard snapshots; chunked
backfills with retry.

## Decision

Adopt `docs/CONVENTIONS.md` in full (several containers):

- One installable package `src/reddit_signal/`, `pyproject.toml` + lock file, Python 3.11.
- Three containers as subpackages: `api` (public read-only), `worker` (queue consumer + scheduler),
  `cli` (operator commands). Shared below them: `schemas.py`, `config.py`, `db.py`, `core/` (pure),
  adapters, `repositories/`.
- One validated `Config` built at each entry point; no env reads elsewhere; no fallback DSNs.
- Daily/monthly Bash phases become typed functions in `worker/pipelines.py`. Decisions such as "promote
  this run?" and "which posts to extract next?" become pure functions in `core/` with case-table tests.
- Keep, inside the new structure: the job queue and lanes, `system_settings`, the migration ledger and the
  existing migration files (append-only, numbering continues at `v2_047`), the Path C SQL views and
  snapshot tables, backfill chunking, the source adapter registry.
- Import contracts enforced with import-linter.

## Consequences

- Old entry points (`python -m src.sources`, `scripts/b_ops_*.sh`, `src.dashboard.api:app`) are retired at
  the Contabo cutover; there are no other consumers.
- Most tests no longer need a database.
- Work is phased (see `docs/agent/plan-2026-10-restructure.md`); behaviour-preserving moves come before
  behaviour changes so regressions can be attributed.

## Alternatives rejected

- **Keep the current layout, patch the bugs.** Fixes the symptoms of October but leaves config scattered,
  Bash orchestration untestable and the repo unrunnable outside one server.
- **Rewrite from scratch.** Throws away proven machinery (queue, migrations, ranking SQL) and years of
  calibration encoded in the views.
