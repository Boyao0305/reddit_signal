"""Public read-only payloads for Seedcast (``/api/public/*``).

Shapes follow ``frontend/src/lib/seedcast/types.ts``. Exposes no viability, competition or Pareto scores
and no usernames. Cluster ids are lineage-stable: the earliest ancestor along 'continued' edges.
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

from reddit_signal.core.public_view import (
    audience_label,
    community_label,
    month_label,
    percentages,
    source_label,
    stitch_weeks,
    strip_usernames,
)
from reddit_signal.dashboard.snapshot import row_to_cluster

from ..repositories import clusters as clusters_repo
from ..repositories import public as repo
from ..repositories.runs import NoClusterRun, default_ids, week_anchor_for_run

AUDIENCE_CLASSIFIER_VERSION = "v1"
SERIES_WEEKS = 26
HISTORY_WEEKS = 52
QUOTES = 6
CACHE_TTL_SECONDS = 1800   # data changes once a day; the first build takes ~10 s

_cache: dict[tuple, tuple[float, Any]] = {}


def _cached(key: tuple, build):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    value = build()
    _cache[key] = (time.monotonic(), value)
    return value


def _weeks(anchor: date, n: int) -> list[date]:
    return [anchor - timedelta(weeks=n - 1 - i) for i in range(n)]


def _run_ids(cluster_run_id: int | None, viability_run_id: int | None) -> tuple[int, int]:
    if cluster_run_id is None or viability_run_id is None:
        return default_ids()
    return cluster_run_id, viability_run_id


def _snapshot_rows(run: int, vrun: int) -> list[dict[str, Any]]:
    rows = clusters_repo.query_snapshot_clusters(run, vrun, AUDIENCE_CLASSIFIER_VERSION, [], [], 100_000, 0)
    return [row_to_cluster(r) for r in rows]


def _cluster(row: dict[str, Any], public_id: int, summary: dict[str, Any], series: list[float]) -> dict[str, Any]:
    ratio = row.get("velocity_4w_ratio")
    burst = row.get("burst_lift")
    workaround = row.get("workaround_ratio")
    return {
        "id": public_id,
        "name": row.get("label") or summary.get("statement") or f"Cluster {public_id}",
        "audience": audience_label(row.get("audience_type")),
        "statement": strip_usernames(summary.get("statement")),
        "icp": row.get("icp_hint"),
        "posts4w": int(row.get("pain_count_last_4w") or 0),
        "total": int(row.get("member_count") or 0),
        "ratio": float(ratio) if ratio is not None else None,
        "burst": float(burst) if burst is not None else None,
        "bursting": row.get("burst_label") is not None,
        "communities": int(row.get("subreddit_count") or 0),
        "topCommunity": community_label(summary.get("top_surface"), summary.get("top_source")),
        "firstSeen": month_label(summary.get("first_seen")),
        "workaroundPct": round(100 * float(workaround)) if workaround is not None else 0,
        "series": [int(v) for v in series],
    }


def snapshot_payload(*, cluster_run_id: int | None = None, viability_run_id: int | None = None) -> dict[str, Any]:
    try:
        run, vrun = _run_ids(cluster_run_id, viability_run_id)
    except NoClusterRun:  # new installation before its first monthly run
        return {"updatedAt": None, "clusters": []}
    return _cached(("snapshot", run, vrun), lambda: _build_snapshot(run, vrun))


def _build_snapshot(run: int, vrun: int) -> dict[str, Any]:
    rows = _snapshot_rows(run, vrun)
    ids = [int(r["cluster_id"]) for r in rows]
    stable = repo.stable_ids(run)
    summaries = repo.member_summaries(ids)
    weeks = _weeks(week_anchor_for_run(run), SERIES_WEEKS)
    weekly = repo.weekly_by_generation(ids, weeks[0], weeks[-1])
    clusters = []
    for row in rows:
        cid = int(row["cluster_id"])
        counts = {d: {w: v[0] for w, v in by_week.items()} for d, by_week in weekly.get(cid, {}).items()}
        clusters.append(_cluster(row, stable.get(cid, cid), summaries.get(cid, {}), stitch_weeks(counts, weeks)))
    snapshot_at = max((r.get("snapshot_at") for r in rows if r.get("snapshot_at")), default=None)
    return {"updatedAt": snapshot_at.isoformat() if hasattr(snapshot_at, "isoformat") else snapshot_at,
            "clusters": clusters}


def cluster_detail_payload(
    public_id: int, *, cluster_run_id: int | None = None, viability_run_id: int | None = None
) -> dict[str, Any] | None:
    try:
        run, vrun = _run_ids(cluster_run_id, viability_run_id)
    except NoClusterRun:
        return None
    return _cached(("detail", run, vrun, public_id), lambda: _build_detail(public_id, run, vrun))


def _build_detail(public_id: int, run: int, vrun: int) -> dict[str, Any] | None:
    stable = repo.stable_ids(run)
    by_public = {pub: cid for cid, pub in stable.items()}
    cid = by_public.get(public_id, public_id if public_id in stable else None)
    if cid is None:
        return None
    row = next((r for r in _snapshot_rows(run, vrun) if int(r["cluster_id"]) == cid), None)
    if row is None:
        return None

    summary = repo.member_summaries([cid]).get(cid, {})
    weeks = _weeks(week_anchor_for_run(run), HISTORY_WEEKS)
    weekly = repo.weekly_by_generation([cid], weeks[0], weeks[-1]).get(cid, {})
    counts = {d: {w: v[0] for w, v in by_week.items()} for d, by_week in weekly.items()}
    shares = {d: {w: v[1] for w, v in by_week.items() if v[1] is not None} for d, by_week in weekly.items()}
    history = stitch_weeks(counts, weeks)

    base = _cluster(row, stable[cid], summary, history[-SERIES_WEEKS:])
    breakdown = repo.member_breakdown(cid)
    members = clusters_repo.query_cluster_members(cid, QUOTES, 0)
    quotes = [
        {
            "text": strip_usernames(m.get("raw_quote") or m.get("pain_statement")),
            "source": source_label(m.get("source")),
            "where": community_label(m.get("source_surface"), m.get("source")),
            "date": (m.get("source_created") or "")[:10],
            "url": None,
        }
        for m in members
    ]
    return {
        **base,
        "history": [int(v) for v in history],
        "share": [round(100 * float(v), 4) for v in stitch_weeks(shares, weeks)],
        "quotes": quotes,
        "where": percentages((community_label(s, src), n) for s, src, n in breakdown["surfaces"]),
        "sources": percentages((source_label(s), n) for s, _, n in breakdown["sources"]),
        "tools": [{"name": t, "n": n} for t, _, n in breakdown["tools"]],
    }
