"""Single source of truth for the 14 scorable dimensions + percentile rank.

`pain_count_last_4w` is intentionally NOT in this list — it's a row-display
field, not a scorable dimension (see spec §3.6).

Sparse dimensions (see DIMENSION_META):
  `velocity_4w_ratio_normalized` — weight pinned to 0.0 until the monthly
  lifecycle is stable. It is a recent 4w acceleration metric and intentionally
  returns NULL for clusters with insufficient recent/prior volume.
"""
from __future__ import annotations

from typing import Any, List, Dict, Sequence, Optional, Tuple

import numpy as np


# (key, label, group, default_weight, missing_policy)
DIMENSIONS: List[Tuple[str, str, str, float, str]] = [
    ("coherence",                    "Coherence",          "quality",   1.0, "skip"),
    ("thread_diversity",             "Thread diversity",   "quality",   1.0, "skip"),
    ("author_diversity",             "Author diversity",   "quality",   1.0, "skip"),
    ("subreddit_count",              "Subreddit breadth",  "quality",   1.0, "skip"),
    ("specificity_mean",             "Specificity",        "quality",   1.0, "skip"),
    ("workaround_ratio",             "Workaround signal",  "quality",   1.0, "skip"),
    ("separation",                   "Cluster separation", "quality",   1.0, "skip"),
    ("temporal_spread_days",         "Temporal spread",    "quality",   1.0, "skip"),
    ("q_specificity",                "LLM specificity",    "viability", 1.0, "skip"),
    ("v_buildability",               "Buildability",       "viability", 1.0, "skip"),
    ("v_icp_clarity",                "ICP clarity",        "viability", 1.0, "skip"),
    ("v_moat_potential",             "Moat potential",     "viability", 1.0, "skip"),
    ("slope_per_week",               "Long-term slope",    "velocity",  0.0, "skip"),
    # Sparse for low-volume / stale snapshot runs — weight pinned to 0.0 (no
    # scoring impact) until monthly run lifecycle and coverage are stable.
    ("velocity_4w_ratio_normalized", "Recent trend",       "velocity",  0.0, "skip"),
]

# Lifecycle metadata for dimensions that carry extra annotations. Keys must be a
# subset of DIMENSION_KEYS and are merged into score_config_payload() per-dimension.
DIMENSION_META: Dict[str, Dict[str, Any]] = {
    "velocity_4w_ratio_normalized": {
        "status": "sparse",
        "deprecated_since": None,
        "deprecation_reason": (
            "Recent trend is a 4w/prior-4w acceleration metric normalized by corpus "
            "movement. It is intentionally NULL when the recent or prior window has "
            "too little volume, and historical snapshots must be anchored to their "
            "own window_end rather than wall-clock NOW()."
        ),
        "restore_condition": (
            "Restore a non-zero default_weight only after monthly run promotion is "
            "stable and coverage is high enough for the metric to be calibrated."
        ),
    },
}

GROUPS = [
    {"key": "quality",   "label": "Quality"},
    {"key": "viability", "label": "Viability (LLM)"},
    {"key": "velocity",  "label": "Velocity"},
]

DIMENSION_KEYS: List[str] = [d[0] for d in DIMENSIONS]


def score_config_payload() -> dict:
    return {
        "groups": GROUPS,
        "dimensions": [
            {
                "key": key,
                "label": label,
                "group": group,
                "default_weight": w,
                "missing_policy": miss,
                **({
                    "status": DIMENSION_META[key]["status"],
                    "deprecated_since": DIMENSION_META[key].get("deprecated_since"),
                    "deprecation_reason": DIMENSION_META[key].get("deprecation_reason"),
                    "restore_condition": DIMENSION_META[key].get("restore_condition"),
                } if key in DIMENSION_META else {}),
            }
            for (key, label, group, w, miss) in DIMENSIONS
        ],
    }


def percentile_ranks(values: Sequence[Optional[float]]) -> List[Optional[float]]:
    """Average-rank percentile in [0,1]; NULLs stay NULL.

    Uses numpy.argsort with average ranks for ties (Hyndman-Fan style normalised
    to 0..1). For N non-null values, ranks are (rank-0.5)/N so the median
    sits at 0.5 and ties get the same percentile.
    """
    n = len(values)
    out: List[Optional[float]] = [None] * n
    idx = [i for i, v in enumerate(values) if v is not None]
    if not idx:
        return out
    arr = np.array([float(values[i]) for i in idx], dtype=np.float64)
    # Average ranks for ties.
    order = np.argsort(arr, kind="mergesort")
    ranks = np.empty_like(order, dtype=np.float64)
    # Walk sorted, assign average rank within ties.
    i = 0
    sorted_arr = arr[order]
    m = len(arr)
    while i < m:
        j = i
        while j + 1 < m and sorted_arr[j + 1] == sorted_arr[i]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0  # 1-indexed average rank
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    pcts = (ranks - 0.5) / m
    for k, orig_i in enumerate(idx):
        out[orig_i] = float(pcts[k])
    return out


def build_dimensions_map(
    row: Dict,
    pct_by_dim: Dict[str, List[Optional[float]]],
    row_index: int,
) -> Dict[str, Dict[str, Optional[float]]]:
    """Given a row dict and precomputed pct arrays, assemble the 14-key map."""
    out: Dict[str, Dict[str, Optional[float]]] = {}
    for key in DIMENSION_KEYS:
        raw = row.get(key)
        val: Optional[float]
        if raw is None:
            val = None
        else:
            # Preserve int where natural.
            try:
                fv = float(raw)
                val = int(fv) if fv.is_integer() and not isinstance(raw, float) else fv
            except (TypeError, ValueError):
                val = raw  # type: ignore[assignment]
        pct = pct_by_dim[key][row_index]
        out[key] = {"value": val, "pct": pct}
    return out
