from __future__ import annotations

import datetime as dt

import pytest

from reddit_signal.dashboard.backend.repositories import clusters as repo
from reddit_signal.dashboard.backend.services import clusters as cluster_service
from reddit_signal.dashboard.backend.services import search_expansion


@pytest.fixture(autouse=True)
def clear_caches():
    search_expansion.clear_search_expansion_cache()
    cluster_service._clusters_cache.clear()
    yield
    search_expansion.clear_search_expansion_cache()
    cluster_service._clusters_cache.clear()


def _patch_settings(monkeypatch, values: dict[str, str]) -> None:
    def fake_get_setting(key: str, default: str = "") -> str:
        return values.get(key, default)

    monkeypatch.setattr(search_expansion.runtime_settings, "get_setting", fake_get_setting)


def test_expand_search_terms_adds_deduped_llm_terms(monkeypatch):
    _patch_settings(
        monkeypatch,
        {
            "DASHBOARD_SEMANTIC_SEARCH_ENABLED": "true",
            "OPENROUTER_MODEL": "google/gemini-2.5-flash-lite",
            "OPENROUTER_API_KEY": "sk-test",
        },
    )
    monkeypatch.setattr(
        search_expansion,
        "_request_expansion",
        lambda *args: (
            '{"terms": ["CRM", "sales pipeline", "customer relationship management", '
            '"sales pipeline", "pipeline tracking", "lead management", '
            '"deal tracking", "account management"]}'
        ),
    )

    assert search_expansion.expand_search_terms(" crm ") == (
        "crm",
        "sales pipeline",
        "customer relationship management",
        "pipeline tracking",
        "lead management",
        "deal tracking",
        "account management",
    )


def test_expand_search_terms_falls_back_without_api_key(monkeypatch):
    _patch_settings(
        monkeypatch,
        {
            "DASHBOARD_SEMANTIC_SEARCH_ENABLED": "true",
            "OPENROUTER_MODEL": "google/gemini-2.5-flash-lite",
            "OPENROUTER_API_KEY": "",
        },
    )
    monkeypatch.setattr(
        search_expansion,
        "_request_expansion",
        lambda *args: pytest.fail("LLM should not be called without an API key"),
    )

    assert search_expansion.expand_search_terms("crm") == ("crm",)


def test_expand_search_terms_falls_back_for_short_disabled_bad_json_and_exception(monkeypatch):
    _patch_settings(
        monkeypatch,
        {
            "DASHBOARD_SEMANTIC_SEARCH_ENABLED": "true",
            "OPENROUTER_MODEL": "google/gemini-2.5-flash-lite",
            "OPENROUTER_API_KEY": "sk-test",
        },
    )
    called = {"count": 0}

    def fake_request(*args):
        called["count"] += 1
        return "not json"

    monkeypatch.setattr(search_expansion, "_request_expansion", fake_request)
    assert search_expansion.expand_search_terms("ai") == ("ai",)
    assert called["count"] == 0
    assert search_expansion.expand_search_terms("crm") == ("crm",)
    assert called["count"] == 1

    def raise_request(*args):
        raise RuntimeError("provider down")

    monkeypatch.setattr(search_expansion, "_request_expansion", raise_request)
    assert search_expansion.expand_search_terms("support tickets") == ("support tickets",)

    _patch_settings(
        monkeypatch,
        {
            "DASHBOARD_SEMANTIC_SEARCH_ENABLED": "false",
            "OPENROUTER_MODEL": "google/gemini-2.5-flash-lite",
            "OPENROUTER_API_KEY": "sk-test",
        },
    )
    assert search_expansion.expand_search_terms("retention") == ("retention",)


def test_cluster_filter_sql_or_matches_snapshot_terms():
    filters, params = repo.cluster_filter_sql(
        audience_type="all",
        include_ineligible=True,
        search="",
        search_terms=("crm", "sales pipeline"),
        min_members=None,
        min_pain_4w=None,
        snapshot=True,
    )

    assert filters == [
        "((label ILIKE %s OR row_data::text ILIKE %s) OR "
        "(label ILIKE %s OR row_data::text ILIKE %s))"
    ]
    assert params == ["%crm%", "%crm%", "%sales pipeline%", "%sales pipeline%"]


def test_cluster_filter_sql_or_matches_live_terms():
    filters, params = repo.cluster_filter_sql(
        audience_type="B2C",
        include_ineligible=False,
        search="",
        search_terms=("crm", "sales pipeline"),
        min_members=10,
        min_pain_4w=3,
        snapshot=False,
    )

    assert filters == [
        "audience_type = %s",
        "(is_eligible IS TRUE OR is_eligible IS NULL)",
        "(label ILIKE %s OR label ILIKE %s)",
        "COALESCE(member_count, 0) >= %s",
        "COALESCE(pain_count_last_4w, 0) >= %s",
    ]
    assert params == ["B2C", "%crm%", "%sales pipeline%", 10, 3]


def test_list_clusters_payload_passes_expanded_terms(monkeypatch):
    captured = {}
    monkeypatch.setattr(cluster_service, "expand_search_terms", lambda search: ("crm", "sales pipeline"))
    monkeypatch.setattr(cluster_service.repo, "has_dashboard_snapshot", lambda *a, **k: True)

    def fake_filter_sql(**kwargs):
        captured.update(kwargs)
        return ([], [])

    monkeypatch.setattr(cluster_service.repo, "cluster_filter_sql", fake_filter_sql)
    monkeypatch.setattr(cluster_service.repo, "query_snapshot_clusters", lambda **k: [])
    monkeypatch.setattr(cluster_service.repo, "query_corpus_burst_snapshot", lambda run_id: None)
    monkeypatch.setattr(cluster_service.repo, "query_freshness", lambda *args: {})
    monkeypatch.setattr(cluster_service.repo, "query_emerging_opportunities", lambda run_id: [])

    cluster_service.list_clusters_payload(
        cluster_run_id=10,
        viability_run_id=4,
        audience_classifier_version="v1",
        audience_type="B2C",
        include_ineligible=True,
        search="crm",
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

    assert captured["search"] == "crm"
    assert captured["search_terms"] == ("crm", "sales pipeline")


def test_list_clusters_payload_cache_key_uses_expanded_terms(monkeypatch):
    expansions = iter([("crm", "sales"), ("crm", "support")])
    calls = {"query": 0}

    monkeypatch.setattr(cluster_service, "expand_search_terms", lambda search: next(expansions))
    monkeypatch.setattr(cluster_service.repo, "has_dashboard_snapshot", lambda *a, **k: True)
    monkeypatch.setattr(cluster_service.repo, "cluster_filter_sql", lambda **k: ([], []))

    def fake_query_snapshot_clusters(**kwargs):
        calls["query"] += 1
        return []

    monkeypatch.setattr(cluster_service.repo, "query_snapshot_clusters", fake_query_snapshot_clusters)
    monkeypatch.setattr(cluster_service.repo, "query_corpus_burst_snapshot", lambda run_id: None)
    monkeypatch.setattr(cluster_service.repo, "query_freshness", lambda *args: {})
    monkeypatch.setattr(cluster_service.repo, "query_emerging_opportunities", lambda run_id: [])

    kwargs = dict(
        cluster_run_id=10,
        viability_run_id=4,
        audience_classifier_version="v1",
        audience_type="B2C",
        include_ineligible=True,
        search="crm",
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
    cluster_service.list_clusters_payload(**kwargs)
    cluster_service.list_clusters_payload(**kwargs)

    assert calls["query"] == 2
