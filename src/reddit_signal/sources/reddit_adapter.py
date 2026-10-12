"""
Reddit adapter — wraps the existing Arctic Shift fetcher (L1) and projects
posts/comments into signal_events (L2).
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

import psycopg2.extras

from reddit_signal.data_ingestion.db import get_conn, put_conn
from reddit_signal.sources.base import (
    AdapterCapabilities,
    BaseAdapter,
    EventBundle,
    SignalEvent,
)
from reddit_signal.sources.db import (
    build_source_id_map,
    build_source_id_map_for_rows,
    upsert_events_fast,
)
from reddit_signal.sources.registry import register_adapter

logger = logging.getLogger(__name__)


@register_adapter
class RedditAdapter(BaseAdapter):
    """Adapter for Reddit data via Arctic Shift."""

    name = "reddit"
    display_name = "Reddit (Arctic Shift)"
    description = "B2B subreddit discussions: posts + comments via arctic-shift REST API"
    capabilities = AdapterCapabilities(
        produces_pain=True,
        produces_temporal=True,
    )

    BATCH_SIZE = 5_000

    # ─── fetch (L1) ─────────────────────────────────────────────────────────

    def fetch(
        self,
        query: str = "",
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 10_000,
    ) -> int:
        """Delegate to existing Arctic Shift pipeline.

        ``query`` remains a manual override for compatibility. In production,
        active subreddit rows from ``reddit_subreddit_sources`` are preferred so
        operators can manage Reddit coverage without editing a raw query string.
        """
        try:
            from reddit_signal.data_ingestion import pipeline as arcticshift_pipeline
        except ImportError as exc:
            logger.warning(
                "Arctic Shift pipeline import failed (%s); fetch is a no-op.",
                exc,
            )
            return 0
        subreddits, lookback_days, managed_subreddits = _fetch_plan(query, since, until)
        if not subreddits:
            logger.info("No Reddit subreddits selected for fetch.")
            return 0
        after = since
        before = until
        if managed_subreddits:
            # Callers pass until=now; Arctic Shift times out on its newest data, so stop INGEST_LAG short.
            latest = datetime.now(timezone.utc) - arcticshift_pipeline.INGEST_LAG
            before = min(until, latest) if until else latest
            after = before - timedelta(days=lookback_days)
        mode = "full"
        days = lookback_days
        results = arcticshift_pipeline.run_pipeline(
            subreddits=subreddits,
            days=days,
            fetch_comments=True,
            mode=mode,
            after_date=after,
            before_date=before,
        )
        fetched = sum(int(row.get("posts", 0)) + int(row.get("comments", 0)) for row in results)
        _mark_fetch_success([row["subreddit"] for row in results if int(row.get("errors", 0)) == 0])
        return fetched

    def simulate_fetch(
        self,
        query: str = "",
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 5,
    ) -> dict:
        """Fetch a small Arctic Shift sample without writing L1 rows."""
        from reddit_signal.data_ingestion import arcticshift as api

        subreddits, lookback_days, managed = _fetch_plan(query, since, until)
        if not subreddits:
            raise ValueError("reddit has no subreddits selected")
        end = until or datetime.now(timezone.utc)
        start = since or (end - timedelta(days=lookback_days or 7))
        sample_limit = min(max(int(limit or 1), 1), 10)
        subreddit = subreddits[0]
        posts = []
        for post in api.fetch_posts(subreddit, start, end):
            posts.append(post)
            if len(posts) >= sample_limit:
                break
        return {
            "source": self.name,
            "dry_run": True,
            "writes": 0,
            "request": {
                "provider": "arcticshift",
                "endpoint": "/posts/search",
                "params": {
                    "subreddit": subreddit,
                    "after": int(start.timestamp()),
                    "before": int(end.timestamp()),
                    "limit": sample_limit,
                    "sort": "asc",
                    "sort_type": "created_utc",
                },
                "managed": managed,
                "candidate_subreddits": subreddits[:10],
            },
            "count": len(posts),
            "items": [_reddit_preview_item(post) for post in posts],
            "raw_sample": {
                "item_count": len(posts),
                "first_item": posts[0].raw_json if posts else None,
            },
        }

    # ─── project (L1 → L2) ──────────────────────────────────────────────────

    def project(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> int:
        """Project posts and comments into signal_events. Idempotent.

        Steps:
          1. Stream posts (batch=5k) → upsert as event_type='post'.
          2. Build {posts.id → signal_events.id} map for parent linkage.
          3. Stream comments (batch=5k) → upsert as event_type='comment'
             with parent_event_id set + surface inherited from parent post.
        """
        n_posts = self._project_posts(since, until)
        logger.info("Projected %d posts → signal_events", n_posts)

        # Build parent map AFTER posts upserted (one server-side query)
        post_id_map = build_source_id_map("reddit", "posts")
        logger.info("Loaded %d post→event id mappings", len(post_id_map))

        n_comments = self._project_comments(since, until, post_id_map)
        logger.info("Projected %d comments → signal_events", n_comments)
        return n_posts + n_comments

    def project_subreddit_window(
        self,
        subreddit: str,
        since: Optional[datetime],
        until: Optional[datetime],
    ) -> int:
        """Project one subreddit/window without scanning all Reddit mappings."""
        n_posts = self._project_posts(since, until, subreddit=subreddit)
        post_ids = self._post_source_row_ids(since, until, subreddit=subreddit)
        post_id_map = build_source_id_map_for_rows("reddit", "posts", post_ids)
        n_comments = self._project_comments(
            since,
            until,
            post_id_map,
            subreddit=subreddit,
        )
        logger.info(
            "Projected Reddit %s window: %d posts, %d comments",
            subreddit,
            n_posts,
            n_comments,
        )
        return n_posts + n_comments

    def _project_posts(
        self,
        since: Optional[datetime],
        until: Optional[datetime],
        subreddit: Optional[str] = None,
    ) -> int:
        sql = """
            SELECT p.id, p.reddit_id, p.title, p.selftext, p.author,
                   p.score, p.num_comments, p.upvote_ratio, p.created_utc,
                   p.permalink, s.name AS surface
            FROM posts p
            JOIN subreddits s ON s.id = p.subreddit_id
            WHERE (%(since)s IS NULL OR p.created_utc >= %(since)s)
              AND (%(until)s IS NULL OR p.created_utc <  %(until)s)
              AND (%(subreddit)s IS NULL OR s.name = %(subreddit)s)
            ORDER BY p.id
        """
        params = {"since": since, "until": until, "subreddit": subreddit}
        return self._stream_and_upsert(sql, params, self._post_row_to_event)

    def _post_source_row_ids(
        self,
        since: Optional[datetime],
        until: Optional[datetime],
        subreddit: Optional[str] = None,
    ) -> list[str]:
        sql = """
            SELECT p.id
            FROM posts p
            JOIN subreddits s ON s.id = p.subreddit_id
            WHERE (%(since)s IS NULL OR p.created_utc >= %(since)s)
              AND (%(until)s IS NULL OR p.created_utc <  %(until)s)
              AND (%(subreddit)s IS NULL OR s.name = %(subreddit)s)
            ORDER BY p.id
        """
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, {"since": since, "until": until, "subreddit": subreddit})
                return [str(row[0]) for row in cur.fetchall()]
        finally:
            put_conn(conn)

    def _project_comments(
        self,
        since: Optional[datetime],
        until: Optional[datetime],
        post_id_map: dict[str, int],
        subreddit: Optional[str] = None,
    ) -> int:
        sql = """
            SELECT c.id, c.reddit_id, c.body, c.author, c.score, c.depth,
                   c.post_id, c.parent_reddit_id, c.created_utc,
                   s.name AS surface
            FROM comments c
            JOIN posts p ON p.id = c.post_id
            JOIN subreddits s ON s.id = p.subreddit_id
            WHERE (%(since)s IS NULL OR c.created_utc >= %(since)s)
              AND (%(until)s IS NULL OR c.created_utc <  %(until)s)
              AND (%(subreddit)s IS NULL OR s.name = %(subreddit)s)
            ORDER BY c.id
        """
        params = {"since": since, "until": until, "subreddit": subreddit}
        return self._stream_and_upsert(
            sql,
            params,
            lambda row: self._comment_row_to_event(row, post_id_map),
        )

    def _stream_and_upsert(
        self,
        sql: str,
        params: dict,
        row_to_event,
    ) -> int:
        """Stream rows from L1 with a server-side cursor, upsert in batches."""
        total = 0
        conn = get_conn()
        try:
            cur_name = f"v2_proj_{id(sql) & 0xffff:x}"
            with conn.cursor(name=cur_name, cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.itersize = self.BATCH_SIZE
                cur.execute(sql, params)
                batch: list[SignalEvent] = []
                for row in cur:
                    ev = row_to_event(row)
                    if ev is None:
                        continue
                    batch.append(ev)
                    if len(batch) >= self.BATCH_SIZE:
                        upsert_events_fast(batch)
                        total += len(batch)
                        if total % (self.BATCH_SIZE * 4) == 0:
                            logger.info("  …projected %d so far", total)
                        batch = []
                if batch:
                    upsert_events_fast(batch)
                    total += len(batch)
        finally:
            put_conn(conn)
        return total

    # ─── row → SignalEvent ──────────────────────────────────────────────────

    def _post_row_to_event(self, row) -> SignalEvent:
        return SignalEvent(
            source="reddit",
            source_table="posts",
            source_row_id=str(row["id"]),
            event_type="post",
            event_time=_ensure_tz(row["created_utc"]),
            surface=row["surface"],
            author=row["author"],
            title=row["title"],
            text=row["selftext"],
            engagement_score=_post_engagement(row["score"], row["num_comments"]),
            raw_meta={
                "reddit_id":     row["reddit_id"],
                "score":         row["score"],
                "num_comments":  row["num_comments"],
                "upvote_ratio":  row["upvote_ratio"],
                "permalink":     row["permalink"],
            },
        )

    def _comment_row_to_event(self, row, post_id_map: dict[str, int]):
        parent_event_id = post_id_map.get(str(row["post_id"]))
        if parent_event_id is None:
            # Orphan comment (parent post not yet projected). Skip; will
            # link on next run.
            return None
        return SignalEvent(
            source="reddit",
            source_table="comments",
            source_row_id=str(row["id"]),
            event_type="comment",
            event_time=_ensure_tz(row["created_utc"]),
            surface=row["surface"],            # inherited from parent post
            author=row["author"],
            title=None,
            text=row["body"],
            engagement_score=_comment_engagement(row["score"]),
            parent_event_id=parent_event_id,
            raw_meta={
                "reddit_id":        row["reddit_id"],
                "parent_reddit_id": row["parent_reddit_id"],
                "score":            row["score"],
                "depth":            row["depth"],
            },
        )

    # ─── pain extraction hooks ──────────────────────────────────────────────

    def build_pain_prompt(self, bundle: EventBundle) -> str:
        """Per-source prompt for pain extraction.

        For Reddit we replicate the existing prompt format used in
        ``reddit_signal.pain_extraction.extraction``; the actual prompt text lives in
        ``reddit_signal.pain_extraction.prompts.reddit``.
        """
        from reddit_signal.pain_extraction.prompts.reddit import build_reddit_prompt
        return build_reddit_prompt(bundle)

    def community_score(self, event: SignalEvent) -> float:
        """Engagement formula matching v1 Reddit logic."""
        meta = event.raw_meta or {}
        if event.event_type == "post":
            return _post_engagement(
                meta.get("score") or 0,
                meta.get("num_comments") or 0,
            )
        return _comment_engagement(meta.get("score") or 0)


# ─── helpers (module-level for reuse + testing) ─────────────────────────────

def _ensure_tz(ts) -> datetime:
    """Ensure a TIMESTAMP is timezone-aware (UTC)."""
    if ts is None:
        return datetime.now(tz=timezone.utc)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _fetch_plan(
    query: str,
    since: Optional[datetime],
    until: Optional[datetime],
) -> tuple[list[str], int, bool]:
    if query.strip():
        from reddit_signal.sources.reddit_subreddits import parse_subreddit_list

        names = parse_subreddit_list(query)
        if since and until:
            days = max(1, math.ceil((until - since).total_seconds() / 86400))
        else:
            days = 7
        return names, days, False

    try:
        from reddit_signal.sources.reddit_subreddits import active_subreddits

        rows = active_subreddits()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load reddit_subreddit_sources (%s); using defaults.", exc)
        from reddit_signal.data_ingestion.pipeline import DEFAULT_SUBREDDITS

        return DEFAULT_SUBREDDITS, 7, True

    names = [row["subreddit_name"] for row in rows]
    max_lookback = max((int(row.get("daily_lookback_days") or 7) for row in rows), default=7)
    return names, max_lookback, True


def _mark_fetch_success(subreddits: list[str]) -> None:
    if not subreddits:
        return
    try:
        from reddit_signal.data_ingestion.db import get_conn, put_conn

        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE reddit_subreddit_sources
                    SET last_fetch_at = NOW()
                    WHERE subreddit_name = ANY(%s)
                    """,
                    (subreddits,),
                )
            conn.commit()
        finally:
            put_conn(conn)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to mark Reddit fetch success: %s", exc)


def _post_engagement(score: int, num_comments: int) -> float:
    """v1 formula: score * log(num_comments + 1)."""
    return float(score or 0) * math.log((num_comments or 0) + 1.0)


def _comment_engagement(score: int) -> float:
    """v1 formula for comments: just score (signed)."""
    return float(score or 0)


def _reddit_preview_item(post) -> dict:
    text = post.selftext or ""
    return {
        "kind": "post",
        "id": post.reddit_id,
        "surface": post.subreddit,
        "author": post.author,
        "title": post.title,
        "text": text if len(text) <= 500 else f"{text[:500]}...",
        "url": post.url or post.permalink,
        "event_time": post.created_utc.isoformat() if post.created_utc else None,
        "engagement_score": _post_engagement(post.score, post.num_comments),
        "metrics": {
            "score": post.score,
            "num_comments": post.num_comments,
            "upvote_ratio": post.upvote_ratio,
        },
    }
