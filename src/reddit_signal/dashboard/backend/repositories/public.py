"""SQL for the public read-only API (Seedcast)."""
from __future__ import annotations

from typing import Any

from reddit_signal.dashboard.db import get_conn

_MEMBER_SOURCE_SQL = "LOWER(COALESCE(se.source, CASE WHEN po.subreddit_name IS NOT NULL THEN 'reddit' ELSE 'unknown' END))"
_SURFACE_SQL = "COALESCE(se.surface, po.subreddit_name)"

# Follow 'continued' lineage edges back to the earliest ancestor: the public id stays the same across
# monthly re-clusters for as long as a cluster keeps continuing.
_STABLE_IDS_SQL = """
WITH RECURSIVE chain AS (
    SELECT pc.id AS cluster_id, pc.id AS ancestor, 0 AS depth
    FROM pain_clusters pc
    WHERE pc.run_id = %s
  UNION ALL
    SELECT chain.cluster_id, cl.parent_cluster, chain.depth + 1
    FROM chain
    JOIN cluster_lineage cl ON cl.child_cluster = chain.ancestor AND cl.relation = 'continued'
    WHERE cl.parent_cluster IS NOT NULL AND chain.depth < 50
)
SELECT DISTINCT ON (cluster_id) cluster_id, ancestor
FROM chain
ORDER BY cluster_id, depth DESC, ancestor
"""


def stable_ids(cluster_run_id: int) -> dict[int, int]:
    """``{cluster_id: public_id}`` for every cluster of the run."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(_STABLE_IDS_SQL, (cluster_run_id,))
        return {int(cid): int(root) for cid, root in cur.fetchall()}


def member_summaries(cluster_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Per cluster: the pain statement closest to the centroid (the most representative one),
    the most common surface, and the earliest pain date."""
    if not cluster_ids:
        return {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            WITH m AS (
                SELECT pcm.cluster_id, po.pain_statement, po.source_created,
                       po.embedding <=> pc.centroid AS distance,
                       {_SURFACE_SQL} AS surface, {_MEMBER_SOURCE_SQL} AS source
                FROM pain_cluster_members pcm
                JOIN pain_clusters pc ON pc.id = pcm.cluster_id
                JOIN pain_objects po ON po.id = pcm.pain_id
                LEFT JOIN signal_events se ON se.id = po.signal_event_id
                WHERE pcm.cluster_id = ANY(%s)
            ),
            top_statement AS (
                SELECT DISTINCT ON (cluster_id) cluster_id, pain_statement
                FROM m ORDER BY cluster_id, distance ASC NULLS LAST, source_created DESC
            ),
            top_surface AS (
                SELECT DISTINCT ON (cluster_id) cluster_id, surface, source
                FROM (SELECT cluster_id, surface, source, COUNT(*) AS n FROM m
                      WHERE surface IS NOT NULL GROUP BY 1, 2, 3) s
                ORDER BY cluster_id, n DESC, surface
            ),
            first_seen AS (SELECT cluster_id, MIN(source_created) AS first_seen FROM m GROUP BY 1)
            SELECT f.cluster_id, ts.pain_statement, tf.surface, tf.source, f.first_seen
            FROM first_seen f
            LEFT JOIN top_statement ts USING (cluster_id)
            LEFT JOIN top_surface tf USING (cluster_id)
            """,
            (cluster_ids,),
        )
        return {
            int(r[0]): {"statement": r[1], "top_surface": r[2], "top_source": r[3], "first_seen": r[4]}
            for r in cur.fetchall()
        }


def member_breakdown(cluster_id: int, *, top: int = 6) -> dict[str, list[tuple[str, str | None, int]]]:
    """Counts by surface, by source and by mentioned tool for one cluster."""
    with get_conn() as conn, conn.cursor() as cur:
        base = f"""
            FROM pain_cluster_members pcm
            JOIN pain_objects po ON po.id = pcm.pain_id
            LEFT JOIN signal_events se ON se.id = po.signal_event_id
            WHERE pcm.cluster_id = %s
        """
        cur.execute(
            f"SELECT {_SURFACE_SQL}, {_MEMBER_SOURCE_SQL}, COUNT(*) {base} GROUP BY 1, 2 ORDER BY 3 DESC",
            (cluster_id,),
        )
        surfaces = [(s, src, int(n)) for s, src, n in cur.fetchall()]
        cur.execute(f"SELECT {_MEMBER_SOURCE_SQL}, NULL, COUNT(*) {base} GROUP BY 1 ORDER BY 3 DESC", (cluster_id,))
        sources = [(s, None, int(n)) for s, _, n in cur.fetchall()]
        cur.execute(
            f"""
            SELECT MIN(tool), NULL, COUNT(*) AS n
            FROM (SELECT TRIM(UNNEST(po.mentioned_tools)) AS tool {base}) t
            WHERE tool <> ''
            GROUP BY LOWER(tool)
            ORDER BY n DESC, MIN(tool)
            LIMIT %s
            """,
            (cluster_id, top),
        )
        tools = [(t, None, int(n)) for t, _, n in cur.fetchall()]
    return {"surfaces": surfaces, "sources": sources, "tools": tools}


def weekly_by_generation(
    cluster_ids: list[int], week_start, week_end
) -> dict[int, dict[int, dict]]:
    """``{cluster_id: {depth: {week_start: (pain_count, share_global)}}}``.

    Depth 0 is the cluster itself; depth n its ancestor n runs back along 'continued' lineage edges,
    so a chart can show weeks from before the current run's window.
    """
    if not cluster_ids:
        return {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH RECURSIVE chain AS (
                SELECT pc.id AS cluster_id, pc.id AS ancestor, pc.run_id AS ancestor_run, 0 AS depth
                FROM pain_clusters pc WHERE pc.id = ANY(%s)
              UNION ALL
                SELECT chain.cluster_id, cl.parent_cluster, cl.parent_run_id, chain.depth + 1
                FROM chain
                JOIN cluster_lineage cl ON cl.child_cluster = chain.ancestor AND cl.relation = 'continued'
                WHERE cl.parent_cluster IS NOT NULL AND chain.depth < 50
            )
            SELECT chain.cluster_id, chain.depth, w.week_start,
                   MAX(w.pain_count_sum) AS pain_count, MAX(w.share_sum) AS share_global
            FROM chain
            JOIN (
                SELECT cluster_run_id, cluster_id, week_start,
                       SUM(pain_count) AS pain_count_sum, SUM(share_global) AS share_sum
                FROM pain_volume_weekly
                WHERE week_start >= %s AND week_start <= %s
                GROUP BY 1, 2, 3
            ) w ON w.cluster_run_id = chain.ancestor_run AND w.cluster_id = chain.ancestor
            GROUP BY 1, 2, 3
            """,
            (cluster_ids, week_start, week_end),
        )
        out: dict[int, dict[int, dict]] = {}
        for cid, depth, week, count, share in cur.fetchall():
            out.setdefault(int(cid), {}).setdefault(int(depth), {})[week] = (
                int(count or 0), float(share) if share is not None else None)
    return out
