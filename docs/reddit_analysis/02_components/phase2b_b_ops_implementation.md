# B-ops Implementation Log

> **Date:** 2026-05-07
> **Branch:** `v2-phase1` (commits `69ada85`, `72e25b9`, `ad6fbe2` — pushed)
> **Status:** B-prep.7 + B-prep.8 + B-ops.1 + B-ops.2 + B-ops.3 ✅ live; cron installed

---

## What landed

| ID | Artifact | Notes |
|---|---|---|
| B-prep.7a | `src/temporal/aggregate_weekly.py` | 31,538 weekly rows for run_id=10 |
| B-prep.7b | `migrations/v2_009_pain_volume_weekly.sql` (`v_cluster_velocity` view) | 4w/prior-4w ratio + slope_per_week + last_4w_count |
| B-prep.7c | sparkline sanity check | mid-March peak genuine; current-week 6K-only is partial-data artifact (self-corrects once daily ops fills the trailing week) |
| B-prep.8 | `migrations/v2_010_cluster_lineage.sql` + `src/clustering/lineage.py` | run_9→run_10 lineage: 390 continued, 2 merged, 0 split, 2203 born, 1150 died — sensible (full-corpus → 90d shrinks vocabulary, 90d window adds many new clusters) |
| B-ops.1 | `scripts/b_ops_1_daily.sh` | 5 phases, flock, dry-run, log to `/var/log/openclaw/`, smoke test ✅ |
| B-ops.2 | `src/clustering/assign_to_centroid.py` | `--calibrate`, `--dry-run`, `--threshold`, `--since`, `--limit`. ivfflat index added to `pain_clusters.centroid` |
| B-ops.3 | `scripts/b_ops_3_monthly.sh` | 7 phases (recluster, gate calibrate, gate apply, viability, lineage, threshold calibrate, mark current) |

Cron installed:
```
5 4 * * *  scripts/b_ops_1_daily.sh
0 5 1 * *  scripts/b_ops_3_monthly.sh
```

---

## Calibration results (run_id=10)

**Centroid-assignment threshold sample (n=5000 each):**
- TP distribution (assigned pains → their own centroid): p10=0.60, p50=0.74, p90=0.84
- Noise distribution (unassigned pains → nearest centroid): p10=0.53, p50=0.66, p90=0.78
- **Overlap warning:** noise p90 ≥ TP p10 → not cleanly separable

**Decision: threshold = 0.70**
- Module-suggested 0.60 was too permissive (noise contamination)
- 0.75 was too strict (would lose ~50% of legitimate signal)
- 0.70 sits at the inflection point: ~70% TP recall, ~70% noise rejection
- Daily assignment is a *bridge* to next monthly recluster — precision > recall (missed pains are caught at month boundary)

**Smoke-test validation:** Real-world assignment rate at threshold=0.70 was **28.1%** (76,436 of 272,001 unassigned pains), higher than the dry-run 18.5% as predicted (dry-run sample was HDBSCAN-rejected noise-enriched).

---

## Smoke test (2026-05-07)

`bash scripts/b_ops_1_daily.sh --limit 100` — 920s wall-clock end-to-end.

| Phase | Result | Time |
|---|---|---|
| 1 — Reddit fetch | 0 new posts (already current) | 0s |
| 2 — Extract+score+embed | 0 unprocessed | 6s |
| 3 — Project to signal_events | 254,471 new events (29,952 posts + 224,519 comments backlog) | 817s |
| 4 — Centroid assignment | 76,436 assigned at threshold=0.70 (28.1%) | 51s |
| 5 — Weekly aggregate (incremental) | 1,252 rows upserted | 0s |

---

## Bugs found and fixed

1. **`SELECT MAX(id) FROM analysis_runs WHERE status='complete'`** — the `status` column doesn't exist in this schema. Replaced with `cluster_count IS NOT NULL` (set on successful completion). Patched in both `b_ops_1_daily.sh` and `b_ops_3_monthly.sh` (commit `ad6fbe2`).
2. **First Claude Code subagent killed by stdin warning at 3s** (exit 143). Fixed by adding `< /dev/null` to the launch command. Lesson: long-running headless `claude -p` invocations need explicit stdin closure.

---

## Subagent cost summary

| Subagent | Task | Cost |
|---|---|---|
| Sonnet 4.6 | B-prep.8 lineage | $0.37 |
| Sonnet 4.6 | B-ops.2 assignment | $0.89 |
| Sonnet 4.6 | B-ops.1 daily wrapper | $0.29 |
| Sonnet 4.6 | B-ops.3 monthly orchestrator | $0.57 |
| **Total** | | **$2.12** |

For ~1,200 LOC of production scripts. ROI excellent.

---

## Open items (not blocking)

- **B-ops.5 — operational runbook** (`03_ops_runbook.md`): defer until after first monthly orchestrator run (June 1) so we have observed failure modes.
- **Dashboard v2 patch:** deferred per user instruction until after competition design.
- **Threshold revisit:** monthly orchestrator logs the calibrated suggestion but doesn't auto-apply. After 2-3 monthly runs, examine drift and decide whether 0.70 should be lowered/raised.
- **`finished_at` not populated** by clustering scripts — minor; we use `cluster_count IS NOT NULL` as the completeness flag instead. Consider plumbing `finished_at = NOW()` into the recluster scripts for cleaner semantics later.
