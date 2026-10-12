# Pipeline Operations Guide

## Architecture

Two independent pipelines:

- **Pipeline A (Ingestion):** Fetch raw data → extract pain objects → score → embed. Append-only, deterministic, no parameters needed.
- **Pipeline B (Analysis):** Cluster → rank → label. Parameterized by time window. Non-destructive — each run creates a new snapshot.

All commands run from the repo root: `~/github/openclaw_boyao_1_llmanalyse/`

---

## Pipeline A: Ingestion

### Step 1 — Fetch Reddit data (Arctic Shift → PostgreSQL)

```bash
# Fetch specific subreddits, last 7 days (default)
python3 -m src.data_ingestion --fetch --subreddits personalfinance ynab Frugal --days 7

# Fetch all default subreddits (defined in pipeline.py)
python3 -m src.data_ingestion --fetch --all

# Fetch with custom time window
python3 -m src.data_ingestion --fetch --subreddits SaaS startup --days 30

# Fetch with explicit date range
python3 -m src.data_ingestion --fetch --subreddits ADHD mentalhealth --after 2026-03-01 --before 2026-04-01

# Incremental: only fetch posts newer than what's already in DB
python3 -m src.data_ingestion --fetch --all --newer

# Backfill: fetch posts older than what's in DB
python3 -m src.data_ingestion --fetch --all --older --days 14

# Control concurrency (default: 6 subreddits in parallel)
python3 -m src.data_ingestion --fetch --subreddits SaaS startup ADHD --workers 10
```

### Step 2 — Extract + Score + Embed (all-in-one)

```bash
# Process all unprocessed posts (idempotent, safe to re-run)
python3 -m src.pain_extraction --ingest

# Limit to N posts (for testing)
python3 -m src.pain_extraction --ingest --limit 100

# Control extraction concurrency (default: 20 LLM workers)
python3 -m src.pain_extraction --ingest --workers 30
```

`--ingest` runs three phases in sequence:
1. **Extract** — sends each post + comments to Gemini Flash Lite via OpenRouter, gets structured pain objects
2. **Score** — VADER sentiment + spaCy specificity + community engagement score
3. **Embed** — sentence-transformers all-MiniLM-L6-v2 (384 dims), stored in pgvector

Each phase skips items already processed. Safe to interrupt and resume.

### Daily Cron (typical production usage)

```bash
python3 -m src.data_ingestion --fetch --all --newer
python3 -m src.pain_extraction --ingest
```

---

## Pipeline B: Analysis

### Run an analysis

```bash
# 7-day window (default if --window omitted)
python3 -m src.pain_extraction --analyze --window 7d

# 30-day window
python3 -m src.pain_extraction --analyze --window 30d

# 90-day window with resolution label
python3 -m src.pain_extraction --analyze --window 90d --resolution quarterly

# Custom date range
python3 -m src.pain_extraction --analyze --after 2026-03-01 --before 2026-04-01

# Control output
python3 -m src.pain_extraction --analyze --window 30d --top 50

# Reproducible clustering (slower but deterministic)
python3 -m src.pain_extraction --analyze --window 30d --deterministic

# Tune HDBSCAN parameters
python3 -m src.pain_extraction --analyze --window 30d --min-cluster-size 10 --min-samples 5
```

Each `--analyze` run:
1. Creates a new `analysis_runs` row (never deletes previous runs)
2. Loads embeddings for pain objects within the time window
3. UMAP dimensionality reduction (384 → 50 dims)
4. HDBSCAN clustering (finds dense groups of similar pains)
5. Splits large incoherent clusters
6. Scores clusters (intensity × prevalence × coherence × market gap)
7. Labels clusters via LLM (Gemini Flash Lite)
8. Prints ranked top opportunities

### Inspect results

```bash
# List all analysis runs
python3 -m src.pain_extraction --runs

# Re-view ranking for a specific run
python3 -m src.pain_extraction --rank --run-id 5
python3 -m src.pain_extraction --rank --run-id 5 --top 100

# Drill into a specific cluster
python3 -m src.pain_extraction --inspect 1346

# Overall database stats
python3 -m src.pain_extraction --stats

# Top pain objects by community score
python3 -m src.pain_extraction --sample --limit 20
```

---

## Checking Data

```bash
# Step 3 stats: posts and comments per subreddit
python3 -m src.data_ingestion --stats

# Sample posts from a subreddit
python3 -m src.data_ingestion --sample --subreddit ADHD

# Step 4 stats: pain objects, embeddings, clusters
python3 -m src.pain_extraction --stats
```

---

## Schema Setup

```bash
# Initialize/migrate database schema (idempotent, safe to re-run)
python3 -m src.pain_extraction --init-db
```

---

## Full Example: Adding New Subreddits

```bash
# 1. Fetch data for new subreddits
python3 -m src.data_ingestion --fetch --subreddits newsubreddit1 newsubreddit2 --days 30

# 2. Extract pain objects from new posts
python3 -m src.pain_extraction --ingest

# 3. Run analysis on the full dataset
python3 -m src.pain_extraction --analyze --window 30d --resolution monthly --top 50

# 4. Check the run
python3 -m src.pain_extraction --runs
python3 -m src.pain_extraction --rank --run-id <RUN_ID> --top 50
python3 -m src.pain_extraction --inspect <CLUSTER_ID>
```

---

## Moving Average Pattern

Run multiple analyses on different windows to track signal evolution:

```bash
# Short-term (what's hurting right now)
python3 -m src.pain_extraction --analyze --window 7d --resolution daily

# Medium-term (what's been building)
python3 -m src.pain_extraction --analyze --window 30d --resolution monthly

# Strategic view (structural market gaps)
python3 -m src.pain_extraction --analyze --window 90d --resolution quarterly
```

Each run is preserved. Compare rankings across runs to see which pains are growing vs fading.

---

## Configuration

| Setting | Location | Default |
|---------|----------|---------|
| OpenRouter API key | env `OPENROUTER_API_KEY` or vault file | Vault: `/home/ubuntu/vaults/openclaw/llm_analyse_project/models and credentials.md` |
| LLM model | env `OPENROUTER_MODEL` | `google/gemini-2.0-flash-lite-001` |
| Database | env `DATABASE_URL` | required, no default |
| Step 3 default subreddits | `src/data_ingestion/pipeline.py` → `DEFAULT_SUBREDDITS` | 6 B2B subs |
| Fetch concurrency | `--workers` flag | 6 |
| Extraction concurrency | `--workers` flag | 20 |
| HDBSCAN min cluster size | `--min-cluster-size` | 5 |
| HDBSCAN min samples | `--min-samples` | 3 |

---

## Backlog / Legacy Commands

These still work but are superseded by `--ingest` and `--analyze`:

```bash
# Individual steps (Pipeline A)
python3 -m src.pain_extraction --extract          # just extraction
python3 -m src.pain_extraction --score            # just scoring
python3 -m src.pain_extraction --embed            # just embedding

# Legacy destructive clustering (overwrites previous clusters)
python3 -m src.pain_extraction --cluster
python3 -m src.pain_extraction --pipeline         # embed → cluster → rank (destructive)
python3 -m src.pain_extraction --label            # label all clusters (not run-scoped)
```
