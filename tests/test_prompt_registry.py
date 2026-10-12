from datetime import datetime, timezone

from reddit_signal.pain_extraction.prompts.registry import get_prompt_converter, list_prompt_sources
from reddit_signal.sources.base import EventBundle, SignalEvent


def test_hackernews_prompt_converter_preserves_signal_event_ids():
    parent = SignalEvent(
        id=100,
        source="hackernews",
        source_table="hackernews_items",
        source_row_id="1001",
        event_type="story",
        event_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        surface="hn:ask",
        author="founder",
        title="Ask HN: Support tooling pain",
        text="Ticket queues are painful.",
        engagement_score=5,
        raw_meta={"hn_id": 1001, "score": 10, "descendants": 1},
    )
    child = SignalEvent(
        id=101,
        source="hackernews",
        source_table="hackernews_items",
        source_row_id="1002",
        event_type="comment",
        event_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        author="operator",
        text="We copy tickets into spreadsheets every morning.",
        raw_meta={"hn_id": 1002, "depth": 1},
    )

    converter = get_prompt_converter("hackernews")
    pwc = converter(EventBundle(parent=parent, children=[child]))

    assert "hackernews" in list_prompt_sources()
    assert pwc.source_name == "hackernews"
    assert pwc.post_reddit_id == "1001"
    assert pwc.signal_event_id == 100
    assert pwc.comments[0]["reddit_id"] == "1002"
    assert pwc.comments[0]["signal_event_id"] == 101


def test_only_supported_prompt_sources_are_registered():
    sources = set(list_prompt_sources())

    assert {"reddit", "hackernews"} <= sources
    assert not sources & {"facebook", "youtube", "twitter", "github", "tiktok", "instagram"}
