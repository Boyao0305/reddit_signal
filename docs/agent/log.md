# Session log — restructure (started 2026-10-10)

Plan: `docs/agent/plan-2026-10-restructure.md`. Decisions: `docs/adr/0001`–`0006`.

## 2026-10-10 — production DB rescue (server, done)

- k3s on Contabo was stopped+disabled by the GTM move on 2026-10-09; DB down since 04:15:53 UTC.
- Cold-copied `/var/lib/rancher/k3s/storage/pvc-9f1ba002…/pgdata` → `/srv/reddit_signal/pgdata`
  (rsync, 222.7 GB, verified zero diff). Running as `reddit-signal-postgres`
  (`pgvector/pgvector:pg18-bookworm`, `127.0.0.1:5433`). Crash recovery clean.
- Docker 29.9 installed with `--no-upgrade`, needrestart suspended; `/etc/docker/daemon.json` binds
  published ports to 127.0.0.1.
- Old `openclaw-*-worker` user services stopped and disabled. `pipeline_jobs` 108 stuck `running`.
- Subscription `openclaw_migrate_sub` (from AWS 54.82.99.49, other AWS account) disabled.
- Read-only role `reddit_signal_ro` (`pg_read_all_data`, read-only default, 10 min timeout); DSN in
  `~/.reddit_signal/readonly.env` on the laptop, used through `ssh -L 15433:127.0.0.1:5433`.
- GTM services were stopped by the GTM mover at 21:49:05, before any of our commands (verified by
  SSH login times). Hands off `/opt/gtm`, nginx, certbot, k3s.

## 2026-10-10 — phase 0 (local)

- `pyproject.toml` + `uv.lock`, Python 3.11, versions pinned to the production worker venv.
  `uv sync --extra worker` installs the ML stack (CPU torch).
- `.gitattributes` forces LF.
- Laptop trap: Windows Smart App Control blocks the locally-built `hdbscan` DLL
  ("Application Control policy has blocked this file"). Everything else imports. Use WSL (Ubuntu,
  present) for anything that runs HDBSCAN.

Baseline, before any code change:

| Suite | Result |
|---|---|
| pytest (`uv run pytest`) | 257 tests: 221 pass, 8 skipped, 15 fail, 13 errors |
| frontend `npm run build` | ok (chunk-size warning, pre-existing) |
| frontend `npm test -- --run` | 66/66 pass |

All 28 pytest failures are environmental or already-obsolete, none are regressions:
13 need a live Postgres (`tests/sources/test_db.py`, `tests/test_dashboard_v2_api.py`); 7 expect 401 from
admin routes whose auth was bypassed in `a7c50ed`; 2 run the Bash ops scripts (Windows); 6 are
`mcp_config_ui` symlink tests (Windows). Admin routes, ops scripts and `mcp_config_ui` are removed in
phase 1 (ADR 0003, 0004).

## Tried and rejected

- Hetzner `168.119.98.30` as the new host: 61 GB free disk, 7.6 GB RAM shared with an agent stack.
- Running Postgres in place on the k3s volume: would require restarting k3s (resurrects GTM's old
  Zeabur services).
- Stopping the AWS instance from the laptop: it is not in account 200889996426.

## 2026-10-10 — phase 1a: drop out-of-scope modules and the admin website

- Deleted (ADR 0004): `report_pipeline`, `mcp_config_ui`, `competition_density`, `competition_layer`,
  SocialCrawl adapters (facebook/twitter/youtube) + `social_venues`, their prompts, tests and scripts.
  Monthly phase 8 (competition) now prints a skip line; the Bash script itself goes in phase 2.
- Deleted (ADR 0003), **re-sequenced from phase 4**: admin routes (`admin_ops`, `admin_sources`,
  `reports`), login/session auth (`auth.py`, `deps.py`), and the admin frontend (settings, sources,
  backfill, extraction, jobs, cost, report, login). Reason: these were exactly the surfaces being
  dropped, nothing runs them (web pod dead since ~July, workers stopped), and the CLI ships before any
  deploy, so there is no window without operator tooling. Kept: the read-only analytics views and API
  (`dashboard_data` router; cluster table, compare, detail, weights) for local use, and Seedcast.
- `source_runtime_config` rows for dropped sources stay in the DB and are ignored by
  `list_source_configs` (asserted in `tests/sources/test_runtime_config.py`).
- Results: pytest 100 tests → 77 pass, 8 skip, 13 errors (need live Postgres), 2 fail (Bash ops
  scripts on Windows) — same causes as the baseline. Frontend: build ok (main bundle < 500 kB, warning
  gone), vitest 44/44. The `ECONNREFUSED` stderr from `score.test.ts` pre-dates this change.

## 2026-10-10 — phase 1b: one installable package

- `src/*` → `src/reddit_signal/*` (git renames, history kept); `src/dashboard/frontend` → `frontend/`;
  package tests `src/<pkg>/tests` → `tests/<pkg>/`. 254 reference lines rewritten in 78 files
  (imports, `python -m` strings, dynamic adapter/prompt discovery, path comments, Dockerfile).
- `pyproject.toml`: hatchling build, `uv sync` installs `reddit-signal` editable; pytest uses
  `--import-mode=importlib`; `google-play-scraper` dropped.
- Results unchanged: pytest 77 pass / 8 skip / 13 need Postgres / 2 Bash-on-Windows; frontend build ok,
  vitest 44/44. `tests/dashboard/test_ops_scripts.py` repo-root depth fixed after the move.
- Laptop trap: the two Bash ops-script tests fail on Windows because subprocess output is decoded as
  GBK; they are retired with the scripts in phase 2.

## 2026-10-10 — phase 1c: one config, one pool, no hard-coded credentials

- New `reddit_signal/config.py` (only module reading bootstrap env: `DATABASE_URL`,
  `REDDIT_SIGNAL_DB_POOL_MAX`, `REDDIT_SIGNAL_WORKER_ID` with `OPENCLAW_WORKER_ID` fallback,
  `PIPELINE_JOB_ID`); missing `DATABASE_URL` is a `ConfigError`, never a fallback. `redact_dsn` for logs.
- New `reddit_signal/db.py`: one `ThreadedConnectionPool` + semaphore so callers **block** when all
  connections are busy. The four old pools were `SimpleConnectionPool` (documented as not thread-safe)
  used from 20 extraction threads, and psycopg2 pools raise on exhaustion. Old modules re-export
  `get_conn`/`put_conn`/`get_pool`; `dashboard.db.get_conn` stays a context manager.
- `runtime_settings`, `pipeline_jobs`, `dashboard.snapshot` use `db.connect()`; env reads in `src/`
  are now only `config.py` and `runtime_settings` (DB-backed settings, env fallback by design).
- Removed the production DB password from the current tree (`scripts/b_prep_2_backfill_18mo.sh`,
  `tests/sources/test_db.py` fallback) and the dev password defaults; deleted one-off scripts and junk
  (`step4_full_run.log`, `nohup.out`, `.DS_Store`, `.gemini-research.json`, `.env.zeabur.example`).
  **The production password is still in git history → rotate at cutover (phase 6).**
- Tests: DB tests never use `DATABASE_URL`. Writes require `REDDIT_SIGNAL_TEST_DATABASE_URL`
  (disposable); reads accept `REDDIT_SIGNAL_READONLY_DATABASE_URL`. Previously `tests/sources/test_db.py`
  (inserts/deletes `signal_events`) fell back to the production password on localhost.
- Stale assertions fixed: `/api/v2/clusters` has paginated (50/page) since June; tests now page through.
  "Default run == 10" replaced by "default == the live run".
- Results: pytest 121 → 98 pass, 21 skip (DB), 2 fail (Bash on Windows). With the read-only tunnel:
  `tests/test_dashboard_v2_api.py` + `tests/test_dashboard_v2_path_c.py` 17/17 pass against production
  data (run 10 still has exactly 740 viable B2C clusters; ranking order verified).

## 2026-10-10 — phase 1d: dead code

- Deleted `viability_scoring` (v1 "practicality" scorer, imported only by itself; superseded by
  `clustering/quality_viability_llm`, which monthly runs), `scripts/v2_recluster_full.py`,
  `scripts/v2_backfill_pain_signal_event_id.sql` (one-off May jobs), and `data/reddit_brave` (31 scraped
  Reddit posts + zip from an April experiment; must not be published).
- Kept for later decisions: `pain_extraction/ranking.py` (monthly still writes v1 scores into deprecated
  `pain_clusters` columns — decide in phase 2 when monthly is ported), `scripts/seed_subreddit_audience_priors.sql`
  and `config/watchlist.json` (seed data for fresh installs), `scripts/zeabur/` and
  `requirements*.txt` (used by the current Dockerfile until phase 3).
- Import-linter contracts need the target layers (core/adapters/containers) → phase 2.
- Results unchanged: 98 pass, 21 skip, 2 fail (Bash on Windows).

## 2026-10-10 — phase 2.1: pure core with case tables

- Removed the `core/` and `jobs/` re-export stubs (unused); `core/` is now pure decision logic only.
- `core/schedule.py` (ported from the worker), `core/promotion.py` (completeness + collapse guard,
  experiment 0002), `core/extraction_policy.py` (daily = newest-first recent window; backlog = explicit
  range), `core/job_recovery.py` (stale `running` jobs). 56 case-table tests; the promotion test replays
  production runs 9–17 and blocks only run 17.
- Findings that shape the next steps:
  - The extraction selector orders by `signal_events.id` (insertion order), `sources/db.py:262`.
  - Reddit produces ~9.5K parent posts/day (1.12M in 120 days); `DAILY_EXTRACTION_LIMIT=5000` covers about
    half. Newest-first alone does not fix coverage → owner decision with the experiment 0001 cost numbers.
  - Runs 15 and 16 were already half-sized from the same backlog; the decline started in July.
  - The old worker did not heartbeat while a job ran (job 108's heartbeat froze at claim time), so
    stale-job detection needs the new worker to heartbeat during jobs.

## 2026-10-10 — phase 2.2: migrations

- `migrations/` → `db/migrations/`; runner moved into the package (`python -m reddit_signal.migrate`,
  `scripts/migrate.py` is a wrapper). Working-tree copies had CRLF (pre-`.gitattributes` checkout) and
  were re-materialised as LF; all 50 local SHA-256 checksums verified identical to the production ledger.
- Each file now runs under `psql --single-transaction` unless it has its own BEGIN/COMMIT (20 old files),
  uses CONCURRENTLY (v2_019, v2_047), or has `-- migrate: no-transaction`.
- Behaviour change: `sync` baselines only on an empty ledger. Before, it marked any unrecorded file up to
  the cutoff as applied without running it, even with a non-empty ledger.
- `plan` is read-only (it used to CREATE the ledger table first; found by running it as `reddit_signal_ro`).
  Against production: 50 applied, 1 pending.
- New `v2_047_signal_events_recent_parents_index.sql` (partial index for newest-first extraction). Not
  applied — production change, phase 6.

## 2026-10-10 — phase 2.3: newest-first extraction with a maturity delay

- `sources/db.get_event_bundles_batch(window=(start, end))`: unprocessed parents in the window, ordered
  `event_time DESC`; without a window the old insertion order is kept. Plumbed through
  `pain_extraction.db.get_event_bundles_batch_as_pwc` and `pain_extraction.main.cmd_ingest(window=...)`.
- Verified on production (read-only): 200 bundles per call, newest-first and inside the window for both a
  daily window and a Jul–Sep backlog window. 25–41 s per call today without the v2_047 index (the old
  query is equally slow) → verify the index brings this down at deploy.
- Found while testing: the newest unprocessed posts had **zero comments** projected yet. Newest-first would
  extract posts before their threads exist, once and for all. Experiment 0003 (39,998 comments): 95% arrive
  within 2 days → daily window is now posts 2–14 days old (`DEFAULT_MATURITY_DAYS`, `DEFAULT_RECENCY_DAYS`).
- Known, unchanged: extraction errors that never call `save_fn` leave the post unprocessed, so it is picked
  again in the next batch (as before with id order). Bounded by the batch "no progress" guard and the limit.

## 2026-10-10 — phase 2.4: memory-light embedding load

- `pain_extraction/embedding.py`: window/all loaders stream rows through a server-side cursor (10K rows
  per chunk) into preallocated arrays; count and read share one REPEATABLE READ snapshot.
- Verified on production data (164,162 embeddings, 2026-06-09..06-23, the size of a monthly window), each
  loader in its own process: old peak RSS 1,238 MB, new 383 MB; ids and float32 bytes identical; 160 s vs
  140 s.
- Not changed (noted): the on-disk cache is trusted when only the row count matches.

## 2026-10-10 — phase 2.5: worker container replaces the Bash pipelines

- `reddit_signal.worker` (`python -m reddit_signal.worker`): loop with lanes (pipeline / extraction /
  source-backfill / snapshot / all), scheduler, snapshot refresh after success, stale-job recovery each
  iteration, **heartbeats every 10 s during jobs**. Jobs run as `python -m reddit_signal.worker run-job ID`
  child processes (memory returned, cancel by process group); output read by a thread (works on Windows).
- `worker/pipelines.py`: typed `run_daily`, `run_monthly`, `run_extraction` against Protocols; real steps
  in `reddit_signal/pipeline_steps.py`; stages recorded directly by `job_recorder.JobRecorder` (no stdout
  regex parsing). Shapes crossing the boundary in `schemas.py`.
- Monthly: phases 1-9 as before minus competition; phase 10 = `core.promotion` decision + atomic promote.
  A blocked or failed run is marked `failed` and the job fails with the reasons.
- Daily: stage 3 extracts the 2-14-day newest-first window; assignment at 0.70 (`ASSIGN_THRESHOLD`
  setting; module default is 0.55). New settings with defaults: `EXTRACTION_RECENCY_DAYS` 14,
  `EXTRACTION_MATURITY_DAYS` 2. `REDDIT_SIGNAL_DAILY_UTC`/`MONTHLY_UTC` read first, `OPENCLAW_*` fallback.
- Ported: `scripts/run_sources.py` → `sources/daily.py`; `scripts/b_prep_6_recluster_90d.py` →
  `clustering/rolling.py`; `quality_metrics.compute_run`, `candidate_gate.calibrate_and_apply` extracted from
  their `main()`s. Removed: both Bash pipelines, `ec2_pipeline_worker.py`, `log_pipeline_step.py`,
  `reddit_backfill_worker.py` (legacy lane, table empty in production), `zeabur/scheduler.py`.
- Verified read-only on production: `repositories.analysis_runs` + `core.promotion` would have blocked run 17
  (0.05x / 0.03x / 0.00x) and promoted run 16.
- import-linter: 3 contracts kept. pytest: 201 tests, 180 pass, 21 skip (DB), **0 fail**.

## 2026-10-10 — phase 2.6: Arctic Shift throttling

- Daily 2026-10-05: 1,529 Arctic Shift requests in 10.5 min (6 fetch threads, no pacing); 314 (20.5%)
  got **422**, zero 429/5xx. Each 422 raised and dropped that subreddit's posts or comments for the day.
  Probing the API: invalid parameters return **400** with a message, so 422 is its throttle response.
- `data_ingestion/arcticshift.py`: process-wide rate limiter (`ARCTIC_SHIFT_MAX_RPS`, default 1/s, the
  documented limit) and retries for 422/429 (wait = `x-ratelimit-reset`/`retry-after`, 1–60 s) and 5xx
  (exponential), `MAX_RETRIES` 6. Expected daily fetch ≈ 25 min at 1/s instead of 10 min with 20% loss.
- Live check: r/ADHD one day, 256 posts in 4.5 s through the new client.
- pytest 214 → 193 pass, 21 skip, 0 fail.

## 2026-10-11 — model decision and extraction robustness

- Experiments 0001 and 0005 merged (data outside git: `D:/programmation/Github/reddit_signal-experiment-data/0005/`;
  0001's data was lost when its worktree was removed with --force — results remain in the write-up).
- Owner accepted ADR 0007: gpt-oss-120b pinned to Venice with fallback.
- `pain_extraction/extraction.py`: `request_options()` (EXTRACTION_MAX_TOKENS, OPENROUTER_PROVIDER_ORDER,
  OPENROUTER_ALLOW_FALLBACKS); cut-off output retried with double budget up to 8,192; empty choices retried
  (they were re-raised immediately despite the "attempt n/3" message); `load_pain_array` finds the array
  inside surrounding text and raises `UnusableOutput` instead of returning [] (the cause of ~6% silent loss);
  removed the no-op `replace("\'", "'")`; removed import-time DB reads (`MODEL_ID`) in extraction and labeling.
- Failures go to `pain_extraction_attempts` (existed since v2_008, never written): selector skips an event for
  6 h after a failure and permanently after 3 (`failed`), so a bad post is not re-picked every batch.
- Not yet set in production (phase 6): OPENROUTER_MODEL, OPENROUTER_PROVIDER_ORDER, DAILY_EXTRACTION_LIMIT.
  **OpenRouter credit is nearly exhausted** (~$0.5–1 left) — must be topped up before any extraction.
- pytest 234 → 213 pass, 21 skip, 0 fail.

## 2026-10-11 — phase 3: public read-only API for Seedcast

- `GET /api/public/snapshot`, `GET /api/public/clusters/{id}` (`dashboard/backend/{routers,services,repositories}/public.py`,
  pure display rules in `core/public_view.py`). No viability/competition/Pareto fields, no usernames
  (`strip_usernames` on statements and quotes; author never selected into the payload).
- Lineage-stable public ids: earliest ancestor along `continued` edges (recursive CTE); detail accepts the
  public id or the current cluster id. Series (26 weeks) and history (52 weeks) are stitched from ancestor
  clusters' `pain_volume_weekly` for weeks before the current run's window.
- Representative statement = member closest to the centroid (`embedding <=> centroid`); the most-engaged
  member was often an off-topic anecdote.
- Verified read-only on run 16: 247 clusters in 4.5–9 s cold (30-min cache), detail 1.7 s; 147 clusters get
  pre-window history from ancestors. Through the Vite proxy the browser-facing path returns the same data.
- Frontend: `api/seedcast.ts` calls the API by default (`VITE_SEEDCAST_FIXTURES=1` for sample data);
  Seedcast component tests mock the API client. vitest 44/44, build ok.
- Visual browser check not possible: another Chrome extension blocks screenshots of local pages.
- Live run is still run 17 (1 cluster) — the public site shows real data only after a healthy monthly run.

## 2026-10-11 — phase 4: operator CLI

- `python -m reddit_signal.cli`: status [--coverage], run daily|monthly|extraction (backlog via --since/--until),
  jobs list|show|cancel|recover, backfill (ported from scripts/source_backfill.py), subreddits, settings,
  migrate, snapshot refresh, promote [--force]. Every command that starts work only enqueues a job.
- Shared ops moved to `reddit_signal/maintenance.py` (stale-job recovery, snapshot refresh) so the CLI does not
  import the worker; new import-linter contract "containers do not import each other" (4 contracts kept).
- `cli status --coverage` against production (read-only, 20 s): reddit posts 2–14 days old **0/91,552
  extracted (0%) LOW**, hackernews 1,671/1,671; job 108 shown running with a 45-hour-stale worker. The first
  coverage query (EXISTS + `post_id::text = source_row_id`) timed out after 10 min; rewritten as indexed joins.
- Settings: `load_settings` no longer creates the table or swallows errors (it silently fell back to built-in
  defaults during DB problems); obsolete settings removed; new ones declared; model default gpt-oss-120b.
- Removed `scripts/source_backfill.py`, `scripts/refresh_dashboard_snapshot.py` (CLI replaces them).
- pytest 277 → 256 pass, 21 skip, 0 fail.

## 2026-10-11 — packaging

- `reddit_signal.api.main:app`: public API only; `/api/v2` analytics with `REDDIT_SIGNAL_ANALYTICS_API=1`;
  serves the Seedcast build at `/` + `/assets` when `REDDIT_SIGNAL_STATIC_DIR` is set.
- `Dockerfile`: targets `api` (slim, frontend baked in) and `worker` (ML stack, also the CLI image), deps from
  `uv.lock`, non-root user. `compose.yaml`: postgres (pg18-bookworm, PGDATA path of the existing volume),
  api on 127.0.0.1:8080, workers per lane with memory limits, `cli` profile.
- Removed `scripts/zeabur/`, `requirements*.txt`. **Images not built yet** (no Docker on the laptop) — first build
  is step 1 of the deploy checklist.

## 2026-10-11 — deployment approved (state before context compaction)

- Owner confirmed: OpenRouter topped up; daily limit 10,000; backlog Jul 1 → now−14 d; deploy key OK; run the
  deploy checklist (`docs/agent/deploy-checklist-2026-10.md`) through step 11, then stop and report.
- Branch `claude/seedcast-public-frontend`, last commit before deploy: see `git log -1`. Tests 280 (259 pass,
  21 DB-skipped), 4 import contracts kept. Images not built yet.
- Production right now: Postgres = `docker run` container `reddit-signal-postgres` on 127.0.0.1:5433, data
  `/srv/reddit_signal/pgdata`, db `zeabur`, roles `root` (superuser, socket trust), `llmanalyse` (app),
  `reddit_signal_ro` (read-only, DSN in laptop `~/.reddit_signal/readonly.env` via `ssh -L 15433:127.0.0.1:5433`).
  Old openclaw workers stopped+disabled; job 108 stuck `running`; live run 17 (degenerate). k3s disabled by the GTM
  mover — never restart it. GTM services stopped (moving to Hetzner); nginx 80/443 still GTM's.
- Laptop: local preview servers and the SSH tunnel were stopped for low memory (not restarted).

## 2026-10-11 — deployment, checklist steps 1–10 (server 94.72.121.103)

- Deploy key: read-only GitHub deploy key `contabo 94.72.121.103 (read-only)`, private key
  `/root/.ssh/reddit_signal_deploy`; clone at `/srv/reddit_signal/app`, detached at `fbb6ec1`.
- Images built on the server in 7 min 45 s: `reddit_signal-api` 746 MB, `reddit_signal-worker` 2.77 GB. Smoke test
  with plain `docker run` (not `compose run`, which would have started a second Postgres on the same data dir).
- Cutover: `docker stop -t 120 reddit-signal-postgres` (restart policy set to `no`, container kept for
  rollback), `compose up -d postgres` on `/srv/reddit_signal/pgdata`. Database `zeabur` → `reddit_signal`, role
  `llmanalyse` → `reddit_signal` with a new random password; `/srv/reddit_signal/reddit_signal.env` (600, root)
  holds `DATABASE_URL`. pg_hba already requires scram for non-local hosts. Laptop read-only DSN renamed.
- Every compose command on the server needs
  `REDDIT_SIGNAL_ENV_FILE=/srv/reddit_signal/reddit_signal.env REDDIT_SIGNAL_PGDATA=/srv/reddit_signal/pgdata`.
- Migration v2_047 applied (CONCURRENTLY, 389 s, index 150 MB, valid); plan: 0 pending, 51 recorded.
- `cli jobs recover`: job 108 failed ("worker lost").
- OpenRouter: stored key is the new one (not `sk-or-v1-db0c93…`), valid, **per-key cap $20, $19.31 left** —
  enough for dailies, not for the ~$56 backlog.
- Settings: model `openai/gpt-oss-120b`, providers `venice`, fallbacks on, daily limit 10,000, daily 04:05,
  monthly 05:00. Later `ARCTIC_SHIFT_MAX_RPS` 0.8 (see below).
- Old openclaw user units disabled+failed. An OpenClaw agent gateway (node, port 18789, user `openclaw`) runs on
  the host — not ours, left alone.
- `compose up -d api worker-pipeline worker-extraction worker-backfill`: all up, api healthy, `/healthz` 200,
  `/` serves Seedcast, `/api/public/snapshot` 200 (still run 17's data).

## 2026-10-11 — first daily (job 109)

- Arctic Shift budget looks like ~50 requests per fixed one-minute window (no remaining-count header, only
  `x-ratelimit-reset`). At 1 req/s about 5–6 requests a minute got 422 and each stalled a fetch thread ~55 s, so
  throughput fell to 10–25 requests/min. `ARCTIC_SHIFT_MAX_RPS` set to 0.8 for the next jobs (read once per job).
- Not all 422s are throttling: body `"Timeout. Maybe slow down a bit"` is a query timeout, deterministic for the
  newest ~30 min of a subreddit's comments (ADHD, CPTSD, EntrepreneurRideAlong). Those failed after 6 × 55 s.
  Loss is only the newest minutes, which the next overlapping window refetches. Fixed in `776b33d` (not
  deployed): query timeouts fail fast, windows end 1 h before now, stable worker hostnames.
- Fetch pace on the old code: ~45 subreddits/hour of 261 → fetch stage ≈ 6 h (old dailies took ~1 h but dropped
  20% of requests).
- 4 h in: 93 of 261 subreddits fetched; the remaining 168 include the heaviest (personalfinance, legaladvice,
  relationships, cscareerquestions, sysadmin…). Comment pagination dominates (Mommit ≈ 19,000 comments a week =
  190 pages). The old dailies' ~1,529 requests were small because each 422 cut a subreddit's pagination short.
- Watchlist has **38 case-duplicate subreddits** (`Accounting`/`accounting`, `Plumbing`/`plumbing`, …): 223
  unique of 261, each duplicate fetched twice (upserts, so no duplicate rows).
- `5a454dc`: the daily passes an explicit window from the Reddit adapter, so the ingest lag is applied there.
- Removed sources (twitter, youtube, facebook) still have enabled rows in `source_runtime_config`; harmless,
  `SUPPORTED_RUNTIME_SOURCES` filters them out.
- Stopped and reported to the owner with job 109 still in stage 1; proposed: deactivate the 38 duplicates,
  reddit lookback 7 → 3 days (experiment 0003: 95% of comments within 2 days), deploy `5a454dc`.

## 2026-10-12 — first daily, second attempt (owner approved the four changes)

- Job 109 cancelled. 37 lowercase case-duplicates paused (`cli subreddits set NAME --status paused`; the 38th was a
  `candidate`); capitalised spellings kept because they hold most of the stored posts. Posts of each pair are
  still split across two `subreddits` rows — merge later. Active subreddits: 221.
- Lookback: the effective daily lookback is `max(reddit_subreddit_sources.daily_lookback_days)` over active rows
  (`source_runtime_config.lookback_days` is not used for managed subreddits) → set to 3 on all active rows (SQL).
- Deployed `8f40318`; job 110: fetch **1 h 50 min** (vs ~45 subreddits/h before). The 422s were almost all query
  timeouts ("Timeout. Maybe slow down a bit"), not throttling, and recovered with the short retry. The lag
  never applied: `SourceRuntimeConfig.since_until` passes `until=now` → fixed in `8520f48` (window capped at
  now − 1 h; the test now passes `until`).
- Job 110 then hung in projection: one `FETCH` > 2 h, Postgres I/O workers reading 156 MB/s (856 GB since start).
  Causes: (1) the cold copy reset cumulative stats, so autoanalyze never ran; statistics still predated the
  copy and the planner estimated **1 row** for recent comments → walked `comments_pkey` (61M rows, random heap
  reads) with a seq scan of posts per match; (2) `comments` (136 GB) had no index on `created_utc`.
  Fix: cancelled job 110 (plus the orphaned backend), `ANALYZE` + `vacuumdb --analyze-only` (main tables in
  < 1 min), migration v2_048 `idx_comments_created_utc` (CONCURRENTLY, 1,302 s, 1.1 GB). Plan now uses it.
  Deployed `af46bf3`.
- Lesson: after any physical copy/restore, run `vacuumdb --analyze-only` before the first job.
- Scheduled daily #111 started 04:05 with a 3-day window ending 1 h before start.
- **Job 111 succeeded** (scheduled, 04:05–10:05 UTC, 6 h 00 min): fetch 1 h 28 min, projection 8 min 29 s,
  extract/score/embed 4 h 23 min, stages 4–7 < 20 s. 7,788 new pain objects embedded, 0 dead letters, 236
  cut-off answers retried. Live run is still 17 (assignment + snapshot ran against it).
- Two extraction problems found in job 111:
  1. Limit split 5,000/5,000: hackernews used 133, reddit stopped at 5,000 → reddit coverage of posts 2–14 days
     old **4,979 / 99,710 (5%)**; ~8,300 new reddit posts a day. Fixed in `e7bd7f2`: budget left after the first
     pass goes to the sources that hit their cap.
  2. gpt-oss-120b at its default reasoning effort: ~60 s per call, 19 posts/min with 20 workers, USD 0.00017
     per post (2.9x the experiment) — the experiments used `reasoning.effort=low`, production never sent it.
     New setting `EXTRACTION_REASONING_EFFORT` (default `low`). Deployed `e7bd7f2`.
- Check job 112 (`cli run extraction --limit 200`, daily window): 200 posts in 2 min 15 s including model load,
  0 errors, 0 cut-offs, **USD 0.000055/post** (OpenRouter key usage delta). Second pass visible in the log.
- Labeling (monthly) also uses OPENROUTER_MODEL without a reasoning effort — small volume, left as is.

## 2026-10-12 — backlog started, seedcast.co public

- OpenRouter key limit is USD 20 **per day** (reset daily). Backlog job #113: `cli run extraction --since
  2026-07-01 --until 2026-09-28 --limit 100000`, 20 workers: ~150 posts/min (~215k/day ≈ USD 12/day), 0 errors,
  USD 0.00006/post. Workers not raised — twice the speed would cross the daily cap and park posts.
- Owner chose option 1: `cli promote 16` (run 17 → historical; undo: `cli promote 17 --force`), snapshot
  refreshed, public API 247 clusters.
- nginx: separate site `/etc/nginx/sites-available/seedcast` from `deploy/nginx/seedcast.conf`; GTM's
  `gtm.seedcast.co` site untouched (still 401 basic auth). certbot `--nginx --redirect` for seedcast.co + www
  (existing LE account, auto-renew timer).
- Route 53 zone Z09315522IUOYD3F65M8F, change C03755343JGGNFNVS36SF: UPSERT A 94.72.121.103 and AAAA
  2605:a141:2343:6993::1 for seedcast.co and www.seedcast.co, TTL 300 (were aliases to CloudFront
  d2729igixwl4j0). MX/TXT/other subdomains unchanged. Previous records saved outside the repo.
- CloudFront E7QB936EYVRKS aliases: removed seedcast.co + www, kept event.seedcast.co (still 200).
- Verified from outside: https://seedcast.co and www 200 with valid certificate, http → 301 https.
