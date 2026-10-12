"""Daily fetch and projection over the enabled sources (ported from ``scripts/run_sources.py``).

A source whose fetch fails is reported and skipped; the others continue. Projection failures stop the
phase, because downstream stages would silently run on stale events.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from reddit_signal.sources.registry import discover_adapters, get_adapter
from reddit_signal.sources.runtime_config import list_source_configs

logger = logging.getLogger(__name__)

Note = Callable[..., None]


def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


def fetch_enabled_sources(*, note: Note = _noop) -> dict[str, Any]:
    discover_adapters()
    configs = [cfg for cfg in list_source_configs() if cfg.fetch_enabled]
    if not configs:
        note("No sources enabled for fetch", event_type="source_phase_empty")
        return {"fetched": 0, "failed": [], "sources": 0}
    total, failed = 0, []
    for cfg in configs:
        since, until = cfg.since_until
        details = {"source": cfg.source, "query": cfg.query, "lookback_days": cfg.lookback_days,
                   "fetch_limit": cfg.fetch_limit}
        note(f"Source {cfg.source}: fetch start", event_type="source_fetch_start", details=details)
        try:
            fetched = get_adapter(cfg.source).fetch(query=cfg.query, since=since, until=until, limit=cfg.fetch_limit)
        except Exception as exc:  # noqa: BLE001 - one source must not stop the others
            logger.warning("Source %s: fetch failed; continuing: %s", cfg.source, exc, exc_info=True)
            failed.append(cfg.source)
            note(f"Source {cfg.source}: fetch failed; continuing: {exc}", event_type="warning",
                 details={**details, "error": str(exc)})
            continue
        total += fetched
        note(f"Source {cfg.source}: fetch done rows={fetched}", event_type="source_fetch_done",
             details={**details, "fetched": fetched})
    return {"fetched": total, "failed": failed, "sources": len(configs)}


def project_enabled_sources(*, note: Note = _noop) -> dict[str, Any]:
    discover_adapters()
    configs = [cfg for cfg in list_source_configs() if cfg.project_enabled]
    if not configs:
        note("No sources enabled for projection", event_type="source_phase_empty")
        return {"projected": 0, "sources": 0}
    total = 0
    for cfg in configs:
        since, until = cfg.since_until
        projected = get_adapter(cfg.source).project(since=since, until=until)
        total += projected
        note(f"Source {cfg.source}: project done events={projected}", event_type="source_project_done",
             details={"source": cfg.source, "projected": projected})
    return {"projected": total, "sources": len(configs)}
