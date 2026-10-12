# Teamwork protocol — Claude (orchestrator) and Claude Code (implementer)

> **Status:** Living document. Edit when the working pattern changes.
> **Created:** 2026-05-12
> **Audience:** Future versions of Claude operating on this project, in any session.

## 1. Why this file exists

This project is built by a human (the founder) collaborating with two distinct AI agents:

- **Claude with MCP tools** (this orchestrator role) — operates in chat, has live access to the openclaw vault, the project filesystem (read+write to specific paths), the Postgres database, web search, and visualization.
- **Claude Code** (delegated implementer) — operates in a terminal against the project repo, can write files, run npm/python/curl, hit a local FastAPI dev server, iterate on errors.

Each session of either agent starts cold. Without a written protocol, every session re-derives "who does what" — wasting tokens and risking inconsistent decisions. This file is the durable answer.

## 2. The two agents and their capabilities

### Claude (this orchestrator role)

Has:
- `openclaw-pg` — live read-only Postgres access. Can validate SQL against real data, sample distributions, sanity-check assumptions.
- `openclaw-fs` — read+write to `/home/ubuntu/github/openclaw_boyao_1_llmanalyse` (the repo) and `/home/ubuntu/vaults/openclaw` (the vault).
- `openclaw-guide` — curated orientation docs (the vault subset that future-me reads on startup).
- Visualization, web search, web fetch.
- Full context of the vault on session start (if it loads via `openclaw-guide:list_orientation_docs` → `read_orientation_doc`).

Lacks:
- Cannot run `npm install`, `vite build`, `python -m pytest`, or start the dashboard dev server.
- Cannot iteratively debug a running React app (no DOM inspection of a live UI).
- Cannot persist anything between sessions except by writing to the vault or the repo.
- Limited context window — cannot hold 20+ source files coherently in one session.

### Claude Code

Has:
- Full filesystem write, terminal execution, package management.
- Can run the test suite, build the frontend, hit the API, observe HTTP errors and stack traces.
- Tight iteration loops on build/lint/typecheck errors.

Lacks (by default — unless explicitly given):
- The full vault context. Does not auto-load `01_locked_decisions.md` or `02_principles.md` unless instructed.
- Live Postgres credentials (operates from spec, not from live data inspection).
- The cross-session history that the vault provides.

## 3. The leverage split — what goes where

The principle: **each agent does what's strictly easier for it than the other, with "easier" including "produces output of higher quality."**

### Claude does (and Claude Code does not):

- **Live SQL validation.** Backfills, view definitions, migrations that need to be checked against real distributions. Claude can run the SQL, query the result, compare to expectation, iterate. Claude Code would need DB credentials and live data, which is a sharper-than-necessary tool for the task.
- **Design specs in `02_components/`.** Component plans, API contracts, architectural decisions. Needs full vault context (locked decisions, principles, prior failure log), which Claude has on startup. Claude Code starting cold would re-derive too much.
- **Decision ledger entries (`01_locked_decisions.md`, `02_principles.md`).** These are append-only and require the full historical context of prior decisions to write correctly. Claude does this, never Claude Code.
- **Deferred / exploratory designs (`05_potential_implementations/`).** Same reason — depend on knowing what's *not* being built and why.
- **Orientation index updates (`_conventions.md`, `05_folder_structure.md`).** Structural metadata about the vault itself.
- **Initial visualization sketches.** Mockups, prototype views, design illustrations that exist to align the user with the orchestrator before implementation begins.

### Claude Code does (and Claude does not):

- **Frontend implementation.** React components, Vue templates, Vite/webpack configs. Anything that needs `npm run build` or `npm run dev` to verify.
- **Backend implementation against the spec.** FastAPI endpoint implementations once the contract (request/response shapes) is fixed in the design doc.
- **Pipeline edits in `src/`.** Changes to existing Python modules (extraction, scoring, aggregation, orchestration). Needs `pytest` access to verify behavior.
- **Tests and CI configuration.** Adding test cases, fixing flaky tests, configuring lint/typecheck.
- **Iterative debugging.** When a build breaks or an endpoint returns 500, Claude Code looks at the stack trace and fixes it.

### Either, depending on scope:

- **Small migrations (single SQL file, single execution).** Claude when validation against live data is needed; Claude Code when the migration is part of a larger feature being shipped.
- **Docstrings, README sections about implementation details.** Claude Code typically, because they're tightly coupled to the implementation it's writing.

## 4. Operational rules

These hold across sessions and agents.

1. **The design spec in `02_components/` is the contract.** Both agents read it. Only Claude writes it. Claude Code does not modify design docs to match what it built — if implementation reveals a spec gap, Claude Code surfaces the gap in a handoff note and Claude folds the change into the spec in the next orchestrator session.

2. **Source code never goes in the vault.** Illustrative snippets inside design docs are clearly fenced and labelled "illustrative — adapt to project conventions." This preserves the rule that vault is design and repo is code, with no overlap.

3. **No code-style "example" files in `src/`.** A file in `src/` is loadable, importable, and inevitably load-bearing. Putting an "example component" there means it gets imported and extended; the "this is just an example" intent dies the moment the file exists. Examples live in the design doc as fenced code, not in the source tree.

4. **Locked decisions and principles flow only through the vault.** A decision Claude Code makes during implementation that conflicts with `01_locked_decisions.md` must be surfaced as a handoff blocker, not silently adopted in the code. Claude reviews and either updates the decision ledger (with a new entry that supersedes the old) or asks Claude Code to revise.

5. **Append-only files stay append-only across agents.** `01_locked_decisions.md`, `03_failure_log.md`, and `03_handoffs/*` are never edited in place. New entries supersede old; old entries stay for audit.

6. **Every Claude Code session ends with a handoff file.** Goes in `03_handoffs/YYYY-MM-DD.md`. Documents: what was built, what verified, what failed, what spec gaps emerged, what decisions came up. Claude reads this on the next session.

7. **Production deploys pull from local `main`; no server-only source edits.** Local `main` is the source of truth. Commit locally first, pull that commit on the EC2 repo, then run/restart. The production checkout must not contain a repo-local `.env`; `DATABASE_URL` is only the bootstrap connection and operational settings/secrets come from PostgreSQL `system_settings`. See root `CLAUDE.md` / `AGENTS.md` for the short-form rule.

## 5. Handoff conventions

### Claude → Claude Code (design hands off to implementation)

The artifact: a complete `02_components/<phase>_<name>.md` spec containing:
- The goal in one paragraph.
- The API contract (request/response shapes, endpoint signatures).
- The data shape on disk (any DB columns/views being added).
- Hard constraints (must-not-violate rules from locked decisions).
- Illustrative code snippets where they convey intent faster than prose.
- An explicit "out of scope" list.
- The acceptance criteria for "done."

Claude Code starts from this file. It should not need to read `01_locked_decisions.md` or `02_principles.md` to do the work, because the relevant constraints are already in the spec.

### Claude Code → Claude (implementation hands back to orchestrator)

The artifact: a handoff file in `03_handoffs/YYYY-MM-DD.md` containing:
- Components completed (file paths).
- Tests passing / failing.
- Decisions made during implementation that weren't in the spec (these are the ones Claude has to ratify or rewrite).
- Spec gaps encountered (with proposed resolution).
- The next session's recommended focus.

### After successful implementation

The relevant `02_components/<phase>_<name>.md` gets a one-line retrospective at the top:
> `v1.0 (YYYY-MM-DD) — implemented. Notes: <one-line summary>.`

This matches the existing vault convention for `02_components/` ("Write-once. Once executed, append a one-line retrospective at the top.").

## 6. Anti-patterns to avoid

These have caused real problems on similar projects:

- **"Just one example component in `src/`"** — gets imported and becomes load-bearing. Always belongs in the design doc as a fenced snippet, never in the source tree.
- **Claude Code modifies the design doc to match what was built** — bypasses review, breaks the audit trail. The fix: Claude Code surfaces the discrepancy in the handoff; Claude updates the spec.
- **Claude writes 20+ React files in chat** — exceeds context coherence budget. The first half is fine; the second half degrades and gets truncated. The fix: Claude writes the spec, Claude Code writes the files.
- **Either agent re-deriving the split each session** — wastes tokens, risks inconsistency. The fix: this file.
- **Locked decisions modified in place across sessions** — destroys the audit trail. Always supersede with a new entry; never edit the old.
- **Backfill SQL run by Claude Code without live validation** — Claude Code can run SQL, but it can't iteratively sanity-check the result against real distributions. SQL that touches production data goes through Claude.
- **Vault docs imported by source code** — would couple design and implementation in a way that breaks both. The vault is read by humans and agents, not by the running system.

## 7. Session-start checklist

For any Claude session on this project:

1. Call `openclaw-guide:list_orientation_docs`.
2. Read in order: `_conventions.md`, then this file (`06_teamwork_protocol.md`), then the most recent handoff in `03_handoffs/`.
3. Read targeted vault files based on the user's first message — design docs if the task touches architecture, locked decisions if it touches a known controversy, runbooks if it touches operations.
4. Do not propose work that contradicts a locked decision without flagging it as a proposal to override.

For any Claude Code session on this project:

1. Read the relevant `02_components/<phase>_<name>.md` spec in full before writing any code.
2. Read the most recent handoff if continuing prior work.
3. Do not read or modify vault files except the spec and the handoff being written.
4. End the session by writing a handoff file.
