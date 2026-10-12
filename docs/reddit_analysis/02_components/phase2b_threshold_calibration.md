# Added Component: Per-Run Threshold Calibration

> **Status:** Implemented 2026-05-06 (commit pending)
> **Trigger:** Discovered during B-prep.6 (run_id=10) — run9-calibrated thresholds cut 92% of B2C clusters, far above the design-intent ~10%. Root cause: distributions shift between corpora.
> **Companion docs:** `00_system_design_v2.md`, `01_database_schema_v2.md`, `02_temporal_plan.md`

---

## 1. Why this component exists

The candidate gate uses three deterministic thresholds on top of the audience/coherence gate:

```
min_thread_diversity  = 0.50   # cuts viral-thread echo chambers
min_author_diversity  = 0.60   # cuts bot/repeat-poster artifacts
min_specificity_mean  = 0.15   # cuts generic venting
```

These were calibrated against **run_id=9** (full 18-month corpus, 426K pain_objects, 1542 clusters) at p10 (cut bottom 10%) and committed as defaults in `src/clustering/candidate_gate.py::DEFAULT_THRESHOLDS`.

When applied unchanged to **run_id=10** (90-day rolling window, 815K pains in window, 270K clustered into 2594 clusters), the same thresholds cut 1395 → 105 clusters (92% gate-cut). Empirical p10 had shifted:

| Metric | run9 p10 | run10 p10 | Drift |
|---|---|---|---|
| `thread_diversity` | 0.53 | **0.21** | -60% |
| `author_diversity` | 0.76 | 0.77 | +1% |
| `specificity_mean` | 0.15 | **0.06** | -60% |

Two metrics drifted ~60%. Hardcoded thresholds become wrong.

## 2. Why distributions shift between runs

Cluster-shape metrics depend on:

1. **Corpus size and density.** A 90-day window has higher pain density than the full 18mo corpus → HDBSCAN forms larger, denser clusters → diversity ratios (`distinct_threads / member_count`) drop mechanically.
2. **Subreddit activity rebalance.** A busy month in r/personalfinance vs. r/Parenting changes the average specificity_mean (spaCy-based heuristic with sub-specific defaults).
3. **Topical concentration.** Viral/breaking events compress cluster shape (many pains from a single event → low thread diversity).
4. **HDBSCAN bias toward larger clusters.** More embeddings → UMAP has more local structure → bigger clusters.

Between two consecutive monthly 90d windows, expected drift is **5–25%** (small, manageable). Between a 90d window and the full corpus, drift was **60%** (a different regime entirely — don't reuse).

## 3. Component: `--calibrate` mode in `candidate_gate`

Added to `src/clustering/candidate_gate.py`:

```bash
# Dry-run: prints suggested p10 thresholds, doesn't apply
python -m src.clustering.candidate_gate --run-id 10 --calibrate --dry-run

# Apply: calibrates and runs the gate
python -m src.clustering.candidate_gate --run-id 10 --calibrate

# Custom percentile (default 0.10 = bottom 10%)
python -m src.clustering.candidate_gate --run-id 10 --calibrate --percentile 0.05
```

### Algorithm

1. Define **eligible population** = clusters that pass everything *except* the three diversity/specificity gates (i.e. audience-correct + member≥30 + coherence≥0.4).
2. Compute `percentile_cont(p)` of `thread_diversity`, `author_diversity`, `specificity_mean` over that eligible population.
3. Use those three values as the new thresholds. p50 of each is also returned for sanity-check (the gap between p_x and p50 tells you whether the chosen percentile is meaningful or noisy).
4. Persist into `analysis_runs.candidate_thresholds` (already done by `compute_candidates`).

The eligible-population scoping matters: calibrating against ALL clusters (including audience-mismatch and tiny ones) would let those drag the p10 down spuriously.

### Output sample (run_id=10)

```
Calibration on run_id=10 (eligible_n=1395, p10):
  Suggested thresholds: {
    'audiences': ['B2C'], 'min_audience_confidence': 0.6,
    'min_member_count': 30, 'min_coherence': 0.4,
    'min_thread_diversity': 0.214,
    'min_author_diversity': 0.767,
    'min_specificity_mean': 0.059,
    'metrics_version': 'v1'
  }
  Reference p50: {
    'thread_diversity': 0.367,
    'author_diversity': 0.889,
    'specificity_mean': 0.096
  }
```

## 4. Operational policy (locked decisions)

| Decision | Value | Rationale |
|---|---|---|
| **When to calibrate** | Every monthly re-cluster | Default; safe and cheap. |
| **Percentile** | 0.10 (cut bottom 10%) | Matches the original design intent; user-approved. |
| **Drift tolerance** | If new p10 vs. previous p10 differs **>25%**, use new value; else reuse previous. | Stability over churn. <25% drift on three metrics ≈ same threshold regime. |
| **Eligible population** | audience B2C + conf ≥0.6 + member ≥30 + coherence ≥0.4 | Calibrating on the same population the gate operates on. |
| **Override** | `--thresholds <json>` overrides --calibrate's output | For experiments / threshold sweeps. |
| **Persistence** | Thresholds + metrics_version stored in `analysis_runs.candidate_thresholds` | Audit trail. |
| **Re-run viability LLM** | After every recalibration where the candidate set changes | The LLM step is keyed on `(viability_run_id, cluster_id)` so partial overlaps are idempotent. |

## 5. Integration into B-ops.4 (monthly orchestrator)

The monthly re-cluster orchestrator (`scripts/monthly_recluster.sh`, planned in `02_temporal_plan.md`) becomes:

```
cluster_all  → audience  → quality_metrics
            ↓
candidate_gate --calibrate           ← NEW (was: hardcoded defaults)
            ↓
quality_viability_llm
            ↓
mark new run as "current"
            ↓
pain_volume_weekly rebuild
```

Calibration is fast (single SQL aggregate, <1s) and idempotent. The added latency to the orchestrator is negligible.

## 6. What this component does NOT do

- **Doesn't auto-tune the percentile.** 0.10 stays unless explicitly overridden.
- **Doesn't track historical thresholds.** Each run only stores its own thresholds in `analysis_runs.candidate_thresholds`. Cross-run comparison is a manual SQL today; could be a `v_threshold_history` view in a future iteration.
- **Doesn't auto-recalibrate `min_member_count` or `min_coherence`.** Those are fixed by design (HDBSCAN minimum + observed coherence floor); no evidence yet that they need per-run tuning.
- **Doesn't affect the LLM tier-2 step.** `quality_viability_llm` operates on whatever cluster set has `is_candidate=TRUE`; recalibrating the gate just changes which clusters get LLM-scored.

## 7. Why this didn't get caught earlier

The original Phase-1-baseline → Phase-2-A pipeline was a single-run system. We didn't have a second run to expose the distribution-shift problem. Discovering it on the *first* monthly re-cluster (run_id=10) is the design working as intended — the stop-and-review checkpoint after B-prep.6 specifically asked us to verify quality vs. run_id=9, which is what surfaced the issue.

## 8. References

- Skill: `data-science/openclaw-v2-postgres-migration` §10 ("Always observe distributions before committing thresholds")
- Code: `src/clustering/candidate_gate.py::calibrate_thresholds`
- DB: `analysis_runs.candidate_thresholds` (jsonb, per-run)
- Plan: `02_temporal_plan.md` § 3 (B-ops.4 monthly orchestrator)
