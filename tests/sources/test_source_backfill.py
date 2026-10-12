from reddit_signal.sources import source_backfill
from reddit_signal.sources.runtime_config import SourceRuntimeConfig


def test_plan_backfill_builds_chunks_for_all_supported_sources(monkeypatch):
    monkeypatch.setattr(source_backfill, "discover_adapters", lambda: None)
    monkeypatch.setattr(
        source_backfill.runtime_config,
        "list_source_configs",
        lambda include_disabled=False: [
            SourceRuntimeConfig(source="reddit", enabled=True),
            SourceRuntimeConfig(source="hackernews", enabled=True),
        ],
    )

    def fake_reddit_targets(*, scope, query):
        assert scope == "active"
        return [
            source_backfill.BackfillTarget(
                source="reddit",
                target_type="subreddit",
                target_id="SaaS",
                target_label="r/SaaS",
                query="SaaS",
                coverage_mode="full_window",
                coverage_note="reddit full window",
            )
        ]

    monkeypatch.setattr(source_backfill, "_reddit_targets", fake_reddit_targets)

    plan = source_backfill.plan_backfill(
        sources=["reddit", "hackernews"],
        date_from="2026-01-01",
        date_to="2026-01-15",
        chunk_days=7,
        scope="active",
        mode="fetch-project",
    )

    # reddit: 1 subreddit x 2 windows; hackernews: 3 default story lists x 2 windows
    assert plan["target_count"] == 4
    assert plan["chunk_count"] == 8
    assert plan["source_counts"] == {"reddit": 2, "hackernews": 6}
    assert plan["coverage_counts"] == {"full_window": 2, "recent_only": 6}
    first = plan["chunks"][0]
    assert first["source"] == "reddit"
    assert first["window_start"] == "2026-01-01T00:00:00Z"
    assert first["window_end"] == "2026-01-08T00:00:00Z"


def test_plan_backfill_rejects_dropped_sources(monkeypatch):
    monkeypatch.setattr(source_backfill, "discover_adapters", lambda: None)
    monkeypatch.setattr(
        source_backfill.runtime_config, "list_source_configs", lambda include_disabled=False: []
    )
    try:
        source_backfill.plan_backfill(
            sources=["youtube"], date_from="2026-01-01", date_to="2026-01-08", chunk_days=7
        )
    except ValueError as exc:
        assert "unsupported source" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_create_backfill_rejects_empty_plan(monkeypatch):
    monkeypatch.setattr(source_backfill, "discover_adapters", lambda: None)
    monkeypatch.setattr(
        source_backfill.runtime_config,
        "list_source_configs",
        lambda include_disabled=False: [SourceRuntimeConfig(source="reddit", enabled=True)],
    )
    monkeypatch.setattr(source_backfill, "_reddit_targets", lambda *, scope, query: [])

    try:
        source_backfill.create_backfill_job(
            sources=["reddit"],
            date_from="2026-01-01",
            date_to="2026-01-08",
            chunk_days=7,
            scope="active",
            mode="fetch-project",
        )
    except ValueError as exc:
        assert "no backfill chunks planned" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_extract_chunks_are_skipped_without_running_llm(monkeypatch):
    def fail_get_adapter(source):
        raise AssertionError("extract mode should not load source adapters")

    monkeypatch.setattr(source_backfill, "get_adapter", fail_get_adapter)

    result = source_backfill._process_chunk(
        {
            "mode": "extract",
            "source": "reddit",
            "query": "SaaS",
            "window_start": "2026-01-01T00:00:00Z",
            "window_end": "2026-01-08T00:00:00Z",
            "coverage_mode": "full_window",
            "coverage_note": "",
        }
    )

    assert result["skipped"] is True
    assert "pain extraction pipeline separately" in result["reason"]


def test_reddit_chunk_uses_scoped_projection(monkeypatch):
    calls = []

    class FakeRedditAdapter:
        def fetch(self, **kwargs):
            calls.append(("fetch", kwargs))
            return 3

        def project(self, **kwargs):
            raise AssertionError("source backfill should not run full reddit project")

        def project_subreddit_window(self, **kwargs):
            calls.append(("project_subreddit_window", kwargs))
            return 5

    monkeypatch.setattr(source_backfill, "get_adapter", lambda source: FakeRedditAdapter())
    monkeypatch.setattr(
        source_backfill.runtime_config,
        "get_source_config",
        lambda source: SourceRuntimeConfig(source=source, enabled=True, fetch_limit=100),
    )

    result = source_backfill._process_chunk(
        {
            "mode": "fetch-project",
            "source": "reddit",
            "target_id": "ADHD",
            "query": "ADHD",
            "window_start": "2026-01-01T00:00:00Z",
            "window_end": "2026-01-08T00:00:00Z",
            "coverage_mode": "full_window",
            "coverage_note": "",
        }
    )

    assert result["rows_fetched"] == 3
    assert result["rows_projected"] == 5
    assert calls[1][0] == "project_subreddit_window"
    assert calls[1][1]["subreddit"] == "ADHD"
