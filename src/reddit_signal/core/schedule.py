"""When the daily and monthly jobs are next due. Times are UTC; ``now`` is always passed in."""
from __future__ import annotations

from datetime import datetime, timedelta

DEFAULT_DAILY = (4, 5)
DEFAULT_MONTHLY = (5, 0)


def parse_hhmm(value: str | None, default: tuple[int, int]) -> tuple[int, int]:
    """``"04:05"`` -> ``(4, 5)``; anything malformed or out of range -> ``default``."""
    try:
        hour_text, minute_text = str(value).split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (TypeError, ValueError):
        return default
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return default


def next_daily(now: datetime, hhmm: str | None) -> datetime:
    """The next ``hh:mm`` strictly after ``now``."""
    hour, minute = parse_hhmm(hhmm, DEFAULT_DAILY)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target if target > now else target + timedelta(days=1)


def next_monthly(now: datetime, hhmm: str | None) -> datetime:
    """The next 1st-of-month at ``hh:mm`` strictly after ``now``."""
    hour, minute = parse_hhmm(hhmm, DEFAULT_MONTHLY)
    target = now.replace(day=1, hour=hour, minute=minute, second=0, microsecond=0)
    if target > now:
        return target
    if target.month == 12:
        return target.replace(year=target.year + 1, month=1)
    return target.replace(month=target.month + 1)


def window_key(job_type: str, scheduled_for: datetime) -> str:
    """Identity of a scheduled slot, so a slot is enqueued at most once (``2026-10-05`` / ``2026-10``)."""
    return scheduled_for.strftime("%Y-%m-%d" if job_type == "daily" else "%Y-%m")
