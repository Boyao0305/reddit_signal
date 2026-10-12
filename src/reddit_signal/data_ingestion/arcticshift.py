"""
Arctic Shift API client.

Docs: https://arctic-shift.photon-reddit.com
- Posts:    GET /api/posts/search?subreddit=X&after=EPOCH&before=EPOCH&limit=100&sort=asc&sort_type=created_utc
- Comments: GET /api/comments/search?link_id=t3_X&limit=100&sort=asc&sort_type=created_utc
- Max 100 items per request.
- Paginate by setting after = last item's created_utc.
- No auth required. Rate limit: 1 req/sec.

Throttling: the API answers bursts with **422** (it reports invalid parameters as 400 with a message).
On 2026-10-05, 314 of 1,529 daily requests (20.5%) got 422 and each one dropped that subreddit's
posts or comments for the day. Requests now go through one process-wide rate limiter
(``ARCTIC_SHIFT_MAX_RPS``, default 1/s) and 422/429 are retried, honouring ``x-ratelimit-reset``.

422 also means "query timeout": body ``{"error": "Timeout. Maybe slow down a bit"}``. On 2026-10-11 it was
deterministic for the newest ~30 minutes of a subreddit's comments, so waiting for the rate-limit reset and
retrying six times only stalled a fetch thread for five minutes. Query timeouts get two short retries, then
raise ``QueryTimeout``.
"""

import logging
import threading
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Generator, Optional

import httpx

from .models import Comment, Post

logger = logging.getLogger(__name__)

BASE_URL = "https://arctic-shift.photon-reddit.com/api"
MAX_RETRIES = 6
PAGE_SIZE = 100
DEFAULT_MAX_RPS = 1.0
MAX_THROTTLE_WAIT = 60.0
THROTTLED = {422, 429}
SERVER_ERRORS = {500, 502, 503, 504}
QUERY_TIMEOUT_RETRIES = 2


class QueryTimeout(RuntimeError):
    """Arctic Shift gave up on the query itself (not throttling); retrying the same query rarely helps."""


def error_text(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("error") or "")
    except Exception:  # noqa: BLE001 - non-JSON body
        return resp.text[:200]


def is_query_timeout(status: int, error: str) -> bool:
    return status == 422 and "timeout" in error.lower()


def retry_delay(status: int, headers: Mapping[str, str], attempt: int) -> float | None:
    """Seconds to wait before retrying, or ``None`` if the response should not be retried."""
    if status in THROTTLED:
        for name in ("x-ratelimit-reset", "retry-after"):
            raw = headers.get(name)
            try:
                if raw is not None:
                    return min(max(float(raw), 1.0), MAX_THROTTLE_WAIT)
            except ValueError:
                pass
        return min(2.0 ** attempt, MAX_THROTTLE_WAIT)
    if status in SERVER_ERRORS:
        return min(2.0 ** attempt, MAX_THROTTLE_WAIT)
    return None


class _RateLimiter:
    """Spaces request starts across all threads of the process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next = 0.0
        self._rps: float | None = None

    def _max_rps(self) -> float:
        if self._rps is None:
            try:
                from reddit_signal import runtime_settings

                self._rps = float(runtime_settings.get_setting("ARCTIC_SHIFT_MAX_RPS", str(DEFAULT_MAX_RPS)))
            except Exception:  # noqa: BLE001 - settings unavailable: use the documented limit
                self._rps = DEFAULT_MAX_RPS
        return self._rps

    def wait(self) -> None:
        rps = self._max_rps()
        if rps <= 0:
            return
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + 1.0 / rps
        if start > now:
            time.sleep(start - now)


_limiter = _RateLimiter()


# ─── HTTP helper ──────────────────────────────────────────────────────────────

def _get(path: str, params: dict) -> dict:
    """GET through the shared rate limiter; retries throttling (422/429) and server errors."""
    url = f"{BASE_URL}{path}"
    for attempt in range(MAX_RETRIES):
        try:
            _limiter.wait()
            resp = httpx.get(url, params=params, timeout=30)
            error = error_text(resp) if resp.status_code >= 400 else ""
            if is_query_timeout(resp.status_code, error):
                if attempt >= QUERY_TIMEOUT_RETRIES:
                    raise QueryTimeout(f"Arctic Shift query timeout after {attempt + 1} attempts: {url} {params}")
                wait = 5.0 * (attempt + 1)
                logger.warning("Arctic Shift query timeout (%s), retrying in %.0fs", error, wait)
                time.sleep(wait)
                continue
            wait = retry_delay(resp.status_code, resp.headers, attempt)
            if wait is not None:
                logger.warning("HTTP %s from Arctic Shift (%s), retrying in %.1fs (attempt %d/%d)",
                               resp.status_code, error, wait, attempt + 1, MAX_RETRIES)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except httpx.TimeoutException:
            wait = 2 ** attempt
            logger.warning("Timeout on attempt %d, waiting %.1fs", attempt + 1, wait)
            time.sleep(wait)
        except httpx.HTTPStatusError as exc:
            logger.error("HTTP error %s for %s", exc.response.status_code, url)
            raise
    raise RuntimeError(f"Failed after {MAX_RETRIES} retries: {url} {params}")


# ─── Timestamp helpers ────────────────────────────────────────────────────────

def _parse_utc(val) -> Optional[datetime]:
    if val is None:
        return None
    try:
        return datetime.fromtimestamp(float(val), tz=timezone.utc)
    except (TypeError, ValueError):
        return None


# ─── Model builders ──────────────────────────────────────────────────────────

def _post_from_raw(raw: dict) -> Post:
    return Post(
        reddit_id=raw["id"],
        subreddit=raw.get("subreddit", ""),
        title=raw.get("title", ""),
        selftext=raw.get("selftext") or None,
        author=raw.get("author") or None,
        score=int(raw.get("score", 0) or 0),
        upvote_ratio=float(raw["upvote_ratio"]) if raw.get("upvote_ratio") is not None else None,
        num_comments=int(raw.get("num_comments", 0) or 0),
        permalink=raw.get("permalink") or None,
        url=raw.get("url") or None,
        link_flair_text=raw.get("link_flair_text") or None,
        is_self=bool(raw.get("is_self", True)),
        over_18=bool(raw.get("over_18", False)),
        created_utc=_parse_utc(raw.get("created_utc")),
        raw_json=raw,
    )


def _comment_from_raw(raw: dict, post_reddit_id: str) -> Comment:
    # link_id is like "t3_abc123" — strip the "t3_" prefix
    link_id = raw.get("link_id", f"t3_{post_reddit_id}")
    if link_id.startswith("t3_"):
        resolved_post_id = link_id[3:]
    else:
        resolved_post_id = post_reddit_id

    return Comment(
        reddit_id=raw["id"],
        post_reddit_id=resolved_post_id,
        parent_reddit_id=raw.get("parent_id") or None,
        author=raw.get("author") or None,
        body=raw.get("body") or None,
        score=int(raw.get("score", 0) or 0),
        is_submitter=bool(raw.get("is_submitter", False)),
        depth=int(raw.get("depth", 0) or 0),
        created_utc=_parse_utc(raw.get("created_utc")),
        raw_json=raw,
    )


# ─── Public API ───────────────────────────────────────────────────────────────

def fetch_posts(
    subreddit: str,
    after: datetime,
    before: datetime,
) -> Generator[Post, None, None]:
    """
    Yield all Post objects for a subreddit within [after, before].
    Paginates automatically using created_utc of the last item.
    """
    after_epoch = int(after.timestamp())
    before_epoch = int(before.timestamp())
    page_after = after_epoch
    total = 0

    while True:
        params = {
            "subreddit": subreddit,
            "after": page_after,
            "before": before_epoch,
            "limit": PAGE_SIZE,
            "sort": "asc",
            "sort_type": "created_utc",
        }
        logger.debug("Fetching posts: %s (after=%s)", subreddit, page_after)
        data = _get("/posts/search", params)
        items = data.get("data", [])

        if not items:
            break

        for raw in items:
            yield _post_from_raw(raw)

        total += len(items)
        logger.debug("  got %d posts (cumulative: %d)", len(items), total)

        if len(items) < PAGE_SIZE:
            break

        # Advance pagination cursor: use created_utc of last item
        last_ts = items[-1].get("created_utc")
        if last_ts is None:
            break
        new_after = int(float(last_ts))
        if new_after <= page_after:
            # No progress — prevent infinite loop
            break
        page_after = new_after


def fetch_comments_for_post(post_reddit_id: str) -> Generator[Comment, None, None]:
    """
    Yield all Comment objects for a given post reddit_id.
    Paginates using created_utc.
    """
    link_id = f"t3_{post_reddit_id}"
    page_after = 0
    total = 0

    while True:
        params = {
            "link_id": link_id,
            "limit": PAGE_SIZE,
            "sort": "asc",
            "sort_type": "created_utc",
        }
        if page_after > 0:
            params["after"] = page_after

        logger.debug("Fetching comments for post %s (after=%s)", post_reddit_id, page_after)
        data = _get("/comments/search", params)
        items = data.get("data", [])

        if not items:
            break

        for raw in items:
            yield _comment_from_raw(raw, post_reddit_id)

        total += len(items)
        logger.debug("  got %d comments (cumulative: %d)", len(items), total)

        if len(items) < PAGE_SIZE:
            break

        last_ts = items[-1].get("created_utc")
        if last_ts is None:
            break
        new_after = int(float(last_ts))
        if new_after <= page_after:
            break
        page_after = new_after



def fetch_comments_by_subreddit(
    subreddit: str,
    after: datetime,
    before: datetime,
) -> Generator[Comment, None, None]:
    """
    Yield ALL comments from a subreddit within [after, before].

    Much more efficient than fetching per-post: O(total_comments/100) requests
    instead of O(total_posts) requests.

    Each comment's post_reddit_id is derived from its link_id field.
    """
    after_epoch = int(after.timestamp())
    before_epoch = int(before.timestamp())
    page_after = after_epoch
    total = 0

    while True:
        params = {
            "subreddit": subreddit,
            "after": page_after,
            "before": before_epoch,
            "limit": PAGE_SIZE,
            "sort": "asc",
            "sort_type": "created_utc",
        }
        logger.debug("Fetching comments: %s (after=%s)", subreddit, page_after)
        data = _get("/comments/search", params)
        items = data.get("data", [])

        if not items:
            break

        for raw in items:
            # Derive post_reddit_id from link_id (t3_XXXX)
            link_id = raw.get("link_id", "")
            if link_id.startswith("t3_"):
                post_reddit_id = link_id[3:]
            else:
                post_reddit_id = link_id
            yield _comment_from_raw(raw, post_reddit_id)

        total += len(items)
        logger.debug("  got %d comments (cumulative: %d)", len(items), total)

        if len(items) < PAGE_SIZE:
            break

        last_ts = items[-1].get("created_utc")
        if last_ts is None:
            break
        new_after = int(float(last_ts))
        if new_after <= page_after:
            break
        page_after = new_after
