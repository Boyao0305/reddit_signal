"""Service layer for /api/v2/runs — caching and formatting.

Separate from the HTTP router so business rules and cache policy can evolve
independently of FastAPI concerns.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any, Optional, Tuple

from ..repositories.runs import list_analysis_runs, list_viability_runs

_RUNS_CACHE_TTL_SECONDS = 30.0
_CACHE_KEY = "runs"
_runs_cache: dict[str, Tuple[float, dict[str, Any]]] = {}


def _iso_date(d: Any) -> Optional[str]:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    return str(d)


def _iso_datetime(d: Any) -> Optional[str]:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.isoformat()
    return str(d)


def get_runs_payload() -> dict[str, Any]:
    """Return the cached (or fresh) /runs payload including computed defaults."""
    cached = _runs_cache.get(_CACHE_KEY)
    if cached is not None and (time.time() - cached[0]) < _RUNS_CACHE_TTL_SECONDS:
        return cached[1]

    cluster_runs: list[dict[str, Any]] = [
        {
            "id": r["id"],
            "window_start": _iso_date(r["window_start"]),
            "window_end": _iso_date(r["window_end"]),
            "cluster_count": r["cluster_count"],
            "is_legacy": r["is_legacy"],
            "status": r["status"],
            "live_at": _iso_datetime(r.get("live_at")),
            "signal_data_through_day": _iso_date(r.get("signal_data_through_day")),
            "snapshot_at": _iso_datetime(r.get("snapshot_at")),
        }
        for r in list_analysis_runs()
    ]
    viability_runs = list_viability_runs()

    live = next((r for r in cluster_runs if r["status"] == "live"), None)
    if live is None:
        fallback = next(
            (
                r
                for r in cluster_runs
                if r.get("status") not in ("building", "failed", "legacy")
            ),
            None,
        )
        default_cluster_run_id: Optional[int] = fallback["id"] if fallback else None
    else:
        default_cluster_run_id = live["id"]
    default_viability_run_id: Optional[int] = next(
        (
            v["id"]
            for v in viability_runs
            if v["cluster_run_id"] == default_cluster_run_id
        ),
        None,
    )

    payload: dict[str, Any] = {
        "cluster_runs": cluster_runs,
        "viability_runs": viability_runs,
        "defaults": {
            "cluster_run_id": default_cluster_run_id,
            "viability_run_id": default_viability_run_id,
            "audience_classifier_version": "v1",
        },
    }
    _runs_cache[_CACHE_KEY] = (time.time(), payload)
    return payload
