"""Windowed extraction pages with a (event_time, id) cursor instead of re-scanning processed parents."""
from datetime import datetime, timedelta, timezone

from reddit_signal.pain_extraction import db as pdb
from reddit_signal.sources import db as sdb
from reddit_signal.sources.base import EventBundle, SignalEvent

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def parent(i: int) -> SignalEvent:
    # Two parents share each timestamp, so the id must break ties.
    return SignalEvent(source="hackernews", source_table="hackernews_items", source_row_id=str(i), event_type="story",
                       event_time=T0 - timedelta(hours=i // 2), id=1000 - i)


def test_each_batch_continues_after_the_previous_one(monkeypatch):
    parents = [parent(i) for i in range(7)]          # already newest first, as the query orders them
    calls = []

    def fake_batch(source, *, prompt_version, offset, limit, only_unprocessed, window, before=None):
        calls.append(before)
        rows = [p for p in parents if before is None or (p.event_time, p.id) < before]
        return [EventBundle(parent=p) for p in rows[:limit]]

    monkeypatch.setattr(sdb, "get_event_bundles_batch", fake_batch)
    monkeypatch.setattr("reddit_signal.pain_extraction.prompts.registry.get_prompt_converter",
                        lambda source: lambda bundle: bundle.parent.id)

    cursor, seen = {}, []
    window = (T0 - timedelta(days=30), T0 + timedelta(days=1))
    while batch := pdb.get_event_bundles_batch_as_pwc("hackernews", limit=3, window=window, cursor=cursor):
        seen += batch

    assert seen == [p.id for p in parents]          # every parent exactly once, in order
    assert calls[0] is None and calls[1] == (parents[2].event_time, parents[2].id)


def _run_extract(monkeypatch, n_batches, limit):
    from types import SimpleNamespace

    from reddit_signal.pain_extraction import main

    batches = [[SimpleNamespace(signal_event_id=b * 10 + i) for i in range(3)] for b in range(n_batches)]
    fetched, extracted = [], []

    def fake_fetch(source, **kw):
        assert kw["cursor"] is not None                # windowed runs page with the cursor
        fetched.append(len(fetched))
        return batches[len(fetched) - 1] if len(fetched) <= len(batches) else []

    def fake_extract(posts, **kw):
        extracted.append([p.signal_event_id for p in posts])
        return {"extracted": len(posts), "errors": 0, "total": len(posts)}

    monkeypatch.setattr(main.db, "get_event_bundles_batch_as_pwc", fake_fetch)
    monkeypatch.setattr(main.extraction, "extract_batch_concurrent", fake_extract)
    monkeypatch.setattr(main, "_job_event", lambda **_: None)
    monkeypatch.setattr(main, "get_use_signal_events", lambda: True)
    window = (T0 - timedelta(days=30), T0)
    result = main._cmd_ingest_extract_source(source="hackernews", limit=limit, workers=1, window=window)
    return extracted, result


def test_prefetched_batches_are_each_extracted_once_in_order(monkeypatch):
    extracted, result = _run_extract(monkeypatch, n_batches=4, limit=None)
    assert extracted == [[0, 1, 2], [10, 11, 12], [20, 21, 22], [30, 31, 32]]
    assert result["processed"] == 12


def test_prefetching_stops_at_the_limit(monkeypatch):
    extracted, result = _run_extract(monkeypatch, n_batches=4, limit=6)
    assert len(extracted) == 2 and result["processed"] == 6
