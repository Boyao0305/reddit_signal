"""Repository for cluster listing, detail, and member queries.

Owns all SQL that reads or filters clusters, their audience metadata, viability
scores, and pain_cluster_members.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional, Sequence, Tuple

import psycopg2
import psycopg2.extras

from reddit_signal.dashboard.db import get_conn


# ── filter builder (pure string / param construction, no SQL execution) ────────

def cluster_filter_sql(
    *,
    audience_type: str,
    include_ineligible: bool,
    search: str,
    search_terms: Sequence[str] | None = None,
    min_members: Optional[int],
    min_pain_4w: Optional[int],
    snapshot: bool,
) -> Tuple[list[str], list[Any]]:
    """Return (where_clauses, params) for the dynamic cluster-list WHERE.

    Callers append additions (run_id / classifier version clauses) before
    building the final WHERE string.
    """
    where: list[str] = []
    params: list[Any] = []
    if audience_type and audience_type != "all":
        where.append("audience_type = %s")
        params.append(audience_type)
    if not include_ineligible:
        where.append("(is_eligible IS TRUE OR is_eligible IS NULL)")
    raw_terms = search_terms if search_terms is not None else [search]
    terms: list[str] = []
    seen_terms: set[str] = set()
    for raw in raw_terms:
        term = raw.strip()
        key = term.lower()
        if not term or key in seen_terms:
            continue
        terms.append(term)
        seen_terms.add(key)
    if terms:
        search_clauses: list[str] = []
        for term in terms:
            if snapshot:
                search_clauses.append("(label ILIKE %s OR row_data::text ILIKE %s)")
                params.extend([f"%{term}%", f"%{term}%"])
            else:
                search_clauses.append("label ILIKE %s")
                params.append(f"%{term}%")
        where.append(f"({' OR '.join(search_clauses)})")
    if min_members is not None:
        where.append("COALESCE(member_count, 0) >= %s")
        params.append(min_members)
    if min_pain_4w is not None:
        where.append("COALESCE(pain_count_last_4w, 0) >= %s")
        params.append(min_pain_4w)
    return where, params


# ── snapshot / live decision ───────────────────────────────────────────────────

def has_dashboard_snapshot(
    cluster_run_id: int,
    viability_run_id: int,
    audience_classifier_version: str,
) -> bool:
    """Return True if ``dashboard_market_opportunities`` contains a snapshot."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    SELECT 1
                    FROM dashboard_market_opportunities
                    WHERE cluster_run_id = %s
                      AND viability_run_id = %s
                      AND audience_classifier_version = %s
                    LIMIT 1
                    """,
                    (cluster_run_id, viability_run_id, audience_classifier_version),
                )
                return cur.fetchone() is not None
            except psycopg2.errors.UndefinedTable:
                cur.connection.rollback()
                return False


# ── cluster list queries ───────────────────────────────────────────────────────

def query_snapshot_clusters(
    cluster_run_id: int,
    viability_run_id: int,
    audience_classifier_version: str,
    filters: list[str],
    filter_params: list[Any],
    limit: int,
    offset: int,
    sort: str = "default",
    date_from: date | None = None,
    date_to: date | None = None,
    subreddits: list[str] | None = None,
    min_window_pain: int | None = None,
) -> list[dict[str, Any]]:
    """Return raw rows from ``dashboard_market_opportunities``."""
    where = [
        "cluster_run_id = %s",
        "viability_run_id = %s",
        "audience_classifier_version = %s",
        *filters,
    ]
    base_params: list[Any] = [
        cluster_run_id,
        viability_run_id,
        audience_classifier_version,
        *filter_params,
    ]
    window_params: list[Any] = []
    post_where_params: list[Any] = []
    subreddits = subreddits or []
    where_sql = "WHERE " + " AND ".join(where)
    window_join = ""
    window_select = """
                NULL::INT AS filter_window_pain_count,
                NULL::INT AS filter_window_days_active,
                NULL::INT AS filter_window_subreddit_count
    """
    window_where = ""
    has_window_filter = date_from is not None or date_to is not None or bool(subreddits)
    if has_window_filter:
        window_conditions = ["pcm.cluster_id = dmo.cluster_id", "po.source_created IS NOT NULL"]
        if date_from is not None:
            window_conditions.append("po.source_created::date >= %s")
            window_params.append(date_from)
        if date_to is not None:
            window_conditions.append("po.source_created::date <= %s")
            window_params.append(date_to)
        if subreddits:
            window_conditions.append("po.subreddit_name = ANY(%s)")
            window_params.append(subreddits)
        window_join = f"""
                LEFT JOIN LATERAL (
                    SELECT
                        COUNT(*)::INT AS pain_count,
                        COUNT(DISTINCT po.source_created::date)::INT AS days_active,
                        COUNT(DISTINCT po.subreddit_name)::INT AS subreddit_count
                    FROM pain_cluster_members pcm
                    JOIN pain_objects po ON po.id = pcm.pain_id
                    WHERE {' AND '.join(window_conditions)}
                ) fw ON TRUE
        """
        window_select = """
                COALESCE(fw.pain_count, 0)::INT AS filter_window_pain_count,
                COALESCE(fw.days_active, 0)::INT AS filter_window_days_active,
                COALESCE(fw.subreddit_count, 0)::INT AS filter_window_subreddit_count
        """
        if min_window_pain is not None:
            window_where = "AND COALESCE(fw.pain_count, 0) >= %s"
            post_where_params.append(min_window_pain)
    elif min_window_pain is not None:
        where_sql += " AND COALESCE(window_pain_count, 0) >= %s"
        base_params.append(min_window_pain)
    if sort == "burst":
        order_sql = """
                ORDER BY dmo.burst_score DESC NULLS LAST,
                         dmo.burst_lift DESC NULLS LAST,
                         dmo.pain_count_last_4w DESC NULLS LAST,
                         COALESCE(dmo.competition_adjusted_pareto_layer, dmo.pareto_layer) ASC NULLS LAST,
                         dmo.cluster_id ASC
        """
    elif sort == "window_pain":
        order_sql = """
                ORDER BY filter_window_pain_count DESC NULLS LAST,
                         dmo.pain_count_last_4w DESC NULLS LAST,
                         COALESCE(dmo.competition_adjusted_pareto_layer, dmo.pareto_layer) ASC NULLS LAST,
                         dmo.cluster_id ASC
        """
    else:
        order_sql = """
                ORDER BY COALESCE(dmo.competition_adjusted_pareto_layer, dmo.pareto_layer) ASC NULLS LAST,
                         COALESCE(dmo.competition_adjusted_balance_score, dmo.balance_score) DESC NULLS LAST,
                         dmo.cluster_id ASC
        """

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT dmo.*, {window_select}
                FROM dashboard_market_opportunities dmo
                {window_join}
                {where_sql}
                {window_where}
                {order_sql}
                LIMIT %s OFFSET %s
                """,
                tuple(window_params + base_params + post_where_params + [limit, offset]),
            )
            return [dict(r) for r in cur.fetchall()]


def query_live_clusters(
    cluster_run_id: int,
    viability_run_id: int,
    audience_classifier_version: str,
    filters: list[str],
    params: list[Any],
    limit: int,
    offset: int,
    sort: str = "default",
    date_from: date | None = None,
    date_to: date | None = None,
    subreddits: list[str] | None = None,
    min_window_pain: int | None = None,
) -> list[dict[str, Any]]:
    """Return raw rows from the competition-aware live ranking view.

    Requires SET LOCAL of the three GUCs before the SELECT.
    """
    where_sql = ("WHERE " + " AND ".join(filters)) if filters else ""
    query_params = list(params)
    subreddits = subreddits or []
    window_join = ""
    window_select = """
                NULL::INT AS filter_window_pain_count,
                NULL::INT AS filter_window_days_active,
                NULL::INT AS filter_window_subreddit_count
    """
    window_where = ""
    window_params: list[Any] = []
    post_where_params: list[Any] = []
    has_window_filter = date_from is not None or date_to is not None or bool(subreddits)
    if has_window_filter:
        window_conditions = ["pcm.cluster_id = ranked.cluster_id", "po.source_created IS NOT NULL"]
        if date_from is not None:
            window_conditions.append("po.source_created::date >= %s")
            window_params.append(date_from)
        if date_to is not None:
            window_conditions.append("po.source_created::date <= %s")
            window_params.append(date_to)
        if subreddits:
            window_conditions.append("po.subreddit_name = ANY(%s)")
            window_params.append(subreddits)
        window_join = f"""
                LEFT JOIN LATERAL (
                    SELECT
                        COUNT(*)::INT AS pain_count,
                        COUNT(DISTINCT po.source_created::date)::INT AS days_active,
                        COUNT(DISTINCT po.subreddit_name)::INT AS subreddit_count
                    FROM pain_cluster_members pcm
                    JOIN pain_objects po ON po.id = pcm.pain_id
                    WHERE {' AND '.join(window_conditions)}
                ) fw ON TRUE
        """
        window_select = """
                COALESCE(fw.pain_count, 0)::INT AS filter_window_pain_count,
                COALESCE(fw.days_active, 0)::INT AS filter_window_days_active,
                COALESCE(fw.subreddit_count, 0)::INT AS filter_window_subreddit_count
        """
        if min_window_pain is not None:
            window_where = "AND COALESCE(fw.pain_count, 0) >= %s"
            post_where_params.append(min_window_pain)
    if sort == "window_pain":
        order_sql = """
                ORDER BY filter_window_pain_count DESC NULLS LAST,
                         ranked.pain_count_last_4w DESC NULLS LAST,
                         COALESCE(ranked.competition_adjusted_pareto_layer, ranked.pareto_layer) ASC NULLS LAST,
                         ranked.cluster_id ASC
        """
        legacy_order_sql = """
                ORDER BY filter_window_pain_count DESC NULLS LAST,
                         ranked.pain_count_last_4w DESC NULLS LAST,
                         ranked.pareto_layer ASC NULLS LAST,
                         ranked.cluster_id ASC
        """
    else:
        order_sql = """
                ORDER BY COALESCE(ranked.competition_adjusted_pareto_layer, ranked.pareto_layer) ASC NULLS LAST,
                         COALESCE(ranked.competition_adjusted_balance_score, ranked.balance_score) DESC NULLS LAST,
                         ranked.cluster_id ASC
        """
        legacy_order_sql = """
                ORDER BY ranked.pareto_layer ASC NULLS LAST,
                         ranked.balance_score DESC NULLS LAST,
                         ranked.cluster_id ASC
        """

    def _select_from_view(cur, view_name: str, order_by_sql: str) -> list[dict[str, Any]]:
        cur.execute(
            f"""
            SELECT ranked.*, {window_select}
            FROM {view_name} ranked
            {window_join}
            {where_sql}
            {window_where}
            {order_by_sql}
            LIMIT %s OFFSET %s
            """,
            tuple(window_params + query_params + post_where_params + [limit, offset]),
        )
        return [dict(r) for r in cur.fetchall()]

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SET LOCAL llm_analyse.cluster_run_id = %s",
                (str(cluster_run_id),),
            )
            cur.execute(
                "SET LOCAL llm_analyse.viability_run_id = %s",
                (str(viability_run_id),),
            )
            cur.execute(
                "SET LOCAL llm_analyse.audience_classifier_version = %s",
                (audience_classifier_version,),
            )
            try:
                return _select_from_view(cur, "v_market_opportunities_competition_ranked", order_sql)
            except psycopg2.errors.UndefinedTable:
                cur.connection.rollback()
                # If the new migration has not been applied yet, live mode still
                # works with the old Path C surface. Snapshot mode requires the
                # migration because it references physical added columns.
                cur.execute("BEGIN")
                cur.execute("SET LOCAL llm_analyse.cluster_run_id = %s", (str(cluster_run_id),))
                cur.execute("SET LOCAL llm_analyse.viability_run_id = %s", (str(viability_run_id),))
                cur.execute(
                    "SET LOCAL llm_analyse.audience_classifier_version = %s",
                    (audience_classifier_version,),
                )
                return _select_from_view(cur, "v_market_opportunities_ranked", legacy_order_sql)


# ── cluster detail queries ─────────────────────────────────────────────────────

def query_cluster_audience(
    cluster_id: int,
    classifier_version: str,
) -> Optional[dict[str, Any]]:
    """Return audience reasoning + sample pain IDs for a cluster, or None."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT reasoning, sample_pain_ids, surface_distribution
                FROM cluster_audience
                WHERE cluster_id = %s AND classifier_version = %s
                """,
                (cluster_id, classifier_version),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {
                "reasoning": row[0],
                "sample_pain_ids": row[1],
                "surface_distribution": row[2],
            }


def query_cluster_viability(
    cluster_id: int,
    viability_run_id: int,
) -> Optional[dict[str, Any]]:
    """Return viability LLM detail for a cluster, or None."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sample_pain_ids, llm_input_tokens, llm_output_tokens
                FROM viability_scores
                WHERE cluster_id = %s AND viability_run_id = %s
                """,
                (cluster_id, viability_run_id),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {
                "sample_pain_ids": row[0],
                "llm_input_tokens": row[1],
                "llm_output_tokens": row[2],
            }


def query_cluster_competition(
    cluster_id: int,
    cluster_run_id: int,
    viability_run_id: int,
    audience_classifier_version: str,
) -> Optional[dict[str, Any]]:
    """Return active competition assessment + top competitors for a cluster."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                cur.execute(
                    """
                    SELECT
                        cr.id AS competition_run_id,
                        cr.status AS competition_status,
                        cca.market_structure,
                        cca.research_confidence,
                        cca.external_supply_gap,
                        cca.competition_intensity,
                        cca.competition_adjusted_s_axis,
                        cca.solution_gap_evidence,
                        cca.verified_direct_count,
                        cca.direct_competitor_count,
                        cca.rationale,
                        cca.updated_at
                    FROM competition_runs cr
                    JOIN cluster_competition_assessments cca
                      ON cca.competition_run_id = cr.id
                    WHERE cr.cluster_run_id = %s
                      AND cr.viability_run_id = %s
                      AND cr.audience_classifier_version = %s
                      AND cr.status = 'succeeded'
                      AND cr.is_active IS TRUE
                      AND cca.cluster_id = %s
                    ORDER BY cr.id DESC
                    LIMIT 1
                    """,
                    (
                        cluster_run_id,
                        viability_run_id,
                        audience_classifier_version,
                        cluster_id,
                    ),
                )
                row = cur.fetchone()
            except psycopg2.errors.UndefinedTable:
                cur.connection.rollback()
                return None

            if row is None:
                return None

            cur.execute(
                """
                SELECT name, url, category, pricing, business_model, positioning,
                       evidence, confidence, relevance_score, strength_score,
                       satisfaction_gap
                FROM cluster_competitors
                WHERE competition_run_id = %s
                  AND cluster_id = %s
                ORDER BY
                    CASE category
                        WHEN 'direct' THEN 1
                        WHEN 'incumbent' THEN 2
                        ELSE 3
                    END,
                    strength_score DESC,
                    relevance_score DESC,
                    name ASC
                LIMIT 8
                """,
                (row["competition_run_id"], cluster_id),
            )
            competitors = [dict(r) for r in cur.fetchall()]

    out = dict(row)
    updated_at = out.get("updated_at")
    out["updated_at"] = updated_at.isoformat() if updated_at else None
    out["competitors"] = competitors
    return out


def query_corpus_burst_snapshot(cluster_run_id: int) -> Optional[dict[str, Any]]:
    """Return the run-level corpus burst sentinel row, or None.

    One row per refresh of refresh_dashboard_market_opportunities(). Describes
    unclustered (orphan) pain surge recent 3d vs prior 28d, used by the dashboard
    banner that warns about mid-month emerging topics outside the live clusters.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                cur.execute(
                    """
                    SELECT cluster_run_id, day_anchor, total_recent, total_baseline,
                           orphan_recent, orphan_baseline, orphan_lift, orphan_zscore,
                           orphan_label, corpus_lift, snapshot_at
                    FROM corpus_burst_snapshot
                    WHERE cluster_run_id = %s
                    """,
                    (cluster_run_id,),
                )
                row = cur.fetchone()
            except psycopg2.errors.UndefinedTable:
                cur.connection.rollback()
                return None

            if row is None:
                return None

            out = dict(row)
            day_anchor = out.get("day_anchor")
            out["day_anchor"] = day_anchor.isoformat() if day_anchor else None
            snapshot_at = out.get("snapshot_at")
            out["snapshot_at"] = snapshot_at.isoformat() if snapshot_at else None
            return out


def query_freshness(
    cluster_run_id: int,
    viability_run_id: int,
    audience_classifier_version: str,
) -> dict[str, Any]:
    """Return map and signal freshness metadata for a cluster run."""
    rich_sql = """
        SELECT
            ar.id AS cluster_run_id,
            ar.window_start AS map_window_start,
            ar.window_end AS map_window_end,
            ar.live_at AS map_live_at,
            (
                SELECT MAX(snapshot_at)
                FROM dashboard_market_opportunities dmo
                WHERE dmo.cluster_run_id = ar.id
                  AND dmo.viability_run_id = %s
                  AND dmo.audience_classifier_version = %s
            ) AS snapshot_at,
            (
                SELECT MAX(day_start)
                FROM pain_volume_daily_corpus pvdc
                WHERE pvdc.cluster_run_id = ar.id
            ) AS corpus_data_through_day,
            (
                SELECT MAX(day_start)
                FROM pain_volume_daily pvd
                WHERE pvd.cluster_run_id = ar.id
            ) AS cluster_data_through_day,
            (
                SELECT MAX(finished_at)
                FROM pipeline_jobs pj
                WHERE pj.job_type = 'daily'
                  AND pj.status = 'succeeded'
            ) AS last_daily_job_at
        FROM analysis_runs ar
        WHERE ar.id = %s
    """
    fallback_sql = """
        SELECT
            ar.id AS cluster_run_id,
            ar.window_start AS map_window_start,
            ar.window_end AS map_window_end,
            ar.live_at AS map_live_at,
            NULL::TIMESTAMPTZ AS snapshot_at,
            NULL::DATE AS corpus_data_through_day,
            (
                SELECT MAX(day_start)
                FROM pain_volume_daily pvd
                WHERE pvd.cluster_run_id = ar.id
            ) AS cluster_data_through_day,
            NULL::TIMESTAMPTZ AS last_daily_job_at
        FROM analysis_runs ar
        WHERE ar.id = %s
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                cur.execute(
                    rich_sql,
                    (viability_run_id, audience_classifier_version, cluster_run_id),
                )
            except psycopg2.errors.UndefinedTable:
                cur.connection.rollback()
                cur.execute(fallback_sql, (cluster_run_id,))
            row = cur.fetchone()
            if row is None:
                return {}
            out = dict(row)
    for key in ("map_window_start", "map_window_end", "corpus_data_through_day", "cluster_data_through_day"):
        value = out.get(key)
        out[key] = value.isoformat() if value else None
    for key in ("map_live_at", "snapshot_at", "last_daily_job_at"):
        value = out.get(key)
        out[key] = value.isoformat() if value else None
    out["signal_data_through_day"] = (
        out.get("corpus_data_through_day")
        or out.get("cluster_data_through_day")
        or out.get("map_window_end")
    )
    return out


def query_emerging_opportunities(cluster_run_id: int, limit: int = 8) -> list[dict[str, Any]]:
    """Return latest succeeded deterministic emerging opportunities for a run."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                cur.execute(
                    """
                    WITH latest_run AS (
                        SELECT id, lookback_days, created_at
                        FROM emerging_opportunity_runs
                        WHERE cluster_run_id = %s
                          AND status = 'succeeded'
                        ORDER BY id DESC
                        LIMIT 1
                    )
                    SELECT
                        eo.id,
                        eo.emerging_run_id,
                        eo.cluster_run_id,
                        eo.label,
                        eo.pain_count,
                        eo.author_count,
                        eo.subreddit_count,
                        eo.coherence,
                        eo.burst_score,
                        eo.source_window_start,
                        eo.source_window_end,
                        eo.sample_pain_ids,
                        eo.sample_quotes,
                        eo.created_at,
                        lr.lookback_days
                    FROM emerging_opportunities eo
                    JOIN latest_run lr ON lr.id = eo.emerging_run_id
                    ORDER BY eo.burst_score DESC, eo.coherence DESC, eo.pain_count DESC, eo.id ASC
                    LIMIT %s
                    """,
                    (cluster_run_id, limit),
                )
                rows = [dict(r) for r in cur.fetchall()]
            except psycopg2.errors.UndefinedTable:
                cur.connection.rollback()
                return []

    for row in rows:
        for key in ("source_window_start", "source_window_end"):
            value = row.get(key)
            row[key] = value.isoformat() if value else None
        created_at = row.get("created_at")
        row["created_at"] = created_at.isoformat() if created_at else None
    return rows


# ── cluster members queries ────────────────────────────────────────────────────

_MEMBER_SOURCE_SQL = """
LOWER(COALESCE(se.source, CASE WHEN po.subreddit_name IS NOT NULL THEN 'reddit' ELSE 'unknown' END))
"""


def count_cluster_members(cluster_id: int) -> int:
    """Return total member count for a cluster."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM pain_cluster_members WHERE cluster_id = %s",
                (cluster_id,),
            )
            return int(cur.fetchone()[0])


def _member_filters_sql(
    *,
    date_from: date | None,
    date_to: date | None,
    subreddits: list[str] | None,
    source: str | None = None,
) -> tuple[list[str], list[Any]]:
    filters: list[str] = []
    params: list[Any] = []
    if date_from is not None:
        filters.append("po.source_created::date >= %s")
        params.append(date_from)
    if date_to is not None:
        filters.append("po.source_created::date <= %s")
        params.append(date_to)
    if subreddits:
        filters.append("po.subreddit_name = ANY(%s)")
        params.append(subreddits)
    source_value = (source or "").strip().lower()
    if source_value and source_value != "all":
        filters.append(f"{_MEMBER_SOURCE_SQL} = %s")
        params.append(source_value)
    return filters, params


def count_cluster_members_filtered(
    cluster_id: int,
    *,
    date_from: date | None,
    date_to: date | None,
    subreddits: list[str] | None,
    source: str | None = None,
) -> int:
    filters, params = _member_filters_sql(
        date_from=date_from,
        date_to=date_to,
        subreddits=subreddits,
        source=source,
    )
    where_sql = ""
    if filters:
        where_sql = " AND " + " AND ".join(filters)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM pain_cluster_members pcm
                JOIN pain_objects po ON po.id = pcm.pain_id
                LEFT JOIN signal_events se ON se.id = po.signal_event_id
                WHERE pcm.cluster_id = %s
                {where_sql}
                """,
                tuple([cluster_id, *params]),
            )
            return int(cur.fetchone()[0])


def query_cluster_member_source_counts(
    cluster_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    subreddits: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return source distribution for a cluster's member quotes."""
    filters, filter_params = _member_filters_sql(
        date_from=date_from,
        date_to=date_to,
        subreddits=subreddits,
    )
    where_sql = ""
    if filters:
        where_sql = " AND " + " AND ".join(filters)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_MEMBER_SOURCE_SQL} AS source, COUNT(*)::INT AS count
                FROM pain_cluster_members pcm
                JOIN pain_objects po ON po.id = pcm.pain_id
                LEFT JOIN signal_events se ON se.id = po.signal_event_id
                WHERE pcm.cluster_id = %s
                {where_sql}
                GROUP BY 1
                ORDER BY count DESC, source ASC
                """,
                tuple([cluster_id, *filter_params]),
            )
            return [{"source": r[0], "count": r[1]} for r in cur.fetchall()]


def query_cluster_members(
    cluster_id: int,
    limit: int,
    offset: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    subreddits: list[str] | None = None,
    source: str | None = None,
) -> list[dict[str, Any]]:
    """Return a paginated list of cluster members (pain_objects joined)."""
    filters, filter_params = _member_filters_sql(
        date_from=date_from,
        date_to=date_to,
        subreddits=subreddits,
        source=source,
    )
    where_sql = ""
    if filters:
        where_sql = " AND " + " AND ".join(filters)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT po.id, po.pain_statement, po.raw_quote, po.subreddit_name,
                       COALESCE(se.author, po.source_author) AS source_author,
                       po.source_created, po.specificity,
                       po.sentiment_score, po.is_workaround, po.mentioned_tools,
                       po.community_score,
                       {_MEMBER_SOURCE_SQL} AS source,
                       COALESCE(se.surface, po.subreddit_name) AS source_surface
                FROM pain_cluster_members pcm
                JOIN pain_objects po ON po.id = pcm.pain_id
                LEFT JOIN signal_events se ON se.id = po.signal_event_id
                WHERE pcm.cluster_id = %s
                {where_sql}
                ORDER BY po.community_score DESC NULLS LAST, po.source_created DESC
                LIMIT %s OFFSET %s
                """,
                tuple([cluster_id, *filter_params, limit, offset]),
            )
            return [
                {
                    "pain_id": r[0],
                    "pain_statement": r[1],
                    "raw_quote": r[2],
                    "subreddit_name": r[3],
                    "source_author": r[4],
                    "source_created": r[5].isoformat() if r[5] else None,
                    "specificity": r[6],
                    "sentiment_score": r[7],
                    "is_workaround": r[8],
                    "mentioned_tools": r[9],
                    "source": r[11],
                    "source_surface": r[12],
                }
                for r in cur.fetchall()
            ]


# ── cluster compare (cross-run) ─────────────────────────────────────────────────

def query_lineage_match(
    cluster_id: int,
    ref_run_id: int,
    cmp_run_id: int,
) -> Optional[dict[str, Any]]:
    """Find the matching cluster for ``cluster_id`` across two runs via lineage.

    Tries parent→child first (ref is older), then child→parent (ref is newer).
    Returns None if no lineage record exists or cluster_lineage table is missing.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                # Direction 1: cluster belongs to ref_run, find child in cmp_run
                cur.execute(
                    """
                    SELECT parent_cluster AS ref_cluster_id,
                           child_cluster AS cmp_cluster_id,
                           relation, overlap_jaccard, shared_members
                    FROM cluster_lineage
                    WHERE parent_run_id = %s
                      AND parent_cluster = %s
                      AND child_run_id = %s
                    ORDER BY overlap_jaccard DESC NULLS LAST
                    LIMIT 1
                    """,
                    (ref_run_id, cluster_id, cmp_run_id),
                )
                row = cur.fetchone()
                if row is not None:
                    return dict(row)

                # Direction 2: cluster belongs to cmp_run, find parent in ref_run
                cur.execute(
                    """
                    SELECT parent_cluster AS ref_cluster_id,
                           child_cluster AS cmp_cluster_id,
                           relation, overlap_jaccard, shared_members
                    FROM cluster_lineage
                    WHERE parent_run_id = %s
                      AND child_run_id = %s
                      AND child_cluster = %s
                    ORDER BY overlap_jaccard DESC NULLS LAST
                    LIMIT 1
                    """,
                    (ref_run_id, cmp_run_id, cluster_id),
                )
                row = cur.fetchone()
                if row is not None:
                    return dict(row)

                return None
            except psycopg2.errors.UndefinedTable:
                cur.connection.rollback()
                return None


def query_snapshot_metrics(
    cluster_id: int,
    cluster_run_id: int,
    viability_run_id: int,
    audience_classifier_version: str,
) -> Optional[dict[str, Any]]:
    """Return key metrics for one cluster from the dashboard snapshot.

    Extracts columns from ``dashboard_market_opportunities`` and the 14
    dimension keys from ``row_data`` JSONB.  Returns None if the cluster
    is not present in the snapshot for the given run tuple.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                cur.execute(
                    """
                    SELECT
                        cluster_id,
                        member_count,
                        pain_count_last_4w,
                        pareto_layer,
                        balance_score,
                        competition_adjusted_pareto_layer,
                        competition_adjusted_balance_score,
                        competition_adjusted_s_axis,
                        external_supply_gap,
                        market_structure,
                        competition_confidence,
                        is_eligible,
                        s_axis_source,
                        burst_score,
                        burst_lift,
                        burst_zscore,
                        burst_recent_count,
                        burst_baseline_count,
                        burst_corpus_lift,
                        burst_label,
                        row_data->>'q_axis' AS q_axis,
                        row_data->>'m_axis' AS m_axis,
                        row_data->>'s_axis' AS s_axis,
                        row_data->>'coherence' AS coherence,
                        row_data->>'thread_diversity' AS thread_diversity,
                        row_data->>'author_diversity' AS author_diversity,
                        row_data->>'subreddit_count' AS subreddit_count,
                        row_data->>'specificity_mean' AS specificity_mean,
                        row_data->>'workaround_ratio' AS workaround_ratio,
                        row_data->>'separation' AS separation,
                        row_data->>'temporal_spread_days' AS temporal_spread_days,
                        row_data->>'q_specificity' AS q_specificity,
                        row_data->>'v_buildability' AS v_buildability,
                        row_data->>'v_icp_clarity' AS v_icp_clarity,
                        row_data->>'v_moat_potential' AS v_moat_potential,
                        row_data->>'slope_per_week' AS slope_per_week,
                        row_data->>'velocity_4w_ratio_normalized' AS velocity_4w_ratio_normalized
                    FROM dashboard_market_opportunities
                    WHERE cluster_run_id = %s
                      AND viability_run_id = %s
                      AND audience_classifier_version = %s
                      AND cluster_id = %s
                    """,
                    (
                        cluster_run_id,
                        viability_run_id,
                        audience_classifier_version,
                        cluster_id,
                    ),
                )
                row = cur.fetchone()
            except psycopg2.errors.UndefinedTable:
                cur.connection.rollback()
                return None

            if row is None:
                return None

            out = dict(row)
            # Convert numeric text from JSONB to float
            for key in (
                "q_axis", "m_axis", "s_axis", "competition_adjusted_s_axis",
                "balance_score", "competition_adjusted_balance_score",
                "external_supply_gap", "competition_confidence",
                "coherence", "thread_diversity", "author_diversity",
                "subreddit_count", "specificity_mean", "workaround_ratio",
                "separation", "temporal_spread_days", "q_specificity",
                "v_buildability", "v_icp_clarity", "v_moat_potential",
                "slope_per_week", "velocity_4w_ratio_normalized",
                "burst_score", "burst_lift", "burst_zscore",
            ):
                val = out.get(key)
                if val is not None and not isinstance(val, (int, float)):
                    try:
                        out[key] = float(val)
                    except (TypeError, ValueError):
                        out[key] = None
            return out


def query_compare_summary_rows(
    *,
    ref_run_id: int,
    cmp_run_id: int,
    ref_viability_run_id: int,
    cmp_viability_run_id: int,
    audience_classifier_version: str,
    cluster_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Return bulk compare inputs for all or selected comparison-run clusters."""
    cluster_filter_sql = ""
    cluster_filter_params: list[Any] = []
    if cluster_ids:
        cluster_filter_sql = "AND cluster_id = ANY(%s)"
        cluster_filter_params.append(cluster_ids)

    select_columns = """
        cmp.cluster_id AS cluster_id,
        COALESCE(l.parent_cluster, cmp.cluster_id) AS ref_cluster_id,
        cmp.cluster_id AS cmp_cluster_id,
        l.relation,
        l.overlap_jaccard,
        l.shared_members,

        ref.cluster_id AS ref_snapshot_cluster_id,
        ref.member_count AS ref_member_count,
        ref.pain_count_last_4w AS ref_pain_count_last_4w,
        ref.pareto_layer AS ref_pareto_layer,
        ref.balance_score AS ref_balance_score,
        ref.competition_adjusted_pareto_layer AS ref_competition_adjusted_pareto_layer,
        ref.competition_adjusted_balance_score AS ref_competition_adjusted_balance_score,
        ref.competition_adjusted_s_axis AS ref_competition_adjusted_s_axis,
        ref.s_axis_source AS ref_s_axis_source,
        ref.row_data->>'s_axis' AS ref_s_axis,

        cmp.cluster_id AS cmp_snapshot_cluster_id,
        cmp.member_count AS cmp_member_count,
        cmp.pain_count_last_4w AS cmp_pain_count_last_4w,
        cmp.pareto_layer AS cmp_pareto_layer,
        cmp.balance_score AS cmp_balance_score,
        cmp.competition_adjusted_pareto_layer AS cmp_competition_adjusted_pareto_layer,
        cmp.competition_adjusted_balance_score AS cmp_competition_adjusted_balance_score,
        cmp.competition_adjusted_s_axis AS cmp_competition_adjusted_s_axis,
        cmp.s_axis_source AS cmp_s_axis_source,
        cmp.row_data->>'s_axis' AS cmp_s_axis
    """

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                cur.execute(
                    f"""
                    WITH cmp AS (
                        SELECT *
                        FROM dashboard_market_opportunities
                        WHERE cluster_run_id = %s
                          AND viability_run_id = %s
                          AND audience_classifier_version = %s
                          {cluster_filter_sql}
                    ),
                    lineage_ranked AS (
                        SELECT
                            parent_cluster,
                            child_cluster,
                            relation,
                            overlap_jaccard,
                            shared_members,
                            ROW_NUMBER() OVER (
                                PARTITION BY child_cluster
                                ORDER BY overlap_jaccard DESC NULLS LAST
                            ) AS rn
                        FROM cluster_lineage
                        WHERE parent_run_id = %s
                          AND child_run_id = %s
                          AND child_cluster IN (SELECT cluster_id FROM cmp)
                    )
                    SELECT {select_columns}
                    FROM cmp
                    LEFT JOIN lineage_ranked l
                      ON l.child_cluster = cmp.cluster_id
                     AND l.rn = 1
                    LEFT JOIN dashboard_market_opportunities ref
                      ON ref.cluster_run_id = %s
                     AND ref.viability_run_id = %s
                     AND ref.audience_classifier_version = %s
                     AND ref.cluster_id = COALESCE(l.parent_cluster, cmp.cluster_id)
                    ORDER BY cmp.cluster_id ASC
                    """,
                    (
                        cmp_run_id,
                        cmp_viability_run_id,
                        audience_classifier_version,
                        *cluster_filter_params,
                        ref_run_id,
                        cmp_run_id,
                        ref_run_id,
                        ref_viability_run_id,
                        audience_classifier_version,
                    ),
                )
                return [dict(row) for row in cur.fetchall()]
            except psycopg2.errors.UndefinedTable:
                cur.connection.rollback()
                cur.execute(
                    f"""
                    WITH cmp AS (
                        SELECT *
                        FROM dashboard_market_opportunities
                        WHERE cluster_run_id = %s
                          AND viability_run_id = %s
                          AND audience_classifier_version = %s
                          {cluster_filter_sql}
                    )
                    SELECT {select_columns}
                    FROM cmp
                    LEFT JOIN dashboard_market_opportunities ref
                      ON ref.cluster_run_id = %s
                     AND ref.viability_run_id = %s
                     AND ref.audience_classifier_version = %s
                     AND ref.cluster_id = cmp.cluster_id
                    LEFT JOIN LATERAL (
                        SELECT
                            NULL::INT AS parent_cluster,
                            NULL::INT AS child_cluster,
                            NULL::TEXT AS relation,
                            NULL::DOUBLE PRECISION AS overlap_jaccard,
                            NULL::INT AS shared_members
                    ) l ON TRUE
                    ORDER BY cmp.cluster_id ASC
                    """,
                    (
                        cmp_run_id,
                        cmp_viability_run_id,
                        audience_classifier_version,
                        *cluster_filter_params,
                        ref_run_id,
                        ref_viability_run_id,
                        audience_classifier_version,
                    ),
                )
                return [dict(row) for row in cur.fetchall()]


# ── cluster filter options ─────────────────────────────────────────────────────

def query_cluster_filter_options(cluster_run_id: int) -> dict[str, Any]:
    """Return available source-created date range and Reddit surface counts."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    MIN(po.source_created::date) AS min_day,
                    MAX(po.source_created::date) AS max_day
                FROM pain_clusters pc
                JOIN pain_cluster_members pcm ON pcm.cluster_id = pc.id
                JOIN pain_objects po ON po.id = pcm.pain_id
                WHERE pc.run_id = %s
                  AND po.source_created IS NOT NULL
                """,
                (cluster_run_id,),
            )
            range_row = dict(cur.fetchone() or {})
            cur.execute(
                """
                SELECT po.subreddit_name, COUNT(*)::INT AS pain_count
                FROM pain_clusters pc
                JOIN pain_cluster_members pcm ON pcm.cluster_id = pc.id
                JOIN pain_objects po ON po.id = pcm.pain_id
                WHERE pc.run_id = %s
                  AND po.subreddit_name IS NOT NULL
                GROUP BY po.subreddit_name
                ORDER BY pain_count DESC, subreddit_name ASC
                LIMIT 500
                """,
                (cluster_run_id,),
            )
            subreddit_rows = [dict(row) for row in cur.fetchall()]
    return {
        "date_min": range_row.get("min_day").isoformat() if range_row.get("min_day") else None,
        "date_max": range_row.get("max_day").isoformat() if range_row.get("max_day") else None,
        "subreddits": subreddit_rows,
    }
