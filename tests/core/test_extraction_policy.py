from datetime import datetime, timedelta, timezone

import pytest

from reddit_signal.core.extraction_policy import plan_backlog, plan_daily, split_limit

NOW = datetime(2026, 10, 10, 4, 5, tzinfo=timezone.utc)


def test_daily_takes_mature_recent_posts():
    # Defaults: posts 2..14 days old. Younger posts wait for their comments (experiment 0003).
    plan = plan_daily(NOW, limit=10_000)
    assert (plan.mode, plan.window_start, plan.window_end, plan.limit) == (
        "daily", NOW - timedelta(days=14), NOW - timedelta(days=2), 10_000
    )


def test_daily_window_is_configurable():
    plan = plan_daily(NOW, recency_days=7, maturity_days=0, limit=None)
    assert (plan.window_start, plan.window_end) == (NOW - timedelta(days=7), NOW)


@pytest.mark.parametrize(
    ("recency", "maturity"),
    [(0, 0), (-1, 0), (91, 2), (14, -1), (14, 14), (3, 5)],
)
def test_daily_window_must_be_sane(recency, maturity):
    with pytest.raises(ValueError):
        plan_daily(NOW, recency_days=recency, maturity_days=maturity, limit=100)


def test_backlog_is_an_explicit_range():
    since, until = datetime(2026, 7, 1, tzinfo=timezone.utc), datetime(2026, 9, 26, tzinfo=timezone.utc)
    plan = plan_backlog(since=since, until=until, limit=None)
    assert (plan.mode, plan.window_start, plan.window_end, plan.limit) == ("backlog", since, until, None)


def test_backlog_range_must_not_be_empty():
    with pytest.raises(ValueError):
        plan_backlog(since=NOW, until=NOW, limit=10)


def test_negative_limit_is_rejected():
    with pytest.raises(ValueError):
        plan_daily(NOW, recency_days=7, limit=-1)


@pytest.mark.parametrize(
    ("limit", "sources", "expected"),
    [
        (None, ["reddit", "hackernews"], {"reddit": None, "hackernews": None}),   # no cap
        (10_000, ["reddit", "hackernews"], {"reddit": 5_000, "hackernews": 5_000}),
        (5, ["reddit", "hackernews"], {"reddit": 3, "hackernews": 2}),            # remainder to the first
        (0, ["reddit", "hackernews"], {"reddit": 0, "hackernews": 0}),
        (100, [], {}),
    ],
)
def test_split_limit(limit, sources, expected):
    assert split_limit(limit, sources) == expected
