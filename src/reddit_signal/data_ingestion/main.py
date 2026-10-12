"""
CLI for the Arctic Shift → PostgreSQL pipeline.

Usage:
  python -m reddit_signal.data_ingestion --clean              # wipe all old data
  python -m reddit_signal.data_ingestion --fetch --all        # fetch all 6 subreddits, 7 days
  python -m reddit_signal.data_ingestion --fetch --all --days 14
  python -m reddit_signal.data_ingestion --fetch --subreddit sysadmin
  python -m reddit_signal.data_ingestion --stats              # show row counts
  python -m reddit_signal.data_ingestion --sample             # sample posts from each subreddit
  python -m reddit_signal.data_ingestion --sample --subreddit SaaS
"""

import argparse
import logging
import sys

from . import db, pipeline
from .pipeline import DEFAULT_SUBREDDITS


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        level=level,
    )
    # Quiet noisy libs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def cmd_clean():
    print("Wiping all data from comments, posts, subreddits...")
    db.truncate_all()
    print("Done. Database is clean.")


def cmd_fetch(subreddits: list[str], days: int, no_comments: bool,
              mode: str = "full", after_date=None, before_date=None,
              workers: int = pipeline.DEFAULT_WORKERS):
    if not db.test_connection():
        print("ERROR: Cannot connect to database.", file=sys.stderr)
        sys.exit(1)
    db.init_db()

    # Show current boundaries if incremental
    if mode in ("newer", "older"):
        for sub in subreddits:
            b = db.get_fetch_boundaries(sub)
            if b["oldest"]:
                print(f"  [{sub}] DB range: {b['oldest'].strftime('%Y-%m-%d')} → {b['newest'].strftime('%Y-%m-%d')} ({b['post_count']} posts)")
            else:
                print(f"  [{sub}] No existing data in DB")
        print()

    results = pipeline.run_pipeline(
        subreddits=subreddits,
        days=days,
        fetch_comments=not no_comments,
        mode=mode,
        after_date=after_date,
        before_date=before_date,
        workers=workers,
    )
    print("\n=== Fetch Summary ===")
    total_posts = 0
    total_comments = 0
    total_errors = 0
    for r in results:
        print(f"  {r['subreddit']:20s}  posts={r['posts']:5d}  comments={r['comments']:6d}  errors={r['errors']}")
        total_posts += r["posts"]
        total_comments += r["comments"]
        total_errors += r["errors"]
    print(f"  {'TOTAL':20s}  posts={total_posts:5d}  comments={total_comments:6d}  errors={total_errors}")


def cmd_stats():
    if not db.test_connection():
        print("ERROR: Cannot connect to database.", file=sys.stderr)
        sys.exit(1)
    stats = db.get_stats()
    print("\n=== Database Stats ===")
    print(f"  Subreddits : {stats['subreddits']}")
    print(f"  Posts      : {stats['posts']}")
    print(f"  Comments   : {stats['comments']}")
    if stats.get("oldest_post"):
        print(f"  Date range : {stats['oldest_post'].strftime('%Y-%m-%d')} → {stats['newest_post'].strftime('%Y-%m-%d')}")
    print()
    print(f"  {'Subreddit':20s}  {'Posts':>6}  {'Comments':>8}")
    print(f"  {'-'*20}  {'-'*6}  {'-'*8}")
    for row in stats["per_subreddit"]:
        print(f"  {row['name']:20s}  {row['post_count']:6d}  {row['comment_count']:8d}")

    # Timestamp window check
    window = db.check_timestamp_window(days=7)
    print()
    print("=== Data Quality Check (7-day window) ===")
    print(f"  Posts in window    : {window['in_window']} / {window['total']}")
    print(f"  Posts out of window: {window['out_of_window']}")
    print(f"  Orphan comments    : {window['orphan_comments']}")


def cmd_sample(subreddits: list[str]):
    if not db.test_connection():
        print("ERROR: Cannot connect to database.", file=sys.stderr)
        sys.exit(1)
    for sub in subreddits:
        posts = db.get_sample_posts(sub, limit=3)
        print(f"\n=== Sample posts from r/{sub} ===")
        if not posts:
            print("  (no posts found)")
            continue
        for p in posts:
            created = p["created_utc"].strftime("%Y-%m-%d") if p["created_utc"] else "unknown"
            print(f"  [{created}] score={p['score']:4d}  comments={p['stored_comments']:3d}  {p['title'][:80]}")


def main():
    parser = argparse.ArgumentParser(description="Arctic Shift → PostgreSQL pipeline")
    parser.add_argument("--clean", action="store_true", help="Wipe all data from the database")
    parser.add_argument("--fetch", action="store_true", help="Fetch data from Arctic Shift")
    parser.add_argument("--stats", action="store_true", help="Show database stats")
    parser.add_argument("--sample", action="store_true", help="Show sample posts")
    parser.add_argument("--all", action="store_true", help="Use all default subreddits")
    parser.add_argument("--subreddit", type=str, help="Single subreddit to process")
    parser.add_argument("--subreddits", type=str, nargs="+", help="Multiple subreddits")
    parser.add_argument("--days", type=int, default=7, help="Days to look back (default: 7)")
    parser.add_argument("--no-comments", action="store_true", help="Skip comment fetching")
    parser.add_argument("--newer", action="store_true",
                        help="Only fetch posts newer than what's in DB")
    parser.add_argument("--older", action="store_true",
                        help="Backfill: fetch posts older than what's in DB")
    parser.add_argument("--after", type=str,
                        help="Fetch posts after this date (YYYY-MM-DD)")
    parser.add_argument("--before", type=str,
                        help="Fetch posts before this date (YYYY-MM-DD)")
    parser.add_argument("--workers", type=int, default=pipeline.DEFAULT_WORKERS,
                        help=f"Concurrent subreddit fetches (default: {pipeline.DEFAULT_WORKERS})")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    args = parser.parse_args()
    setup_logging(args.verbose)

    if not any([args.clean, args.fetch, args.stats, args.sample]):
        parser.print_help()
        sys.exit(0)

    if args.clean:
        cmd_clean()

    # Resolve subreddit list
    if args.all:
        subreddits = DEFAULT_SUBREDDITS
    elif args.subreddits:
        subreddits = args.subreddits
    elif args.subreddit:
        subreddits = [args.subreddit]
    else:
        subreddits = DEFAULT_SUBREDDITS

    if args.fetch:
        # Determine mode
        if args.newer:
            mode = "newer"
        elif args.older:
            mode = "older"
        else:
            mode = "full"

        # Parse explicit date range
        after_date = None
        before_date = None
        if args.after:
            from datetime import datetime as dt, timezone as tz
            after_date = dt.strptime(args.after, "%Y-%m-%d").replace(tzinfo=tz.utc)
        if args.before:
            from datetime import datetime as dt, timezone as tz
            before_date = dt.strptime(args.before, "%Y-%m-%d").replace(tzinfo=tz.utc)

        cmd_fetch(subreddits, days=args.days, no_comments=args.no_comments,
                  mode=mode, after_date=after_date, before_date=before_date,
                  workers=args.workers)

    if args.stats:
        cmd_stats()

    if args.sample:
        cmd_sample(subreddits)


if __name__ == "__main__":
    main()
