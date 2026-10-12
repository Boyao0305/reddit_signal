from __future__ import annotations

import datetime as dt

from reddit_signal.dashboard.backend.services import clusters as cluster_service


def test_cluster_detail_payload_includes_competition(monkeypatch):
    monkeypatch.setattr(cluster_service.repo, "query_cluster_audience", lambda *args: None)
    monkeypatch.setattr(cluster_service.repo, "query_cluster_viability", lambda *args: None)
    monkeypatch.setattr(
        cluster_service.repo,
        "query_cluster_competition",
        lambda *args: {
            "competition_run_id": 3,
            "market_structure": "adjacent_only",
            "research_confidence": 0.8,
            "competitors": [{"name": "Existing Tool"}],
        },
    )

    payload = cluster_service.cluster_detail_payload(
        cluster_id=123,
        cluster_run_id=10,
        viability_run_id=4,
        audience_classifier_version="v1",
    )

    assert payload["cluster_id"] == 123
    assert payload["competition"]["competition_run_id"] == 3
    assert payload["competition"]["competitors"][0]["name"] == "Existing Tool"


def test_cluster_members_payload_threads_source_filter_and_counts(monkeypatch):
    cluster_service._members_cache.clear()
    captured = {}

    def fake_count(cluster_id, **kwargs):
        captured["count"] = (cluster_id, kwargs)
        return 1

    def fake_source_counts(cluster_id, **kwargs):
        captured["source_counts"] = (cluster_id, kwargs)
        return [{"source": "reddit", "count": 3}, {"source": "twitter", "count": 1}]

    def fake_members(cluster_id, limit, offset, **kwargs):
        captured["members"] = (cluster_id, limit, offset, kwargs)
        return [
            {
                "pain_id": 42,
                "source": "twitter",
                "source_surface": "danshipit",
                "pain_statement": "Churn is hard.",
            }
        ]

    monkeypatch.setattr(cluster_service.repo, "count_cluster_members_filtered", fake_count)
    monkeypatch.setattr(cluster_service.repo, "query_cluster_member_source_counts", fake_source_counts)
    monkeypatch.setattr(cluster_service.repo, "query_cluster_members", fake_members)

    payload = cluster_service.cluster_members_payload(
        cluster_id=21058,
        page=1,
        page_size=20,
        source="Twitter",
    )

    assert payload["total"] == 1
    assert payload["filter_window"]["source"] == "twitter"
    assert payload["source_counts"][1] == {"source": "twitter", "count": 1}
    assert payload["members"][0]["source_surface"] == "danshipit"
    assert captured["count"][1]["source"] == "twitter"
    assert captured["members"][1:3] == (20, 0)
    assert captured["members"][3]["source"] == "twitter"


def test_list_clusters_payload_fills_canonical_competition_fields_without_active_run(monkeypatch):
    cluster_service._clusters_cache.clear()
    monkeypatch.setattr(cluster_service.repo, "has_dashboard_snapshot", lambda *a, **k: True)
    monkeypatch.setattr(cluster_service.repo, "cluster_filter_sql", lambda **k: ([], []))
    monkeypatch.setattr(
        cluster_service.repo,
        "query_snapshot_clusters",
        lambda **k: [
            {
                "cluster_id": 123,
                "label": "Pain",
                "audience_type": "B2C",
                "member_count": 20,
                "pain_count_last_4w": 8,
                "pareto_layer": 2,
                "balance_score": 0.42,
                "q_axis": 0.7,
                "m_axis": 0.6,
                "s_axis": 0.5,
                "competition_adjusted_s_axis": None,
                "competition_adjusted_balance_score": None,
                "competition_adjusted_pareto_layer": None,
                "s_axis_source": "missing",
            }
        ],
    )
    monkeypatch.setattr(
        cluster_service.velocity_repo,
        "query_sparklines",
        lambda *a, **k: {123: {}},
    )
    monkeypatch.setattr(cluster_service.repo, "query_corpus_burst_snapshot", lambda run_id: None)
    monkeypatch.setattr(cluster_service.repo, "query_freshness", lambda *args: {})
    monkeypatch.setattr(cluster_service.repo, "query_emerging_opportunities", lambda run_id: [])

    payload = cluster_service.list_clusters_payload(
        cluster_run_id=10,
        viability_run_id=4,
        audience_classifier_version="v1",
        audience_type="B2C",
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
        anchor_week={"weeks": [dt.date(2026, 6, 16), dt.date(2026, 6, 23)]},
    )

    cluster = payload["clusters"][0]
    assert cluster["competition_adjusted_s_axis"] == 0.5
    assert cluster["competition_adjusted_balance_score"] == 0.42
    assert cluster["competition_adjusted_pareto_layer"] == 2
    assert cluster["s_axis_source"] == "reddit_proxy"


def test_get_cluster_compare_summaries_batches_deltas(monkeypatch):
    monkeypatch.setattr(
        cluster_service.repo,
        "query_compare_summary_rows",
        lambda **k: [
            {
                "cluster_id": 201,
                "ref_cluster_id": 101,
                "cmp_cluster_id": 201,
                "relation": "continued",
                "overlap_jaccard": 0.71,
                "shared_members": 12,
                "ref_snapshot_cluster_id": 101,
                "ref_member_count": 20,
                "ref_pain_count_last_4w": 5,
                "ref_pareto_layer": 3,
                "ref_balance_score": 0.30,
                "ref_competition_adjusted_pareto_layer": 3,
                "ref_competition_adjusted_balance_score": 0.30,
                "ref_competition_adjusted_s_axis": 0.40,
                "ref_s_axis_source": "competition_adjusted",
                "ref_s_axis": 0.35,
                "cmp_snapshot_cluster_id": 201,
                "cmp_member_count": 35,
                "cmp_pain_count_last_4w": 9,
                "cmp_pareto_layer": 2,
                "cmp_balance_score": 0.55,
                "cmp_competition_adjusted_pareto_layer": 2,
                "cmp_competition_adjusted_balance_score": 0.55,
                "cmp_competition_adjusted_s_axis": 0.62,
                "cmp_s_axis_source": "competition_adjusted",
                "cmp_s_axis": 0.50,
            },
            {
                "cluster_id": 202,
                "ref_cluster_id": 202,
                "cmp_cluster_id": 202,
                "relation": None,
                "overlap_jaccard": None,
                "shared_members": None,
                "ref_snapshot_cluster_id": None,
                "cmp_snapshot_cluster_id": 202,
                "cmp_member_count": 8,
                "cmp_pain_count_last_4w": 3,
                "cmp_pareto_layer": 5,
                "cmp_balance_score": 0.12,
                "cmp_competition_adjusted_pareto_layer": 5,
                "cmp_competition_adjusted_balance_score": 0.12,
                "cmp_competition_adjusted_s_axis": 0.20,
                "cmp_s_axis_source": "competition_adjusted",
                "cmp_s_axis": 0.20,
            },
        ],
    )

    rows = cluster_service.get_cluster_compare_summaries(
        ref_run_id=3,
        cmp_run_id=7,
        viability_run_id=4,
        ref_viability_run_id=2,
        cmp_viability_run_id=6,
        audience_classifier_version="v1",
    )

    assert rows[0]["cluster_id"] == 201
    assert rows[0]["matched"] is True
    assert rows[0]["ref_cluster_id"] == 101
    assert rows[0]["member_count_delta"]["absolute_change"] == 15
    assert rows[0]["pareto_layer_delta"]["absolute_change"] == -1
    assert abs(rows[0]["balance_score_delta"]["absolute_change"] - 0.25) < 1e-9

    assert rows[1]["cluster_id"] == 202
    assert rows[1]["matched"] is False
    assert rows[1]["relation"] == "unmatched"
    assert rows[1]["member_count_delta"]["direction"] == "up"
