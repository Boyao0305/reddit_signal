"""Prompt conversion registry for signal_events sources.

Prompt modules are auto-discovered from ``reddit_signal.pain_extraction.prompts.*``
(excluding this file and ``models``).  Each module must export a callable
``bundle_to_postwithcomments(bundle: EventBundle) -> PostWithComments``.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Callable

from reddit_signal.pain_extraction.models import PostWithComments
from reddit_signal.sources.base import EventBundle

BundleConverter = Callable[[EventBundle], PostWithComments]
logger = logging.getLogger(__name__)

_CONVERTERS: dict[str, BundleConverter] = {}


def register_prompt_source(source: str, converter: BundleConverter) -> None:
    if source in _CONVERTERS:
        raise ValueError(f"prompt source {source!r} already registered")
    _CONVERTERS[source] = converter


def get_prompt_converter(source: str) -> BundleConverter:
    _ensure_builtin_sources()
    try:
        return _CONVERTERS[source]
    except KeyError as exc:
        raise KeyError(
            f"No pain extraction prompt converter for source={source!r}. "
            f"Registered: {sorted(_CONVERTERS)}"
        ) from exc


def list_prompt_sources() -> list[str]:
    _ensure_builtin_sources()
    return sorted(_CONVERTERS)


def _ensure_builtin_sources() -> None:
    """Auto-discover prompt modules in ``reddit_signal.pain_extraction.prompts.*``.

    Each module whose name is not ``registry`` or ``models`` is imported.
    If it exports a callable ``bundle_to_postwithcomments`` it is registered
    under the module name as the source key.
    """
    import reddit_signal.pain_extraction.prompts as pkg

    for mod_info in pkgutil.iter_modules(pkg.__path__):
        if mod_info.name in ("registry", "models"):
            continue
        if mod_info.name in _CONVERTERS:
            continue
        try:
            mod = importlib.import_module(
                f"reddit_signal.pain_extraction.prompts.{mod_info.name}"
            )
            conv = getattr(mod, "bundle_to_postwithcomments", None)
            if callable(conv):
                register_prompt_source(mod_info.name, conv)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to load prompt module %s: %s", mod_info.name, exc
            )
