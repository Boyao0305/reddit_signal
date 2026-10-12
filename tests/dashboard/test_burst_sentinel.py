"""Tests for the burst sentinel (v2_034): corpus burst snapshot + Emerging label.

Covers:
  * query_corpus_burst_snapshot() return mapping and UndefinedTable defense.
  * list_clusters_payload() attaches corpus_burst to the response.
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import psycopg2

from reddit_signal.dashboard.backend.repositories import clusters as repo
from reddit_signal.dashboard.backend.services import clusters as cluster_service


def _fake_conn_ctx(query_side_effect):
    """Build a context manager that yields a mock conn/cursor pair.

    The cursor.fetchone() is delegated to query_side_effect so each test can
    script the returned row (or raise UndefinedTable).
    """
    cur = MagicMock()
    cur.fetchone.side_effect = query_side_effect
    cur.fetchall.return_value = []
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    conn.cursor.return_value.__enter__.return_value = cur
    conn.cursor.return_value.__exit__.return_value = False
    return conn


def test_query_corpus_burst_snapshot_maps_row():
    day = dt.date(2026, 6, 23)
    snap = dt.datetime(2026, 6, 23, 5, 0, 0, tzinfo=dt.timezone.utc)
    row = {
        "cluster_run_id": 10,
        "day_anchor": day,
        "total_recent": 120,
        "total_baseline": 900.0,
        "orphan_recent": 40,
        "orphan_baseline": 50.0,
        "orphan_lift": 7.4,
        "orphan_zscore": 3.1,
        "orphan_label": "Bursting",
        "corpus_lift": 1.05,
        "snapshot_at": snap,
    }
    conn = _fake_conn_ctx(lambda: row)
    with patch.object(repo, "get_conn", return_value=conn):
        out = repo.query_corpus_burst_snapshot(10)

    assert out is not None
    assert out["cluster_run_id"] == 10
    assert out["day_anchor"] == "2026-06-23"
    assert out["orphan_label"] == "Bursting"
    assert out["orphan_lift"] == 7.4
    assert out["snapshot_at"] == "2026-06-23T05:00:00+00:00"


def test_query_corpus_burst_snapshot_undefined_table_returns_none():
    def raise_undefined():
        raise psycopg2.errors.UndefinedTable("relation does not exist")
    cur = MagicMock()
    cur.fetchone.side_effect = raise_undefined
    cur.connection.rollback.return_value = None
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    conn.cursor.return_value.__enter__.return_value = cur
    conn.cursor.return_value.__exit__.return_value = False

    with patch.object(repo, "get_conn", return_value=conn):
        assert repo.query_corpus_burst_snapshot(10) is None


def test_query_corpus_burst_snapshot_no_row_returns_none():
    conn = _fake_conn_ctx(lambda: None)
    with patch.object(repo, "get_conn", return_value=conn):
        assert repo.query_corpus_burst_snapshot(10) is None


def test_list_clusters_payload_includes_corpus_burst(monkeypatch):
    # Force the snapshot path with an empty cluster list so only a handful of
    # repo calls are exercised. Clear the service cache so prior tests do not
    # leak a cached payload with the same (run_id, viability, audience) key.
    cluster_service._clusters_cache.clear()
    monkeypatch.setattr(
        cluster_service.repo, "has_dashboard_snapshot", lambda *a, **k: True
    )
    monkeypatch.setattr(
        cluster_service.repo,
        "cluster_filter_sql",
        lambda **k: ([], []),
    )
    monkeypatch.setattr(
        cluster_service.repo, "query_snapshot_clusters", lambda **k: []
    )
    monkeypatch.setattr(
        cluster_service.repo,
        "query_corpus_burst_snapshot",
        lambda run_id: {
            "cluster_run_id": run_id,
            "day_anchor": "2026-06-23",
            "total_recent": 120,
            "total_baseline": 900.0,
            "orphan_recent": 40,
            "orphan_baseline": 50.0,
            "orphan_lift": 7.4,
            "orphan_zscore": 3.1,
            "orphan_label": "Bursting",
            "corpus_lift": 1.05,
            "snapshot_at": "2026-06-23T05:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        cluster_service.repo,
        "query_freshness",
        lambda *args: {"signal_data_through_day": "2026-06-23"},
    )
    monkeypatch.setattr(
        cluster_service.repo,
        "query_emerging_opportunities",
        lambda run_id: [{"id": 1, "label": "new pain"}],
    )

    anchor_week = {"weeks": [dt.date(2026, 5, 5), dt.date(2026, 6, 23)]}
    payload = cluster_service.list_clusters_payload(
        cluster_run_id=10,
        viability_run_id=4,
        audience_classifier_version="v1",
        audience_type="all",
        include_ineligible=False,
        search="",
        min_members=None,
        min_pain_4w=None,
        sort="default",
        date_from=None,
        date_to=None,
        subreddits=[],
        min_window_pain=None,
        page=1,
        page_size=50,
        anchor_week=anchor_week,
    )

    assert "corpus_burst" in payload
    assert payload["corpus_burst"]["orphan_label"] == "Bursting"
    assert payload["corpus_burst"]["cluster_run_id"] == 10
    assert payload["freshness"]["signal_data_through_day"] == "2026-06-23"
    assert payload["emerging_opportunities"][0]["label"] == "new pain"
    assert payload["clusters"] == []


def test_list_clusters_payload_corpus_burst_none_when_absent(monkeypatch):
    cluster_service._clusters_cache.clear()
    monkeypatch.setattr(
        cluster_service.repo, "has_dashboard_snapshot", lambda *a, **k: True
    )
    monkeypatch.setattr(
        cluster_service.repo,
        "cluster_filter_sql",
        lambda **k: ([], []),
    )
    monkeypatch.setattr(
        cluster_service.repo, "query_snapshot_clusters", lambda **k: []
    )
    monkeypatch.setattr(
        cluster_service.repo, "query_corpus_burst_snapshot", lambda run_id: None
    )
    monkeypatch.setattr(cluster_service.repo, "query_freshness", lambda *args: {})
    monkeypatch.setattr(
        cluster_service.repo,
        "query_emerging_opportunities",
        lambda run_id: [],
    )

    anchor_week = {"weeks": [dt.date(2026, 5, 5), dt.date(2026, 6, 23)]}
    payload = cluster_service.list_clusters_payload(
        cluster_run_id=10,
        viability_run_id=4,
        audience_classifier_version="v1",
        audience_type="all",
        include_ineligible=False,
        search="",
        min_members=None,
        min_pain_4w=None,
        sort="default",
        date_from=None,
        date_to=None,
        subreddits=[],
        min_window_pain=None,
        page=1,
        page_size=50,
        anchor_week=anchor_week,
    )

    assert payload["corpus_burst"] is None
    assert payload["emerging_opportunities"] == []
