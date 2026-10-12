"""Pure presentation rules for the public (Seedcast) API."""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime

_AUDIENCE = {"b2b": "B2B", "b2c": "B2C", "mixed": "Mixed"}
_SOURCE = {"reddit": "Reddit", "hackernews": "Hacker News"}
_USERNAME = re.compile(r"(?<![\w/])(?:/?u/|@)[A-Za-z0-9_-]{2,}", re.IGNORECASE)


def audience_label(audience_type: str | None) -> str:
    return _AUDIENCE.get((audience_type or "").strip().lower(), "Unclear")


def source_label(source: str | None) -> str:
    key = (source or "").strip().lower()
    return _SOURCE.get(key, key.replace("_", " ").title() or "Unknown")


def community_label(surface: str | None, source: str | None) -> str:
    """``ADHD`` + reddit -> ``r/ADHD``; any Hacker News surface -> ``Hacker News``."""
    src = (source or "").strip().lower()
    if src == "hackernews" or (surface or "").startswith("hn:"):
        return "Hacker News"
    name = (surface or "").strip()
    if not name:
        return source_label(source)
    if src in ("", "reddit") and not name.startswith("r/"):
        return f"r/{name}"
    return name


def month_label(value: date | datetime | None) -> str:
    """``2026-03-14`` -> ``Mar '26``."""
    if value is None:
        return ""
    return f"{value:%b} '{value:%y}"


def strip_usernames(text: str | None) -> str:
    """Remove ``u/name``, ``/u/name`` and ``@name`` mentions; public quotes carry no usernames."""
    return _USERNAME.sub("[user]", text or "").strip()


def percentages(counts: Iterable[tuple[str, int]], *, top: int = 5) -> list[dict[str, int | str]]:
    """Merge equal names, keep the ``top`` largest, express each as a whole percentage of the total."""
    merged: dict[str, int] = {}
    for name, n in counts:
        merged[name] = merged.get(name, 0) + int(n)
    total = sum(merged.values())
    if total <= 0:
        return []
    ranked = sorted(merged.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    return [{"name": name, "pct": round(100 * n / total)} for name, n in ranked]


def stitch_weeks(
    by_depth: Mapping[int, Mapping[date, float]], weeks: Sequence[date], *, missing: float = 0
) -> list[float]:
    """For each week, the value from the closest lineage generation that has data for it.

    ``by_depth[0]`` is the cluster itself, ``by_depth[1]`` its parent in the previous run, and so on.
    """
    out = []
    for week in weeks:
        value = missing
        for depth in sorted(by_depth):
            if week in by_depth[depth]:
                value = by_depth[depth][week]
                break
        out.append(value)
    return out
