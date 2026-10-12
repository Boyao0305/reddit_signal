"""Database access for deterministic emerging opportunities."""
from __future__ import annotations

from typing import Any

import psycopg2.extras
from psycopg2.extras import Json

from reddit_signal.dashboard.db import get_conn


def list_orphan_pains(*, cluster_run_id: int, lookback_days: int, limit: int | None = None) -> list[dict[str, Any]]:
    """Return recent embedded pains not assigned to any cluster in this run."""
    limit_sql = "LIMIT %(limit)s" if limit else ""
    params = {
        "cluster_run_id": cluster_run_id,
        "lookback_days": lookback_days,
        "limit": limit,
    }
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    po.id,
                    po.pain_statement,
                    po.raw_quote,
                    po.subreddit_name,
                    po.source_author,
                    po.source_created,
                    po.embedding::text AS embedding
                FROM pain_objects po
                WHERE po.embedding IS NOT NULL
                  AND po.source_created IS NOT NULL
                  AND po.source_created >= (
                      (NOW() AT TIME ZONE 'UTC')::date - (%(lookback_days)s::INT - 1)
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM pain_cluster_members pcm
                      JOIN pain_clusters pc ON pc.id = pcm.cluster_id
                      WHERE pcm.pain_id = po.id
                        AND pc.run_id = %(cluster_run_id)s
                  )
                ORDER BY po.source_created DESC, po.id DESC
                {limit_sql}
                """,
                params,
            )
            return [dict(r) for r in cur.fetchall()]


def create_run(
    *,
    cluster_run_id: int,
    lookback_days: int,
    min_size: int,
    eps: float,
    candidate_count: int,
) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO emerging_opportunity_runs (
                    cluster_run_id, lookback_days, min_size, eps, candidate_count
                )
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (cluster_run_id, lookback_days, min_size, eps, candidate_count),
            )
            run_id = int(cur.fetchone()[0])
        conn.commit()
        return run_id


def finish_run(*, emerging_run_id: int, opportunity_count: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE emerging_opportunity_runs
                SET status = 'succeeded',
                    opportunity_count = %s,
                    finished_at = NOW()
                WHERE id = %s
                """,
                (opportunity_count, emerging_run_id),
            )
        conn.commit()


def fail_run(*, emerging_run_id: int, error_log: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE emerging_opportunity_runs
                SET status = 'failed',
                    error_log = %s,
                    finished_at = NOW()
                WHERE id = %s
                """,
                (error_log, emerging_run_id),
            )
        conn.commit()


def save_opportunities(
    *,
    emerging_run_id: int,
    cluster_run_id: int,
    opportunities: list[dict[str, Any]],
) -> int:
    """Insert opportunities and members for a completed run."""
    inserted = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for opp in opportunities:
                cur.execute(
                    """
                    INSERT INTO emerging_opportunities (
                        emerging_run_id,
                        cluster_run_id,
                        label,
                        pain_count,
                        author_count,
                        subreddit_count,
                        coherence,
                        burst_score,
                        source_window_start,
                        source_window_end,
                        sample_pain_ids,
                        sample_quotes
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        emerging_run_id,
                        cluster_run_id,
                        opp["label"],
                        opp["pain_count"],
                        opp["author_count"],
                        opp["subreddit_count"],
                        opp["coherence"],
                        opp["burst_score"],
                        opp["source_window_start"],
                        opp["source_window_end"],
                        opp["sample_pain_ids"],
                        Json(opp["sample_quotes"]),
                    ),
                )
                opportunity_id = int(cur.fetchone()[0])
                inserted += 1
                members = [
                    (opportunity_id, member["pain_id"], member["similarity"])
                    for member in opp["members"]
                ]
                if members:
                    psycopg2.extras.execute_values(
                        cur,
                        """
                        INSERT INTO emerging_opportunity_members (
                            emerging_opportunity_id, pain_id, similarity
                        )
                        VALUES %s
                        ON CONFLICT DO NOTHING
                        """,
                        members,
                        page_size=500,
                    )
        conn.commit()
    return inserted

