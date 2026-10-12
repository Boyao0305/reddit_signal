"""CLI for deterministic emerging opportunities."""
from __future__ import annotations

import argparse
import json
import logging
from typing import Any

from .runner import (
    DEFAULT_EPS,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_MIN_AUTHORS,
    DEFAULT_MIN_COHERENCE,
    DEFAULT_MIN_SIZE,
    DEFAULT_MIN_SUBREDDITS,
    run_emerging_opportunities,
)


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build deterministic emerging opportunities.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run")
    run_p.add_argument("--cluster-run-id", type=int, required=True)
    run_p.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    run_p.add_argument("--min-size", type=int, default=DEFAULT_MIN_SIZE)
    run_p.add_argument("--min-authors", type=int, default=DEFAULT_MIN_AUTHORS)
    run_p.add_argument("--min-subreddits", type=int, default=DEFAULT_MIN_SUBREDDITS)
    run_p.add_argument("--eps", type=float, default=DEFAULT_EPS)
    run_p.add_argument("--min-coherence", type=float, default=DEFAULT_MIN_COHERENCE)
    run_p.add_argument("--limit", type=int)
    run_p.add_argument("--dry-run", action="store_true")
    run_p.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.cmd == "run":
        result = run_emerging_opportunities(
            cluster_run_id=args.cluster_run_id,
            lookback_days=args.lookback_days,
            min_size=args.min_size,
            min_authors=args.min_authors,
            min_subreddits=args.min_subreddits,
            eps=args.eps,
            min_coherence=args.min_coherence,
            limit=args.limit,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default))


if __name__ == "__main__":
    main()

