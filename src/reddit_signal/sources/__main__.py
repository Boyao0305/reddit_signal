"""
CLI entry point for the source adapters.

Examples:
  python -m reddit_signal.sources --list
  python -m reddit_signal.sources reddit --project-only --rebuild
  python -m reddit_signal.sources reddit --query SaaS --days 7
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

from reddit_signal.sources.registry import discover_adapters, get_adapter, list_adapters


def _setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m reddit_signal.sources",
        description="Adapter framework CLI: fetch L1 + project to signal_events (L2).",
    )
    parser.add_argument("adapter", nargs="?", help="Adapter name, e.g. 'reddit'.")
    parser.add_argument("--list", action="store_true", help="List registered adapters.")
    parser.add_argument("--enabled", action="store_true", help="List enabled runtime sources.")
    parser.add_argument("--query", default="", help="Query / topic filter (adapter-specific).")
    parser.add_argument("--days", type=int, default=7, help="Time window in days (ignored with --rebuild).")
    parser.add_argument("--limit", type=int, default=10_000, help="Max rows for fetch.")
    parser.add_argument(
        "--project-only",
        action="store_true",
        help="Skip fetch; just (re)project existing L1 rows into signal_events.",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Project all historical L1 data (since/until both NULL).",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    discover_adapters()

    if args.enabled:
        from reddit_signal.sources.runtime_config import list_source_configs
        for cfg in list_source_configs():
            print(
                f"{cfg.source}\tfetch={cfg.fetch_enabled}\t"
                f"project={cfg.project_enabled}\textract={cfg.extract_enabled}"
            )
        return 0

    if args.list or not args.adapter:
        names = list_adapters()
        if not names:
            print("(no adapters registered)")
            return 0
        for n in names:
            print(n)
        return 0

    try:
        adapter = get_adapter(args.adapter)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    until = datetime.now(timezone.utc)
    since = None if args.rebuild else until - timedelta(days=args.days)

    if args.project_only:
        n = adapter.project(since=since, until=until)
        print(f"projected {n} events for adapter={adapter.name}")
        return 0

    fetched, projected = adapter.fetch_and_project(
        query=args.query, since=since, until=until, limit=args.limit
    )
    print(f"fetched {fetched}, projected {projected} for adapter={adapter.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
