from datetime import datetime, timedelta, timezone

import pytest

from reddit_signal.data_ingestion import pipeline
from reddit_signal.sources import reddit_adapter


@pytest.mark.parametrize("until", [None, "now"])  # the daily passes until=now (SourceRuntimeConfig.since_until)
def test_daily_window_ends_before_now_by_the_ingest_lag(monkeypatch, until):
    calls = {}
    monkeypatch.setattr(reddit_adapter, "_fetch_plan", lambda q, s, u: (["ynab"], 7, True))
    monkeypatch.setattr(reddit_adapter, "_mark_fetch_success", lambda subs: None)
    monkeypatch.setattr(pipeline, "run_pipeline", lambda **kw: calls.update(kw) or [])

    reddit_adapter.RedditAdapter().fetch(until=datetime.now(timezone.utc) if until else None)

    now = datetime.now(timezone.utc)
    before, after = calls["before_date"], calls["after_date"]
    assert now - pipeline.INGEST_LAG - timedelta(minutes=1) < before <= now - pipeline.INGEST_LAG
    assert before - after == timedelta(days=7)
