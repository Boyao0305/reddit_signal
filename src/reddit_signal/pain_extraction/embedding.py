"""
Embedding module for pain objects.

Uses sentence-transformers all-MiniLM-L6-v2 (384 dims) locally.
Embeds pain_statement text and stores vectors in pgvector column.
"""

import hashlib
import logging
import os
from typing import List, Optional

import numpy as np
import psycopg2.extras

from . import db

logger = logging.getLogger(__name__)

# ─── Lazy-load model ──────────────────────────────────────────────────────────

_model = None
MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading sentence-transformers model: %s", MODEL_NAME)
        _model = SentenceTransformer(MODEL_NAME)
        logger.info("Model loaded. Embedding dim: %d", EMBEDDING_DIM)
    return _model


# ─── Core functions ───────────────────────────────────────────────────────────

def embed_texts(texts: List[str], batch_size: int = 256) -> np.ndarray:
    """Encode a list of texts into embeddings. Returns (N, 384) array."""
    model = _get_model()
    embeddings = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=True,
        normalize_embeddings=True,  # cosine similarity = dot product
    )
    return embeddings


def get_unembedded_pain_objects(limit: Optional[int] = None) -> List[dict]:
    """Return pain objects that have no embedding yet."""
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            sql = """
                SELECT id, pain_statement
                FROM pain_objects
                WHERE embedding IS NULL
                ORDER BY id
            """
            if limit:
                sql += f" LIMIT {int(limit)}"
            cur.execute(sql)
            return [dict(r) for r in cur.fetchall()]
    finally:
        db.put_conn(conn)


def save_embeddings_batch(ids: List[int], embeddings: np.ndarray):
    """Write embeddings to DB in a single transaction."""
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            # Use pgvector's vector format: '[0.1, 0.2, ...]'
            data = []
            for i, pain_id in enumerate(ids):
                vec_str = "[" + ",".join(f"{v:.6f}" for v in embeddings[i]) + "]"
                data.append((vec_str, pain_id))

            psycopg2.extras.execute_batch(
                cur,
                "UPDATE pain_objects SET embedding = %s::vector WHERE id = %s",
                data,
                page_size=500,
            )
        conn.commit()
        logger.info("Saved %d embeddings to DB.", len(ids))
    except Exception:
        conn.rollback()
        raise
    finally:
        db.put_conn(conn)


def embed_all_pain_objects(batch_size: int = 512):
    """
    Main entry: embed all pain objects that don't have embeddings yet.
    Processes in batches to manage memory.
    """
    unembedded = get_unembedded_pain_objects()
    total = len(unembedded)

    if total == 0:
        print("All pain objects already embedded.")
        return {"embedded": 0, "total": 0}

    print(f"Embedding {total} pain objects (model: {MODEL_NAME})...")

    ids = [r["id"] for r in unembedded]
    texts = [r["pain_statement"] for r in unembedded]

    # Embed in one pass (14k texts is fine for MiniLM)
    embeddings = embed_texts(texts, batch_size=batch_size)

    # Save in batches of 1000
    save_batch_size = 1000
    for start in range(0, total, save_batch_size):
        end = min(start + save_batch_size, total)
        save_embeddings_batch(ids[start:end], embeddings[start:end])
        print(f"  Saved embeddings [{start+1}..{end}] / {total}")

    print(f"Done. Embedded {total} pain objects.")
    return {"embedded": total, "total": total}


_FETCH_CHUNK = 10_000


def _stream_embeddings(conn, where_sql: str, params: tuple, expected: int) -> tuple[np.ndarray, np.ndarray]:
    """Read ``(id, embedding)`` rows into preallocated arrays, one chunk at a time.

    Replaces a ``fetchall()`` of every vector as text followed by two copies (list of arrays, then
    ``np.array``): peak memory was several times the 384 x 4-byte result. A server-side cursor streams
    ``_FETCH_CHUNK`` rows at a time. Count and read share one REPEATABLE READ snapshot so ``expected``
    matches the rows read. Parsing is unchanged, so the output is identical.
    """
    ids = np.empty(expected, dtype=np.int64)
    embeddings = np.empty((expected, EMBEDDING_DIM), dtype=np.float32)
    filled = 0
    with conn.cursor(name="embedding_stream") as cur:
        cur.itersize = _FETCH_CHUNK
        cur.execute(
            f"SELECT id, embedding::text FROM pain_objects WHERE embedding IS NOT NULL {where_sql} ORDER BY id",
            params,
        )
        while True:
            rows = cur.fetchmany(_FETCH_CHUNK)
            if not rows:
                break
            for pain_id, text in rows:
                ids[filled] = pain_id
                embeddings[filled] = np.fromstring(text.strip("[]"), sep=",", dtype=np.float32)
                filled += 1
    if filled != expected:
        raise RuntimeError(f"embedding stream read {filled} rows, expected {expected}")
    return ids, embeddings


def _count_in_snapshot(conn, where_sql: str, params: tuple) -> int:
    """Start a REPEATABLE READ transaction and count the rows the stream will read."""
    with conn.cursor() as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        cur.execute(f"SELECT COUNT(*) FROM pain_objects WHERE embedding IS NOT NULL {where_sql}", params)
        return cur.fetchone()[0]


def _window_cache_path(window_start, window_end) -> str:
    key = f"{window_start}_{window_end}"
    h = hashlib.md5(key.encode()).hexdigest()[:12]
    return f"/tmp/step4_embeddings_window_{h}.npz"


def load_embeddings_for_window(window_start, window_end) -> tuple:
    """
    Load pain object IDs and embeddings for objects within a time window.
    Filters by source_created BETWEEN window_start AND window_end.
    Returns (ids: List[int], embeddings: np.ndarray of shape (N, 384)).
    Caches to /tmp/ as .npz; reloads from DB when row count changes.
    """
    cache_path = _window_cache_path(window_start, window_end)
    where_sql = "AND source_created >= %s AND source_created < %s"
    params = (window_start, window_end)
    conn = db.get_conn()
    try:
        db_count = _count_in_snapshot(conn, where_sql, params)

        if db_count == 0:
            return [], np.array([])

        # Load from cache if row count matches
        if os.path.exists(cache_path):
            try:
                cached = np.load(cache_path, allow_pickle=False)
                if len(cached["ids"]) == db_count:
                    logger.info("Loaded %d window embeddings from cache %s",
                                db_count, cache_path)
                    return cached["ids"].tolist(), cached["embeddings"]
            except Exception as exc:
                logger.warning("Cache load failed (%s), re-fetching from DB", exc)

        ids_arr, embeddings = _stream_embeddings(conn, where_sql, params, db_count)

        try:
            np.savez(cache_path, ids=ids_arr, embeddings=embeddings)
            logger.info("Saved %d window embeddings to cache %s", len(ids_arr), cache_path)
        except Exception as exc:
            logger.warning("Could not write embedding cache: %s", exc)

        return ids_arr.tolist(), embeddings
    finally:
        conn.rollback()  # end the read-only snapshot before returning the connection
        db.put_conn(conn)


_ALL_CACHE = "/tmp/step4_embeddings_all.npz"


def load_all_embeddings() -> tuple:
    """
    Load all pain object IDs and their embeddings from DB.
    Returns (ids: List[int], embeddings: np.ndarray of shape (N, 384)).
    Only returns rows that HAVE embeddings.
    Caches to /tmp/step4_embeddings_all.npz; reloads from DB when row count changes.
    """
    conn = db.get_conn()
    try:
        db_count = _count_in_snapshot(conn, "", ())

        if db_count == 0:
            return [], np.array([])

        # Load from cache if row count matches
        if os.path.exists(_ALL_CACHE):
            try:
                cached = np.load(_ALL_CACHE, allow_pickle=False)
                if len(cached["ids"]) == db_count:
                    logger.info("Loaded %d embeddings from cache %s",
                                db_count, _ALL_CACHE)
                    return cached["ids"].tolist(), cached["embeddings"]
            except Exception as exc:
                logger.warning("Cache load failed (%s), re-fetching from DB", exc)

        ids_arr, embeddings = _stream_embeddings(conn, "", (), db_count)

        try:
            np.savez(_ALL_CACHE, ids=ids_arr, embeddings=embeddings)
            logger.info("Saved %d embeddings to cache %s", len(ids_arr), _ALL_CACHE)
        except Exception as exc:
            logger.warning("Could not write embedding cache: %s", exc)

        return ids_arr.tolist(), embeddings
    finally:
        conn.rollback()
        db.put_conn(conn)
