from datetime import datetime, timezone

from reddit_signal.sources.base import SignalEvent
from reddit_signal.sources.hackernews_adapter import (
    HackerNewsAdapter,
    _clean_hn_text,
    _hn_row,
    _parse_story_lists,
)


def test_parse_story_lists_defaults_and_aliases():
    assert _parse_story_lists("") == ["newstories", "askstories", "showstories"]
    assert _parse_story_lists("new,ask show jobs nope") == [
        "newstories",
        "askstories",
        "showstories",
        "jobstories",
    ]


def test_hn_row_normalizes_root_and_comment():
    root = {
        "id": 1,
        "type": "story",
        "by": "pg",
        "time": 1_700_000_000,
        "title": "Ask HN: Better support tooling?",
        "text": "Support queues are painful",
        "score": 12,
        "descendants": 2,
        "kids": [2],
    }
    child = {
        "id": 2,
        "type": "comment",
        "by": "alice",
        "time": 1_700_000_100,
        "parent": 1,
        "text": "We manually triage every morning.",
    }

    root_row = _hn_row(root, "askstories", root_id=1, fallback_parent=None, depth=0)
    child_row = _hn_row(child, "askstories", root_id=1, fallback_parent=1, depth=1)

    assert root_row["parent_hn_id"] is None
    assert root_row["root_hn_id"] == 1
    assert root_row["kids"] == [2]
    assert child_row["parent_hn_id"] == 1
    assert child_row["root_hn_id"] == 1
    assert child_row["depth"] == 1


def test_clean_hn_text_removes_html():
    assert _clean_hn_text("Manual<br>work &amp; <i>pain</i>") == "Manual\nwork & pain"


def test_parent_and_comment_rows_map_to_signal_events():
    adapter = HackerNewsAdapter()
    parent = adapter._parent_row_to_event({
        "hn_id": 10,
        "item_type": "story",
        "by_author": "founder",
        "time_utc": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "title": "Ask HN: CRM pain",
        "text": "Existing tools are <b>slow</b>.",
        "url": None,
        "score": 9,
        "descendants": 3,
        "fetched_from": "askstories",
        "kids": [11],
        "parts": [],
        "deleted": False,
        "dead": False,
        "raw_json": {},
    })
    parent.id = 99
    child = adapter._comment_row_to_event({
        "hn_id": 11,
        "item_type": "comment",
        "by_author": "operator",
        "time_utc": datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
        "text": "We copy tickets into spreadsheets.",
        "score": None,
        "parent_hn_id": 10,
        "root_hn_id": 10,
        "depth": 1,
        "fetched_from": "askstories",
        "kids": [],
        "parts": [],
        "deleted": False,
        "dead": False,
        "raw_json": {},
    }, {"10": 99})

    assert isinstance(parent, SignalEvent)
    assert parent.source == "hackernews"
    assert parent.source_row_id == "10"
    assert parent.event_type == "story"
    assert parent.surface == "hn:ask"
    assert parent.text == "Existing tools are slow."
    assert parent.raw_meta["hn_id"] == 10
    assert child is not None
    assert child.parent_event_id == 99
    assert child.source_row_id == "11"
    assert child.raw_meta["root_hn_id"] == 10
