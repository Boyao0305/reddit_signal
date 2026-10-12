"""Repository for velocity, corpus-velocity, and sparkline queries.

All queries read ``pain_volume_weekly``, grouped by cluster_run_id + window.
"""

from __future__ import annotations

from typing import Any

from reddit_signal.dashboard.db import get_conn


# ── sparklines (shared with clusters list) ─────────────────────────────────────

def query_sparklines(
    cluster_run_id: int,
    cluster_ids: list[int],
    week_start,
    week_end,
) -> dict[int, dict]:
    """Return {cluster_id: {week_start: pain_count}} for the given window."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT cluster_id, week_start, SUM(pain_count)::int AS pc
                FROM pain_volume_weekly
                WHERE cluster_run_id = %s
                  AND cluster_id = ANY(%s)
                  AND week_start >= %s
                  AND week_start <= %s
                GROUP BY cluster_id, week_start
                """,
                (cluster_run_id, cluster_ids, week_start, week_end),
            )
            result: dict[int, dict] = {}
            for r in cur.fetchall():
                cid = r[0]
                ws = r[1]
                pc = r[2]
                result.setdefault(cid, {})[ws] = pc
    return result


# ── single-cluster velocity ────────────────────────────────────────────────────

def query_cluster_velocity(
    cluster_run_id: int,
    cluster_id: int,
    week_start,
    week_end,
) -> dict:
    """Return {week_start: (pain_count, share_global, share_field)}."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT week_start,
                       SUM(pain_count)::int AS pain_count,
                       AVG(share_global)::float AS share_global,
                       AVG(share_field)::float AS share_field
                FROM pain_volume_weekly
                WHERE cluster_run_id = %s
                  AND cluster_id = %s
                  AND week_start >= %s
                  AND week_start <= %s
                GROUP BY week_start
                """,
                (cluster_run_id, cluster_id, week_start, week_end),
            )
            return {r[0]: r for r in cur.fetchall()}


def query_velocity_median(
    cluster_run_id: int,
    cluster_id: int,
) -> float | None:
    """Return median weekly pain_count for a cluster, or None."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY weekly_total)::float
                FROM (
                    SELECT SUM(pain_count) AS weekly_total
                    FROM pain_volume_weekly
                    WHERE cluster_run_id = %s AND cluster_id = %s
                    GROUP BY week_start
                ) t
                """,
                (cluster_run_id, cluster_id),
            )
            row = cur.fetchone()
            return row[0] if row else None


# ── corpus-wide velocity ───────────────────────────────────────────────────────

def query_corpus_velocity(
    cluster_run_id: int,
    week_start,
    week_end,
) -> dict:
    """Return {week_start: total_pain} for the entire run."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT week_start, SUM(pain_count)::int AS total_pain
                FROM pain_volume_weekly
                WHERE cluster_run_id = %s
                  AND week_start >= %s
                  AND week_start <= %s
                GROUP BY week_start
                """,
                (cluster_run_id, week_start, week_end),
            )
            return {r[0]: r[1] for r in cur.fetchall()}
