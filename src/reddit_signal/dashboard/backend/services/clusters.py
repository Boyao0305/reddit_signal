"""Service layer for cluster listing, detail, and members.

Coordinates repository calls, caching, and post-processing (percentile ranks,
dimension maps, sparkline assembly).  Does not depend on FastAPI.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from reddit_signal.dashboard.scoring import (
    DIMENSION_KEYS,
    build_dimensions_map,
    percentile_ranks,
)
from reddit_signal.dashboard.snapshot import row_to_cluster

from ..repositories import clusters as repo
from ..repositories import velocity as velocity_repo
from .search_expansion import expand_search_terms


# ── caches ─────────────────────────────────────────────────────────────────────

_CLUSTERS_CACHE_TTL_SECONDS = 300.0
_DEFAULT_CACHE_TTL_SECONDS = 60.0
_CACHE_MAX_ITEMS = 1024

_clusters_cache: dict[Tuple[Any, ...], Tuple[float, dict[str, Any]]] = {}
_cluster_detail_cache: dict[Tuple[Any, ...], Tuple[float, dict[str, Any]]] = {}
_members_cache: dict[Tuple[Any, ...], Tuple[float, dict[str, Any]]] = {}


# ── helpers ────────────────────────────────────────────────────────────────────

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


def _with_canonical_competition_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Populate canonical competition fields from the best currently available source."""
    out = dict(row)
    has_adjusted_s = (
        out.get("s_axis_source") == "competition_adjusted"
        and out.get("competition_adjusted_s_axis") is not None
    )
    if has_adjusted_s:
        return out

    if out.get("competition_adjusted_s_axis") is None:
        out["competition_adjusted_s_axis"] = out.get("s_axis")
    if out.get("competition_adjusted_balance_score") is None:
        out["competition_adjusted_balance_score"] = out.get("balance_score")
    if out.get("competition_adjusted_pareto_layer") is None:
        out["competition_adjusted_pareto_layer"] = out.get("pareto_layer")
    if out.get("competition_adjusted_s_axis") is not None:
        out["s_axis_source"] = "reddit_proxy"
    return out


# ── list clusters (the big one) ────────────────────────────────────────────────

def list_clusters_payload(
    *,
    cluster_run_id: int,
    viability_run_id: int,
    audience_classifier_version: str,
    audience_type: str,
    include_ineligible: bool,
    search: str,
    min_members: Optional[int],
    min_pain_4w: Optional[int],
    sort: str,
    date_from: date | None,
    date_to: date | None,
    subreddits: list[str],
    min_window_pain: Optional[int],
    page: int,
    page_size: int,
    anchor_week,
) -> dict[str, Any]:
    """Return the full payload for GET /api/v2/clusters."""
    search_terms = expand_search_terms(search)
    cache_key = (
        cluster_run_id,
        viability_run_id,
        audience_classifier_version,
        audience_type,
        include_ineligible,
        tuple(term.lower() for term in search_terms),
        min_members,
        min_pain_4w,
        sort,
        date_from.isoformat() if date_from else None,
        date_to.isoformat() if date_to else None,
        tuple(sorted(subreddits)),
        min_window_pain,
        page,
        page_size,
    )
    cached = _cache_get(_clusters_cache, cache_key, _CLUSTERS_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached

    offset = (page - 1) * page_size
    fetch_limit = page_size + 1

    use_snapshot = repo.has_dashboard_snapshot(
        cluster_run_id, viability_run_id, audience_classifier_version
    )

    if use_snapshot:
        filters, filter_params = repo.cluster_filter_sql(
            audience_type=audience_type,
            include_ineligible=include_ineligible,
            search=search,
            search_terms=search_terms,
            min_members=min_members,
            min_pain_4w=min_pain_4w,
            snapshot=True,
        )
        fetched_rows = repo.query_snapshot_clusters(
            cluster_run_id=cluster_run_id,
            viability_run_id=viability_run_id,
            audience_classifier_version=audience_classifier_version,
            filters=filters,
            filter_params=filter_params,
            limit=fetch_limit,
            offset=offset,
            sort=sort,
            date_from=date_from,
            date_to=date_to,
            subreddits=subreddits,
            min_window_pain=min_window_pain,
        )
    else:
        filters, params = repo.cluster_filter_sql(
            audience_type=audience_type,
            include_ineligible=include_ineligible,
            search=search,
            search_terms=search_terms,
            min_members=min_members,
            min_pain_4w=min_pain_4w,
            snapshot=False,
        )
        fetched_rows = repo.query_live_clusters(
            cluster_run_id=cluster_run_id,
            viability_run_id=viability_run_id,
            audience_classifier_version=audience_classifier_version,
            filters=filters,
            params=params,
            limit=fetch_limit,
            offset=offset,
            sort=sort,
            date_from=date_from,
            date_to=date_to,
            subreddits=subreddits,
            min_window_pain=min_window_pain,
        )

    has_more = len(fetched_rows) > page_size
    rows = fetched_rows[:page_size]

    if has_more:
        total = offset + len(rows) + 1
        total_is_exact = False
    else:
        total = offset + len(rows)
        total_is_exact = True

    # sparkline data
    sparklines: dict[int, dict] = {}
    if rows:
        cluster_ids = [r["cluster_id"] for r in rows]
        sparklines = velocity_repo.query_sparklines(
            cluster_run_id, cluster_ids, anchor_week["weeks"][0], anchor_week["weeks"][-1]
        )

    # post-processing
    ranked_rows = [_with_canonical_competition_fields(row_to_cluster(r)) for r in rows]

    pct_by_dim: dict[str, list[Optional[float]]] = {}
    for key in DIMENSION_KEYS:
        col = [r.get(key) for r in ranked_rows]
        pct_by_dim[key] = percentile_ranks(col)

    weeks = anchor_week["weeks"]
    weeks_iso = [w.isoformat() for w in weeks]

    out_clusters: list[dict[str, Any]] = []
    for i, row in enumerate(ranked_rows):
        cid = row["cluster_id"]
        spark_map = sparklines.get(cid, {})
        sparkline = [spark_map.get(w) for w in weeks]
        out_clusters.append({
            **row,
            "burst": row.get("burst"),
            "filter_window": {
                "pain_count": row.get("filter_window_pain_count"),
                "days_active": row.get("filter_window_days_active"),
                "subreddit_count": row.get("filter_window_subreddit_count"),
                "date_from": date_from.isoformat() if date_from else None,
                "date_to": date_to.isoformat() if date_to else None,
                "subreddits": subreddits,
            },
            "dimensions": build_dimensions_map(row, pct_by_dim, i),
            "sparkline": sparkline,
            "sparkline_weeks": weeks_iso,
            "sparkline_partial_from_index": 14,
        })

    payload = {
        "cluster_run_id": cluster_run_id,
        "viability_run_id": viability_run_id,
        "audience_classifier_version": audience_classifier_version,
        "total": total,
        "total_is_exact": total_is_exact,
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
        "clusters": out_clusters,
        "corpus_burst": repo.query_corpus_burst_snapshot(cluster_run_id),
        "freshness": repo.query_freshness(
            cluster_run_id,
            viability_run_id,
            audience_classifier_version,
        ),
        "emerging_opportunities": repo.query_emerging_opportunities(cluster_run_id),
    }
    return _cache_set(_clusters_cache, cache_key, payload)


# ── cluster detail ─────────────────────────────────────────────────────────────

def cluster_detail_payload(
    *,
    cluster_id: int,
    cluster_run_id: int,
    viability_run_id: int,
    audience_classifier_version: str,
) -> dict[str, Any]:
    """Return the full payload for GET /api/v2/clusters/{cluster_id}."""
    cache_key = (
        cluster_id,
        cluster_run_id,
        viability_run_id,
        audience_classifier_version,
    )
    cached = _cache_get(_cluster_detail_cache, cache_key)
    if cached is not None:
        return cached

    audience_block = repo.query_cluster_audience(cluster_id, audience_classifier_version)
    viability_block = repo.query_cluster_viability(cluster_id, viability_run_id)
    competition_block = repo.query_cluster_competition(
        cluster_id,
        cluster_run_id,
        viability_run_id,
        audience_classifier_version,
    )

    payload = {
        "cluster_id": cluster_id,
        "audience": audience_block,
        "viability": viability_block,
        "competition": competition_block,
    }
    return _cache_set(_cluster_detail_cache, cache_key, payload)


# ── cluster members ────────────────────────────────────────────────────────────

def cluster_members_payload(
    *,
    cluster_id: int,
    page: int,
    page_size: int,
    date_from: date | None = None,
    date_to: date | None = None,
    subreddits: list[str] | None = None,
    source: str = "all",
) -> dict[str, Any]:
    """Return the full payload for GET /api/v2/clusters/{cluster_id}/members."""
    subreddits = subreddits or []
    source = (source or "all").strip().lower()
    cache_key = (
        cluster_id,
        page,
        page_size,
        date_from.isoformat() if date_from else None,
        date_to.isoformat() if date_to else None,
        tuple(sorted(subreddits)),
        source,
    )
    cached = _cache_get(_members_cache, cache_key)
    if cached is not None:
        return cached

    offset = (page - 1) * page_size
    total = repo.count_cluster_members_filtered(
        cluster_id,
        date_from=date_from,
        date_to=date_to,
        subreddits=subreddits,
        source=source,
    )
    source_counts = repo.query_cluster_member_source_counts(
        cluster_id,
        date_from=date_from,
        date_to=date_to,
        subreddits=subreddits,
    )
    members = repo.query_cluster_members(
        cluster_id,
        page_size,
        offset,
        date_from=date_from,
        date_to=date_to,
        subreddits=subreddits,
        source=source,
    )

    pages = (total + page_size - 1) // page_size if total else 0
    payload = {
        "cluster_id": cluster_id,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "filter_window": {
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "subreddits": subreddits,
            "source": source,
        },
        "source_counts": source_counts,
        "members": members,
    }
    return _cache_set(_members_cache, cache_key, payload)


def cluster_filter_options_payload(*, cluster_run_id: int) -> dict[str, Any]:
    return {
        "cluster_run_id": cluster_run_id,
        **repo.query_cluster_filter_options(cluster_run_id),
    }


# ── cluster compare (cross-run) ────────────────────────────────────────────────

def _compute_delta(
    ref_value: float | None,
    cmp_value: float | None,
) -> dict[str, Any]:
    """Return a MetricDelta dict for two numeric values."""
    direction: str = "flat"
    absolute_change: float | None = None
    percent_change: float | None = None

    if ref_value is not None and cmp_value is not None:
        absolute_change = cmp_value - ref_value
        if ref_value != 0:
            percent_change = absolute_change / ref_value
        direction = "up" if absolute_change > 0 else ("down" if absolute_change < 0 else "flat")
    elif cmp_value is not None and ref_value is None:
        direction = "up"
    elif ref_value is not None and cmp_value is None:
        direction = "down"

    return {
        "ref_value": ref_value,
        "cmp_value": cmp_value,
        "absolute_change": absolute_change,
        "percent_change": percent_change,
        "direction": direction,
    }


def _prefixed_compare_metrics(row: dict[str, Any], prefix: str) -> dict[str, Any] | None:
    """Build a small metrics dict from bulk compare row aliases."""
    snapshot_cluster_id = row.get(f"{prefix}_snapshot_cluster_id")
    if snapshot_cluster_id is None:
        return None

    out = {
        "cluster_id": snapshot_cluster_id,
        "member_count": row.get(f"{prefix}_member_count"),
        "pain_count_last_4w": row.get(f"{prefix}_pain_count_last_4w"),
        "pareto_layer": row.get(f"{prefix}_pareto_layer"),
        "balance_score": row.get(f"{prefix}_balance_score"),
        "competition_adjusted_pareto_layer": row.get(
            f"{prefix}_competition_adjusted_pareto_layer"
        ),
        "competition_adjusted_balance_score": row.get(
            f"{prefix}_competition_adjusted_balance_score"
        ),
        "competition_adjusted_s_axis": row.get(f"{prefix}_competition_adjusted_s_axis"),
        "s_axis_source": row.get(f"{prefix}_s_axis_source"),
        "s_axis": row.get(f"{prefix}_s_axis"),
    }
    for key in (
        "member_count",
        "pain_count_last_4w",
        "pareto_layer",
        "balance_score",
        "competition_adjusted_pareto_layer",
        "competition_adjusted_balance_score",
        "competition_adjusted_s_axis",
        "s_axis",
    ):
        val = out.get(key)
        if val is not None and not isinstance(val, (int, float)):
            try:
                out[key] = float(val)
            except (TypeError, ValueError):
                out[key] = None
    return _with_canonical_competition_fields(out)


def get_cluster_compare_summaries(
    *,
    ref_run_id: int,
    cmp_run_id: int,
    viability_run_id: int,
    ref_viability_run_id: int | None = None,
    cmp_viability_run_id: int | None = None,
    audience_classifier_version: str,
    cluster_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Return lightweight compare summaries in one batch for table rendering."""
    ref_vri = ref_viability_run_id if ref_viability_run_id is not None else viability_run_id
    cmp_vri = cmp_viability_run_id if cmp_viability_run_id is not None else viability_run_id
    rows = repo.query_compare_summary_rows(
        ref_run_id=ref_run_id,
        cmp_run_id=cmp_run_id,
        ref_viability_run_id=ref_vri,
        cmp_viability_run_id=cmp_vri,
        audience_classifier_version=audience_classifier_version,
        cluster_ids=cluster_ids,
    )

    summaries: list[dict[str, Any]] = []
    for row in rows:
        relation = row.get("relation") or "unmatched"
        matched = relation != "unmatched"
        ref_metrics = _prefixed_compare_metrics(row, "ref")
        cmp_metrics = _prefixed_compare_metrics(row, "cmp")

        def _ref(key: str) -> float | None:
            return (ref_metrics or {}).get(key)

        def _cmp(key: str) -> float | None:
            return (cmp_metrics or {}).get(key)

        summaries.append({
            "cluster_id": row.get("cmp_cluster_id"),
            "matched": matched,
            "ref_cluster_id": row.get("ref_cluster_id"),
            "cmp_cluster_id": row.get("cmp_cluster_id"),
            "relation": relation,
            "overlap_jaccard": row.get("overlap_jaccard"),
            "shared_members": row.get("shared_members"),
            "member_count_delta": _compute_delta(_ref("member_count"), _cmp("member_count")),
            "pareto_layer_delta": _compute_delta(
                _ref("competition_adjusted_pareto_layer"),
                _cmp("competition_adjusted_pareto_layer"),
            ),
            "balance_score_delta": _compute_delta(
                _ref("competition_adjusted_balance_score"),
                _cmp("competition_adjusted_balance_score"),
            ),
            "s_axis_delta": _compute_delta(
                _ref("competition_adjusted_s_axis"),
                _cmp("competition_adjusted_s_axis"),
            ),
        })
    return summaries


def _build_snapshot_dict(
    metrics: dict[str, Any] | None,
    velocity_data: dict[str, Any] | None,
    competition_data: dict[str, Any] | None,
    sparkline: list[int | None],
    sparkline_weeks: list[str],
) -> dict[str, Any] | None:
    """Assemble a ClusterRunSnapshot dict from raw pieces."""
    if metrics is None:
        return None
    metrics = _with_canonical_competition_fields(metrics)

    dim_map: dict[str, dict[str, float | None]] = {}
    for key in DIMENSION_KEYS:
        raw = metrics.get(key)
        val: float | None = None
        if raw is not None:
            try:
                val = float(raw)
            except (TypeError, ValueError):
                pass
        dim_map[key] = {"value": val, "pct": None}

    burst: dict[str, Any] | None = None
    if metrics.get("burst_label") is not None or metrics.get("burst_score") is not None:
        burst = {
            "score": metrics.get("burst_score"),
            "lift": metrics.get("burst_lift"),
            "zscore": metrics.get("burst_zscore"),
            "recent_count": metrics.get("burst_recent_count"),
            "baseline_count": metrics.get("burst_baseline_count"),
            "corpus_lift": metrics.get("burst_corpus_lift"),
            "label": metrics.get("burst_label"),
            "window": "3d_vs_prior_28d",
        }

    return {
        "cluster_id": metrics.get("cluster_id"),
        "member_count": metrics.get("member_count"),
        "pain_count_last_4w": metrics.get("pain_count_last_4w"),
        "q_axis": metrics.get("q_axis"),
        "m_axis": metrics.get("m_axis"),
        "s_axis": metrics.get("s_axis"),
        "competition_adjusted_s_axis": metrics.get("competition_adjusted_s_axis"),
        "pareto_layer": metrics.get("pareto_layer"),
        "balance_score": metrics.get("balance_score"),
        "competition_adjusted_pareto_layer": metrics.get("competition_adjusted_pareto_layer"),
        "competition_adjusted_balance_score": metrics.get("competition_adjusted_balance_score"),
        "market_structure": metrics.get("market_structure"),
        "external_supply_gap": metrics.get("external_supply_gap"),
        "is_eligible": metrics.get("is_eligible"),
        "burst": burst,
        "dimensions": dim_map,
        "sparkline": sparkline,
        "sparkline_weeks": sparkline_weeks,
        "velocity": velocity_data["weeks"] if velocity_data else [],
        "competition": competition_data,
    }


def get_cluster_compare(
    *,
    cluster_id: int,
    ref_run_id: int,
    cmp_run_id: int,
    viability_run_id: int,
    ref_viability_run_id: int | None = None,
    cmp_viability_run_id: int | None = None,
    audience_classifier_version: str,
) -> dict[str, Any]:
    """Return the full comparison payload for GET /api/v2/clusters/{id}/compare.

    Matches the cluster across two runs via ``cluster_lineage``, fetches
    snapshot metrics, velocity, and competition data for both sides, and
    computes deltas.
    """
    from datetime import timedelta

    from ..repositories.runs import week_anchor_for_run
    from .velocity import _velocity_weeks

    # 1. Find lineage match
    ref_vri = ref_viability_run_id if ref_viability_run_id is not None else viability_run_id
    cmp_vri = cmp_viability_run_id if cmp_viability_run_id is not None else viability_run_id
    lineage = repo.query_lineage_match(cluster_id, ref_run_id, cmp_run_id)
    matched = lineage is not None
    ref_cluster_id = lineage["ref_cluster_id"] if matched else cluster_id
    cmp_cluster_id = lineage["cmp_cluster_id"] if matched else cluster_id

    if not matched:
        # Try direct lookup: same cluster_id might exist in both snapshots
        lineage = {
            "ref_cluster_id": ref_cluster_id,
            "cmp_cluster_id": cmp_cluster_id,
            "relation": "unmatched",
            "overlap_jaccard": None,
            "shared_members": None,
        }

    # 2. Snapshot metrics for both sides
    ref_metrics = repo.query_snapshot_metrics(
        ref_cluster_id, ref_run_id, ref_vri, audience_classifier_version
    )
    cmp_metrics = repo.query_snapshot_metrics(
        cmp_cluster_id, cmp_run_id, cmp_vri, audience_classifier_version
    )

    # 3. Velocity + sparkline for both sides
    ref_anchor = week_anchor_for_run(ref_run_id)
    ref_weeks = _velocity_weeks(ref_anchor)
    cmp_anchor = week_anchor_for_run(cmp_run_id)
    cmp_weeks = _velocity_weeks(cmp_anchor)

    ref_velocity_raw = velocity_repo.query_cluster_velocity(
        ref_run_id, ref_cluster_id, ref_weeks[0], ref_weeks[-1]
    )
    cmp_velocity_raw = velocity_repo.query_cluster_velocity(
        cmp_run_id, cmp_cluster_id, cmp_weeks[0], cmp_weeks[-1]
    )

    def _build_velocity(by_wk, weeks, anchor):
        out = []
        for w in weeks:
            r = by_wk.get(w)
            if r is None:
                out.append({
                    "week_start": w.isoformat(),
                    "pain_count": None,
                    "share_global": None,
                    "share_field": None,
                    "is_partial": w >= anchor,
                })
            else:
                out.append({
                    "week_start": w.isoformat(),
                    "pain_count": r[1],
                    "share_global": r[2],
                    "share_field": r[3],
                    "is_partial": w >= anchor,
                })
        return out

    ref_velocity_payload = {
        "cluster_id": ref_cluster_id,
        "cluster_run_id": ref_run_id,
        "weeks": _build_velocity(ref_velocity_raw, ref_weeks, ref_anchor),
    }
    cmp_velocity_payload = {
        "cluster_id": cmp_cluster_id,
        "cluster_run_id": cmp_run_id,
        "weeks": _build_velocity(cmp_velocity_raw, cmp_weeks, cmp_anchor),
    }

    # Sparklines
    ref_sparkline_raw = velocity_repo.query_sparklines(
        ref_run_id, [ref_cluster_id], ref_weeks[0], ref_weeks[-1]
    )
    cmp_sparkline_raw = velocity_repo.query_sparklines(
        cmp_run_id, [cmp_cluster_id], cmp_weeks[0], cmp_weeks[-1]
    )
    ref_sp = ref_sparkline_raw.get(ref_cluster_id, {})
    cmp_sp = cmp_sparkline_raw.get(cmp_cluster_id, {})
    ref_sparkline = [ref_sp.get(w) for w in ref_weeks]
    cmp_sparkline = [cmp_sp.get(w) for w in cmp_weeks]
    ref_sparkline_weeks = [w.isoformat() for w in ref_weeks]
    cmp_sparkline_weeks = [w.isoformat() for w in cmp_weeks]

    # 4. Competition data for both sides
    ref_competition = repo.query_cluster_competition(
        ref_cluster_id, ref_run_id, ref_vri, audience_classifier_version
    )
    cmp_competition = repo.query_cluster_competition(
        cmp_cluster_id, cmp_run_id, cmp_vri, audience_classifier_version
    )

    # 5. Canonicalize ranking fields and assemble snapshots
    if ref_metrics is not None:
        ref_metrics = _with_canonical_competition_fields(ref_metrics)
    if cmp_metrics is not None:
        cmp_metrics = _with_canonical_competition_fields(cmp_metrics)

    ref_snapshot = _build_snapshot_dict(
        ref_metrics, ref_velocity_payload, ref_competition,
        ref_sparkline, ref_sparkline_weeks,
    )
    cmp_snapshot = _build_snapshot_dict(
        cmp_metrics, cmp_velocity_payload, cmp_competition,
        cmp_sparkline, cmp_sparkline_weeks,
    )

    # 6. Compute deltas
    def _v(key: str) -> float | None:
        m = ref_metrics or {}
        return m.get(key)

    def _c(key: str) -> float | None:
        m = cmp_metrics or {}
        return m.get(key)

    def _competitor_names(comp: dict[str, Any] | None) -> set[str]:
        if comp is None:
            return set()
        return {c["name"] for c in comp.get("competitors", [])}

    ref_comp_names = _competitor_names(ref_competition)
    cmp_comp_names = _competitor_names(cmp_competition)

    deltas = {
        "member_count": _compute_delta(_v("member_count"), _c("member_count")),
        "pain_count_last_4w": _compute_delta(_v("pain_count_last_4w"), _c("pain_count_last_4w")),
        "pareto_layer": _compute_delta(_v("pareto_layer"), _c("pareto_layer")),
        "balance_score": _compute_delta(_v("balance_score"), _c("balance_score")),
        "competition_adjusted_pareto_layer": _compute_delta(
            _v("competition_adjusted_pareto_layer"), _c("competition_adjusted_pareto_layer"),
        ),
        "competition_adjusted_balance_score": _compute_delta(
            _v("competition_adjusted_balance_score"), _c("competition_adjusted_balance_score"),
        ),
        "competition_adjusted_s_axis": _compute_delta(
            _v("competition_adjusted_s_axis"), _c("competition_adjusted_s_axis"),
        ),
        "external_supply_gap": _compute_delta(
            _v("external_supply_gap"), _c("external_supply_gap"),
        ),
        "market_structure_changed": (
            ref_metrics is not None
            and cmp_metrics is not None
            and ref_metrics.get("market_structure") != cmp_metrics.get("market_structure")
        ),
        "market_structure_ref": (ref_metrics or {}).get("market_structure"),
        "market_structure_cmp": (cmp_metrics or {}).get("market_structure"),
        "new_competitors": sorted(cmp_comp_names - ref_comp_names),
        "dropped_competitors": sorted(ref_comp_names - cmp_comp_names),
    }

    return {
        "cluster_id": cluster_id,
        "ref_run_id": ref_run_id,
        "cmp_run_id": cmp_run_id,
        "ref_viability_run_id": ref_vri,
        "cmp_viability_run_id": cmp_vri,
        "lineage": lineage,
        "ref": ref_snapshot,
        "cmp": cmp_snapshot,
        "deltas": deltas,
    }
