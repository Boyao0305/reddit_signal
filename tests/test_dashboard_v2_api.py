"""Integration / smoke tests for Dashboard v2 API.

Covers:
- §8.22 — /api/v2/clusters smoke test with run_id=10, viability_run_id=4
- §8.21 — dimensions-source-of-truth invariant (score-config <-> per-row dimensions)
- §8.5..§8.10 — basic shape of each endpoint
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from reddit_signal.dashboard import api
from reddit_signal.dashboard.scoring import DIMENSION_KEYS


@pytest.fixture(scope="module")
def client(configured_readonly_db):
    # Read-only, unauthenticated analytics API (ADR 0003).
    return TestClient(api.app)


def fetch_all_clusters(client, **params):
    """Page through /api/v2/clusters (50 per page since pagination was added) and return every row."""
    rows, page = [], 1
    while True:
        r = client.get("/api/v2/clusters", params={**params, "page": page})
        assert r.status_code == 200, r.text
        body = r.json()
        rows.extend(body["clusters"])
        if not body["has_more"]:
            assert body["total_is_exact"] and body["total"] == len(rows)
            return rows
        page += 1


# ---- §8.22 + §8.21 ---------------------------------------------------------

def test_clusters_smoke(client):
    # Run 10 / viability run 4 is the historical baseline: 740 viable B2C clusters (LD-#33).
    clusters = fetch_all_clusters(client, cluster_run_id=10, viability_run_id=4)
    assert len(clusters) == 740, f"expected 740, got {len(clusters)}"

    # Every cluster has all 14 dimension keys present.
    expected = set(DIMENSION_KEYS)
    for c in clusters:
        got = set(c["dimensions"].keys())
        assert got == expected, f"cluster {c['cluster_id']} missing dims: {expected ^ got}"
        # Each dim entry has both 'value' and 'pct' fields.
        for k, v in c["dimensions"].items():
            assert "value" in v and "pct" in v


def test_dimensions_source_of_truth_invariant(client):
    cfg = client.get("/api/v2/score-config").json()
    cfg_keys = {d["key"] for d in cfg["dimensions"]}
    assert len(cfg_keys) == 14

    cls = client.get(
        "/api/v2/clusters",
        params={"cluster_run_id": 10, "viability_run_id": 4},
    ).json()
    assert cls["clusters"], "need clusters to verify invariant"
    for c in cls["clusters"]:
        row_keys = set(c["dimensions"].keys())
        assert row_keys == cfg_keys, (
            f"cluster {c['cluster_id']} dimensions drift from /score-config: "
            f"missing={cfg_keys - row_keys} extra={row_keys - cfg_keys}"
        )


# ---- §8.5..§8.10 -----------------------------------------------------------

def test_runs(client):
    r = client.get("/api/v2/runs")
    assert r.status_code == 200
    body = r.json()
    # Exactly one 'live' row, and the defaults point at it (whichever run is live today).
    live = [c for c in body["cluster_runs"] if c["status"] == "live"]
    assert len(live) == 1
    assert body["defaults"]["cluster_run_id"] == live[0]["id"]
    assert any(v["id"] == body["defaults"]["viability_run_id"] for v in body["viability_runs"])


def test_clusters_sparkline_shape(client):
    r = client.get("/api/v2/clusters", params={"cluster_run_id": 10, "viability_run_id": 4})
    body = r.json()
    for c in body["clusters"][:5]:
        assert len(c["sparkline"]) == 15
        assert len(c["sparkline_weeks"]) == 15
        assert c["sparkline_partial_from_index"] == 14


def test_cluster_velocity(client):
    r = client.get("/api/v2/clusters/16800/velocity", params={"cluster_run_id": 10})
    assert r.status_code == 200
    body = r.json()
    assert len(body["weeks"]) == 15
    assert body["weeks"][-1]["is_partial"] is True
    # Historical weeks should have share_global populated for this populous B2C cluster.
    historical = [w for w in body["weeks"][:-1] if w["pain_count"] is not None]
    assert historical, "expected at least one historical week with data"
    assert any(w["share_global"] is not None for w in historical)


def test_cluster_members(client):
    r = client.get("/api/v2/clusters/16800/members", params={"page": 1, "page_size": 20})
    assert r.status_code == 200
    body = r.json()
    assert body["page_size"] == 20
    assert len(body["members"]) <= 20
    assert body["total"] >= len(body["members"])


def test_corpus_velocity(client):
    r = client.get("/api/v2/corpus-velocity", params={"cluster_run_id": 10})
    assert r.status_code == 200
    body = r.json()
    assert len(body["weeks"]) == 15
    total = sum((w["total_pain"] or 0) for w in body["weeks"])
    # Spec says ≈ 250K; we observed 271,988. Allow generous band.
    assert 150_000 <= total <= 400_000, f"total_pain sum out of band: {total}"


def test_invalid_cluster_run_id_returns_404(client):
    r = client.get(
        "/api/v2/clusters",
        params={"cluster_run_id": 999999, "viability_run_id": 4},
    )
    assert r.status_code == 404


def test_score_config_shape(client):
    body = client.get("/api/v2/score-config").json()
    assert len(body["dimensions"]) == 14
    assert {g["key"] for g in body["groups"]} == {"quality", "viability", "velocity"}
    # pain_count_last_4w is intentionally NOT a scoring dimension.
    assert "pain_count_last_4w" not in {d["key"] for d in body["dimensions"]}
