from datetime import date, datetime, timezone

import pytest

from reddit_signal.core.public_view import (
    audience_label,
    community_label,
    month_label,
    percentages,
    source_label,
    stitch_weeks,
    strip_usernames,
)


@pytest.mark.parametrize(
    ("raw", "label"),
    [("B2C", "B2C"), ("b2b", "B2B"), ("mixed", "Mixed"), ("unclear", "Unclear"), (None, "Unclear"), ("", "Unclear")],
)
def test_audience_label(raw, label):
    assert audience_label(raw) == label


@pytest.mark.parametrize(
    ("surface", "source", "label"),
    [
        ("ADHD", "reddit", "r/ADHD"),
        ("r/ADHD", "reddit", "r/ADHD"),          # already prefixed
        ("ADHD", None, "r/ADHD"),                # legacy rows without a source are Reddit
        ("hn:ask", "hackernews", "Hacker News"),
        ("hn:show", None, "Hacker News"),
        (None, "hackernews", "Hacker News"),
        (None, None, "Unknown"),
    ],
)
def test_community_label(surface, source, label):
    assert community_label(surface, source) == label


def test_source_label():
    assert [source_label(s) for s in ("reddit", "hackernews", "some_new_source", None)] == [
        "Reddit", "Hacker News", "Some New Source", "Unknown"]


def test_month_label():
    assert month_label(datetime(2026, 3, 14, tzinfo=timezone.utc)) == "Mar '26"
    assert month_label(None) == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("as u/someone said, invoices take hours", "as [user] said, invoices take hours"),
        ("thanks /u/Some_Body-1!", "thanks [user]!"),
        ("ping @jane_doe about it", "ping [user] about it"),
        ("I use QuickBooks", "I use QuickBooks"),
        ("email me at a@b.com", "email me at a@b.com"),     # an address is not a mention
        ("see r/ADHD for more", "see r/ADHD for more"),     # subreddits stay
        (None, ""),
    ],
)
def test_strip_usernames(text, expected):
    assert strip_usernames(text) == expected


def test_percentages_merge_rank_and_round():
    counts = [("r/ADHD", 50), ("r/Anxiety", 30), ("r/ADHD", 10), ("Hacker News", 10)]
    assert percentages(counts, top=2) == [{"name": "r/ADHD", "pct": 60}, {"name": "r/Anxiety", "pct": 30}]
    assert percentages([]) == []


def test_stitch_weeks_prefers_the_closest_generation():
    w = [date(2026, 6, 1), date(2026, 6, 8), date(2026, 6, 15), date(2026, 6, 22)]
    by_depth = {
        0: {w[2]: 7, w[3]: 9},          # the current cluster (its run starts mid-June)
        1: {w[0]: 3, w[1]: 4, w[2]: 99},  # its parent; w[2] overlaps and is ignored
    }
    assert stitch_weeks(by_depth, w) == [3, 4, 7, 9]
    assert stitch_weeks({0: {w[3]: 1}}, w) == [0, 0, 0, 1]
