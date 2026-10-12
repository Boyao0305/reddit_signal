
> How this vault is organized and maintained.
> Read this before adding or moving any file.

## How to use the `openclaw-guide` MCP tools

This vault is exposed to AI agents through the `openclaw-guide` MCP server. It provides two tools:

- **`list_orientation_docs`** — returns a JSON index of every readable orientation document in this vault (top-level files, design docs, handoffs) with a one-line description for each. Call this first; it's cheap and tells you what's available without burning tokens reading whole files.
- **`read_orientation_doc(name)`** — fetches the contents of one document by its vault-relative path (e.g. `_conventions.md`, `01_design/system_design.md`, `03_handoffs/2026-05-12.md`). Only names that appeared in `list_orientation_docs` are accepted.

Recommended pattern when starting a new session: call `list_orientation_docs`, read `_conventions.md` first (this file), then pull only the specific files relevant to the task at hand. Handoff files are listed newest-first — the top one is the most recent session's notes. For full filesystem access (writing, searching arbitrary files), use the separate `fs` MCP server instead; `openclaw-guide` is read-only and scoped to the orientation set.

you should read the top levels files first. for the handoff document, only read the latest one to start. if you have inrecognized information, you can read others.

## Top-level files (read first, in order)

| File                       | Purpose                                                              | Mutability                                                                      |
| -------------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `_conventions.md`          | This file. Rules for the vault itself.                               | Edit only when the structure changes.                                           |
| 01_design/system_design.md | system design                                                        | update only with major system structure change                                  |
| `01_locked_decisions.md`   | Append-only ledger of every approved design decision.                | Append only. Never rewrite past entries.                                        |
| `02_principles.md`         | How-we-decide rules: architecture, calibration, execution, scope.    | Edit when a new principle emerges. Old principles stay; mark superseded inline. |
| `03_failure_log.md`        | Short cautionary entries about things that broke.                    | Append only.                                                                    |
| `04_open_questions.md`     | Parking lot for things noticed but deferred.                         | Items move out when answered (into decisions or principles).                    |
| `06_teamwork_protocol.md`  | How Claude (orchestrator) and Claude Code (implementer) divide work. | Living. Edit when the working pattern changes.                                  |

## Folders

| Folder                          | Holds                                                                                                                                             | Versioning                                                                                                                                                                                 |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `01_design/`                    | Current architecture documents (system_design.md, database_schema.md).                                                                            | Edit in place with a CHANGELOG section at the top. Fork to `design/archive/vN_*.md` only when the structure genuinely changes (new layers, broken backwards-compat).                       |
| `02_components/`                | One file per discrete piece of work (a plan, an implementation log).                                                                              | Write-once. Once executed, append a one-line retrospective at the top. New work goes in a new file.                                                                                        |
| `03_handoffs/`                  | One file per session boundary, named by date (`YYYY-MM-DD.md`).                                                                                   | Time-stamped. Never edited after the next session starts.                                                                                                                                  |
| `04_runbooks/`                  | Steady-state operational procedures (e.g. `monthly_recluster.md`).                                                                                | Living. Edit in place when procedures change.                                                                                                                                              |
| `05_potential_implementations/` | Deferred design plans not in active scope. Reference-only until reopened.                                                                         | Living. Each file documents its own reopening triggers; edit only when a trigger fires or the deferred design itself changes.                                                              |
| `07_context_for_claude/`        | Session-startup primers written by Claude for future Claude sessions on this project. One file per future session, named `YYYY-MM-DD_<topic>.md`. | Add-on-create. Old primers stay for audit; never edited after the next session has consumed them. The user pastes the contents into the next session manually — files are not auto-loaded. |
| `last_version_dont_read/`       | Archive of pre-restructure docs.                                                                                                                  | Frozen. Hermes and future-me ignore this.                                                                                                                                                  |

## When to fork vs edit in place

**Edit in place** when the change is additive, refines an existing concept, or fixes a mistake. Add a CHANGELOG entry at the top of the file: `vN.M (YYYY-MM-DD) — what changed and why`.

**Fork** (move current version to `archive/`, start fresh) only when:
- The layer model changes (e.g. v1 had L6 separately, v2 fused it into L4).
- A reader looking for "the current architecture" would be confused if the old content stayed.
- The change is large enough that a CHANGELOG header would be longer than the doc.

Forking is rare. The default is in-place edit.

## Append-only files

`01_locked_decisions.md`, `03_failure_log.md`, and `handoffs/*` are append-only. Past entries are immutable. If a decision is later reversed, add a new entry that supersedes the old one — do not edit the original. The audit trail is the value.

## What goes where (when in doubt)

- A design choice the user explicitly approved → `01_locked_decisions.md`.
- A general rule that applies across decisions → `02_principles.md`.
- Something that broke and what we learned → `03_failure_log.md`.
- A question we noticed but deferred → `04_open_questions.md`.
- A specific piece of implementation work → `02_components/<phase>_<name>.md`.
- The current state of the system at session end → `03_handoffs/<date>.md`.
- An ongoing operational procedure → `04_runbooks/<procedure>.md`.
- A design plan that's intentionally not being built now → `05_potential_implementations/<area>/<name>.md`.
- A rule about how Claude and Claude Code divide work → `06_teamwork_protocol.md`.
- A primer for a planned future Claude session on a specific topic → `07_context_for_claude/<date>_<topic>.md`.

## Conventions for Hermes (and future agents)

When working on this project, load these files at session start in order: `_conventions.md → 06_teamwork_protocol.md → 01_locked_decisions.md → 02_principles.md → 01_design/system_design.md`. The handoff for the most recent session goes last. Do not propose work that contradicts a locked decision without explicitly flagging it as a proposal to override. Do not edit append-only files in place.

Claude Code sessions follow the abbreviated startup in `06_teamwork_protocol.md §7` — they read the relevant `02_components/` spec and the latest handoff, not the full vault.
