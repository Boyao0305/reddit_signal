"""LLM-backed semantic keyword expansion for Dashboard cluster search."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from openai import OpenAI

from reddit_signal import runtime_settings


log = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MIN_QUERY_CHARS = 3
MAX_EXTRA_TERMS = 6
MAX_TERM_CHARS = 64
REQUEST_TIMEOUT_SECONDS = 8.0
EXPANSION_CACHE_TTL_SECONDS = 24 * 60 * 60
EXPANSION_CACHE_MAX_ITEMS = 512

_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_search_expansion_cache: dict[tuple[str, str], tuple[float, tuple[str, ...]]] = {}


def clear_search_expansion_cache() -> None:
    _search_expansion_cache.clear()


def _semantic_search_enabled() -> bool:
    raw = runtime_settings.get_setting("DASHBOARD_SEMANTIC_SEARCH_ENABLED", "true")
    return raw.strip().lower() not in _FALSE_VALUES


def _normalize_query(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _sanitize_term(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    term = _normalize_query(value)
    term = term.strip(" \t\r\n\"'`")
    if not term:
        return ""
    return term[:MAX_TERM_CHARS]


def _dedupe_terms(original: str, extras: list[Any]) -> tuple[str, ...]:
    terms = [original]
    seen = {original.lower()}
    for raw in extras:
        term = _sanitize_term(raw)
        if not term:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
        if len(terms) >= MAX_EXTRA_TERMS + 1:
            break
    return tuple(terms)


def _cache_get(key: tuple[str, str]) -> tuple[str, ...] | None:
    cached = _search_expansion_cache.get(key)
    if cached and time.time() - cached[0] < EXPANSION_CACHE_TTL_SECONDS:
        return cached[1]
    if cached:
        _search_expansion_cache.pop(key, None)
    return None


def _cache_set(key: tuple[str, str], extras: tuple[str, ...]) -> tuple[str, ...]:
    if key not in _search_expansion_cache and len(_search_expansion_cache) >= EXPANSION_CACHE_MAX_ITEMS:
        oldest_key = min(_search_expansion_cache, key=lambda k: _search_expansion_cache[k][0])
        _search_expansion_cache.pop(oldest_key, None)
    _search_expansion_cache[key] = (time.time(), extras)
    return extras


def _extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass

    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence_match:
        return _extract_json_object(fence_match.group(1))

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        parsed = json.loads(text[start:end + 1])
        return parsed if isinstance(parsed, dict) else {}
    raise json.JSONDecodeError("could not extract JSON object", text, 0)


def _request_expansion(query: str, model: str, api_key: str) -> str:
    client = OpenAI(
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=0,
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You expand product-discovery dashboard search queries into concise "
                    "semantic keyword variants. Return only JSON."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Generate up to 6 short English search terms that are semantically "
                    "similar to this query. Include synonyms, adjacent product-category "
                    "phrases, and user problem wording. Do not include explanations.\n\n"
                    f"Query: {query!r}\n\n"
                    'Return exactly: {"terms": ["term one", "term two"]}'
                ),
            },
        ],
        temperature=0.0,
        response_format={"type": "json_object"},
        max_tokens=300,
    )
    msg = resp.choices[0].message
    text = (msg.content or "").strip()
    if text:
        return text
    reasoning = getattr(msg, "reasoning", None) or ""
    return reasoning.strip()


def expand_search_terms(query: str) -> tuple[str, ...]:
    """Return original query plus cached LLM-generated semantic keyword variants.

    Any configuration, network, or parsing failure falls back to the original
    keyword so cluster listing remains available even when the LLM path is down.
    """
    original = _sanitize_term(query)
    if not original:
        return ()
    if len(original) < MIN_QUERY_CHARS or not _semantic_search_enabled():
        return (original,)

    model = runtime_settings.get_setting("OPENROUTER_MODEL").strip()
    api_key = runtime_settings.get_setting("OPENROUTER_API_KEY").strip()
    if not model or not api_key:
        return (original,)

    cache_key = (model, original.lower())
    cached_extras = _cache_get(cache_key)
    if cached_extras is not None:
        return _dedupe_terms(original, list(cached_extras))

    try:
        raw = _request_expansion(original, model, api_key)
        parsed = _extract_json_object(raw)
        extras = tuple(_dedupe_terms(original, parsed.get("terms", []))[1:])
        _cache_set(cache_key, extras)
        return _dedupe_terms(original, list(extras))
    except Exception as exc:  # pragma: no cover - exact SDK errors vary by provider
        log.warning(
            "Semantic search expansion failed for query_len=%d model=%s: %s",
            len(original),
            model,
            exc,
        )
        return (original,)
