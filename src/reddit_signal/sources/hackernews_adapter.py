"""Hacker News adapter using the official Firebase API."""
from __future__ import annotations

import html
import logging
import math
import re
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import httpx
import psycopg2.extras

from reddit_signal.data_ingestion.db import get_conn, put_conn
from reddit_signal.sources.base import AdapterCapabilities, BaseAdapter, EventBundle, SignalEvent
from reddit_signal.sources.db import build_source_id_map, upsert_events_fast
from reddit_signal.sources.registry import register_adapter

logger = logging.getLogger(__name__)

BASE_URL = "https://hacker-news.firebaseio.com/v0"
MAX_RETRIES = 4
REQUEST_TIMEOUT = 20
HN_SOURCE_TABLE = "hackernews_items"

_STORY_LISTS = {
    "new": "newstories",
    "newstories": "newstories",
    "ask": "askstories",
    "askstories": "askstories",
    "show": "showstories",
    "showstories": "showstories",
    "top": "topstories",
    "topstories": "topstories",
    "best": "beststories",
    "beststories": "beststories",
    "job": "jobstories",
    "jobs": "jobstories",
    "jobstories": "jobstories",
}
_DEFAULT_STORY_LISTS = ["newstories", "askstories", "showstories"]


@register_adapter
class HackerNewsAdapter(BaseAdapter):
    name = "hackernews"
    display_name = "Hacker News"
    description = "Founder/developer discussions via the official Hacker News Firebase API"
    capabilities = AdapterCapabilities(
        produces_pain=True,
        produces_traction=True,
        produces_temporal=True,
    )

    BATCH_SIZE = 5_000

    def fetch(
        self,
        query: str = "",
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 1_000,
    ) -> int:
        if limit <= 0:
            return 0
        self._ensure_hn_schema()
        rows = self._fetch_items(query=query, since=since, until=until, limit=limit)
        return self._upsert_hn_items(rows)

    def simulate_fetch(
        self,
        query: str = "",
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        sample_limit = min(max(int(limit or 1), 1), 10)
        rows = self._fetch_items(
            query=query,
            since=since,
            until=until,
            limit=sample_limit,
        )
        story_lists = _parse_story_lists(query)
        return {
            "source": self.name,
            "dry_run": True,
            "writes": 0,
            "request": {
                "provider": "hackernews-firebase",
                "endpoint": "/v0/{story_list}.json + /v0/item/{id}.json",
                "params": {
                    "story_lists": story_lists,
                    "limit": sample_limit,
                    "since": since.isoformat() if since else None,
                    "until": until.isoformat() if until else None,
                },
            },
            "count": len(rows),
            "items": [_hn_preview_item(row) for row in rows[:sample_limit]],
            "raw_sample": {
                "item_count": len(rows),
                "first_item": rows[0]["raw_json"] if rows else None,
            },
        }

    def project(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> int:
        self._ensure_hn_schema()
        n_parents = self._project_parents(since, until)
        logger.info("Projected %d Hacker News parents -> signal_events", n_parents)

        event_id_map = build_source_id_map(self.name, HN_SOURCE_TABLE)
        n_comments = self._project_comments(since, until, event_id_map)
        logger.info("Projected %d Hacker News comments -> signal_events", n_comments)
        return n_parents + n_comments

    def build_pain_prompt(self, bundle: EventBundle) -> str:
        from reddit_signal.pain_extraction.prompts.hackernews import build_hackernews_prompt
        return build_hackernews_prompt(bundle)

    def community_score(self, event: SignalEvent) -> float:
        meta = event.raw_meta or {}
        if event.event_type in ("story", "job", "poll"):
            return _parent_engagement(
                meta.get("score") or 0,
                meta.get("descendants") or 0,
            )
        return _comment_engagement(meta.get("depth") or 0)

    def _fetch_items(
        self,
        *,
        query: str,
        since: Optional[datetime],
        until: Optional[datetime],
        limit: int,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[int] = set()
        story_lists = _parse_story_lists(query)

        for story_list in story_lists:
            root_ids = _get_json(f"/{story_list}.json") or []
            if not isinstance(root_ids, list):
                logger.warning("HN story list %s returned %s", story_list, type(root_ids))
                continue
            for raw_id in root_ids:
                if len(rows) >= limit:
                    return rows
                try:
                    root_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if root_id in seen:
                    continue
                root = _get_item(root_id)
                if not root:
                    continue
                seen.add(root_id)
                root_time = _parse_hn_time(root.get("time"))
                if since and root_time and root_time < since:
                    continue
                if until and root_time and root_time >= until:
                    continue
                rows.extend(
                    self._collect_thread_rows(
                        root=root,
                        fetched_from=story_list,
                        seen=seen,
                        remaining=limit - len(rows),
                    )
                )
                if len(rows) >= limit:
                    return rows[:limit]
        return rows[:limit]

    def _collect_thread_rows(
        self,
        *,
        root: dict[str, Any],
        fetched_from: str,
        seen: set[int],
        remaining: int,
    ) -> list[dict[str, Any]]:
        if remaining <= 0:
            return []
        rows: list[dict[str, Any]] = []
        root_id = int(root["id"])
        queue = deque([(root, None, 0)])

        while queue and len(rows) < remaining:
            raw, fallback_parent, depth = queue.popleft()
            item_id = int(raw["id"])
            rows.append(_hn_row(raw, fetched_from, root_id, fallback_parent, depth))
            for kid_id in raw.get("kids") or []:
                if len(rows) + len(queue) >= remaining:
                    break
                try:
                    kid_id = int(kid_id)
                except (TypeError, ValueError):
                    continue
                if kid_id in seen:
                    continue
                child = _get_item(kid_id)
                if child is None:
                    continue
                seen.add(kid_id)
                queue.append((child, item_id, depth + 1))
        return rows

    def _upsert_hn_items(self, rows: Iterable[dict[str, Any]]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        sql = """
            INSERT INTO hackernews_items (
                hn_id, item_type, by_author, time_utc, title, text, url,
                score, descendants, parent_hn_id, root_hn_id, depth,
                fetched_from, kids, parts, deleted, dead, raw_json
            ) VALUES %s
            ON CONFLICT (hn_id) DO UPDATE SET
                item_type = EXCLUDED.item_type,
                by_author = EXCLUDED.by_author,
                time_utc = EXCLUDED.time_utc,
                title = EXCLUDED.title,
                text = EXCLUDED.text,
                url = EXCLUDED.url,
                score = EXCLUDED.score,
                descendants = EXCLUDED.descendants,
                parent_hn_id = EXCLUDED.parent_hn_id,
                root_hn_id = EXCLUDED.root_hn_id,
                depth = EXCLUDED.depth,
                fetched_from = EXCLUDED.fetched_from,
                kids = EXCLUDED.kids,
                parts = EXCLUDED.parts,
                deleted = EXCLUDED.deleted,
                dead = EXCLUDED.dead,
                raw_json = EXCLUDED.raw_json,
                updated_at = NOW()
        """
        values = [
            (
                row["hn_id"],
                row["item_type"],
                row["by_author"],
                row["time_utc"],
                row["title"],
                row["text"],
                row["url"],
                row["score"],
                row["descendants"],
                row["parent_hn_id"],
                row["root_hn_id"],
                row["depth"],
                row["fetched_from"],
                row["kids"],
                row["parts"],
                row["deleted"],
                row["dead"],
                psycopg2.extras.Json(row["raw_json"]),
            )
            for row in rows
        ]
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(cur, sql, values, page_size=500)
            conn.commit()
            return len(values)
        except Exception:
            conn.rollback()
            raise
        finally:
            put_conn(conn)

    def _project_parents(
        self,
        since: Optional[datetime],
        until: Optional[datetime],
    ) -> int:
        sql = """
            SELECT hn_id, item_type, by_author, time_utc, title, text, url,
                   score, descendants, fetched_from, kids, parts, deleted, dead,
                   raw_json
            FROM hackernews_items
            WHERE parent_hn_id IS NULL
              AND item_type IN ('story', 'job', 'poll')
              AND deleted IS FALSE
              AND dead IS FALSE
              AND (%(since)s IS NULL OR time_utc >= %(since)s)
              AND (%(until)s IS NULL OR time_utc < %(until)s)
            ORDER BY hn_id
        """
        return self._stream_and_upsert(sql, {"since": since, "until": until}, self._parent_row_to_event)

    def _project_comments(
        self,
        since: Optional[datetime],
        until: Optional[datetime],
        event_id_map: dict[str, int],
    ) -> int:
        sql = """
            SELECT hn_id, item_type, by_author, time_utc, text, score,
                   parent_hn_id, root_hn_id, depth, fetched_from, kids, parts,
                   deleted, dead, raw_json
            FROM hackernews_items
            WHERE item_type = 'comment'
              AND root_hn_id IS NOT NULL
              AND deleted IS FALSE
              AND dead IS FALSE
              AND (%(since)s IS NULL OR time_utc >= %(since)s)
              AND (%(until)s IS NULL OR time_utc < %(until)s)
            ORDER BY root_hn_id, depth, hn_id
        """
        return self._stream_and_upsert(
            sql,
            {"since": since, "until": until},
            lambda row: self._comment_row_to_event(row, event_id_map),
        )

    def _stream_and_upsert(self, sql: str, params: dict, row_to_event) -> int:
        total = 0
        conn = get_conn()
        try:
            with conn.cursor(
                name=f"hn_project_{id(sql) & 0xffff:x}",
                cursor_factory=psycopg2.extras.RealDictCursor,
            ) as cur:
                cur.itersize = self.BATCH_SIZE
                cur.execute(sql, params)
                batch: list[SignalEvent] = []
                for row in cur:
                    event = row_to_event(row)
                    if event is None:
                        continue
                    batch.append(event)
                    if len(batch) >= self.BATCH_SIZE:
                        total += upsert_events_fast(batch)
                        batch = []
                if batch:
                    total += upsert_events_fast(batch)
            return total
        finally:
            put_conn(conn)

    def _parent_row_to_event(self, row) -> SignalEvent:
        meta = _base_raw_meta(row)
        return SignalEvent(
            source=self.name,
            source_table=HN_SOURCE_TABLE,
            source_row_id=str(row["hn_id"]),
            event_type=row["item_type"],
            event_time=_ensure_tz(row["time_utc"]),
            surface=_surface(row),
            author=row["by_author"],
            title=row["title"],
            text=_clean_hn_text(row["text"] or ""),
            engagement_score=_parent_engagement(row["score"], row["descendants"]),
            raw_meta=meta,
        )

    def _comment_row_to_event(self, row, event_id_map: dict[str, int]) -> SignalEvent | None:
        root_event_id = event_id_map.get(str(row["root_hn_id"]))
        if root_event_id is None:
            return None
        meta = _base_raw_meta(row)
        meta["parent_hn_id"] = row["parent_hn_id"]
        meta["root_hn_id"] = row["root_hn_id"]
        meta["depth"] = row["depth"]
        return SignalEvent(
            source=self.name,
            source_table=HN_SOURCE_TABLE,
            source_row_id=str(row["hn_id"]),
            event_type="comment",
            event_time=_ensure_tz(row["time_utc"]),
            surface=_surface(row),
            author=row["by_author"],
            text=_clean_hn_text(row["text"] or ""),
            engagement_score=_comment_engagement(row["depth"]),
            parent_event_id=root_event_id,
            raw_meta=meta,
        )

    def _ensure_hn_schema(self) -> None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS hackernews_items (
                        hn_id BIGINT PRIMARY KEY,
                        item_type TEXT NOT NULL,
                        by_author TEXT,
                        time_utc TIMESTAMPTZ,
                        title TEXT,
                        text TEXT,
                        url TEXT,
                        score INTEGER,
                        descendants INTEGER,
                        parent_hn_id BIGINT,
                        root_hn_id BIGINT,
                        depth INTEGER NOT NULL DEFAULT 0,
                        fetched_from TEXT NOT NULL DEFAULT '',
                        kids BIGINT[] NOT NULL DEFAULT '{}',
                        parts BIGINT[] NOT NULL DEFAULT '{}',
                        deleted BOOLEAN NOT NULL DEFAULT FALSE,
                        dead BOOLEAN NOT NULL DEFAULT FALSE,
                        raw_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS ix_hn_items_type_time ON hackernews_items (item_type, time_utc DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS ix_hn_items_root ON hackernews_items (root_hn_id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS ix_hn_items_parent ON hackernews_items (parent_hn_id)"
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            put_conn(conn)


def _get_json(path: str) -> Any:
    url = f"{BASE_URL}{path}"
    for attempt in range(MAX_RETRIES):
        try:
            resp = httpx.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = 2 ** attempt
                logger.warning("HN API %s for %s, waiting %ss", resp.status_code, url, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except httpx.TimeoutException:
            wait = 2 ** attempt
            logger.warning("HN API timeout for %s, waiting %ss", url, wait)
            time.sleep(wait)
    raise RuntimeError(f"Hacker News API request failed after retries: {url}")


def _get_item(hn_id: int) -> dict[str, Any] | None:
    item = _get_json(f"/item/{hn_id}.json")
    return item if isinstance(item, dict) else None


def _parse_story_lists(query: str) -> list[str]:
    if not query.strip():
        return list(_DEFAULT_STORY_LISTS)
    story_lists: list[str] = []
    for token in re.split(r"[\s,]+", query.strip().lower()):
        if not token:
            continue
        story_list = _STORY_LISTS.get(token)
        if story_list and story_list not in story_lists:
            story_lists.append(story_list)
        elif not story_list:
            logger.warning("Ignoring unsupported Hacker News source list: %s", token)
    return story_lists or list(_DEFAULT_STORY_LISTS)


def _hn_preview_item(row: dict[str, Any]) -> dict[str, Any]:
    text = _clean_hn_text(row.get("text") or "")
    return {
        "kind": row.get("item_type"),
        "id": row.get("hn_id"),
        "surface": _surface(row),
        "author": row.get("by_author"),
        "title": row.get("title"),
        "text": text if len(text) <= 500 else f"{text[:500]}...",
        "url": row.get("url") or f"https://news.ycombinator.com/item?id={row.get('hn_id')}",
        "event_time": row["time_utc"].isoformat() if row.get("time_utc") else None,
        "engagement_score": _parent_engagement(row.get("score"), row.get("descendants"))
        if row.get("parent_hn_id") is None
        else _comment_engagement(row.get("depth")),
        "metrics": {
            "score": row.get("score"),
            "descendants": row.get("descendants"),
            "depth": row.get("depth"),
        },
    }


def _hn_row(
    raw: dict[str, Any],
    fetched_from: str,
    root_id: int,
    fallback_parent: int | None,
    depth: int,
) -> dict[str, Any]:
    item_id = int(raw["id"])
    parent_hn_id = raw.get("parent") or fallback_parent
    is_root = item_id == root_id
    return {
        "hn_id": item_id,
        "item_type": raw.get("type") or "unknown",
        "by_author": raw.get("by"),
        "time_utc": _parse_hn_time(raw.get("time")),
        "title": raw.get("title"),
        "text": raw.get("text"),
        "url": raw.get("url"),
        "score": _maybe_int(raw.get("score")),
        "descendants": _maybe_int(raw.get("descendants")),
        "parent_hn_id": None if is_root else _maybe_int(parent_hn_id),
        "root_hn_id": root_id,
        "depth": depth,
        "fetched_from": fetched_from,
        "kids": [_maybe_int(k) for k in (raw.get("kids") or []) if _maybe_int(k) is not None],
        "parts": [_maybe_int(p) for p in (raw.get("parts") or []) if _maybe_int(p) is not None],
        "deleted": bool(raw.get("deleted", False)),
        "dead": bool(raw.get("dead", False)),
        "raw_json": raw,
    }


def _base_raw_meta(row) -> dict[str, Any]:
    return {
        "hn_id": row["hn_id"],
        "item_type": row["item_type"],
        "url": row.get("url"),
        "score": row.get("score"),
        "descendants": row.get("descendants"),
        "fetched_from": row.get("fetched_from"),
        "kids": row.get("kids") or [],
        "parts": row.get("parts") or [],
    }


def _surface(row) -> str:
    fetched_from = row.get("fetched_from") or "hackernews"
    return f"hn:{fetched_from.replace('stories', '')}"


def _parse_hn_time(value) -> datetime | None:
    value = _maybe_int(value)
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _ensure_tz(ts) -> datetime:
    if ts is None:
        return datetime.now(tz=timezone.utc)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _maybe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_hn_text(text: str) -> str:
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _parent_engagement(score, descendants) -> float:
    return math.log1p(max(int(score or 0), 0)) * 2.0 + math.log1p(max(int(descendants or 0), 0))


def _comment_engagement(depth) -> float:
    return 1.0 / (1.0 + max(int(depth or 0), 0))
