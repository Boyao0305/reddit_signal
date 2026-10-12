"""Which unprocessed posts the extraction stage takes, and how many.

Before October 2026 the stage took unprocessed events in insertion order (``ORDER BY signal_events.id``)
under a 5,000/day cap. Backfilled history and fresh posts competed for the same cap, and from July on no
new post was extracted at all (see ``docs/agent/log.md``). The policy is now explicit:

* **daily** — newest first, posts between ``recency_days`` and ``maturity_days`` old. Posts younger than
  ``maturity_days`` wait: their comments are still arriving, and a post is extracted only once
  (about 95% of a post's comments arrive within 2 days). Anything older than ``recency_days`` is backlog.
* **backlog** — an explicitly requested date range, newest first, with its own budget.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

MAX_RECENCY_DAYS = 90
DEFAULT_RECENCY_DAYS = 14
DEFAULT_MATURITY_DAYS = 2   # ~95% of comments arrive within 2 days


@dataclass(frozen=True)
class ExtractionPlan:
    mode: str                        # "daily" | "backlog"
    window_start: datetime           # inclusive, compared with signal_events.event_time
    window_end: datetime             # exclusive
    limit: int | None                # posts across all sources; None = no cap


def plan_daily(
    now: datetime,
    *,
    recency_days: int = DEFAULT_RECENCY_DAYS,
    maturity_days: int = DEFAULT_MATURITY_DAYS,
    limit: int | None,
) -> ExtractionPlan:
    if not 1 <= recency_days <= MAX_RECENCY_DAYS:
        raise ValueError(f"recency_days must be between 1 and {MAX_RECENCY_DAYS}, got {recency_days}")
    if not 0 <= maturity_days < recency_days:
        raise ValueError(f"maturity_days must be >= 0 and < recency_days, got {maturity_days}")
    _check_limit(limit)
    return ExtractionPlan(
        "daily", now - timedelta(days=recency_days), now - timedelta(days=maturity_days), limit
    )


def plan_backlog(*, since: datetime, until: datetime, limit: int | None) -> ExtractionPlan:
    if until <= since:
        raise ValueError("backlog range is empty: 'until' must be after 'since'")
    _check_limit(limit)
    return ExtractionPlan("backlog", since, until, limit)


def split_limit(limit: int | None, sources: list[str]) -> dict[str, int | None]:
    """Share a total budget across sources; earlier sources get the remainder first."""
    if limit is None:
        return {source: None for source in sources}
    if limit <= 0 or not sources:
        return {source: 0 for source in sources}
    base, extra = divmod(limit, len(sources))
    return {source: base + (1 if i < extra else 0) for i, source in enumerate(sources)}


def _check_limit(limit: int | None) -> None:
    if limit is not None and limit < 0:
        raise ValueError(f"limit must be >= 0 or None, got {limit}")
