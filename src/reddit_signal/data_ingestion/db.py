"""
Database layer for the Arctic Shift Reddit pipeline.

Adapted from step2/db.py. Adds truncate_all() for cleaning old data.
Uses raw psycopg2 with the shared pool in reddit_signal.db.
All upserts use ON CONFLICT ... DO UPDATE so re-fetching is idempotent.
"""

import json
import logging
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras

from .models import Comment, Post, Subreddit

logger = logging.getLogger(__name__)

# Connections come from the single shared pool (reddit_signal.db).
from reddit_signal.db import get_conn, get_pool, put_conn  # noqa: E402,F401


# ─── Schema init ─────────────────────────────────────────────────────────────

def init_db():
    """Create all tables and indexes (idempotent — uses IF NOT EXISTS)."""
    schema_path = Path(__file__).parent / "schema.sql"
    sql = schema_path.read_text()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        logger.info("Database schema initialised.")
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def test_connection() -> bool:
    """Return True if the DB is reachable, False otherwise."""
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        put_conn(conn)
        return True
    except Exception as exc:
        logger.error("DB connection test failed: %s", exc)
        return False


# ─── Clean / truncate ─────────────────────────────────────────────────────────

def truncate_all():
    """
    Wipe all rows from comments, posts, subreddits.
    Order matters due to FK constraints.
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE comments RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE TABLE posts RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE TABLE subreddits RESTART IDENTITY CASCADE")
        conn.commit()
        logger.info("All tables truncated.")
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


# ─── Upserts ─────────────────────────────────────────────────────────────────

def upsert_subreddit(sub: Subreddit) -> int:
    """Insert or update a subreddit row. Returns the internal integer id."""
    sql = """
        INSERT INTO subreddits (name, display_name, subscribers, description, updated_at)
        VALUES (%s, %s, %s, %s, NOW())
        ON CONFLICT (name) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            subscribers  = COALESCE(EXCLUDED.subscribers, subreddits.subscribers),
            description  = COALESCE(EXCLUDED.description,  subreddits.description),
            updated_at   = NOW()
        RETURNING id
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (sub.name, sub.display_name, sub.subscribers, sub.description))
            row = cur.fetchone()
        conn.commit()
        return row[0]
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def get_subreddit_id(name: str) -> Optional[int]:
    """Look up the internal id for a subreddit name."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM subreddits WHERE name = %s", (name,))
            row = cur.fetchone()
        return row[0] if row else None
    finally:
        put_conn(conn)


def upsert_post(post: Post, subreddit_id: int) -> int:
    """Insert or update a post row. Returns the internal integer id."""
    sql = """
        INSERT INTO posts (
            reddit_id, subreddit_id, title, selftext, author, score,
            upvote_ratio, num_comments, permalink, url, link_flair_text,
            is_self, over_18, created_utc, raw_json
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s
        )
        ON CONFLICT (reddit_id) DO UPDATE SET
            score        = EXCLUDED.score,
            num_comments = EXCLUDED.num_comments,
            upvote_ratio = EXCLUDED.upvote_ratio,
            raw_json     = EXCLUDED.raw_json,
            fetched_at   = NOW()
        RETURNING id
    """
    raw = json.dumps(post.raw_json) if post.raw_json else None
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (
                post.reddit_id, subreddit_id, post.title, post.selftext,
                post.author, post.score, post.upvote_ratio, post.num_comments,
                post.permalink, post.url, post.link_flair_text,
                post.is_self, post.over_18, post.created_utc, raw,
            ))
            row = cur.fetchone()
        conn.commit()
        return row[0]
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def upsert_comment(comment: Comment, post_id: int) -> int:
    """Insert or update a comment row. Returns the internal integer id."""
    sql = """
        INSERT INTO comments (
            reddit_id, post_id, parent_reddit_id, author, body,
            score, is_submitter, depth, created_utc, raw_json
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s
        )
        ON CONFLICT (reddit_id) DO UPDATE SET
            score      = EXCLUDED.score,
            body       = EXCLUDED.body,
            raw_json   = EXCLUDED.raw_json,
            fetched_at = NOW()
        RETURNING id
    """
    raw = json.dumps(comment.raw_json) if comment.raw_json else None
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (
                comment.reddit_id, post_id, comment.parent_reddit_id,
                comment.author, comment.body, comment.score,
                comment.is_submitter, comment.depth, comment.created_utc, raw,
            ))
            row = cur.fetchone()
        conn.commit()
        return row[0]
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def upsert_comments_batch(comments_with_post_ids: list[tuple[Comment, int]]) -> int:
    """
    Batch upsert comments in a single transaction.
    Takes list of (Comment, post_db_id) tuples.
    Returns number of rows upserted.
    """
    if not comments_with_post_ids:
        return 0
    sql = """
        INSERT INTO comments (
            reddit_id, post_id, parent_reddit_id, author, body,
            score, is_submitter, depth, created_utc, raw_json
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s
        )
        ON CONFLICT (reddit_id) DO UPDATE SET
            score      = EXCLUDED.score,
            body       = EXCLUDED.body,
            raw_json   = EXCLUDED.raw_json,
            fetched_at = NOW()
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            data = []
            for comment, post_id in comments_with_post_ids:
                raw = json.dumps(comment.raw_json) if comment.raw_json else None
                data.append((
                    comment.reddit_id, post_id, comment.parent_reddit_id,
                    comment.author, comment.body, comment.score,
                    comment.is_submitter, comment.depth, comment.created_utc, raw,
                ))
            psycopg2.extras.execute_batch(cur, sql, data, page_size=100)
        conn.commit()
        return len(data)
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def upsert_posts_batch(posts_with_sub_id: list[tuple[Post, int]]) -> dict[str, int]:
    """
    Batch upsert posts in a single transaction.
    Takes list of (Post, subreddit_db_id) tuples.
    Returns {reddit_id: db_id} mapping.
    """
    if not posts_with_sub_id:
        return {}
    sql = """
        INSERT INTO posts (
            reddit_id, subreddit_id, title, selftext, author, score,
            upvote_ratio, num_comments, permalink, url, link_flair_text,
            is_self, over_18, created_utc, raw_json
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s
        )
        ON CONFLICT (reddit_id) DO UPDATE SET
            score        = EXCLUDED.score,
            num_comments = EXCLUDED.num_comments,
            upvote_ratio = EXCLUDED.upvote_ratio,
            raw_json     = EXCLUDED.raw_json,
            fetched_at   = NOW()
        RETURNING id, reddit_id
    """
    conn = get_conn()
    try:
        id_map = {}
        with conn.cursor() as cur:
            for post, subreddit_id in posts_with_sub_id:
                raw = json.dumps(post.raw_json) if post.raw_json else None
                cur.execute(sql, (
                    post.reddit_id, subreddit_id, post.title, post.selftext,
                    post.author, post.score, post.upvote_ratio, post.num_comments,
                    post.permalink, post.url, post.link_flair_text,
                    post.is_self, post.over_18, post.created_utc, raw,
                ))
                row = cur.fetchone()
                id_map[row[1]] = row[0]
        conn.commit()
        return id_map
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def get_post_id(reddit_id: str) -> Optional[int]:
    """Look up the internal id for a post by its reddit_id."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM posts WHERE reddit_id = %s", (reddit_id,))
            row = cur.fetchone()
        return row[0] if row else None
    finally:
        put_conn(conn)


def get_post_ids_for_subreddit(subreddit_id: int) -> dict:
    """
    Return {reddit_id: db_id} for all posts belonging to this subreddit.
    Used after bulk post upserts to get a guaranteed-accurate mapping.
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT reddit_id, id FROM posts WHERE subreddit_id = %s",
                (subreddit_id,),
            )
            return {row[0]: row[1] for row in cur.fetchall()}
    finally:
        put_conn(conn)


# ─── Stats helpers ────────────────────────────────────────────────────────────

def get_stats() -> dict:
    """Return row counts per table and per subreddit."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) AS total FROM subreddits")
            total_subs = cur.fetchone()["total"]

            cur.execute("SELECT COUNT(*) AS total FROM posts")
            total_posts = cur.fetchone()["total"]

            cur.execute("SELECT COUNT(*) AS total FROM comments")
            total_comments = cur.fetchone()["total"]

            cur.execute("""
                SELECT s.name,
                       COUNT(DISTINCT p.id) AS post_count,
                       COUNT(c.id) AS comment_count
                FROM subreddits s
                LEFT JOIN posts p ON p.subreddit_id = s.id
                LEFT JOIN comments c ON c.post_id = p.id
                GROUP BY s.name
                ORDER BY post_count DESC
            """)
            per_sub = cur.fetchall()

            # Date range of posts
            cur.execute("""
                SELECT MIN(created_utc) AS oldest, MAX(created_utc) AS newest
                FROM posts
            """)
            date_range = cur.fetchone()

        return {
            "subreddits": total_subs,
            "posts": total_posts,
            "comments": total_comments,
            "per_subreddit": [dict(r) for r in per_sub],
            "oldest_post": date_range["oldest"],
            "newest_post": date_range["newest"],
        }
    finally:
        put_conn(conn)


def get_sample_posts(subreddit: str, limit: int = 5) -> list:
    """Return sample posts with their stored comment count."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT p.reddit_id, p.title, p.score, p.num_comments,
                       p.created_utc, p.permalink,
                       COUNT(c.id) AS stored_comments
                FROM posts p
                JOIN subreddits s ON s.id = p.subreddit_id
                LEFT JOIN comments c ON c.post_id = p.id
                WHERE s.name = %s
                GROUP BY p.id
                ORDER BY p.score DESC
                LIMIT %s
            """, (subreddit, limit))
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        put_conn(conn)


def get_fetch_boundaries(subreddit_name: str) -> dict:
    """Return the oldest and newest post created_utc for a subreddit."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT MIN(p.created_utc) AS oldest,
                       MAX(p.created_utc) AS newest,
                       COUNT(p.id) AS post_count
                FROM posts p
                JOIN subreddits s ON s.id = p.subreddit_id
                WHERE s.name = %s
            """, (subreddit_name,))
            row = cur.fetchone()
        return dict(row) if row else {"oldest": None, "newest": None, "post_count": 0}
    finally:
        put_conn(conn)


def check_timestamp_window(days: int = 7) -> dict:
    """Verify all posts fall within the expected time window."""
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN created_utc >= %s THEN 1 ELSE 0 END) AS in_window,
                       SUM(CASE WHEN created_utc < %s THEN 1 ELSE 0 END) AS out_of_window
                FROM posts
            """, (cutoff, cutoff))
            row = dict(cur.fetchone())

            # Check comment linkage
            cur.execute("""
                SELECT COUNT(*) AS orphan_comments
                FROM comments c
                WHERE NOT EXISTS (SELECT 1 FROM posts p WHERE p.id = c.post_id)
            """)
            orphan = cur.fetchone()["orphan_comments"]
            row["orphan_comments"] = orphan

        return row
    finally:
        put_conn(conn)
