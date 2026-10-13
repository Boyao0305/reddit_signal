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
