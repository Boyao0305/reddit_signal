# HANDOFF — 2026-05-12 (implementation: dashboard v2)

> **Predecessor:** `2026-05-12_design.md` (same date, design session).
> **Session type:** Implementation (data + API + frontend). Phase 2C complete end-to-end.
> **Status:** All acceptance criteria §8.1–§8.24 either verified live against the DB or covered by the test suite. Servers running locally for the user to interact with.

---

## What this session produced

### DB migrations applied (all four, in order, verified)

| Migration | What it does | Verified by |
|---|---|---|
| `v2_011_share_global_backfill.sql` | Backfills `share_global` / `share_field` on `pain_volume_weekly` rows for run 10. | §8.1 — 0 NULL share_global across 31,995 rows; per-week sums to 1.0000. |
| `v2_012_v_cluster_velocity_normalized.sql` | Recreates `v_cluster_velocity` with `velocity_4w_ratio_normalized` column. | §8.3 — 611 of 740 viable B2C (82.6%) have non-null normalized ratio; corpus_ratio = 0.348 matches the design-session prediction. |
| `v2_013_v_market_opportunities.sql` | Creates the unified ranking surface. | §8.4 — 740 B2C clusters with GUCs (cluster_run_id=10, viability_run_id=4, classifier_version='v1'). |
| `v2_014_analysis_runs_status.sql` | Adds `status` column (live/historical/legacy) + deterministic backfill. | §8.23 — exactly one 'live' row (id=10), 7 'legacy' (matching is_legacy=TRUE), 1 'historical' (id=9). Idempotent on rerun. |

### Repo files created / modified

| Path | Owner | What |
|---|---|---|
| `src/temporal/aggregate_weekly.py` | Hermes | Edited: added `RECOMPUTE_SHARES_SQL` constant + call after rebuild/incremental SQL. §8.2 — incremental×2 keeps `share_global IS NULL` at 0; returns `shares_updated: 31995`. |
| `src/dashboard_2/{__init__.py, api.py, db.py, scoring.py, requirements.txt}` | Claude Code | FastAPI app (7 endpoints), psycopg2 GUC-aware pool, percentile-rank engine, single source of truth for 14 dimensions. |
| `tests/test_dashboard_v2_api.py` | Claude Code | 9 integration tests against live DB. All pass. |
| `src/dashboard_2/frontend/**` | Claude Code | Vite + React + TS + Tailwind frontend, 22 files (components, hooks, lib, tests). |

### Vault files this session

| Path | Action |
|---|---|
| `02_components/phase2c_dashboard_v2.md` | Retrospective line appended at top per `_conventions.md` `02_components/` convention. Migration v2_013 was patched mid-session per Decision #33 below. |
| `01_locked_decisions.md` | Appended decision #33 (viability gate uses `IS TRUE`, not `COALESCE`-to-true). |
| `03_failure_log.md` | Appended two entries (sparkline null-current-week bug + view-vs-spec off-by-one). |
| `03_handoffs/2026-05-12_implementation.md` | This file. |

No other vault files touched. No design docs amended in-place during implementation (per `06_teamwork_protocol.md §4.1`).

---

## Spec-vs-implementation deltas (for the next orchestrator pass)

These were resolved during implementation. The spec at `02_components/phase2c_dashboard_v2.md` is internally consistent against the implemented behavior but the prose in a few spots could be tightened next time Claude (orchestrator) edits the file.

1. **§3.3 + v2_013 viability gate semantics.** Spec wrote `pc.is_candidate ∧ vs.is_real_pain ∧ vs.is_software_solvable`. The first draft of `migrations/v2_013` used `COALESCE(vs.is_real_pain, true) AND COALESCE(vs.is_software_solvable, true)`, which made the view return 741 instead of the spec's 740 because one B2C candidate (cluster 16780) had no row at all in `viability_scores` for the targeted run. After user input, switched to `vs.is_real_pain IS TRUE AND vs.is_software_solvable IS TRUE`. Now: shown ⇔ evaluated AND passed both gates. Spec §3.3 wording is correct; the migration file's comment block was also updated to reflect the chosen semantics. **See locked decision #33.**

2. **§5.2 `/runs` payload schema.** Per §8.23 the response must carry `status` per cluster run; the schema text in §5.2 still lists the older field set. The implementation includes `status`; one-line spec doc fix next pass.

3. **§5.2 `/clusters/{id}` `audience_classifier_version` default.** Spec params list it without a default; the implementation defaults to `'v1'` to match `/clusters`.

4. **§5.2 sparkline aggregation rule.** Spec said "pain_count for the 15 most recent weeks" but didn't pin the rule for collapsing multiple (audience_type, function_tag) rows per (cluster, week). Implementation `SUM`s — same rule `v_cluster_velocity` uses for `pain_count_last_4w`, so the sparkline visually agrees with the chip values in the same row. Worth one explicit line in the spec.

5. **§5.2 `/clusters/{id}/velocity` `median_pain_count` basis.** Spec doesn't pin "median over what window." Chose all weekly history per Boyao's direction during build. One-line note next pass.

6. **§6.6 Sparkline algorithm.** Spec described "dashed covers indices ≥ partialFromIndex". In live data the partial week is null (no `pain_volume_weekly` row yet), which collapsed the dashed polyline to one point and silently violated §3.5. **Fix:** Sparkline now always renders an always-visible faint dashed vertical *boundary marker* between `partialFromIndex - 1` and `partialFromIndex`, plus the original dashed polyline when both endpoints have values. Regression test added. Spec §6.6 should describe the marker.

7. **§6.5 default-weights bootstrap.** FE needs a hardcoded fallback for first render before `/score-config` arrives, then snaps to the API values. Worth a sentence.

8. **§6.3 normalized-ratio tile arithmetic.** Spec says "explains the arithmetic" without pinning field names. Shipped: last_4w / prior_4w / raw ratio / corpus last/prior / normalized.

9. **§6.3 partial-week dashed line in detail chart.** To avoid a visual gap between solid history and dashed partial section, the partial-line series in `VelocityChart` includes the last solid point. (Same trick the row-level Sparkline uses.)

10. **`pain_count_last_4w` is a row-display field, not a scorable dimension.** Already correctly enforced by the implementation (only 14 dimensions in `score-config`, never 15). Spec §3.6 and §8.21 are consistent, but §8.21 test-case-5 prose mentions "pain_count_last_4w" as if it were a scorable dimension; correct reading is "in addition to the row display." Tightening recommended.

11. **Decisions deferred to implementation-time per §10 — all settled:**
    - Virtualization: `@tanstack/react-virtual`.
    - Test frameworks: vitest (FE), pytest (API).
    - No `react-query` / `swr` — plain fetch+useState was sufficient.
    - Tooltip mechanism: native `title` for slider rows; floating popover for ScoreCell breakdown.
    - Sparkline stroke color: `stone-600` (warm dark gray) — was indigo-600 initially, downgraded after vision-AI flagged it as too saturated against the cream palette.

---

## Visual design ratification

The user requested a Claude-product-style aesthetic during chunk 2 ("more stylish like Claude's design"). Final tokens, none load-bearing but worth knowing:

- App background: cream `#FAF7F2`. No outer table-card chrome — each cluster row is its own rounded-[20px] white bubble with a soft warm two-layer shadow, ~7px vertical gap between cards.
- H1: Source Serif 4 600 ("LLM Analyse"). Body: Inter.
- Audience pills: lowercase sky/violet/amber/stone soft pastels.
- Trend chips: emerald (up) / orange (down) / stone (flat).
- Sparkline: stone-600 stroke, 80×24 inline SVG, always-visible boundary marker at partial-week.
- Detail panel: slide-in from right, `min(640px, 50vw)`, same cream background — feels like a focused subview, not a modal.

---

## Acceptance summary

**Data layer ✅** — §8.1, §8.2, §8.3, §8.4, §8.23 all green against live DB.
**API layer ✅** — §8.5–§8.10 verified by curl; §8.22 + §8.21 invariant covered by 9/9 pytest cases.
**Frontend ✅** — §8.11 (build), §8.12 (dev server, 740 rows visible), §8.13 (sparkline dashed) ✅. §8.14 (re-rank <200ms on slider drag), §8.15 (all weights=0 → "—") ✅ via DOM probe. §8.16 (detail panel + 3 chart toggles + window highlight on tile hover), §8.17 (members paginates) ✅ via browser_vision against live UI. §8.18 (no v1 columns) ✅ via grep. §8.19 (no localStorage) ✅ by design. §8.20 (velocity sliders default 0) ✅. §8.21 (score.ts cases + source-of-truth invariant) ✅ via vitest.

---

## Operational state at session end

- Migrations applied to live DB. `pain_volume_weekly` shares populated; `v_market_opportunities` returns 740 with the canonical GUCs; `analysis_runs.status` populated with one 'live' row (id=10).
- Daily cron unchanged in shape — `aggregate_weekly.py` now also recomputes shares (safe addition: was previously NULLing them silently every run).
- Backup of `pain_volume_weekly` pre-backfill is at `/tmp/pvw_pre_v2_011_1778626451.sql` (1.3 MB). Per §10 step 0, can be discarded now that §8.1 verified. Leaving in place until next cleanup.
- Repo branch: `main`. **Nothing committed yet.** Files staged for commit:
  - `M  src/temporal/aggregate_weekly.py`
  - `?? migrations/v2_011_share_global_backfill.sql` through `?? migrations/v2_014_analysis_runs_status.sql`
  - `?? src/dashboard_2/` (full new module + frontend tree)
  - `?? tests/test_dashboard_v2_api.py`
- FastAPI dev server on `:8765`, Vite dev server on `:5173`, both bound `0.0.0.0`. CORS regex allows any `http://<ipv4>:<port>` origin (loosened from chunk-1 localhost-only). User reaching it at http://54.82.99.49:5173/.

---

## Spec gaps surfaced for next orchestrator pass (§5–§6 doc tightening)

Items 1–11 above. None of them are blockers; all are wording fixes that would help the next reader. They should fold into a small spec-touch-up commit alongside the retrospective line.

---

## Next session recommended focus

Two natural paths:

**(a) Boyao spends time with the dashboard.** Most useful immediate outcome. The FilterBar + score-weight sliders + detail panel are the surfaces designed to feed the next round of design decisions — e.g., does the composite reveal a stable weight profile, do velocity normalized-ratios behave as predicted, are there obvious cluster-quality failure modes in the v_rationale text that argue for a tier-1 threshold change next month?

**(b) Folding spec gaps + commit.** Quick orchestrator pass to apply the 11 wording fixes from above into `02_components/phase2c_dashboard_v2.md` (in-place edits with a CHANGELOG line per `_conventions.md`), then commit the entire chunk to `main` with a meaningful message. After this commit, the implementation retrospective is closed and the spec is internally consistent.

The deferred-design follow-ups (`05_potential_implementations/next_tasks/cluster_run_continuation.md`, `signal_generation/backtest_harness.md`, `signal_generation/nonlinear_composite.md`) become candidates after Boyao has used the dashboard for at least a week and a monthly re-cluster cycle has fired (next is 2026-06-01).
