"""
Pipeline orchestrator: fetch posts + comments from Arctic Shift and store in PostgreSQL.

For each subreddit:
  1. Fetch all posts from the past N days (paginating through everything)
  2. For each post, fetch all its comments
  3. Upsert everything into PostgreSQL

Subreddits are fetched concurrently via ThreadPoolExecutor.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import arcticshift as api
from . import db
from .models import Subreddit

logger = logging.getLogger(__name__)

DEFAULT_SUBREDDITS = [
    "smallbusiness",
    "sysadmin",
    "msp",
    "SaaS",
    "Entrepreneur",
    "sales",
]


DEFAULT_WORKERS = 6  # concurrent subreddit fetches
# Windows end this long before now: Arctic Shift times out on its newest, not yet indexed data. Daily windows
# overlap by days, so the skipped hour is fetched by the next run.
INGEST_LAG = timedelta(hours=1)


def run_pipeline(
    subreddits: Optional[list[str]] = None,
    days: int = 7,
    fetch_comments: bool = True,
    mode: str = "full",  # "full", "newer", "older"
    after_date: Optional[datetime] = None,
    before_date: Optional[datetime] = None,
    workers: int = DEFAULT_WORKERS,
) -> list[dict]:
    """
    Run the fetch-and-store pipeline.

    Modes:
      full  — fetch the last N days (default, re-fetches everything)
      newer — only fetch posts newer than what's already in DB
      older — only fetch posts older than what's already in DB (backfill)

    after_date/before_date override mode when provided.
    workers: number of subreddits to fetch concurrently.

    Returns a list of per-subreddit summary dicts.
    """
    if subreddits is None:
        subreddits = DEFAULT_SUBREDDITS

    now = datetime.now(tz=timezone.utc) - INGEST_LAG

    # Build (subreddit, after, before) tasks
    tasks = []
    for subreddit in subreddits:
        if after_date and before_date:
            after = after_date
            before = before_date
        elif mode == "newer":
            boundaries = db.get_fetch_boundaries(subreddit)
            if boundaries["newest"]:
                after = boundaries["newest"].replace(tzinfo=timezone.utc)
                before = now
                print(f"[{subreddit}] Incremental: fetching posts after {after.strftime('%Y-%m-%d %H:%M UTC')}", flush=True)
            else:
                after = now - timedelta(days=days)
                before = now
                print(f"[{subreddit}] No existing data, falling back to last {days} days", flush=True)
        elif mode == "older":
            boundaries = db.get_fetch_boundaries(subreddit)
            if boundaries["oldest"]:
                before = boundaries["oldest"].replace(tzinfo=timezone.utc)
                after = before - timedelta(days=days)
                print(f"[{subreddit}] Backfill: fetching posts from {after.strftime('%Y-%m-%d')} to {before.strftime('%Y-%m-%d')}", flush=True)
            else:
                after = now - timedelta(days=days)
                before = now
                print(f"[{subreddit}] No existing data, falling back to last {days} days", flush=True)
        else:  # full
            after = now - timedelta(days=days)
            before = now
        tasks.append((subreddit, after, before))

    if mode == "full":
        after = now - timedelta(days=days)
        before = now
        print(f"\nFetching data from {after.strftime('%Y-%m-%d %H:%M UTC')} to {before.strftime('%Y-%m-%d %H:%M UTC')}", flush=True)
        print(f"Subreddits: {', '.join(subreddits)} ({workers} concurrent)\n", flush=True)

    # Run subreddits concurrently
    results = []
    effective_workers = min(workers, len(tasks))
    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        future_map = {
            executor.submit(_process_subreddit, sub, after, before, fetch_comments): sub
            for sub, after, before in tasks
        }
        for future in as_completed(future_map):
            result = future.result()
            results.append(result)

    return results


def _process_subreddit(
    subreddit: str,
    after: datetime,
    before: datetime,
    fetch_comments: bool,
) -> dict:
    """Fetch and store all posts (and optionally comments) for one subreddit."""
    print(f"[{subreddit}] Starting...", flush=True)

    # Ensure subreddit row exists
    sub_obj = Subreddit(name=subreddit, display_name=subreddit)
    try:
        subreddit_id = db.upsert_subreddit(sub_obj)
    except Exception as exc:
        logger.error("[%s] Failed to upsert subreddit: %s", subreddit, exc)
        return {"subreddit": subreddit, "posts": 0, "comments": 0, "errors": 1}

    post_count = 0
    comment_count = 0
    error_count = 0
    post_db_ids: dict[str, int] = {}  # reddit_id -> db id

    # ── Fetch posts (batched per API page) ──────────────────────────────────────
    try:
        page_buffer = []
        for post in api.fetch_posts(subreddit, after, before):
            page_buffer.append(post)
            if len(page_buffer) >= 100:
                try:
                    ids = db.upsert_posts_batch([(p, subreddit_id) for p in page_buffer])
                    post_db_ids.update(ids)
                    post_count += len(page_buffer)
                    print(f"  [{subreddit}] posts: {post_count} stored...", flush=True)
                except Exception as exc:
                    logger.error("[%s] Failed to upsert post batch: %s", subreddit, exc)
                    error_count += len(page_buffer)
                page_buffer = []
        # Flush remaining
        if page_buffer:
            try:
                ids = db.upsert_posts_batch([(p, subreddit_id) for p in page_buffer])
                post_db_ids.update(ids)
                post_count += len(page_buffer)
            except Exception as exc:
                logger.error("[%s] Failed to upsert post batch: %s", subreddit, exc)
                error_count += len(page_buffer)
    except Exception as exc:
        logger.error("[%s] Error fetching posts: %s", subreddit, exc)
        error_count += 1

    print(f"  [{subreddit}] posts done: {post_count} total", flush=True)

    # Refresh post_db_ids from DB for guaranteed accuracy before comment fetch.
    if fetch_comments:
        post_db_ids = db.get_post_ids_for_subreddit(subreddit_id)

    # ── Fetch comments (batched per API page) ──────────────────────────────
    if fetch_comments and post_db_ids:
        print(f"  [{subreddit}] fetching comments (bulk subreddit scan)...", flush=True)
        try:
            comment_buffer: list[tuple] = []  # (Comment, post_db_id)
            for comment in api.fetch_comments_by_subreddit(subreddit, after, before):
                post_db_id = post_db_ids.get(comment.post_reddit_id)
                if post_db_id is None:
                    continue
                comment_buffer.append((comment, post_db_id))
                if len(comment_buffer) >= 100:
                    try:
                        db.upsert_comments_batch(comment_buffer)
                        comment_count += len(comment_buffer)
                        if comment_count % 500 < 100:
                            print(
                                f"  [{subreddit}] comments: {comment_count} stored...",
                                flush=True,
                            )
                    except Exception as exc:
                        logger.error("[%s] Failed to upsert comment batch: %s", subreddit, exc)
                        error_count += len(comment_buffer)
                    comment_buffer = []
            # Flush remaining
            if comment_buffer:
                try:
                    db.upsert_comments_batch(comment_buffer)
                    comment_count += len(comment_buffer)
                except Exception as exc:
                    logger.error("[%s] Failed to upsert comment batch: %s", subreddit, exc)
                    error_count += len(comment_buffer)
        except Exception as exc:
            logger.error("[%s] Error fetching comments: %s", subreddit, exc)
            error_count += 1

    print(f"  [{subreddit}] DONE — {post_count} posts, {comment_count} comments, {error_count} errors\n", flush=True)
    return {
        "subreddit": subreddit,
        "posts": post_count,
        "comments": comment_count,
        "errors": error_count,
    }
