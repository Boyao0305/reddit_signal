"""Service layer for cluster velocity and corpus velocity.

Caching, week-range construction, and payload formatting live here.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any, Optional, Tuple

from ..repositories import velocity as repo
from ..repositories.runs import week_anchor_for_run

_DEFAULT_CACHE_TTL_SECONDS = 60.0
_CACHE_MAX_ITEMS = 1024

_velocity_cache: dict[Tuple[Any, ...], Tuple[float, dict[str, Any]]] = {}
_corpus_cache: dict[int, Tuple[float, dict[str, Any]]] = {}


# ── cache helpers ──────────────────────────────────────────────────────────────

def _cache_get(
    cache: dict[Any, Tuple[float, dict[str, Any]]],
    key: Any,
    ttl_seconds: float = _DEFAULT_CACHE_TTL_SECONDS,
) -> Optional[dict[str, Any]]:
    cached = cache.get(key)
    if cached and (time.time() - cached[0]) < ttl_seconds:
        return cached[1]
    return None


def _cache_set(
    cache: dict[Any, Tuple[float, dict[str, Any]]],
    key: Any,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if key not in cache and len(cache) >= _CACHE_MAX_ITEMS:
        oldest_key = min(cache, key=lambda k: cache[k][0])
        cache.pop(oldest_key, None)
    cache[key] = (time.time(), payload)
    return payload


# ── week helpers ───────────────────────────────────────────────────────────────

def _velocity_weeks(anchor_week: date) -> list[date]:
    return [anchor_week - timedelta(weeks=14 - i) for i in range(15)]


# ── cluster velocity payload ───────────────────────────────────────────────────

def cluster_velocity_payload(
    *,
    cluster_id: int,
    cluster_run_id: int,
) -> dict[str, Any]:
    """Return the full payload for GET /api/v2/clusters/{id}/velocity."""
    cache_key = (cluster_run_id, cluster_id)
    cached = _cache_get(_velocity_cache, cache_key)
    if cached is not None:
        return cached

    anchor_week = week_anchor_for_run(cluster_run_id)
    weeks = _velocity_weeks(anchor_week)

    by_wk = repo.query_cluster_velocity(cluster_run_id, cluster_id, weeks[0], weeks[-1])
    median_pain_count = repo.query_velocity_median(cluster_run_id, cluster_id)

    weeks_out = []
    for w in weeks:
        r = by_wk.get(w)
        if r is None:
            weeks_out.append({
                "week_start": w.isoformat(),
                "pain_count": None,
                "share_global": None,
                "share_field": None,
                "is_partial": w >= anchor_week,
            })
        else:
            weeks_out.append({
                "week_start": w.isoformat(),
                "pain_count": r[1],
                "share_global": r[2],
                "share_field": r[3],
                "is_partial": w >= anchor_week,
            })

    last_4w_start = anchor_week - timedelta(weeks=4)
    prior_4w_start = anchor_week - timedelta(weeks=8)
    prior_4w_end = last_4w_start

    payload = {
        "cluster_id": cluster_id,
        "cluster_run_id": cluster_run_id,
        "median_pain_count": median_pain_count,
        "weeks": weeks_out,
        "windows": {
            "last_4w_start": last_4w_start.isoformat(),
            "prior_4w_start": prior_4w_start.isoformat(),
            "prior_4w_end": prior_4w_end.isoformat(),
        },
    }
    return _cache_set(_velocity_cache, cache_key, payload)


# ── corpus velocity payload ────────────────────────────────────────────────────

def corpus_velocity_payload(
    *,
    cluster_run_id: int,
) -> dict[str, Any]:
    """Return the full payload for GET /api/v2/corpus-velocity."""
    cached = _cache_get(_corpus_cache, cluster_run_id)
    if cached is not None:
        return cached

    anchor_week = week_anchor_for_run(cluster_run_id)
    weeks = _velocity_weeks(anchor_week)

    by_wk = repo.query_corpus_velocity(cluster_run_id, weeks[0], weeks[-1])

    weeks_out = [
        {
            "week_start": w.isoformat(),
            "total_pain": by_wk.get(w),
            "is_partial": w >= anchor_week,
        }
        for w in weeks
    ]
    payload = {"cluster_run_id": cluster_run_id, "weeks": weeks_out}
    return _cache_set(_corpus_cache, cluster_run_id, payload)
