# Plan — restructure, repair, deploy Seedcast (2026-10)

Status: **accepted 2026-10-10** — decisions recorded in `docs/adr/0001`–`0006`. Executed phase by phase.

## Goal

Turn the repo into `reddit_signal`, structured per `CONVENTIONS.md` (from `convention.txt`), fix the
pipeline so it produces real data again, and serve the public Seedcast site on `seedcast.co` from the
Contabo server. Keep the production machinery that already works.

## Already done (2026-10-10)

- Production DB copied out of Zeabur's k3s storage to `/srv/reddit_signal/pgdata`, running as Docker
  container `reddit-signal-postgres` on `127.0.0.1:5433` (pgvector pg18, Debian 12 build — must stay
  bookworm to match collations). Copy verified identical; original left in place as a fallback.
- Old workers stopped and disabled. Leftover replication subscription from AWS disabled.

## Working model

Code is written and tested **locally**, committed, pushed to GitHub (`Boyao0305/reddit_signal`), and the
server **pulls a specific commit** and rebuilds. No edits on the server, ever. Each phase ends with tests
green; production is only touched in the deploy phase, with a checklist and a rollback.

Local tooling present: Node 24, uv, Python 3.14 (project pins **3.11** via uv). Missing: Docker, psql.
Database for development — see Decision D4.

## What stays (production machinery)

| Keep | Why |
|---|---|
| `pipeline_jobs` queue + worker lanes + heartbeats + cancel | dashboard/CLI triggers, no overlapping runs, job history |
| `system_settings` (runtime config in DB) | change model/schedule without redeploy |
| Migration ledger (`schema_migrations`), files append-only | existing 46 migrations keep their names; new ones continue at `v2_047+` |
| Path C SQL views, `dashboard_market_opportunities` snapshot | ranking logic already correct and fast |
| Source backfill chunking (fetch/project split, retry failed chunks) | proven on large backfills |
| Adapter registry for sources | clean extension point for contributors |

## Target layout

```
reddit_signal/
├── pyproject.toml  uv.lock          # Python 3.11, pinned deps; import-linter contracts
├── compose.yaml                     # postgres, api, worker (cli runs via `docker compose run`)
├── Dockerfile                       # one image, two targets: api (slim) / worker (ML deps)
├── db/migrations/                   # moved from migrations/, filenames unchanged
├── db/schema.sql                    # dumped from a fresh replay of migrations
├── frontend/                        # moved from src/dashboard/frontend (Seedcast entry)
├── experiments/                     # experiment code; write-ups in docs/experiments/
├── docs/ containers.md  components/{api,worker,cli}.md  data-model.md  domain.md
│        adr/  experiments/  agent/  CONVENTIONS.md
├── src/reddit_signal/
│   ├── schemas.py   config.py       # one validated Config; no env reads elsewhere
│   ├── db.py        migrate.py      # one connection pool; migration runner (per-file transaction)
│   ├── core/                        # PURE: promotion gate, candidate gate calibration, burst,
│   │                                #   lineage (Jaccard), extraction selection policy, ranking helpers
│   ├── sources/                     # adapters: reddit (Arctic Shift), hackernews (+ registry)
│   ├── llm.py  embedder.py          # OpenRouter client; sentence-transformers
│   ├── repositories/                # all SQL, one file per domain
│   ├── api/      main.py pipelines.py   # public read-only endpoints + serves Seedcast build
│   ├── worker/   main.py pipelines.py   # queue loop, lanes, scheduler; daily/monthly/backfill/extract
│   └── cli/      main.py                # status, run, jobs, backfill, subreddits, settings, migrate
└── tests/                           # mirrors src/; core = case tables, no DB needed
```

Three containers (api, worker, cli) + Postgres → full convention applies (§0.4 "several containers").

## Phases

### Phase 0 — groundwork (local, no prod)
- Commit `convention.txt` as `docs/CONVENTIONS.md`.
- `pyproject.toml` + `uv.lock` (3.11), ruff, pytest; make the existing test suite run.
- Measure baseline: which tests pass today, which need a DB.

### Phase 1 — one package, one config, one pool (no behaviour change)
- Move code into `src/reddit_signal/`; replace `from src.x` imports; drop `PYTHONPATH=.`.
- Single `Config` + `db.py`; remove the 6 hard-coded fallback DSNs; stop logging DSNs with passwords.
- Fold duplicates: clustering (`src/clustering` + `src/pain_extraction/clustering.py`), scoring.
- Rename `OPENCLAW_*` → `REDDIT_SIGNAL_*` settings (read old names as fallback during cutover).
- Tests: existing suite green; import-linter contracts added.

### Phase 2 — worker container + pipeline fixes
- Port `b_ops_1_daily.sh` (phases 1–6) and `b_ops_3_monthly.sh` (phases 1–11) into typed
  `worker/pipelines.py` functions; Bash scripts retired.
- Fixes, each with a case-table test in `core/`:
  - **Extraction selection**: newest posts first within a budget, backlog drained separately and
    explicitly (today: `ORDER BY id` + 5000/day cap → Jul–Oct never processed).
  - **Promotion gate**: refuse to promote a monthly run whose input/cluster/candidate counts collapse
    vs the previous live run (today: 4,120-pain run 17 went live).
  - **Stuck-job recovery**: jobs `running` with a dead worker get marked failed with a reason.
  - **Embedding load**: binary, chunked, preallocated (≈1.5 GB → ≈0.3 GB peak, identical output).
  - **Arctic Shift 422s**: investigate pagination params; back off / skip cleanly.
- Migration runner: run each file in one transaction.

### Phase 3 — api container (public, read-only)
- `GET /api/public/snapshot`, `GET /api/public/clusters/{id}` per
  `docs/reddit_analysis/05_potential_implementations/seedcast_public_frontend.md`.
- Lineage-stable cluster IDs for links; strip usernames from quotes; no viability/competition fields.
- Serves the built Seedcast frontend. **No admin endpoints mounted.**

### Phase 4 — cli container (replaces the admin website)
- `status` (queue, workers, last runs, backlog size, disk) · `run daily|monthly` · `jobs list|fail` ·
  `backfill …` · `subreddits list|add|pause` · `settings get|set` · `migrate plan|apply`.

### Phase 5 — experiments (local, real data via read-only access)
- `0001` extraction model eval: ~300 posts, same prompt/parser, 5–6 cheap OpenRouter models vs a
  strong-model reference + ~50 hand-checked; metrics incl. downstream cluster-assignment agreement and
  real cost/post. → ADR picking the model.
- `0002` monthly-job memory profile on exported window embeddings. → ADR on sampling / RAM.

### Phase 6 — deploy on Contabo (production; checklist + approval before each step)
1. Rename on server: user `reddit_signal`, `/srv/reddit_signal/{app,pgdata,logs}`, DB `zeabur` →
   `reddit_signal`, role `llmanalyse` → `reddit_signal` with a **new password**.
2. Adopt the running Postgres into `compose.yaml` (same volume, same image); add memory limits.
3. Deploy api + worker; workers start **paused**; `cli status` checks.
4. nginx vhost for `seedcast.co` + certbot (see D3); DNS switch (see Domain).
5. Run daily on fresh data; process backlog with the chosen model and budget; re-run monthly;
   promotion gate must pass.
6. Remove old `/opt/openclaw`, old systemd units; later the k3s original folder (after a backup exists).

### Phase 7 — open-source release
- New public repo with fresh history (old history has secrets); LICENSE; English README/CLAUDE.md;
  CI (lint, tests, frontend build); rotate every key that ever appeared in history.

## Domain — seedcast.co

Route 53 zone is in the laptop's AWS account. Change **only**:
- `seedcast.co` A/AAAA and `www.seedcast.co` A/AAAA: CloudFront `E7QB936EYVRKS` (redirect to
  benchology.co) → Contabo `94.72.121.103` / `2605:a141:2343:6993::1`.

Untouched: MX/SPF/DKIM/DMARC (Google Workspace), `event.seedcast.co` (same CloudFront, keeps working),
`app`, `team`, `gtm`, `competition`, `crmflowhub`, `data`, `productivekitty`, ACM validation CNAME.
Afterwards remove the two aliases from the CloudFront distribution so it no longer claims the names.
Switch only when Seedcast shows real data (Phase 6.5), not fixtures.

## Decisions (accepted 2026-10-10 — all proposals adopted)

- **D1 — modules to drop from the open-source core** (code remains in the private archive):
  `report_pipeline` (its relay is dead), `mcp_config_ui`, `competition_density` (v1),
  `competition_layer` (paid Perplexity + SocialCrawl; not shown on Seedcast),
  SocialCrawl adapters (Facebook/YouTube/Twitter — no data since July). Proposed: drop all; keep
  Reddit + Hacker News.
- **D2 — admin dashboard**: not deployed (decided). Delete its code once the CLI covers it? Proposed: yes.
- **D3 — nginx on 80/443 belongs to GTM** (`crmflowhub.seedcast.co` still served here). Add our own
  vhost file with `nginx -t` + graceful reload now, or wait until GTM has moved? Proposed: add vhost
  (graceful reload does not drop GTM connections).
- **D4 — development database**: (a) install Docker Desktop locally + small sample DB (standard for
  contributors), or (b) read-only role + SSH tunnel to production for experiments, and fakes for tests.
  Proposed: both — (b) now for experiments, (a) before Phase 3 to test `compose.yaml`.
- **D5 — viability scoring** (MiniMax, monthly, small cost): keep in the pipeline? Proposed: keep; its
  `is_real_pain ∧ is_software_solvable` flags are the hard filter in `v_market_opportunities`, i.e. they
  decide which clusters exist on the ranked surface, even though Seedcast doesn't display the scores.
