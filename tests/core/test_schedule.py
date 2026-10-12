from datetime import datetime, timezone

import pytest

from reddit_signal.core.schedule import next_daily, next_monthly, parse_hhmm, window_key


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("04:05", (4, 5)),
        ("23:59", (23, 59)),
        ("0:0", (0, 0)),
        # Malformed or out of range falls back to the default rather than stopping the scheduler.
        ("24:00", (9, 9)),
        ("12:60", (9, 9)),
        ("noon", (9, 9)),
        ("", (9, 9)),
        (None, (9, 9)),
    ],
)
def test_parse_hhmm(value, expected):
    assert parse_hhmm(value, (9, 9)) == expected


@pytest.mark.parametrize(
    ("now", "hhmm", "expected"),
    [
        (utc(2026, 10, 5, 3, 0), "04:05", utc(2026, 10, 5, 4, 5)),    # later today
        (utc(2026, 10, 5, 4, 5), "04:05", utc(2026, 10, 6, 4, 5)),    # exactly now -> tomorrow
        (utc(2026, 10, 5, 23, 0), "04:05", utc(2026, 10, 6, 4, 5)),
        (utc(2026, 12, 31, 23, 0), "04:05", utc(2027, 1, 1, 4, 5)),   # year rollover
        (utc(2026, 10, 5, 3, 0), "bad", utc(2026, 10, 5, 4, 5)),      # default 04:05
    ],
)
def test_next_daily(now, hhmm, expected):
    assert next_daily(now, hhmm) == expected


@pytest.mark.parametrize(
    ("now", "hhmm", "expected"),
    [
        (utc(2026, 10, 1, 4, 0), "05:00", utc(2026, 10, 1, 5, 0)),    # the 1st, before the time
        (utc(2026, 10, 1, 5, 0), "05:00", utc(2026, 11, 1, 5, 0)),    # exactly now -> next month
        (utc(2026, 10, 15, 0, 0), "05:00", utc(2026, 11, 1, 5, 0)),
        (utc(2026, 12, 20, 0, 0), "05:00", utc(2027, 1, 1, 5, 0)),    # December -> January
        (utc(2026, 1, 31, 23, 0), "05:00", utc(2026, 2, 1, 5, 0)),    # day=1 avoids 31 -> Feb errors
    ],
)
def test_next_monthly(now, hhmm, expected):
    assert next_monthly(now, hhmm) == expected


def test_window_key():
    assert window_key("daily", utc(2026, 10, 5, 4, 5)) == "2026-10-05"
    assert window_key("monthly", utc(2026, 10, 1, 5, 0)) == "2026-10"
