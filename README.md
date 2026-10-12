# reddit_signal

reddit_signal reads Reddit and Hacker News discussions, extracts the problems people describe ("pain
points"), groups similar complaints into clusters, and tracks how each cluster grows or fades over time.
It powers [Seedcast](https://seedcast.co), a public read-only view of the clusters.

```
Reddit (Arctic Shift) ─┐                                     ┌─ daily: assign new pains to the live clusters
                       ├─ fetch ─ project ─ extract ─ embed ─┤
Hacker News API ───────┘                                     └─ monthly: re-cluster the last 90 days
                                                                       │
                                     public API + Seedcast site ◄──────┘
```

## How it works

1. **Fetch.** Posts and comments from a watchlist of subreddits (via the
   [Arctic Shift](https://arctic-shift.photon-reddit.com) archive) and from Hacker News.
2. **Project.** Raw rows become source-independent `signal_events` (a post and its comments).
3. **Extract.** An LLM (through [OpenRouter](https://openrouter.ai)) reads each post with its comments and
   returns the pain points it describes, each with a short statement and a supporting quote. Posts are
   extracted once they are about two days old, when most comments have arrived.
4. **Embed.** Each pain statement gets a 384-dimension embedding (`all-MiniLM-L6-v2`), stored with pgvector.
5. **Cluster (monthly).** UMAP + HDBSCAN group the last 90 days of pain points. Clusters are labeled,
   classified by audience (B2B / B2C / mixed), scored for quality, and linked to their predecessors so a
   topic keeps its identity from month to month. A new run goes live only if it passes a promotion check
   (all stages complete, and not a collapse compared with recent runs).
6. **Assign (daily).** New pain points join the nearest live cluster when they are similar enough, so weekly
   volumes stay current between monthly runs.
7. **Publish.** A read-only API (`/api/public/snapshot`, `/api/public/clusters/{id}`) feeds the Seedcast
   site: cluster size, momentum (last 4 weeks vs the 4 before), audience, communities and quotes. Usernames
   and internal scores are never exposed.

## Architecture

One Python package, `src/reddit_signal`, runs as three containers plus PostgreSQL:

| Container | Command | Role |
|---|---|---|
| `api` | `uvicorn reddit_signal.api.main:app` | public API and the Seedcast site (built from `frontend/`) |
| `worker-*` | `python -m reddit_signal.worker --lane …` | job queue consumers: `pipeline` (scheduler, daily, monthly), `extraction`, `source-backfill` |
| `cli` | `python -m reddit_signal.cli …` | operator commands; anything that does work only queues a job |

Jobs live in PostgreSQL (`pipeline_jobs`); workers claim them, run each one in a child process, send
heartbeats, and recover jobs whose worker disappeared. The only environment variable the services need is
`DATABASE_URL`; every other setting is stored in the `system_settings` table and changed with the CLI.

Code layout: `core/` holds pure logic (scheduling, promotion rules, extraction windows), `repositories/` the
SQL, and the containers (`api/`, `worker/`, `cli/`) do not import each other — enforced by import-linter
contracts in `pyproject.toml`.

## Quick start (Docker Compose)

Requirements: Docker with Compose, about 8 GB of RAM for the monthly clustering, and an OpenRouter API key.

```bash
git clone https://github.com/Boyao0305/reddit_signal.git && cd reddit_signal

# 1. Database credentials for the services (the postgres service creates this user on first start)
echo 'POSTGRES_PASSWORD=choose-a-password' > .env
echo 'DATABASE_URL=postgresql://reddit_signal:choose-a-password@postgres:5432/reddit_signal' > reddit_signal.env

# 2. Build and start the database, then create the schema
docker compose build
docker compose up -d postgres
docker compose run --rm cli migrate apply      # empty database: loads db/schema.sql + db/seed.sql

# 3. Configure and start
docker compose run --rm cli settings set OPENROUTER_API_KEY sk-or-...
docker compose up -d api worker-pipeline worker-extraction worker-backfill
docker compose run --rm cli status
```

The site is served on <http://127.0.0.1:8080>. It shows "No clusters yet" until the first monthly run:

```bash
docker compose run --rm cli run daily     # fetch + extract recent posts (also runs every day at 04:05 UTC)
docker compose run --rm cli run monthly   # cluster the last 90 days (also runs on the 1st at 05:00 UTC)
```

A useful monthly run needs a few weeks of extracted posts. To start with history, backfill raw data and
extract it:

```bash
docker compose run --rm cli backfill create --sources reddit --date-from 2026-07-01 --date-to 2026-10-01 --mode fetch-project
docker compose run --rm cli run extraction --since 2026-07-01 --until 2026-10-01 --limit 100000
```

The starter watchlist (`db/seed.sql`) has about 220 subreddits; manage it with `cli subreddits list|add|set`.

## Configuration

All settings live in the database: `cli settings list`, `cli settings get NAME`, `cli settings set NAME VALUE`.

| Setting | Default | Meaning |
|---|---|---|
| `OPENROUTER_API_KEY` | — | required for extraction, labeling and classification |
| `OPENROUTER_MODEL` | `openai/gpt-oss-120b` | extraction model |
| `OPENROUTER_PROVIDER_ORDER` | — | optional provider pinning, e.g. `venice` |
| `EXTRACTION_REASONING_EFFORT` | `low` | reasoning effort for reasoning models (empty = model default) |
| `DAILY_EXTRACTION_LIMIT` | `1000` | posts extracted per daily run (0 = no cap) |
| `EXTRACTION_RECENCY_DAYS` / `EXTRACTION_MATURITY_DAYS` | `14` / `2` | daily window: posts between 2 and 14 days old |
| `REDDIT_SIGNAL_DAILY_UTC` / `REDDIT_SIGNAL_MONTHLY_UTC` | `04:05` / `05:00` | schedule |
| `ASSIGN_THRESHOLD` | `0.70` | cosine similarity needed to join a live cluster |
| `ARCTIC_SHIFT_MAX_RPS` | `1.0` | request rate to Arctic Shift, per process |

Bootstrap environment variables: `DATABASE_URL` (required), `REDDIT_SIGNAL_DB_POOL_MAX`,
`REDDIT_SIGNAL_WORKER_ID`, `REDDIT_SIGNAL_WORKER_LANE`. See `.env.example`.

## Operating

```
cli status [--coverage]                 queue, workers, live run, extraction coverage
cli run daily | monthly | extraction    queue a job (extraction: --since/--until/--limit for a backlog)
cli jobs list | show ID | cancel ID | recover
cli promote RUN_ID [--force]            make a built run live (applies the promotion check)
cli snapshot refresh                    rebuild the public snapshot
cli migrate plan | apply                SQL migrations in db/migrations (append-only, checksummed)
cli backfill plan | create | status | retry-failed
```

**Cost.** Extraction with `gpt-oss-120b` at low reasoning effort costs about USD 0.00006 per post, so 10,000
posts a day is roughly USD 18 a month. The monthly labeling and classification calls are small in
comparison.

**Data sources.** Reddit content comes from the Arctic Shift archive, which is rate-limited; keep
`ARCTIC_SHIFT_MAX_RPS` at or below 1. Hacker News uses its official Firebase API. Respect both services'
terms, and do not republish usernames or full posts — the public API exposes only short quotes without
authors.

## Development

```bash
uv sync --extra worker          # Python 3.11; the worker extra installs the ML stack (CPU torch)
uv run pytest                   # database tests are skipped unless REDDIT_SIGNAL_TEST_DATABASE_URL is set
uv run lint-imports             # architecture contracts
cd frontend && npm ci && npm test && npm run build
```

`npm run dev` serves the Seedcast frontend with the API proxied to `127.0.0.1:8080`; set
`VITE_SEEDCAST_FIXTURES=1` to use built-in sample data instead.

Schema changes are new files in `db/migrations/` (never edit an applied one). After adding migrations,
regenerate `db/schema.sql` with `pg_dump --schema-only --no-owner --no-privileges` and update its
`schema snapshot through:` header.

## License

[MIT](LICENSE)
