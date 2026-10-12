# Deploy checklist — reddit_signal on the Contabo server (phase 6)

Server `94.72.121.103`. Code = branch `claude/seedcast-public-frontend` at a pinned commit. Each step lists its
check and its rollback. Steps 1–11 and 14 done 2026-10-11/12; step 12 (backlog) running (details in `docs/agent/log.md`).

## 0. Preconditions (owner) — all confirmed 2026-10-11

- [x] OpenRouter credit topped up; old key `sk-or-v1-db0c93…` handled by the owner.
- [x] Daily extraction limit **10,000**. Backlog **2026-07-01 → now−14 d** (≈ $56 at Venice prices).
- [x] Read-only deploy key on the GitHub repo approved.
- [x] Deployment approved. Stop and report after the first daily (step 11); backlog, monthly, nginx/DNS each get
      their own go-ahead.
- [x] GTM status checked 2026-10-12: GTM's nginx site (gtm.seedcast.co) still enabled here; left untouched.

## 1. Code and images (no impact on running services)

1. `git clone` to `/srv/reddit_signal/app`, `git checkout <commit>`.
2. `docker compose build` (api + worker). Check: both images build; `docker compose run --rm cli --help`.
   Rollback: delete images.

## 2. Database cutover (short downtime of the stopped system only)

3. Stop `reddit-signal-postgres` (the `docker run` container) with `docker stop -t 120`.
4. `REDDIT_SIGNAL_PGDATA=/srv/reddit_signal/pgdata docker compose up -d postgres`. Check: `pg_isready`, row counts
   as in phase 0. Rollback: `docker compose stop postgres; docker start reddit-signal-postgres`.
   **Then `vacuumdb --analyze-only`**: a physical copy resets the cumulative stats, autoanalyze never fires, and
   stale statistics made the first daily's projection hang (log 2026-10-12).
5. Rename database `zeabur` → `reddit_signal`, role `llmanalyse` → `reddit_signal`, **new password**; write
   `/srv/reddit_signal/reddit_signal.env` (`DATABASE_URL=...@postgres:5432/reddit_signal`, mode 600). Update the
   laptop's read-only DSN (database name). Check: `cli status`. Rollback: rename back.
6. `cli migrate plan` → `apply` (v2_047 index, built CONCURRENTLY over ~60M rows; minutes). Check: plan shows 0
   pending; selector query time drops from ~25 s (re-run the read-only timing).
7. `cli jobs recover` → job 108 marked failed.

## 3. Settings

8. `cli settings set` OPENROUTER_MODEL=openai/gpt-oss-120b, OPENROUTER_PROVIDER_ORDER=venice,
   OPENROUTER_ALLOW_FALLBACKS=true, DAILY_EXTRACTION_LIMIT=<decision>, REDDIT_SIGNAL_DAILY_UTC=04:05,
   REDDIT_SIGNAL_MONTHLY_UTC=05:00. Check: `cli settings list`.

## 4. Start the new system

9. Disable the old units for good: `systemctl --user -M openclaw@ disable` (already stopped). Keep `/opt/openclaw`
   until step 15.
10. `docker compose up -d api worker-pipeline worker-extraction worker-backfill`. Check: `cli status` shows three
    fresh workers; `curl 127.0.0.1:8080/healthz`.
11. `cli run daily`; watch `cli jobs show <id>` and worker logs. Check: stages 1–7 succeed; coverage of posts
    2–14 days old rises; Arctic Shift 422s retried, not dropped; extraction cost per post ≈ $0.00006 on the
    OpenRouter activity page.
12. Backlog: `cli run extraction --since 2026-07-01 --until <now−14d> --limit <budgeted>`; repeat until done.
    Check: dead letters stay small; spend matches the estimate.
13. `cli run monthly` once the 90-day window is filled. Check: promotion gate passes on its own (no `--force`);
    dashboard snapshot rows ≈ previous healthy runs.

## 5. Public site

14. nginx server block for `seedcast.co` + `www` → `127.0.0.1:8080` (separate file, `nginx -t`, graceful
    reload); certbot for both names. Route 53: only apex + www A/AAAA → `94.72.121.103` /
    `2605:a141:2343:6993::1`; then remove the two aliases from CloudFront `E7QB936EYVRKS`. Check: site loads over
    HTTPS; MX/TXT records unchanged; `event.seedcast.co` still works.

## 6. Clean-up (after a week of healthy runs)

15. Remove `/opt/openclaw`, the `openclaw` user units, the k3s copy of pgdata (only after a backup exists).
16. Backups: nightly `pg_dump -Fc` (location to decide; same disk protects only against mistakes).
17. Ask whoever owns AWS `54.82.99.49` to stop it; drop the disabled replication subscription.
