import pytest

from reddit_signal.sources import daily
from reddit_signal.sources.runtime_config import SourceRuntimeConfig


class FakeAdapter:
    def __init__(self, name, *, fetch_error=None, project_error=None, rows=5):
        self.name, self.fetch_error, self.project_error, self.rows = name, fetch_error, project_error, rows
        self.fetched = self.projected = 0

    def fetch(self, *, query, since, until, limit):
        if self.fetch_error:
            raise self.fetch_error
        self.fetched += 1
        return self.rows

    def project(self, *, since, until):
        if self.project_error:
            raise self.project_error
        self.projected += 1
        return self.rows


@pytest.fixture
def sources(monkeypatch):
    adapters = {
        "reddit": FakeAdapter("reddit", fetch_error=RuntimeError("422 Unprocessable Entity")),
        "hackernews": FakeAdapter("hackernews", rows=7),
    }
    configs = [
        SourceRuntimeConfig(source="reddit", enabled=True, fetch_enabled=True, project_enabled=True),
        SourceRuntimeConfig(source="hackernews", enabled=True, fetch_enabled=True, project_enabled=True),
    ]
    monkeypatch.setattr(daily, "discover_adapters", lambda: None)
    monkeypatch.setattr(daily, "list_source_configs", lambda: configs)
    monkeypatch.setattr(daily, "get_adapter", adapters.__getitem__)
    return adapters


def test_a_failing_fetch_is_reported_and_the_next_source_still_runs(sources):
    notes = []
    result = daily.fetch_enabled_sources(note=lambda msg, **kw: notes.append((kw.get("event_type"), msg)))
    assert result == {"fetched": 7, "failed": ["reddit"], "sources": 2}
    assert sources["hackernews"].fetched == 1
    assert any(kind == "warning" and "reddit: fetch failed" in msg for kind, msg in notes)


def test_a_failing_projection_stops_the_phase(sources):
    sources["hackernews"].project_error = RuntimeError("disk full")
    with pytest.raises(RuntimeError, match="disk full"):
        daily.project_enabled_sources()


def test_nothing_enabled_is_not_an_error(monkeypatch):
    monkeypatch.setattr(daily, "discover_adapters", lambda: None)
    monkeypatch.setattr(daily, "list_source_configs", lambda: [])
    assert daily.fetch_enabled_sources() == {"fetched": 0, "failed": [], "sources": 0}
    assert daily.project_enabled_sources() == {"projected": 0, "sources": 0}
