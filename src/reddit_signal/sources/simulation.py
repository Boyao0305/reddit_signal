"""Source adapter dry-run requests for the admin UI."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from reddit_signal.sources import runtime_config
from reddit_signal.sources.registry import discover_adapters, get_adapter, list_adapters

MAX_SIMULATION_LIMIT = 10


def simulate_source_fetch(source: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    source = str(source or "").strip().lower()
    if not source:
        raise ValueError("source is required")

    discover_adapters()
    if source not in list_adapters():
        raise ValueError(f"source {source!r} is not registered")

    cfg = _source_config(source)
    query = str(payload.get("query") if "query" in payload else (cfg.query if cfg else ""))
    lookback_days = _int_value(
        payload.get("lookback_days") if "lookback_days" in payload else (cfg.lookback_days if cfg else 7),
        default=7,
        minimum=0,
        maximum=3650,
        field="lookback_days",
    )
    limit = _int_value(
        payload.get("limit") if "limit" in payload else 5,
        default=5,
        minimum=1,
        maximum=MAX_SIMULATION_LIMIT,
        field="limit",
    )
    until = datetime.now(timezone.utc)
    since = None if lookback_days <= 0 else until - timedelta(days=lookback_days)

    adapter = get_adapter(source)
    result = adapter.simulate_fetch(
        query=query,
        since=since,
        until=until,
        limit=limit,
    )
    result.setdefault("source", source)
    result["dry_run"] = True
    result["writes"] = 0
    result["config"] = {
        "query": query,
        "lookback_days": lookback_days,
        "limit": limit,
        "since": since.isoformat() if since else None,
        "until": until.isoformat(),
    }
    return result


def _source_config(source: str) -> runtime_config.SourceRuntimeConfig | None:
    try:
        return runtime_config.get_source_config(source)
    except Exception:
        return None


def _int_value(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
    field: str,
) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return parsed
