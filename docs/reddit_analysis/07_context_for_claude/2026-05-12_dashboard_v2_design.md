# Primer for next Claude — writing dashboard_v2_design.md

This file is a session-startup primer stored in `07_context_for_claude/` per the convention in `_conventions.md`. The user will paste its contents into the next Claude session that picks up the dashboard v2 design work. Read it once at the start of that session, then proceed with the file reads in §1. Your output (`02_components/dashboard_v2_design.md` plus the locked-decision entries listed in §4) goes in the standard vault locations, not back into this file.

---

## 1. What to read first, in order

1. `/home/ubuntu/vaults/openclaw/_conventions.md` — vault rules. Note this was updated 2026-05-12; new entries for `05_potential_implementations/` and `06_teamwork_protocol.md`.
2. `/home/ubuntu/vaults/openclaw/06_teamwork_protocol.md` — how Claude (your role) and Claude Code (the implementer that consumes your design doc) divide work. New file as of 2026-05-12. **Read §5 (handoff conventions) before writing the design doc — that's the structural template.**
3. `/home/ubuntu/vaults/openclaw/01_design/system_design.md` — v3, rewritten 2026-05-12. Carries current architecture, operational state, and the design decisions from this session baked into §5.
4. `/home/ubuntu/vaults/openclaw/01_design/database_schema.md` — schema reference. **Stale on row counts** (says `pain_objects` 426K, actually 1.58M; `signal_events` 4M, actually 45.6M). Verify against live DB if numbers matter.
5. `/home/ubuntu/vaults/openclaw/01_locked_decisions.md` — especially #8 (hard binary gates only), #14 (no composite scores — soft-superseded by what this session decided; you will write the supersession entry), #16 (per-run threshold calibration), #23 (centroid threshold 0.70).
6. `/home/ubuntu/vaults/openclaw/02_principles.md` — especially "no premature composites" and the skepticism patterns at the bottom.
7. `/home/ubuntu/vaults/openclaw/04_open_questions.md` — especially #6 (composite ranking, empirical reconsideration), which is what this session's exploratory composite operationalizes.
8. `/home/ubuntu/vaults/openclaw/05_potential_implementations/signal_generation/nonlinear_composite.md` and `backtest_harness.md` — both new 2026-05-12. The dashboard composite is the linear precursor to both.
9. `/home/ubuntu/vaults/openclaw/03_handoffs/2026-05-12.md` — operational handoff from earlier the same day (SIGTERM mystery and the OpenRouter token-burn fix). Background context only.
10. Legacy v1 dashboard has been removed; current dashboard lives at `/home/ubuntu/github/openclaw_boyao_1_llmanalyse/src/dashboard/`. Read `api.py`, `frontend/package.json`, `frontend/src/App.tsx`, `frontend/src/api.ts`, `frontend/src/types.ts`, and the files in `frontend/src/components/`. **v1 is no longer present, so focus on the current v2 structure.** It reads from `v_market_opportunities` and `v_cluster_velocity`. No queries against the deprecated columns on `pain_clusters`.
11. `/home/ubuntu/github/openclaw_boyao_1_llmanalyse/src/temporal/aggregate_weekly.py` — the pipeline file that needs the share_global computation added. Read both REBUILD_SQL and INCREMENTAL_SQL.

Then run a live-DB check via `openclaw-pg`: query `pg_get_viewdef('v_cluster_velocity')` to see the current view DDL verbatim, query `pain_volume_weekly` for the NULL status of `share_global` and `share_field` and the row-per-week structure.

## 2. What was decided in this session

These choices were approved by the user across a long design conversation. They are partially written into `system_design.md §5` but the design doc you write is where they get codified at implementation-level detail. You don't need to re-derive any of them.

### 2.1 Dashboard v2 setup

Fresh project at `src/dashboard/`. Stack: React 18 + Vite + TypeScript + Tailwind + FastAPI. The legacy v1 dashboard has been removed. Single user (the founder); no auth. Reads exclusively from `v_market_opportunities` and `v_cluster_velocity`. No queries against the deprecated columns on `pain_clusters`.

### 2.2 Three-signal velocity model

For each cluster the dashboard renders three trend signals, **never collapsed into one velocity number**:

- `pain_count_last_4w` — pure quantity, "how busy right now," never NULL.
- `velocity_4w_ratio_normalized` — `(cluster_last_4w / cluster_prior_4w) / (corpus_last_4w / corpus_prior_4w)`. Reads 1.0 = corpus pace, > 1.0 = growing vs corpus, < 1.0 = declining vs corpus. NULL when either window has < 3 pains.
- `slope_per_week` — already in `v_cluster_velocity`, linear regression on (week_index, pain_count).

The raw `velocity_4w_ratio` (already in the view) is **not** displayed as a headline — it appears only as a secondary "before normalization" number in the detail panel. Reasoning: in the current dataset (March 2026 corpus surge), the raw ratio is structurally biased downward — mean 0.40, p90 0.69 — making 90%+ of clusters look like decline that isn't really decline.

### 2.3 Partial-week dashed convention

Sparklines and time-series charts render the most recent 1–2 weeks as a dashed line segment to mark them as incomplete. The current week is in progress; the week-before may still be filling via daily centroid assignment. This is a universal rendering convention, not a special case.

### 2.4 Cluster table row layout

Left to right: `label` · `audience badge` · `member_count` · `last_4w` · `sparkline (15w)` · `norm_ratio chip` · `slope chip` · quality columns · truncated `v_rationale`. Sparkline is 80×24px inline SVG; last 2 segments dashed. Chips colored by direction (amber declining, teal growing, gray flat). Recharts will be too slow at 740+ rows for inline sparklines — use inline SVG path generation, or evaluate uPlot if interactivity grows. Mocked up in chat earlier this session; you'll need to redescribe the mockup in the design doc since the user can't paste it in.

### 2.5 Cluster detail panel

Full chart ~600×220 with three view toggles: raw `pain_count` / `share_global` / log scale. Overlays: cluster median (dotted), corpus weekly total on faint secondary axis. The 4w / prior_4w windows lightly shaded by default; hovering the norm_ratio chip lights them up and shows the underlying arithmetic. Below the chart, a small metric strip showing the normalized ratio (headline), slope (headline), and raw ratio (labeled "before normalization").

### 2.6 Exploratory composite

A parameterized weighted-sum composite is allowed for display ordering only. Three guardrails:

1. The score never enters `is_candidate` or any gate. Display only.
2. Weights are visible at all times in a slider panel. There is no canonical score.
3. The score is session-local. Defaults reset on reload.

This softens Decision #14 ("no composite scores in user-facing outputs"). Composite dimensions are the columns of `v_market_opportunities`, **within-run percentile-transformed** before weighting so dimensions on different scales are commensurable. Velocity participates via one slider (suggest defaulting to `slope_per_week` since it's the most stable of the three); the row still displays all three velocity signals independently. Visual rule: velocity stays on the right side of the row; the composite score column sits among the quality columns on the left.

### 2.7 share_global / share_field backfill plus pipeline edit

Two pieces of work, owned differently per the teamwork protocol:

1. **One-shot SQL backfill** for the two NULL columns on `pain_volume_weekly`. Yours to run via `openclaw-pg` and verify against live distributions. Formula:
   - `share_global = pain_count / SUM(pain_count) OVER (PARTITION BY cluster_run_id, week_start)`
   - `share_field = pain_count / SUM(pain_count) OVER (PARTITION BY cluster_run_id, week_start, audience_type)`
2. **Pipeline modification** to `src/temporal/aggregate_weekly.py`. The current `INCREMENTAL_SQL` only writes `pain_count`; without a fix the daily cron leaves share_global at NULL for the current week and silently doesn't recompute it for the prior week as daily-assignment adds pains. The fix is a per-week recompute pass that runs after the upsert (per-row UPDATE doesn't work — adding one pain to cluster A changes the denominator for everyone in that week). This belongs to Claude Code, specified as a diff in the design doc.

### 2.8 v_cluster_velocity extension

Extend the existing view in place (CHANGELOG entry, no fork). Add `velocity_4w_ratio_normalized` as a new column. One extra CTE over the same `pain_volume_weekly` rows partitioned by week_start to compute the corpus 4w/prior_4w denominator, one extra column on the SELECT. Backward compatible with v_market_opportunities (which doesn't currently reference this column).

## 3. The main deliverable

Write `/home/ubuntu/vaults/openclaw/02_components/dashboard_v2_design.md`. Per `06_teamwork_protocol.md §5`, the structure is:

- **Goal** in one paragraph.
- **API contract.** Endpoint signatures and request/response shapes for the v2 backend. The endpoints needed: list clusters (filtered + paginated, reads `v_market_opportunities`), get cluster detail (joins sample pains from `pain_cluster_members`), get cluster velocity series (reads `pain_volume_weekly` for that cluster across all 15 weeks), list runs, list distinct subreddits. **Do not carry forward** the v1 endpoints `scatter-data`, `competition-overview`, or `cluster-competition` — they all read deprecated columns.
- **Data shape on disk.** The share_global backfill SQL (final, executable). The v_cluster_velocity extension SQL (final). The pipeline diff for `aggregate_weekly.py` (specified concretely enough for Claude Code to apply).
- **Hard constraints.** Pulled from locked decisions plus the new softening of #14. List them explicitly so Claude Code doesn't have to read the decision ledger.
- **Illustrative code snippets** — sparkline SVG path generator (10–20 lines), composite-score percentile-transform pseudocode, partial-week dashed rendering. Each marked "illustrative — adapt to project conventions." Per teamwork protocol §4 rule 2, source code never goes in the vault except as fenced illustrative snippets.
- **Out of scope.** No L5 competition (deferred), no historical pain backfill (deferred), no auth, no real-time websocket updates, no population-level "trending" view (Open Question #6 says wait), no AI-assisted cluster labeling.
- **Acceptance criteria.** What "done" means: user can browse all 740 viable B2C clusters in a sortable + filterable table, sort by the exploratory composite, adjust weights and see live re-ranking, click a row to open the detail panel with sample pains and the velocity chart, switch the velocity chart between raw / share_global / log views.

Total length: probably 2000–3500 words. The system_design v3 is a reasonable tone reference — prose carries rationale, snippets carry the trickier rendering ideas, the API contract section is precise without being verbose.

## 4. Side deliverables (after the main spec is approved)

### 4.1 Locked-decision entries to write in `01_locked_decisions.md`

These are append-only entries; pick numbers continuing from the highest existing (#28 at last check, so start at #29). Suggested wording:

- **Composite softening:** A parameterized, transparent, non-gating composite score is permitted for display ordering in user-facing dashboards. Hard gates (#8) and threshold-based gating (#16) remain unchanged. This entry softens #14 in the display-ordering sense only; the prohibition on canonical or gating composites stands.
- **Three-signal velocity rendering:** Velocity is rendered as three independent signals (`pain_count_last_4w`, `velocity_4w_ratio_normalized`, `slope_per_week`), never collapsed into one. The raw `velocity_4w_ratio` is shown only as a secondary "before normalization" number in detail views.
- **Partial-week dashed convention:** The most recent 1–2 weeks in any time-series rendering use a dashed line segment to mark them as incomplete. Applies to sparklines, detail charts, and any future temporal view.

### 4.2 Open-question entries to write in `04_open_questions.md` if unresolved during spec

- Sparkline rendering technology — inline SVG path (DIY, fast at 740 rows) vs uPlot (small lib, decent at scale) vs Recharts (already in v1 deps, sluggish at this row count). Default suggestion: inline SVG path, only move to uPlot if interactivity grows.
- Default weights for the composite — equal weights at v0 are safe but uninteresting. Whether to seed with the founder's stated priorities (and how to elicit them) is a UX question the spec should address briefly even if not fully resolved.

## 5. Before locking the spec — ask the user

The user has pushed back on proposals and rewritten earlier designs across this session. They will want to review your draft before it goes to Claude Code. Specifically, ask:

- Whether the v2 endpoint scope above is right (especially: which v1 endpoints to NOT carry forward).
- Sparkline tech choice (inline SVG vs uPlot vs Recharts).
- Whether to write the locked-decision entries in this same session after the design doc is approved, or split into a separate cleanup pass.
- Whether the share_global backfill SQL gets run in this session (yours to execute via openclaw-pg) or held back until Claude Code is ready to also land the pipeline edit (so the future-data hole doesn't reopen between the backfill and the pipeline fix).

## 6. Style notes

Sentence case, not Title Case. Prose over bullets where possible (the user has stated this preference explicitly — see `_conventions.md` and the existing system_design v3 as tone references). Two weights only (regular and bold), no 600 or 700 weight. Code snippets fenced and labelled "illustrative — adapt to project conventions." The audience for the design doc is Claude Code primarily, then any future Claude orchestrator; it does not need to read other vault files to do the work, because the relevant constraints are already in the spec.

## 7. Things to verify yourself before writing

- The `share_global` columns are actually still NULL (`SELECT COUNT(share_global) FROM pain_volume_weekly` should be 0).
- The current `v_cluster_velocity` DDL matches what's described in 2.2 above (`SELECT pg_get_viewdef('v_cluster_velocity'::regclass, true)`).
- The viable B2C count is still 740 (`SELECT COUNT(*) FROM viability_scores WHERE viability_run_id = 4 AND is_real_pain AND is_software_solvable`).
- Cluster baseline is still `analysis_runs.id = 10` (`SELECT id, cluster_count FROM analysis_runs WHERE id = 10`).

If any of these drift from what's described here, surface the drift to the user before writing — it means something operationally changed between sessions and the assumptions in this primer may need updating.

---

End of primer. Begin by reading the files in §1, then verify §7, then draft the spec.
