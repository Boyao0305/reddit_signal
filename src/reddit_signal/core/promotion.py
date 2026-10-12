"""Should a freshly built monthly run replace the live one?

Two kinds of checks:

* **Completeness** — every stage wrote its rows. Never overridable: an incomplete run breaks the dashboard.
* **Magnitude** — the run is not a collapse compared with recent promoted runs. Overridable with
  ``force`` for deliberate changes (for example removing a source).

Magnitude thresholds come from experiment 0004 (runs 9–17, superseding 0002): against the median of
the last three promoted runs, the smallest ratio seen in a normal month is 0.36–0.45 on every metric;
the threshold is about half of that. The median keeps one bad run from becoming the new baseline. Over
that history the rule blocks run 17 only.

``members`` must be the **clustering-time** member count (``total_points - noise_count`` on
``analysis_runs``): daily centroid assignment keeps adding members to a live run for a month, so current
counts of older runs would be inflated compared with a run that was just built.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median

# Minimum allowed ratio of candidate-run / reference for each metric (experiment 0004).
MIN_RATIO = {
    "clusters": 0.20,
    "members": 0.20,
    "candidates": 0.20,
}
REFERENCE_RUNS = 3


@dataclass(frozen=True)
class RunStats:
    run_id: int
    clusters: int
    candidates: int
    members: int                    # clustering-time members: total_points - noise_count
    audience_rows: int
    quality_rows: int
    viability_attempted: int
    viability_succeeded: int
    has_weekly_volume: bool


@dataclass(frozen=True)
class PromotionDecision:
    promote: bool
    blocked_by: tuple[str, ...]        # reasons the run was not promoted
    overridden: tuple[str, ...]        # magnitude reasons ignored because of ``force``
    reference: dict[str, float] | None  # median of recent promoted runs, per metric


def completeness_problems(run: RunStats) -> list[str]:
    problems = []
    if run.clusters <= 0:
        problems.append("run has no clusters")
    if run.audience_rows < run.clusters:
        problems.append(f"audience classified for {run.audience_rows} of {run.clusters} clusters")
    if run.quality_rows < run.clusters:
        problems.append(f"quality metrics for {run.quality_rows} of {run.clusters} clusters")
    if run.viability_attempted <= 0 or run.viability_succeeded <= 0:
        problems.append("viability scoring produced no results")
    if not run.has_weekly_volume:
        problems.append("weekly volume not built")
    return problems


def reference_stats(recent_promoted: Sequence[RunStats]) -> dict[str, float] | None:
    """Median of the most recent promoted runs (newest first in ``recent_promoted``)."""
    window = list(recent_promoted)[:REFERENCE_RUNS]
    if not window:
        return None
    return {metric: float(median(getattr(r, metric) for r in window)) for metric in MIN_RATIO}


def magnitude_problems(run: RunStats, reference: dict[str, float] | None) -> list[str]:
    if reference is None:
        return []
    problems = []
    for metric, min_ratio in MIN_RATIO.items():
        ref = reference[metric]
        if ref <= 0:
            continue
        ratio = getattr(run, metric) / ref
        if ratio < min_ratio:
            problems.append(
                f"{metric} {getattr(run, metric)} is {ratio:.2f}x the recent median {ref:.0f} "
                f"(minimum {min_ratio:.2f}x)"
            )
    return problems


def decide_promotion(
    run: RunStats, recent_promoted: Sequence[RunStats], *, force: bool = False
) -> PromotionDecision:
    """``recent_promoted``: runs that were live at some point, newest first, excluding ``run``."""
    reference = reference_stats([r for r in recent_promoted if r.run_id != run.run_id])
    incomplete = completeness_problems(run)
    collapse = magnitude_problems(run, reference)
    blocked = incomplete + ([] if force else collapse)
    return PromotionDecision(
        promote=not blocked,
        blocked_by=tuple(blocked),
        overridden=tuple(collapse) if force else (),
        reference=reference,
    )
