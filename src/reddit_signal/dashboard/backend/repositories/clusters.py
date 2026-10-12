"""Repository for cluster listing, detail, and member queries.

Owns all SQL that reads or filters clusters, their audience metadata, viability
scores, and pain_cluster_members.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import psycopg2
import psycopg2.extras

from reddit_signal.dashboard.db import get_conn


# ── filter builder (pure string / param construction, no SQL execution) ────────


# ── snapshot / live decision ───────────────────────────────────────────────────


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


# ── cluster detail queries ─────────────────────────────────────────────────────


# ── cluster members queries ────────────────────────────────────────────────────

_MEMBER_SOURCE_SQL = """
LOWER(COALESCE(se.source, CASE WHEN po.subreddit_name IS NOT NULL THEN 'reddit' ELSE 'unknown' END))
"""


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


# ── cluster filter options ─────────────────────────────────────────────────────

