"""
LLM pain extraction via OpenRouter (OpenAI-compatible API).

Each Reddit post + its comments is sent as a single LLM call.
The model returns a JSON array of pain signal objects.

Supports concurrent extraction with ThreadPoolExecutor.
Saves results incrementally — safe to stop and resume.

Output that cannot be used (unparseable, or cut off at the token limit) is an error, never "no pains":
treating it as "no pains" silently lost ~6% of posts. A response cut
off at ``max_tokens`` is retried with double the budget. Provider routing on OpenRouter is
configurable (``OPENROUTER_PROVIDER_ORDER``, ``OPENROUTER_ALLOW_FALLBACKS``).
"""

import json
import logging
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Callable

from openai import OpenAI, RateLimitError, APIStatusError

from reddit_signal import runtime_settings

from .config import get_openrouter_api_key, get_openrouter_model
from .models import PainObject, PostWithComments

logger = logging.getLogger(__name__)

# ─── OpenRouter client ────────────────────────────────────────────────────────

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MAX_TOKENS = 2048
DEFAULT_REASONING_EFFORT = "low"
MAX_TOKENS_CEILING = 8192

_client: Optional[OpenAI] = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=get_openrouter_api_key(),
            base_url=OPENROUTER_BASE_URL,
        )
    return _client


def smoke_check_model(model_id: str | None = None) -> None:
    """Fail fast when the configured OpenRouter model is unavailable.

    Daily cron calls this before processing thousands of posts so a retired
    model ID or auth issue does not turn into a long batch of per-post errors.
    """
    if model_id is None:
        model_id = get_openrouter_model()
    client = get_client()
    try:
        response = client.chat.completions.create(
            model=model_id,
            messages=[{"role": "user", "content": "Return exactly: ok"}],
            temperature=0.0,
            max_tokens=2,
        )
    except Exception as exc:
        raise RuntimeError(f"OpenRouter smoke check failed for model={model_id}: {exc}") from exc
    if not response.choices:
        raise RuntimeError(f"OpenRouter smoke check returned no choices for model={model_id}")


# ─── Prompt building ──────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a B2B SaaS market research analyst. Your task is to extract pain signals \
from online community discussions. A pain signal is a concrete frustration, problem, unmet \
need, or workaround that a person describes when using software, tools, or workflows.

Rules:
- Extract ONLY genuine, specific pain signals. Skip generic complaints, off-topic posts, \
  or vague dissatisfaction.
- Each pain signal must come from a specific piece of text in the provided content.
- Do NOT invent, paraphrase creatively, or rate anything numerically.
- Return a JSON array (may be empty []) where each element has EXACTLY these fields:
  {
    "pain_statement": "<normalized 1-2 sentence description of the pain>",
    "raw_quote": "<exact verbatim text from the post or comment, max 400 chars>",
    "mentioned_tools": ["<tool/product/service name>", ...],
    "is_workaround": <true if the person describes a manual workaround, false otherwise>,
    "source": "<'post' OR 'comment_<reddit_id>'>",
    "source_author": "<reddit username or '[deleted]'>"
  }
- If no pain signals are found, return exactly: []
- Return ONLY the JSON array. No markdown, no explanation, no code fences.\
"""


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...[truncated]"


def build_prompt(pwc: PostWithComments) -> str:
    """Build the user message for a PostWithComments."""
    if pwc.source_name != "reddit":
        return _build_generic_source_prompt(pwc)

    parts = [
        f"SUBREDDIT: r/{pwc.subreddit_name}",
        f"POST TITLE: {pwc.title}",
        f"POST AUTHOR: u/{pwc.author}",
        f"POST SCORE: {pwc.score}  COMMENTS: {pwc.num_comments}",
        "",
        "POST BODY:",
        _truncate(pwc.selftext or "(no body)", 2000),
    ]

    if pwc.comments:
        parts.append("")
        parts.append(f"COMMENTS ({len(pwc.comments)} stored):")
        for c in pwc.comments[:50]:  # cap at 50 comments per post
            body = _truncate(c.get("body") or "", 500)
            if not body or body in ("[deleted]", "[removed]"):
                continue
            parts.append(
                f"  [comment:{c['reddit_id']}] u/{c.get('author', '?')} "
                f"(score:{c.get('score', 0)}): {body}"
            )

    return "\n".join(parts)


def _build_generic_source_prompt(pwc: PostWithComments) -> str:
    source_label = pwc.source_name
    surface = pwc.subreddit_name
    parts = [
        f"SOURCE: {source_label}",
        f"SURFACE: {surface}",
        f"THREAD ID: {pwc.post_reddit_id}",
        f"THREAD TITLE: {pwc.title}",
        f"THREAD AUTHOR: {pwc.author}",
        f"THREAD SCORE: {pwc.score}  REPLIES: {pwc.num_comments}",
        "",
        "THREAD BODY:",
        _truncate(pwc.selftext or "(no body)", 2000),
    ]

    if pwc.comments:
        parts.append("")
        parts.append(f"REPLIES ({len(pwc.comments)} stored):")
        for c in pwc.comments[:50]:
            body = _truncate(c.get("body") or "", 500)
            if not body or body in ("[deleted]", "[removed]"):
                continue
            parts.append(
                f"  [comment:{c['reddit_id']}] {c.get('author', '?')} "
                f"(score:{c.get('score', 0)}): {body}"
            )

    return "\n".join(parts)


# ─── JSON parsing ─────────────────────────────────────────────────────────────

class ExtractionError(Exception):
    """Raised when all extraction retries are exhausted."""


class UnusableOutput(ExtractionError):
    """The model answered, but not with a usable JSON array (or it was cut off)."""


def _json_candidates(text: str):
    """The text itself, then the outermost [...] inside it (models sometimes add a sentence around it)."""
    yield text
    start, end = text.find("["), text.rfind("]")
    if 0 <= start < end:
        yield text[start:end + 1]


def _repair(text: str) -> str:
    """Escape bare backslashes that are not JSON escapes (e.g. \\' or C:\\path); drop trailing commas."""
    text = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", text)
    return re.sub(r",\s*([\]}])", r"\1", text)


def load_pain_array(raw: str) -> list:
    """The JSON array in a model response; raises UnusableOutput if there is none."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    for candidate in _json_candidates(text):
        for attempt in (candidate, _repair(candidate)):
            try:
                data = json.loads(attempt)
            except json.JSONDecodeError:
                continue
            if isinstance(data, list):
                return data
            raise UnusableOutput(f"expected a JSON array, got {type(data).__name__}")
    raise UnusableOutput(f"no JSON array in response: {text[:120]!r}")


def parse_pain_json(raw: str, pwc: PostWithComments) -> List[PainObject]:
    """Parse the LLM JSON response into PainObject instances. Raises UnusableOutput if unparseable."""
    data = load_pain_array(raw)

    objects = []
    comment_map = {c["reddit_id"]: c for c in pwc.comments}

    for item in data:
        if not isinstance(item, dict):
            continue

        pain_stmt = str(item.get("pain_statement") or "").strip()
        raw_quote = str(item.get("raw_quote") or "").strip()
        if not pain_stmt or not raw_quote:
            continue

        source = str(item.get("source") or "post").strip()
        mentioned = [str(t).strip() for t in (item.get("mentioned_tools") or []) if t]
        is_workaround = bool(item.get("is_workaround", False))
        _author_raw = str(item.get("source_author") or "").strip()
        source_author = _author_raw.removeprefix("u/") or None

        comment_id: Optional[int] = None
        source_created = pwc.post_created

        if source.startswith("comment_"):
            comment_reddit_id = source[len("comment_"):]
            c = comment_map.get(comment_reddit_id)
            if c:
                comment_id = c["id"]
                source_created = c.get("created_utc") or pwc.post_created
                if not source_author:
                    source_author = c.get("author")
                signal_event_id = c.get("signal_event_id")
            else:
                signal_event_id = None
        else:
            signal_event_id = pwc.signal_event_id

        if not source_author:
            source_author = pwc.author if source == "post" else None

        objects.append(PainObject(
            post_id=pwc.post_db_id,
            comment_id=comment_id,
            subreddit_name=pwc.subreddit_name,
            pain_statement=pain_stmt,
            raw_quote=raw_quote[:500],
            mentioned_tools=mentioned,
            is_workaround=is_workaround,
            source_author=source_author,
            source_created=source_created,
            signal_event_id=signal_event_id,
        ))

    return objects


# ─── Single-post extraction with retry ───────────────────────────────────────

class _Retry(Exception):
    """Transient bad response: retry with backoff."""


def _csv(value: str) -> list[str]:
    return [p.strip() for p in value.split(",") if p.strip()]


def request_options() -> dict:
    """max_tokens and OpenRouter provider routing, from system_settings."""
    try:
        max_tokens = int(runtime_settings.get_setting("EXTRACTION_MAX_TOKENS", str(DEFAULT_MAX_TOKENS)))
    except ValueError:
        max_tokens = DEFAULT_MAX_TOKENS
    options: dict = {"max_tokens": max(256, min(max_tokens, MAX_TOKENS_CEILING))}
    extra: dict = {}
    order = _csv(runtime_settings.get_setting("OPENROUTER_PROVIDER_ORDER", ""))
    only = _csv(runtime_settings.get_setting("OPENROUTER_PROVIDER_ONLY", ""))
    max_price = _csv(runtime_settings.get_setting("OPENROUTER_MAX_PRICE", ""))
    provider: dict = {}
    if order and not only:  # an order would make OpenRouter try its first provider instead of spreading load
        provider["order"] = order
        provider["allow_fallbacks"] = (
            runtime_settings.get_setting("OPENROUTER_ALLOW_FALLBACKS", "true").strip().lower() != "false")
    if only:  # OpenRouter load-balances across these, which raises throughput beyond one provider's limits
        provider["only"] = only
    if len(max_price) == 2:  # USD per million tokens: "prompt,completion"
        provider["max_price"] = {"prompt": float(max_price[0]), "completion": float(max_price[1])}
    if provider:
        extra["provider"] = provider
    # Reasoning models are evaluated at effort "low"; at its default level gpt-oss-120b took ~60 s per
    # call and cost about 3x as much, with no better extractions.
    effort = runtime_settings.get_setting("EXTRACTION_REASONING_EFFORT", DEFAULT_REASONING_EFFORT).strip()
    if effort:
        extra["reasoning"] = {"effort": effort}
    if extra:
        options["extra_body"] = extra
    return options


def extract_for_post(pwc: PostWithComments, max_retries: int = 3) -> List[PainObject]:
    """
    Call the LLM for one post. Retries with exponential backoff.
    Thread-safe (each call creates its own request).
    Raises ExtractionError if all retries fail, UnusableOutput if the answer cannot be used.
    """
    client = get_client()
    prompt = build_prompt(pwc)
    options = request_options()
    max_tokens = options.pop("max_tokens")

    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=get_openrouter_model(),
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=max_tokens,
                **options,
            )

            # Guard against malformed responses (response.choices is None): retry.
            if not response.choices:
                raise _Retry(f"empty choices on post {pwc.post_reddit_id}")

            choice = response.choices[0]
            if getattr(choice, "finish_reason", None) == "length":
                if max_tokens < MAX_TOKENS_CEILING:
                    max_tokens = min(max_tokens * 2, MAX_TOKENS_CEILING)
                    logger.info("Output cut off for post %s; retrying with max_tokens=%d",
                                pwc.post_reddit_id, max_tokens)
                    continue
                raise UnusableOutput(f"output cut off at max_tokens={max_tokens}")

            content = choice.message.content or ""
            if not content.strip():
                # Seen under high concurrency (60 parallel calls): the provider answers with no text at all.
                # That says nothing about the post, so retry instead of recording an unusable answer.
                raise _Retry(f"empty content on post {pwc.post_reddit_id} "
                             f"(provider {getattr(response, 'provider', None) or 'unknown'})")
            return parse_pain_json(content, pwc)

        except _Retry as exc:
            if attempt == max_retries:
                raise ExtractionError(f"{exc} (after {max_retries} attempts)")
            logger.info("%s; retrying (attempt %d/%d)", exc, attempt, max_retries)
            time.sleep(2 ** attempt)

        except RateLimitError:
            wait = 2 ** attempt
            logger.warning("Rate limited on post %s (attempt %d/%d), sleeping %ds",
                           pwc.post_reddit_id, attempt, max_retries, wait)
            time.sleep(wait)

        except APIStatusError as exc:
            if exc.status_code in (500, 502, 503, 504):
                wait = 2 ** attempt
                logger.warning("API error %d on post %s (attempt %d/%d), sleeping %ds",
                               exc.status_code, pwc.post_reddit_id, attempt, max_retries, wait)
                time.sleep(wait)
            else:
                logger.error("Non-retryable API error on post %s: %s",
                             pwc.post_reddit_id, exc)
                break

        except ExtractionError:
            raise  # unusable output: the same request would give the same answer

        except Exception as exc:
            logger.warning("Unexpected error on post %s (attempt %d/%d): %s",
                           pwc.post_reddit_id, attempt, max_retries, exc)
            if attempt == max_retries:
                raise ExtractionError(
                    f"All retries exhausted for post {pwc.post_reddit_id}: {exc}"
                )
            time.sleep(2 ** attempt)

    raise ExtractionError(f"All retries exhausted for post {pwc.post_reddit_id}")


# ─── Concurrent batch extraction with incremental save ────────────────────────

def extract_batch_concurrent(
    posts: List[PostWithComments],
    save_fn: Callable[[PostWithComments, List[PainObject]], None],
    max_workers: int = 20,
    progress_every: int = 100,
    fail_fn: Optional[Callable[[PostWithComments, str], None]] = None,
) -> dict:
    """
    Run extraction concurrently with ThreadPoolExecutor.

    save_fn is called after each post completes (thread-safe DB writes); fail_fn, if given, after
    each post that fails, so the failure is recorded instead of the post being picked again at once.
    Returns stats dict: {total, extracted, saved, errors}.
    """
    total = len(posts)
    stats = {"total": total, "extracted": 0, "saved": 0, "errors": 0}
    completed = 0

    def process_one(pwc: PostWithComments):
        """Extract + save for one post. Returns (post_db_id, pain_count)."""
        try:
            pains = extract_for_post(pwc)
            save_fn(pwc, pains)
            return (pwc.post_db_id, len(pains), None)
        except Exception as exc:
            if fail_fn is not None:
                try:
                    fail_fn(pwc, f"{type(exc).__name__}: {exc}")
                except Exception as record_exc:  # noqa: BLE001
                    logger.warning("could not record failure for post %s: %s", pwc.post_reddit_id, record_exc)
            return (pwc.post_db_id, 0, str(exc))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_one, pwc): pwc for pwc in posts}

        for future in as_completed(futures):
            completed += 1
            post_db_id, pain_count, error = future.result()

            if error:
                stats["errors"] += 1
                logger.warning("Error processing post_id=%d: %s", post_db_id, error)
            else:
                stats["extracted"] += pain_count
                stats["saved"] += pain_count

            if completed % progress_every == 0 or completed == total:
                print(f"  [{completed}/{total}] "
                      f"{stats['extracted']} pains extracted, "
                      f"{stats['errors']} errors")

    return stats
