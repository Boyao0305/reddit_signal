"""Analysis runs (cluster runs): lookup, stats for the promotion gate, lifecycle transitions."""
from __future__ import annotations

from reddit_signal import db
from reddit_signal.core.promotion import RunStats

PROMOTED_STATUSES = ("live", "historical")


def live_run_id() -> int:
    """The run that daily assignment and the dashboard use."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id FROM analysis_runs
            WHERE status = 'live' AND cluster_count IS NOT NULL
            ORDER BY id DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("no live analysis run: the last monthly promotion is incomplete")
    return int(row[0])


def latest_viability_run_id(cluster_run_id: int) -> int | None:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM viability_runs WHERE cluster_run_id = %s ORDER BY id DESC LIMIT 1",
            (cluster_run_id,),
        )
        row = cur.fetchone()
    return int(row[0]) if row else None


_STATS_SQL = """
SELECT
    ar.id,
    COALESCE(ar.cluster_count, 0),
    (SELECT COUNT(*) FROM pain_clusters pc WHERE pc.run_id = ar.id AND pc.is_candidate),
    COALESCE(ar.total_points, 0) - COALESCE(ar.noise_count, 0),
    (SELECT COUNT(*) FROM cluster_audience ca
       WHERE ca.cluster_id IN (SELECT id FROM pain_clusters WHERE run_id = ar.id)),
    (SELECT COUNT(*) FROM cluster_quality_metrics q WHERE q.cluster_run_id = ar.id),
    COALESCE(vr.n_clusters, 0),
    COALESCE(vr.n_succeeded, 0),
    EXISTS (SELECT 1 FROM pain_volume_weekly w WHERE w.cluster_run_id = ar.id)
FROM analysis_runs ar
LEFT JOIN LATERAL (
    SELECT n_clusters, n_succeeded FROM viability_runs
    WHERE cluster_run_id = ar.id ORDER BY id DESC LIMIT 1
) vr ON TRUE
"""


def _to_stats(row) -> RunStats:
    return RunStats(
        run_id=int(row[0]),
        clusters=int(row[1]),
        candidates=int(row[2]),
        members=int(row[3]),
        audience_rows=int(row[4]),
        quality_rows=int(row[5]),
        viability_attempted=int(row[6]),
        viability_succeeded=int(row[7]),
        has_weekly_volume=bool(row[8]),
    )


def run_stats(run_id: int) -> RunStats:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(_STATS_SQL + " WHERE ar.id = %s", (run_id,))
        row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"analysis_runs.id={run_id} not found")
    return _to_stats(row)


def recent_promoted_stats(*, before_run_id: int, limit: int = 3) -> list[RunStats]:
    """Runs that were promoted at some point (live or historical), newest first, older than ``before_run_id``."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            _STATS_SQL
            + """
            WHERE ar.id < %s
              AND ar.status IN %s
              AND COALESCE(ar.is_legacy, FALSE) IS FALSE
            ORDER BY ar.id DESC
            LIMIT %s
            """,
            (before_run_id, PROMOTED_STATUSES, limit),
        )
        return [_to_stats(r) for r in cur.fetchall()]


def mark_building(run_id: int) -> None:
    _set_status(run_id, "building", finished=False)


def mark_failed(run_id: int) -> None:
    """Failed build; never demotes a run that is already live."""
    _set_status(run_id, "failed", finished=True)


def _set_status(run_id: int, status: str, *, finished: bool) -> None:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE analysis_runs
            SET status = %s,
                started_at = COALESCE(started_at, created_at, NOW())
                {", finished_at = COALESCE(finished_at, NOW())" if finished else ""}
            WHERE id = %s AND status <> 'live'
            """,
            (status, run_id),
        )
        conn.commit()


def promote(run_id: int) -> None:
    """Make ``run_id`` the live run and demote the previous one, atomically.

    The decision is made by ``core.promotion.decide_promotion``; this only applies it.
    """
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM analysis_runs WHERE status = 'live' OR id = %s FOR UPDATE", (run_id,))
        cur.execute("UPDATE analysis_runs SET status = 'historical' WHERE status = 'live' AND id <> %s", (run_id,))
        cur.execute(
            """
            UPDATE analysis_runs
            SET status = 'live', live_at = COALESCE(live_at, NOW()), finished_at = COALESCE(finished_at, NOW())
            WHERE id = %s
            """,
            (run_id,),
        )
        if cur.rowcount != 1:
            conn.rollback()
            raise RuntimeError(f"analysis_runs.id={run_id} not found; nothing promoted")
        conn.commit()


def sync_member_counts() -> int:
    """Make ``pain_clusters.member_count`` match ``pain_cluster_members`` (LD-#34). Returns rows changed."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH actual AS (
              SELECT cluster_id, COUNT(*) AS cnt FROM pain_cluster_members GROUP BY cluster_id
            )
            UPDATE pain_clusters pc
            SET member_count = COALESCE(a.cnt, 0)
            FROM actual a
            WHERE a.cluster_id = pc.id AND pc.member_count IS DISTINCT FROM a.cnt
            """
        )
        changed = cur.rowcount
        conn.commit()
    return changed


def snapshot_targets() -> list[tuple[int, int]]:
    """``(cluster_run_id, viability_run_id)`` pairs whose dashboard snapshot can be refreshed, live first."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT ar.id, vr.id
            FROM analysis_runs ar
            JOIN viability_runs vr ON vr.cluster_run_id = ar.id
            WHERE COALESCE(ar.is_legacy, FALSE) IS FALSE
              AND COALESCE(ar.status, 'historical') NOT IN ('building', 'failed')
            ORDER BY CASE WHEN ar.status = 'live' THEN 0 ELSE 1 END, ar.id DESC, vr.id DESC
            """
        )
        return [(int(a), int(b)) for a, b in cur.fetchall()]


def live_snapshot_target() -> tuple[int, int] | None:
    targets = snapshot_targets()
    return targets[0] if targets else None
