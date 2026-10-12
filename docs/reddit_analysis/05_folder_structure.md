# 05 — Folder Structure (global view)

> Stable map of where things live. Details change frequently (file names, specific scripts, schema migrations); this file documents the **structural shape** of the project, not the inventory. Update only when the top-level shape changes.
>
> v2026-06-18 update: repository map aligned with the current Zeabur web/API + EC2 worker shape and the incremental modularization target. Root `AGENTS.md` / `CLAUDE.md` now hold the short operational rules for AI agents.

---

## GitHub repo — `openclaw_boyao_1_llmanalyse`

Single branch: `main`. Clone: `https://github.com/Boyao0305/openclaw_boyao_1_llmanalyse`.

```
.
├── src/                          ← production Python code
│   ├── runtime_settings.py         PostgreSQL-backed runtime settings, env fallback
│   ├── pipeline_jobs.py            PostgreSQL-backed daily/monthly job queue
│   ├── data_ingestion/             Reddit fetch → raw posts/comments
│   ├── sources/                    Source adapters + source_runtime_config → signal_events
│   ├── pain_extraction/            signal_events → pain_objects, scoring, embeddings
│   ├── clustering/                 Reclustering, centroid assignment, lineage, quality
│   ├── temporal/                   Weekly aggregation and velocity helpers
│   ├── competition_density/        Competition signal / keyword scraping experiments
│   ├── viability_scoring/          Viability layer
│   ├── report_pipeline/            Market research report stages
│   ├── mcp_config_ui/              Separate MCP config utility app
│   └── dashboard/                  FastAPI backend + Vite/React dashboard
│       ├── api.py                  Compatibility entrypoint for the FastAPI app
│       ├── backend/                Incremental backend modularization target
│       └── frontend/               React app (node_modules and dist/ are gitignored)
│
├── scripts/                      ← operational shell + ad-hoc Python
│   ├── b_ops_1_daily.sh            cron'd daily ingest pipeline
│   ├── b_ops_3_monthly.sh          cron'd monthly orchestrator
│   ├── b_prep_*                    one-off backfills / recluster jobs
│   └── v2_*                        version-2 migration helpers
│
├── migrations/                   ← versioned SQL migrations (`v2_NNN_*.sql`)
│
├── docs/reddit_analysis/         ← architecture, runbooks, handoffs, failure log
│
├── requirements.txt              worker / ML pipeline dependencies
├── requirements.web.txt          Zeabur web/API dependencies
├── CLAUDE.md                     Claude-specific repo rules
├── AGENTS.md                     General AI agent rules
├── README.md                     deployment and operational overview
└── .gitignore
```

### What goes where (when in doubt)

- A new pipeline stage that processes data → its own subpackage under `src/`.
- An operational wrapper that runs an existing module on a schedule → `scripts/b_ops_*.sh`.
- A one-off data migration or recovery → `scripts/b_prep_*` or `scripts/v2_*`.
- A schema change → numbered SQL file in `migrations/`.
- Dashboard HTTP behavior → `src/dashboard/backend/` when modularized; `src/dashboard/api.py` remains the compatibility import surface.
- Dashboard frontend behavior → feature folders under `src/dashboard/frontend/src/`.

### Incremental modularization target

The repo is intentionally moving by compatibility wrappers, not a big-bang rewrite:

- `src.dashboard.api:app` stays importable for Docker/uvicorn while backend code moves under `src/dashboard/backend/`.
- `src/pipeline_jobs.py` and `src/runtime_settings.py` stay importable until callers can move to `src/jobs/` and `src/core/`.
- `src/sources/` and `src/pain_extraction/` keep their CLI entrypoints while internals become more service/repository-shaped.
- Frontend `src/api.ts` may remain as a facade while resource-specific API clients live in `src/api/`.

### What does NOT live in the repo

- Database contents (Postgres `reddit_raw`, accessed via env `DATABASE_URL`).
- Long-running pipeline logs (`/var/log/openclaw/`).
- Built frontend bundle (`src/dashboard/frontend/dist/` — gitignored).
- Cron schedules (live in the user crontab on the AWS server).
- MCP gateway config (`/home/ubuntu/.config/openclaw-mcp/`).

---

## Obsidian vault — `~/vaults/openclaw/`

This vault is the **strategy / decisions / planning layer**. The repo is the **execution layer**. Anything that informs future decisions but doesn't run lives here. The vault is exposed to AI agents via the `openclaw-guide` MCP server (curated subset) and the `fs` MCP server (full read access).

```
.
├── _conventions.md               ← vault map + MCP usage rules (READ FIRST)
│
├── 01_design/                    ← current architecture documents
│   ├── system_design.md            high-level system + data flow
│   ├── database_schema.md          current schema reference
│   └── archive/                    forked older versions when structure changes
│
├── 01_locked_decisions.md        ← append-only ledger of approved decisions
├── 02_principles.md              ← how-we-decide rules
│
├── 02_components/                ← one file per discrete piece of work
│   └── <phaseN>_<name>.md          plans + retrospectives, write-once
│
├── 03_failure_log.md             ← append-only post-mortems
├── 03_handoffs/                  ← one file per session boundary
│   └── YYYY-MM-DD.md               time-stamped, never edited after the next session starts
│
├── 04_open_questions.md          ← parking lot for noticed-but-deferred questions
├── 04_runbooks/                  ← steady-state operational procedures (living docs)
│
├── 05_data_example/              ← representative data snapshots (small samples, for context)
├── 05_folder_structure.md        ← this file
│
├── archived_do_not_read/         ← frozen pre-restructure docs; agents ignore
└── last_version_dont_read/       ← earlier vault layout; agents ignore
```

### What goes where (when in doubt)

- A design choice the user explicitly approved → `01_locked_decisions.md` (append).
- A general rule that applies across decisions → `02_principles.md` (edit).
- Architecture description that's currently true → `01_design/*.md` (edit in place with CHANGELOG).
- A specific piece of implementation work, planned or executed → `02_components/<name>.md` (write-once).
- Something that broke + what we learned → `03_failure_log.md` (append).
- "Where did we leave off?" notes at end of a session → `03_handoffs/YYYY-MM-DD.md` (new file).
- A question we noticed but deferred → `04_open_questions.md`.
- "How do I run X in production?" → `04_runbooks/`.

### Numbering convention

The `0N_` prefix is **priority of read order, not chronology**:
- `_` (underscore) → meta / conventions
- `01_*` → foundations (design, locked decisions)
- `02_*` → rules + work units (principles, components)
- `03_*` → history (failure log, handoffs)
- `04_*` → operational (open questions, runbooks)
- `05_*` → reference (data examples, this file)

When in doubt about priority of a new file, follow the same digit pattern; the underscore-prefix slot is reserved for meta documents.

### Mutability summary

| Path                          | Mutability                                  |
| ----------------------------- | ------------------------------------------- |
| `_conventions.md`             | Edit only when vault structure changes      |
| `01_design/*.md`              | Edit in place + CHANGELOG; fork rarely      |
| `01_locked_decisions.md`      | Append-only                                 |
| `02_principles.md`            | Edit; mark superseded inline                |
| `02_components/*.md`          | Write-once + one-line retrospective         |
| `03_failure_log.md`           | Append-only                                 |
| `03_handoffs/*.md`            | Append-only; never edited after next session|
| `04_open_questions.md`        | Items move out when answered               |
| `04_runbooks/*.md`            | Living docs, edit in place                  |
| `05_*`                        | Edit only on structural change              |
| `archived_*`, `last_version_*`| Frozen                                      |

---

## How the two relate

```
   Obsidian vault (strategy)                  Repo (execution)
   ─────────────────────────                  ─────────────────
   01_design/system_design.md          ──▶    src/<modules>
   01_design/database_schema.md        ──▶    migrations/, schema.sql
   01_locked_decisions.md              ──▶    constants, thresholds, model choices
   02_components/<phase>.md            ──▶    PRs / commits implementing them
   04_runbooks/*.md                    ──▶    scripts/, cron, systemd units
   03_handoffs/*.md                    ──▶    "what state is the code in right now"
```

A decision documented in the vault but not reflected in code is a TODO. Code that exists without a corresponding decision is a smell — backfill the decision into the vault.
