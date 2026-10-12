"""Dashboard data routes for runs, clusters, velocity, and score config."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Query

from reddit_signal.dashboard.backend.repositories.runs import default_ids, week_anchor_for_run
from reddit_signal.dashboard.backend.services.clusters import (
    list_clusters_payload,
    cluster_detail_payload,
    cluster_filter_options_payload,
    cluster_members_payload,
    get_cluster_compare,
    get_cluster_compare_summaries,
)
from reddit_signal.dashboard.backend.services.velocity import (
    cluster_velocity_payload,
    corpus_velocity_payload,
)
from reddit_signal.dashboard.scoring import score_config_payload


log = logging.getLogger("dashboard_v2.data")
router = APIRouter(prefix="/api/v2", tags=["dashboard-data"])


def _parse_subreddits(value: str = "") -> list[str]:
    if not value.strip():
        return []
    out: list[str] = []
    seen: set[str] = set()
    for part in value.replace("\n", ",").replace(" ", ",").split(","):
        name = part.strip()
        if name.startswith("r/"):
            name = name[2:]
        if name.startswith("/r/"):
            name = name[3:]
        name = name.strip().strip("/")
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def _parse_int_csv(value: str = "") -> list[int]:
    if not value.strip():
        return []
    out: list[int] = []
    seen: set[int] = set()
    for part in value.replace("\n", ",").replace(" ", ",").split(","):
        raw = part.strip()
        if not raw:
            continue
        try:
            n = int(raw)
        except ValueError:
            continue
        if n <= 0 or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


@router.get("/runs")
def list_runs(
) -> dict[str, Any]:
    from ..services.runs import get_runs_payload
    return get_runs_payload()


@router.get("/score-config")
def score_config(
) -> dict[str, Any]:
    return score_config_payload()


@router.get("/clusters")
def list_clusters(
    cluster_run_id: Optional[int] = Query(None),
    viability_run_id: Optional[int] = Query(None),
    audience_classifier_version: str = Query("v1"),
    audience_type: str = Query("B2C"),
    include_ineligible: bool = Query(True),
    search: str = Query(""),
    min_members: Optional[int] = Query(None, ge=0),
    min_pain_4w: Optional[int] = Query(None, ge=0),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    subreddits: str = Query(""),
    min_window_pain: Optional[int] = Query(None, ge=0),
    sort: str = Query("default", pattern="^(default|burst|window_pain)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=50),
) -> dict[str, Any]:
    if cluster_run_id is None or viability_run_id is None:
        d_cr, d_vr = default_ids()
        cluster_run_id = cluster_run_id or d_cr
        viability_run_id = viability_run_id or d_vr

    log.info(
        "GET /clusters cluster_run_id=%s viability_run_id=%s acv=%s audience=%s include_ineligible=%s sort=%s page=%s page_size=%s",
        cluster_run_id, viability_run_id, audience_classifier_version, audience_type,
        include_ineligible, sort, page, page_size,
    )

    from datetime import timedelta

    anchor_week = week_anchor_for_run(cluster_run_id)
    weeks = [anchor_week - timedelta(weeks=14 - i) for i in range(15)]

    return list_clusters_payload(
        cluster_run_id=cluster_run_id,
        viability_run_id=viability_run_id,
        audience_classifier_version=audience_classifier_version,
        audience_type=audience_type,
        include_ineligible=include_ineligible,
        search=search,
        min_members=min_members,
        min_pain_4w=min_pain_4w,
        sort=sort,
        date_from=date_from,
        date_to=date_to,
        subreddits=_parse_subreddits(subreddits),
        min_window_pain=min_window_pain,
        page=page,
        page_size=page_size,
        anchor_week={"anchor": anchor_week, "weeks": weeks},
    )


@router.get("/cluster-compare-summaries")
def cluster_compare_summaries(
    ref_run_id: int = Query(...),
    cmp_run_id: int = Query(...),
    viability_run_id: int = Query(...),
    ref_viability_run_id: Optional[int] = Query(None),
    cmp_viability_run_id: Optional[int] = Query(None),
    audience_classifier_version: str = Query("v1"),
    cluster_ids: str = Query(""),
) -> list[dict[str, Any]]:
    return get_cluster_compare_summaries(
        ref_run_id=ref_run_id,
        cmp_run_id=cmp_run_id,
        viability_run_id=viability_run_id,
        ref_viability_run_id=ref_viability_run_id,
        cmp_viability_run_id=cmp_viability_run_id,
        audience_classifier_version=audience_classifier_version,
        cluster_ids=_parse_int_csv(cluster_ids) or None,
    )


@router.get("/clusters/{cluster_id}")
def cluster_detail(
    cluster_id: int,
    cluster_run_id: Optional[int] = Query(None),
    viability_run_id: Optional[int] = Query(None),
    audience_classifier_version: str = Query("v1"),
) -> dict[str, Any]:
    if cluster_run_id is None or viability_run_id is None:
        d_cr, d_vr = default_ids()
        cluster_run_id = cluster_run_id or d_cr
        viability_run_id = viability_run_id or d_vr

    return cluster_detail_payload(
        cluster_id=cluster_id,
        cluster_run_id=cluster_run_id,
        viability_run_id=viability_run_id,
        audience_classifier_version=audience_classifier_version,
    )


@router.get("/clusters/{cluster_id}/velocity")
def cluster_velocity(
    cluster_id: int,
    cluster_run_id: int = Query(...),
) -> dict[str, Any]:
    return cluster_velocity_payload(
        cluster_id=cluster_id,
        cluster_run_id=cluster_run_id,
    )


@router.get("/clusters/{cluster_id}/compare")
def cluster_compare(
    cluster_id: int,
    ref_run_id: int = Query(...),
    cmp_run_id: int = Query(...),
    viability_run_id: int = Query(...),
    ref_viability_run_id: Optional[int] = Query(None),
    cmp_viability_run_id: Optional[int] = Query(None),
    audience_classifier_version: str = Query("v1"),
) -> dict[str, Any]:
    """Compare a cluster across two analysis runs.

    Returns side-by-side metrics, velocity, competition data, and deltas
    for the same cluster (matched via ``cluster_lineage``) in *ref_run_id*
    (reference / earlier) vs. *cmp_run_id* (comparison / later).
    """
    return get_cluster_compare(
        cluster_id=cluster_id,
        ref_run_id=ref_run_id,
        cmp_run_id=cmp_run_id,
        viability_run_id=viability_run_id,
        ref_viability_run_id=ref_viability_run_id,
        cmp_viability_run_id=cmp_viability_run_id,
        audience_classifier_version=audience_classifier_version,
    )


@router.get("/clusters/{cluster_id}/members")
def cluster_members(
    cluster_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    subreddits: str = Query(""),
    source: str = Query("all"),
) -> dict[str, Any]:
    return cluster_members_payload(
        cluster_id=cluster_id,
        page=page,
        page_size=page_size,
        date_from=date_from,
        date_to=date_to,
        subreddits=_parse_subreddits(subreddits),
        source=source,
    )


@router.get("/cluster-filter-options")
def cluster_filter_options(
    cluster_run_id: Optional[int] = Query(None),
) -> dict[str, Any]:
    if cluster_run_id is None:
        d_cr, _ = default_ids()
        cluster_run_id = d_cr
    return cluster_filter_options_payload(cluster_run_id=cluster_run_id)


@router.get("/corpus-velocity")
def corpus_velocity(
    cluster_run_id: int = Query(...),
) -> dict[str, Any]:
    return corpus_velocity_payload(cluster_run_id=cluster_run_id)
