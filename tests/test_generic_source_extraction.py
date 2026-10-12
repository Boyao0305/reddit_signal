import json
from datetime import datetime, timezone

from reddit_signal.pain_extraction.extraction import build_prompt, parse_pain_json
from reddit_signal.pain_extraction.models import PostWithComments


def test_generic_source_prompt_uses_neutral_labels():
    pwc = PostWithComments(
        post_db_id=None,
        post_reddit_id="1001",
        subreddit_name="hn:ask",
        title="Ask HN: Better support tools?",
        selftext="Queues are painful.",
        author="founder",
        score=3,
        num_comments=0,
        post_created=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source_name="hackernews",
        source_table="hackernews_items",
        source_row_id="1001",
        signal_event_id=100,
    )

    prompt = build_prompt(pwc)

    assert "SOURCE: hackernews" in prompt
    assert "THREAD TITLE: Ask HN: Better support tools?" in prompt
    assert "SUBREDDIT:" not in prompt


def test_parse_pain_json_maps_generic_comment_signal_event_id():
    pwc = PostWithComments(
        post_db_id=None,
        post_reddit_id="1001",
        subreddit_name="hn:ask",
        title="Ask HN: Better support tools?",
        selftext="Queues are painful.",
        author="founder",
        score=3,
        num_comments=1,
        post_created=datetime(2026, 1, 1, tzinfo=timezone.utc),
        comments=[
            {
                "reddit_id": "1002",
                "id": None,
                "author": "operator",
                "body": "We copy tickets into spreadsheets every morning.",
                "score": 1,
                "created_utc": datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
                "signal_event_id": 101,
            }
        ],
        source_name="hackernews",
        source_table="hackernews_items",
        source_row_id="1001",
        signal_event_id=100,
    )
    raw = json.dumps([
        {
            "pain_statement": "Support teams manually copy tickets into spreadsheets.",
            "raw_quote": "We copy tickets into spreadsheets every morning.",
            "mentioned_tools": ["spreadsheets"],
            "is_workaround": True,
            "source": "comment_1002",
            "source_author": "operator",
        }
    ])

    pains = parse_pain_json(raw, pwc)

    assert len(pains) == 1
    assert pains[0].post_id is None
    assert pains[0].comment_id is None
    assert pains[0].signal_event_id == 101
    assert pains[0].source_author == "operator"
