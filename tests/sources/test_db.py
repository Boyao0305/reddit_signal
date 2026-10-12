"""Smoke tests for reddit_signal.sources.db: small-batch upsert + read round-trip."""
from datetime import datetime, timezone

import psycopg2
import pytest

from reddit_signal.sources import db as sdb
from reddit_signal.sources.base import SignalEvent

@pytest.fixture
def cleanup_test_events(write_dsn, configured_write_db):
    """Remove any test events before and after. Writes, so only runs against a disposable DB."""
    for _ in range(2):
        conn = psycopg2.connect(write_dsn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM signal_events WHERE source='_test_'")
        conn.commit()
        conn.close()
        yield
        return


def _make_event(rid: str, table: str = "posts", parent: int | None = None,
                event_type: str = "post"):
    return SignalEvent(
        source="_test_",
        source_table=table,
        source_row_id=rid,
        event_type=event_type,
        event_time=datetime(2026, 5, 1, 12, tzinfo=timezone.utc),
        surface="r/_test_",
        author="alice",
        title=f"title {rid}" if event_type == "post" else None,
        text=f"body {rid}",
        engagement_score=1.5,
        parent_event_id=parent,
        raw_meta={"k": rid},
    )


def test_upsert_and_read_back(cleanup_test_events):
    parents = [_make_event(f"p{i}") for i in range(3)]
    n = sdb.upsert_events(parents)
    assert n == 3
    assert all(p.id is not None for p in parents)

    # Re-upsert (idempotent)
    sdb.upsert_events(parents)
    assert sdb.event_count("_test_") == 3

    # Children with parent FK
    p0_id = parents[0].id
    children = [
        _make_event(f"c{i}", table="comments", parent=p0_id, event_type="comment")
        for i in range(2)
    ]
    sdb.upsert_events(children)
    assert sdb.event_count("_test_") == 5

    # Bundle read
    bundles = sdb.get_event_bundles_batch(
        "_test_", prompt_version="v1", limit=10, only_unprocessed=False,
    )
    assert len(bundles) == 3
    p0_bundle = next(b for b in bundles if b.parent.id == p0_id)
    assert len(p0_bundle.children) == 2


def test_lookup_by_source(cleanup_test_events):
    ev = _make_event("xyz")
    sdb.upsert_events([ev])
    found = sdb.lookup_event_by_source("_test_", "posts", "xyz")
    assert found == ev.id


def test_build_source_id_map(cleanup_test_events):
    evs = [_make_event(f"m{i}") for i in range(5)]
    sdb.upsert_events(evs)
    m = sdb.build_source_id_map("_test_", "posts")
    assert len(m) >= 5
    for ev in evs:
        assert m[ev.source_row_id] == ev.id


def test_upsert_events_fast(cleanup_test_events):
    evs = [_make_event(f"f{i}") for i in range(20)]
    n = sdb.upsert_events_fast(evs)
    assert n == 20
    assert sdb.event_count("_test_") == 20


def test_get_event_bundles_excludes_processed_signal_events(monkeypatch):
    executed = []

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, params=None):
            executed.append(sql)

        def fetchall(self):
            return []

    class _Conn:
        def cursor(self, *_, **__):
            return _Cursor()

        def commit(self):
            pass

        def rollback(self):
            pass

    monkeypatch.setattr(sdb, "get_conn", lambda: _Conn())
    monkeypatch.setattr(sdb, "put_conn", lambda _conn: None)

    sdb.get_event_bundles_batch("_test_", only_unprocessed=True)

    assert any("processed_signal_events" in sql for sql in executed)
    assert not any("processed_posts" in sql for sql in executed)
