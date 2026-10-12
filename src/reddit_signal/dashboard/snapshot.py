"""Dashboard market snapshot refresh helpers."""
from __future__ import annotations

import json
from typing import Any, Dict


from reddit_signal import db


def row_to_cluster(row: Dict[str, Any]) -> Dict[str, Any]:
    if "row_data" not in row:
        return dict(row)
    extra = {k: v for k, v in dict(row).items() if k != "row_data"}
    raw = row.get("row_data")
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raw = {}
    return {**raw, **extra}


def _connect():
    return db.connect()


def refresh_market_snapshot(
    cluster_run_id: int,
    viability_run_id: int,
    audience_classifier_version: str,
) -> Dict[str, Any]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM refresh_dashboard_market_opportunities(%s, %s, %s)
                """,
                (cluster_run_id, viability_run_id, audience_classifier_version),
            )
            row = cur.fetchone()
            conn.commit()

    if row is None:
        return {
            "refreshed_rows": 0,
            "refreshed_at": None,
            "skipped": False,
            "error": "refresh function returned no row",
        }
    if hasattr(row, "keys"):
        return dict(row)
    return {
        "refreshed_rows": row[0],
        "refreshed_at": row[1],
        "skipped": row[2],
        "error": row[3],
    }
