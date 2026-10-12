"""Unit tests for reddit_signal.sources.registry — register, get, list, discover."""
from datetime import datetime, timezone

import pytest

from reddit_signal.sources import registry as reg_module
from reddit_signal.sources.base import AdapterCapabilities, BaseAdapter, EventBundle, SignalEvent
from reddit_signal.sources.registry import (
    discover_adapters,
    get_adapter,
    list_adapters,
    register_adapter,
)


@pytest.fixture(autouse=True)
def _isolate_registry(monkeypatch):
    """Each test gets a clean copy of the registry."""
    snapshot = dict(reg_module._REGISTRY)
    yield
    reg_module._REGISTRY.clear()
    reg_module._REGISTRY.update(snapshot)


class _Dummy(BaseAdapter):
    name = "_dummy_test"
    capabilities = AdapterCapabilities(produces_pain=True)

    def fetch(self, **kw): return 0
    def project(self, **kw): return 0
    def build_pain_prompt(self, bundle: EventBundle): return "dummy"
    def community_score(self, event: SignalEvent): return 0.0


def test_register_and_get():
    register_adapter(_Dummy)
    assert "_dummy_test" in list_adapters()
    a = get_adapter("_dummy_test")
    assert isinstance(a, _Dummy)


def test_double_register_fails():
    register_adapter(_Dummy)
    with pytest.raises(ValueError, match="already registered"):
        register_adapter(_Dummy)


def test_get_unknown_raises():
    with pytest.raises(KeyError):
        get_adapter("nonexistent")


def test_register_without_name_fails():
    class NoName(BaseAdapter):
        name = ""
        def fetch(self, **kw): return 0
        def project(self, **kw): return 0
        def build_pain_prompt(self, b): return ""
        def community_score(self, e): return 0.0

    with pytest.raises(ValueError, match="missing 'name'"):
        register_adapter(NoName)


def test_discover_finds_reddit():
    """The reddit_adapter module must self-register on discovery."""
    reg_module._REGISTRY.clear()
    discover_adapters()
    assert "reddit" in list_adapters()


def test_discover_finds_hackernews_adapter():
    """Hacker News is the first real non-Reddit source adapter."""
    reg_module._REGISTRY.clear()
    discover_adapters()
    assert "hackernews" in list_adapters()


def test_discover_finds_exactly_the_supported_adapters():
    reg_module._REGISTRY.clear()
    discover_adapters()
    names = set(list_adapters())

    assert names == {"hackernews", "reddit"}


def test_get_adapter_loads_adapters_when_nothing_imported_them_yet():
    # Source backfill looked adapters up in a fresh worker process: "Unknown adapter: 'reddit'. Registered: []".
    reg_module._REGISTRY.clear()
    assert get_adapter("reddit").name == "reddit"
