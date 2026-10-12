"""
Adapter registry + auto-discovery.

Adapters call ``@register_adapter`` at module-import time.
``discover_adapters()`` imports every ``reddit_signal.sources.<name>_adapter`` to trigger
their registration.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from dataclasses import replace
from typing import List, Type

from reddit_signal.sources.base import AdapterMeta, BaseAdapter

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, Type[BaseAdapter]] = {}

SUPPORTED_ADAPTER_MODULES = frozenset({
    "hackernews_adapter",
    "reddit_adapter",
})


def register_adapter(cls: Type[BaseAdapter]) -> Type[BaseAdapter]:
    """Class decorator: registers the adapter under ``cls.name``."""
    if not getattr(cls, "name", None):
        raise ValueError(f"{cls.__name__} missing 'name' attribute")
    if cls.name in _REGISTRY:
        raise ValueError(
            f"Adapter '{cls.name}' already registered "
            f"(existing: {_REGISTRY[cls.name].__name__}, new: {cls.__name__})"
        )
    _REGISTRY[cls.name] = cls
    logger.info("Registered adapter: %s (%s)", cls.name, cls.__name__)
    return cls


def get_adapter(name: str) -> BaseAdapter:
    """Return an instance of the named adapter."""
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown adapter: {name!r}. Registered: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]()


def list_adapters() -> list[str]:
    return sorted(_REGISTRY)


def list_adapter_infos() -> List[AdapterMeta]:
    """Return structured metadata for all registered adapters.

    Includes the *prompt_registered* flag by cross-referencing the prompt
    registry so the UI can surface whether a source is fully wired up.

    The prompt-registry import is optional — when ``reddit_signal.pain_extraction`` is
    not on the PYTHONPATH (e.g. dashboard-only deployments) the flag defaults
    to False.
    """
    discover_adapters()

    prompt_sources: set[str] = set()
    try:
        from reddit_signal.pain_extraction.prompts.registry import list_prompt_sources
        prompt_sources = set(list_prompt_sources())
    except ModuleNotFoundError:
        pass

    infos: List[AdapterMeta] = []
    for name in sorted(_REGISTRY):
        meta = _REGISTRY[name].metadata()
        infos.append(replace(meta, prompt_registered=name in prompt_sources))
    return infos


def discover_adapters() -> None:
    """Import every reddit_signal.sources.*_adapter module to trigger registration."""
    import reddit_signal.sources as pkg
    for module_info in pkgutil.iter_modules(pkg.__path__):
        if module_info.name.endswith("_adapter"):
            if module_info.name not in SUPPORTED_ADAPTER_MODULES:
                continue
            mod_name = f"reddit_signal.sources.{module_info.name}"
            try:
                module = importlib.import_module(mod_name)
                # Tests and long-lived processes may clear/reload the registry
                # after a module has already been imported. Re-register adapter
                # classes found in the module so discovery remains idempotent.
                for obj in vars(module).values():
                    if (
                        isinstance(obj, type)
                        and issubclass(obj, BaseAdapter)
                        and obj is not BaseAdapter
                        and getattr(obj, "name", None)
                        and obj.name not in _REGISTRY
                    ):
                        register_adapter(obj)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to import %s: %s", mod_name, exc)
