"""
Database layer for Step 4: Pain Extraction Pipeline.

Reuses the connection pool from step3.db and adds:
- Schema init (pain_objects table + pgvector extension)
- Batch fetching of posts + comments
- Pain object upsert
- Stats and sampling helpers
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import psycopg2
import psycopg2.extras

from .models import PainObject, PostWithComments


# ─── v2 generic event-bundle reader ─────────────────────────────────────────

def get_event_bundles_batch_as_pwc(
    source: str = "reddit",
    *,
    prompt_version: str = "v1",
    offset: int = 0,
    limit: int = 100,
    only_unprocessed: bool = True,
    window: "tuple[datetime, datetime] | None" = None,
    cursor: "dict | None" = None,
) -> List[PostWithComments]:
    """Read Layer-2 signal_events as PostWithComments (drop-in replacement
    for ``get_posts_with_comments`` / ``get_unprocessed_posts_batch`` when
    ``USE_SIGNAL_EVENTS=true``).

    Skips parents that already have a pain_objects row for the given
    ``prompt_version`` (when ``only_unprocessed=True``).

    ``cursor`` (a dict owned by the caller, window only) carries ``before`` from one batch to the next.
    """
    from reddit_signal.sources.db import get_event_bundles_batch
    from reddit_signal.pain_extraction.prompts.registry import get_prompt_converter

    if source == "reddit" and only_unprocessed:
        mark_posts_processed([])

    converter = get_prompt_converter(source)
    bundles = get_event_bundles_batch(
        source,
        prompt_version=prompt_version,
        offset=offset,
        limit=limit,
        only_unprocessed=only_unprocessed,
        window=window,
        before=cursor.get("before") if cursor is not None else None,
    )
    if cursor is not None and bundles:
        last = bundles[-1].parent   # oldest of the batch (newest-first order)
        cursor["before"] = (last.event_time, last.id)
    return [converter(b) for b in bundles]


def lookup_signal_event_id(
    post_db_id: int | None,
    comment_db_id: Optional[int] = None,
    *,
    source: str = "reddit",
    source_table: str | None = None,
    source_row_id: str | None = None,
) -> Optional[int]:
    """Resolve a (Reddit post_id [, comment_id]) → signal_events.id.

    Used during the v2 path to populate ``pain_objects.signal_event_id`` on
    insert without a second JOIN.
    """
    from reddit_signal.sources.db import lookup_event_by_source
    if source_table and source_row_id:
        return lookup_event_by_source(source, source_table, source_row_id)
    if source != "reddit":
        return None
    if comment_db_id is not None:
        return lookup_event_by_source("reddit", "comments", str(comment_db_id))
    if post_db_id is None:
        return None
    return lookup_event_by_source("reddit", "posts", str(post_db_id))

logger = logging.getLogger(__name__)

# Connections come from the single shared pool (reddit_signal.db).
from reddit_signal.db import get_conn, get_pool, put_conn  # noqa: E402,F401


# ─── Schema init ─────────────────────────────────────────────────────────────

def init_schema():
    """
    Install pgvector and create pain_objects table (idempotent).
    Reads schema.sql from src/reddit_signal/data_ingestion/ (single source of truth).
    """
    schema_path = Path(__file__).parent.parent / "data_ingestion" / "schema.sql"
    sql = schema_path.read_text()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        logger.info("Step 4 schema initialised.")
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


# ─── Batch post/comment fetching ─────────────────────────────────────────────

def get_posts_with_comments(limit: Optional[int] = None) -> List[PostWithComments]:
    """
    Load posts (ordered by id) together with all their comments.

    Returns a list of PostWithComments. When limit is given, only the first
    N posts (by id) are returned — useful for test runs.
    """
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Fetch posts
            post_sql = """
                SELECT p.id, p.reddit_id, p.title, p.selftext,
                       p.author, p.score, p.num_comments, p.created_utc,
                       s.name AS subreddit_name
                FROM posts p
                JOIN subreddits s ON s.id = p.subreddit_id
                ORDER BY p.id
            """
            if limit:
                post_sql += f" LIMIT {int(limit)}"
            cur.execute(post_sql)
            post_rows = cur.fetchall()

        if not post_rows:
            return []

        post_ids = [r["id"] for r in post_rows]

        # Fetch all comments for these posts in one query
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, reddit_id, post_id, author, body, score,
                       is_submitter, depth, created_utc
                FROM comments
                WHERE post_id = ANY(%s)
                ORDER BY post_id, depth, id
            """, (post_ids,))
            comment_rows = cur.fetchall()

        # Group comments by post_id
        comments_by_post: dict = {}
        for c in comment_rows:
            comments_by_post.setdefault(c["post_id"], []).append(dict(c))

        result = []
        for p in post_rows:
            result.append(PostWithComments(
                post_db_id=p["id"],
                post_reddit_id=p["reddit_id"],
                subreddit_name=p["subreddit_name"],
                title=p["title"] or "",
                selftext=p["selftext"] or "",
                author=p["author"] or "[deleted]",
                score=p["score"] or 0,
                num_comments=p["num_comments"] or 0,
                post_created=p["created_utc"],
                comments=comments_by_post.get(p["id"], []),
            ))
        return result

    finally:
        put_conn(conn)


def get_unscored_pain_objects() -> List[dict]:
    """Return pain objects that have no sentiment_score yet."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT po.id, po.raw_quote, po.post_id, po.comment_id,
                       p.score AS post_score, p.num_comments,
                       c.score AS comment_score
                FROM pain_objects po
                JOIN posts p ON p.id = po.post_id
                LEFT JOIN comments c ON c.id = po.comment_id
                WHERE po.sentiment_score IS NULL
            """)
            return [dict(r) for r in cur.fetchall()]
    finally:
        put_conn(conn)


# ─── Pain object upsert ───────────────────────────────────────────────────────

def upsert_pain_object(pain: PainObject) -> int:
    """
    Insert a PainObject, ignoring duplicates (same post_id + pain_statement).
    Returns the db id (existing or newly created).
    """
    if pain.signal_event_id is not None and pain.post_id is None:
        return _upsert_pain_object_by_signal_event(pain)

    sql = """
        INSERT INTO pain_objects (
            post_id, comment_id, subreddit_name,
            pain_statement, raw_quote, mentioned_tools, is_workaround,
            source_author, sentiment_score, specificity, community_score,
            source_created, signal_event_id, prompt_version
        ) VALUES (
            %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s
        )
        ON CONFLICT (post_id, pain_statement) DO UPDATE SET
            raw_quote       = EXCLUDED.raw_quote,
            mentioned_tools = EXCLUDED.mentioned_tools,
            is_workaround   = EXCLUDED.is_workaround,
            source_author   = EXCLUDED.source_author,
            signal_event_id = COALESCE(pain_objects.signal_event_id, EXCLUDED.signal_event_id),
            prompt_version  = EXCLUDED.prompt_version
        RETURNING id
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (
                pain.post_id,
                pain.comment_id,
                pain.subreddit_name,
                pain.pain_statement,
                pain.raw_quote,
                pain.mentioned_tools or [],
                pain.is_workaround,
                pain.source_author,
                pain.sentiment_score,
                pain.specificity,
                pain.community_score,
                pain.source_created,
                pain.signal_event_id,
                pain.prompt_version,
            ))
            row = cur.fetchone()
        conn.commit()
        return row[0]
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def _upsert_pain_object_by_signal_event(pain: PainObject) -> int:
    sql = """
        INSERT INTO pain_objects (
            post_id, comment_id, subreddit_name,
            pain_statement, raw_quote, mentioned_tools, is_workaround,
            source_author, sentiment_score, specificity, community_score,
            source_created, signal_event_id, prompt_version
        ) VALUES (
            %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s
        )
        ON CONFLICT (signal_event_id, pain_statement, prompt_version)
        WHERE signal_event_id IS NOT NULL
        DO UPDATE SET
            raw_quote       = EXCLUDED.raw_quote,
            mentioned_tools = EXCLUDED.mentioned_tools,
            is_workaround   = EXCLUDED.is_workaround,
            source_author   = EXCLUDED.source_author
        RETURNING id
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (
                pain.post_id,
                pain.comment_id,
                pain.subreddit_name,
                pain.pain_statement,
                pain.raw_quote,
                pain.mentioned_tools or [],
                pain.is_workaround,
                pain.source_author,
                pain.sentiment_score,
                pain.specificity,
                pain.community_score,
                pain.source_created,
                pain.signal_event_id,
                pain.prompt_version,
            ))
            row = cur.fetchone()
        conn.commit()
        return row[0]
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def update_pain_scores(pain_id: int, sentiment: float, specificity: float, community: float):
    """Write computed scores back to a pain_object row."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE pain_objects
                SET sentiment_score = %s,
                    specificity     = %s,
                    community_score = %s
                WHERE id = %s
            """, (sentiment, specificity, community, pain_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


# ─── Stats ────────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    """Return pain extraction stats."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) AS total FROM pain_objects")
            total = cur.fetchone()["total"]

            cur.execute("SELECT COUNT(*) AS scored FROM pain_objects WHERE sentiment_score IS NOT NULL")
            scored = cur.fetchone()["scored"]

            cur.execute("""
                SELECT subreddit_name, COUNT(*) AS pain_count,
                       ROUND(AVG(sentiment_score)::numeric, 3) AS avg_sentiment,
                       ROUND(AVG(community_score)::numeric, 1) AS avg_community,
                       SUM(CASE WHEN is_workaround THEN 1 ELSE 0 END) AS workarounds
                FROM pain_objects
                GROUP BY subreddit_name
                ORDER BY pain_count DESC
            """)
            per_sub = cur.fetchall()

            cur.execute("""
                SELECT COUNT(DISTINCT post_id) AS posts_processed
                FROM pain_objects
            """)
            posts_processed = cur.fetchone()["posts_processed"]

            # More accurate count from processed_posts table
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = 'processed_posts'
                ) AS exists
            """)
            if cur.fetchone()["exists"]:
                cur.execute("SELECT COUNT(*) AS cnt FROM processed_posts")
                posts_processed = cur.fetchone()["cnt"]

            cur.execute("SELECT COUNT(*) AS total FROM posts")
            total_posts = cur.fetchone()["total"]

        return {
            "total_pain_objects": total,
            "scored": scored,
            "posts_processed": posts_processed,
            "total_posts": total_posts,
            "per_subreddit": [dict(r) for r in per_sub],
        }
    finally:
        put_conn(conn)


def get_processed_post_ids() -> set:
    """Return set of post_ids that already have pain_objects (or were marked processed)."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT post_id FROM pain_objects")
            result = {row[0] for row in cur.fetchall()}
            # Also check the processed_posts tracking table
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables 
                    WHERE table_name = 'processed_posts'
                )
            """)
            if cur.fetchone()[0]:
                cur.execute("SELECT post_id FROM processed_posts")
                result.update(row[0] for row in cur.fetchall())
        return result
    finally:
        put_conn(conn)


def mark_posts_processed(post_ids: List[int]):
    """Mark posts as processed (even if they yielded zero pain objects)."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS processed_posts (
                    post_id INT PRIMARY KEY REFERENCES posts(id),
                    processed_at TIMESTAMP DEFAULT NOW()
                )
            """)
            if post_ids:
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO processed_posts (post_id) VALUES %s ON CONFLICT DO NOTHING",
                    [(pid,) for pid in post_ids],
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def mark_signal_events_processed(event_ids: List[int], *, source: str):
    """Mark parent signal_events as processed even when extraction yields zero pains."""
    event_ids = [int(eid) for eid in event_ids if eid is not None]
    if not event_ids:
        return
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS processed_signal_events (
                    signal_event_id BIGINT PRIMARY KEY REFERENCES signal_events(id) ON DELETE CASCADE,
                    source TEXT NOT NULL,
                    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS ix_processed_signal_events_source
                    ON processed_signal_events (source, processed_at DESC)
            """)
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO processed_signal_events (signal_event_id, source)
                VALUES %s
                ON CONFLICT (signal_event_id) DO UPDATE SET
                    source = EXCLUDED.source,
                    processed_at = NOW()
                """,
                [(event_id, source) for event_id in event_ids],
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def migrate_schema():
    """
    Add new columns and tables to support the run-based analysis pipeline.
    Safe to run on existing DBs — all operations are idempotent.
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Add run_id FK to pain_clusters (needs analysis_runs to exist first)
            cur.execute("""
                ALTER TABLE pain_clusters
                ADD COLUMN IF NOT EXISTS run_id INT REFERENCES analysis_runs(id)
            """)
            # FIX-5: Add velocity column for temporal velocity signal
            cur.execute("""
                ALTER TABLE pain_clusters
                ADD COLUMN IF NOT EXISTS velocity FLOAT
            """)
        conn.commit()
        logger.info("Schema migration complete.")
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


# ─── Analysis runs ────────────────────────────────────────────────────────────

def create_analysis_run(
    window_start,
    window_end,
    resolution: str = "adhoc",
    params: dict = None,
) -> int:
    """Insert a new analysis_runs row. Returns the new run id."""
    import json as _json
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO analysis_runs (window_start, window_end, resolution, params)
                VALUES (%s, %s, %s, %s)
                RETURNING id
            """, (
                window_start,
                window_end,
                resolution,
                _json.dumps(params) if params else None,
            ))
            run_id = cur.fetchone()[0]
        conn.commit()
        logger.info("Created analysis run id=%d (%s → %s)", run_id, window_start, window_end)
        return run_id
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def update_analysis_run(run_id: int, cluster_count: int, noise_count: int, total_points: int):
    """Write cluster/noise/total counts back to an analysis_runs row."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE analysis_runs
                SET cluster_count = %s, noise_count = %s, total_points = %s
                WHERE id = %s
            """, (cluster_count, noise_count, total_points, run_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def get_analysis_runs() -> List[dict]:
    """Return all analysis runs ordered by creation time."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, window_start, window_end, resolution,
                       cluster_count, noise_count, total_points, created_at
                FROM analysis_runs
                ORDER BY id
            """)
            return [dict(r) for r in cur.fetchall()]
    finally:
        put_conn(conn)


def get_pain_ids_in_window(window_start, window_end) -> List[int]:
    """Return IDs of pain objects whose source_created falls in [window_start, window_end]."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM pain_objects
                WHERE source_created >= %s AND source_created < %s
                  AND embedding IS NOT NULL
                ORDER BY id
            """, (window_start, window_end))
            return [row[0] for row in cur.fetchall()]
    finally:
        put_conn(conn)


def get_sample_pain_objects(limit: int = 5) -> List[dict]:
    """Return top pain objects by community score for inspection."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT subreddit_name, pain_statement, mentioned_tools,
                       is_workaround, sentiment_score, specificity,
                       community_score, source_author
                FROM pain_objects
                WHERE community_score IS NOT NULL
                ORDER BY community_score DESC
                LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]
    finally:
        put_conn(conn)


# ─── Batched unprocessed post fetching (memory-safe) ────────────────────────

def get_unprocessed_posts_batch(batch_size: int = 200, last_id: int = 0,
                                 after: "datetime | None" = None) -> List[PostWithComments]:
    """
    Memory-safe batched fetch of unprocessed posts.
    Returns up to batch_size posts with ID > last_id that have no pain_objects.
    If `after` is given, restricts to posts with created_utc >= after.
    Returns empty list when no more unprocessed posts remain.
    """
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT p.id, p.reddit_id, p.title, p.selftext,
                       p.author, p.score, p.num_comments, p.created_utc,
                       s.name AS subreddit_name
                FROM posts p
                JOIN subreddits s ON s.id = p.subreddit_id
                WHERE p.id > %(last_id)s
                  AND (%(after)s IS NULL OR p.created_utc >= %(after)s)
                  AND NOT EXISTS (
                      SELECT 1 FROM pain_objects po WHERE po.post_id = p.id
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM processed_posts pp WHERE pp.post_id = p.id
                  )
                ORDER BY p.id
                LIMIT %(batch_size)s
            """, {"last_id": last_id, "batch_size": batch_size, "after": after})
            post_rows = cur.fetchall()

        if not post_rows:
            return []

        post_ids = [r["id"] for r in post_rows]

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, reddit_id, post_id, author, body, score,
                       is_submitter, depth, created_utc
                FROM comments
                WHERE post_id = ANY(%s)
                ORDER BY post_id, depth, id
            """, (post_ids,))
            comment_rows = cur.fetchall()

        comments_by_post: dict = {}
        for c in comment_rows:
            comments_by_post.setdefault(c["post_id"], []).append(dict(c))

        from .models import PostWithComments
        result = []
        for p in post_rows:
            result.append(PostWithComments(
                post_db_id=p["id"],
                post_reddit_id=p["reddit_id"],
                subreddit_name=p["subreddit_name"],
                title=p["title"] or "",
                selftext=p["selftext"] or "",
                author=p["author"] or "",
                score=p["score"] or 0,
                num_comments=p["num_comments"] or 0,
                post_created=p["created_utc"],
                comments=comments_by_post.get(p["id"], []),
            ))
        return result
    finally:
        put_conn(conn)


# ─── Extraction failures (dead-letter, LD-#24) ───────────────────────────────

MAX_EXTRACTION_ATTEMPTS = 3


def record_extraction_failure(signal_event_id: int, error: str, *, max_attempts: int = MAX_EXTRACTION_ATTEMPTS) -> None:
    """Count a failed extraction. The selector skips the event for a while; after ``max_attempts``
    it is parked (``failed``) instead of being marked processed with zero pains."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pain_extraction_attempts (signal_event_id, attempts, last_error, last_attempt_at, failed)
                VALUES (%s, 1, %s, NOW(), %s)
                ON CONFLICT (signal_event_id) DO UPDATE SET
                    attempts = pain_extraction_attempts.attempts + 1,
                    last_error = EXCLUDED.last_error,
                    last_attempt_at = NOW(),
                    failed = pain_extraction_attempts.attempts + 1 >= %s
                """,
                (signal_event_id, error[:2000], max_attempts <= 1, max_attempts),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)
