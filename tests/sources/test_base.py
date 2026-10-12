"""Unit tests for reddit_signal.sources.base — dataclasses + capabilities."""
from datetime import datetime, timezone

from reddit_signal.sources.base import (
    AdapterCapabilities,
    BaseAdapter,
    EventBundle,
    SignalEvent,
)


def test_signal_event_minimal():
    ev = SignalEvent(
        source="reddit",
        source_table="posts",
        source_row_id="42",
        event_type="post",
        event_time=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    assert ev.id is None
    assert ev.surface is None
    assert ev.parent_event_id is None
    assert ev.raw_meta == {}


def test_event_bundle_source():
    parent = SignalEvent(
        source="hackernews",
        source_table="hn_items",
        source_row_id="1",
        event_type="post",
        event_time=datetime.now(tz=timezone.utc),
    )
    bundle = EventBundle(parent=parent)
    assert bundle.source == "hackernews"
    assert bundle.children == []


def test_capabilities_or():
    a = AdapterCapabilities(produces_pain=True)
    b = AdapterCapabilities(produces_competitor=True)
    merged = a | b
    assert merged.produces_pain
    assert merged.produces_competitor
    assert not merged.produces_traction


def test_base_adapter_is_abstract():
    """BaseAdapter cannot be instantiated directly."""
    import pytest
    with pytest.raises(TypeError):
        BaseAdapter()
