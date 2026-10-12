"""Configuration helpers for step4.

Operational LLM settings are read from PostgreSQL-backed system_settings, with
environment/default fallback handled by reddit_signal.runtime_settings. DATABASE_URL is
the only required bootstrap environment variable in production.
"""

import logging

from reddit_signal import runtime_settings

logger = logging.getLogger(__name__)

_api_key: str | None = None


def get_openrouter_api_key() -> str:
    """
    Return the OpenRouter API key. Result is cached module-level.
    """
    global _api_key
    if _api_key is not None:
        return _api_key

    key = runtime_settings.get_setting("OPENROUTER_API_KEY").strip()
    if key:
        _api_key = key
        return _api_key

    _api_key = ""
    return _api_key


def get_openrouter_model() -> str:
    return runtime_settings.get_setting("OPENROUTER_MODEL").strip()


def get_use_signal_events() -> bool:
    return runtime_settings.get_setting("USE_SIGNAL_EVENTS").strip().lower() == "true"
