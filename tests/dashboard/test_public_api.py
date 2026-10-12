"""Public (Seedcast) API: shape, privacy and lineage-stable ids, with repositories mocked."""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from reddit_signal.dashboard.backend.services import public as svc

ANCHOR = date(2026, 9, 7)
ROW = {
    "cluster_id": 24525, "label": "[AI Assistant]: unreliable performance", "audience_type": "mixed",
    "icp_hint": "AI assistant users", "pain_count_last_4w": 4, "member_count": 50, "velocity_4w_ratio": 0.364,
    "burst_lift": 9.75, "burst_label": "Low-volume spike", "subreddit_count": 16, "workaround_ratio": 0.2,
    "snapshot_at": datetime(2026, 10, 1, 5, 25, tzinfo=timezone.utc),
    # Internal scores that must never reach the public payload:
    "balance_score": 0.8, "pareto_layer": 1, "v_rationale": "secret", "is_real_pain": True,
}


@pytest.fixture(autouse=True)
def repos(monkeypatch):
    svc._cache.clear()
    monkeypatch.setattr(svc, "default_ids", lambda: (16, 9))
    monkeypatch.setattr(svc, "week_anchor_for_run", lambda run: ANCHOR)
    monkeypatch.setattr(svc.clusters_repo, "query_snapshot_clusters", lambda *a, **k: [dict(ROW)])
    # Cluster 24525 continues 22001 from an earlier run: its public id is 22001.
    monkeypatch.setattr(svc.repo, "stable_ids", lambda run: {24525: 22001})
    monkeypatch.setattr(svc.repo, "member_summaries", lambda ids: {24525: {
        "statement": "as u/someone said, Claude disconnects", "top_surface": "hn:ask",
        "top_source": "hackernews", "first_seen": datetime(2026, 6, 12, tzinfo=timezone.utc)}})
    monkeypatch.setattr(svc.repo, "weekly_by_generation", lambda ids, start, end: {24525: {
        0: {ANCHOR - timedelta(weeks=1): (7, 0.002), ANCHOR: (2, 0.001)},
        1: {ANCHOR - timedelta(weeks=10): (5, 0.003)},          # history from the parent cluster
    }})
    monkeypatch.setattr(svc.repo, "member_breakdown", lambda cid: {
        "surfaces": [("hn:ask", "hackernews", 20), ("SaaS", "reddit", 11)],
        "sources": [("reddit", None, 30), ("hackernews", None, 20)],
        "tools": [("Claude", None, 38)]})
    monkeypatch.setattr(svc.clusters_repo, "query_cluster_members", lambda cid, limit, offset: [{
        "raw_quote": "thanks /u/Bob, it keeps failing", "pain_statement": "x", "source": "reddit",
        "source_surface": "SaaS", "source_created": "2026-06-19T10:00:00+00:00", "source_author": "Bob"}])


def test_snapshot_shape_and_values():
    snap = svc.snapshot_payload()
    assert snap["updatedAt"] == "2026-10-01T05:25:00+00:00"
    (c,) = snap["clusters"]
    assert c["id"] == 22001                          # lineage-stable, not the run-specific 24525
    assert c["audience"] == "Mixed" and c["bursting"] is True and c["workaroundPct"] == 20
    assert c["topCommunity"] == "Hacker News" and c["firstSeen"] == "Jun '26"
    assert c["statement"] == "as [user] said, Claude disconnects"
    assert len(c["series"]) == 26
    assert c["series"][-2:] == [7, 2] and c["series"][-11] == 5      # parent history stitched in


def test_snapshot_exposes_no_internal_scores_or_usernames():
    c = svc.snapshot_payload()["clusters"][0]
    assert set(c) == {"id", "name", "audience", "statement", "icp", "posts4w", "total", "ratio", "burst",
                      "bursting", "communities", "topCommunity", "firstSeen", "workaroundPct", "series"}


def test_detail_resolves_the_public_id():
    d = svc.cluster_detail_payload(22001)
    assert d["id"] == 22001 and len(d["history"]) == 52 and len(d["share"]) == 52
    assert d["share"][-1] == 0.1                     # share_global 0.001 -> 0.1 %
    assert d["quotes"] == [{"text": "thanks [user], it keeps failing", "source": "Reddit", "where": "r/SaaS",
                            "date": "2026-06-19", "url": None}]
    assert d["where"] == [{"name": "Hacker News", "pct": 65}, {"name": "r/SaaS", "pct": 35}]
    assert d["sources"] == [{"name": "Reddit", "pct": 60}, {"name": "Hacker News", "pct": 40}]
    assert d["tools"] == [{"name": "Claude", "n": 38}]
    assert "source_author" not in str(d) and "Bob" not in str(d)


def test_detail_also_accepts_the_current_cluster_id():
    assert svc.cluster_detail_payload(24525)["id"] == 22001


def test_unknown_cluster_is_none():
    assert svc.cluster_detail_payload(99999) is None


def test_routes():
    from reddit_signal import config
    from reddit_signal.api.main import create_app

    client = TestClient(create_app(config.Config(database_url="postgresql://u:p@localhost/db")))
    assert client.get("/api/public/snapshot").json()["clusters"][0]["id"] == 22001
    assert client.get("/api/public/clusters/22001").status_code == 200
    assert client.get("/api/public/clusters/99999").status_code == 404


def test_new_installation_without_a_cluster_run_gets_an_empty_snapshot(monkeypatch):
    def no_run():
        raise svc.NoClusterRun("no cluster run available")

    monkeypatch.setattr(svc, "default_ids", no_run)
    assert svc.snapshot_payload() == {"updatedAt": None, "clusters": []}
    assert svc.cluster_detail_payload(22001) is None
