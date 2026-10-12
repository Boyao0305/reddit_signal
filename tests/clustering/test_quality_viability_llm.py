from __future__ import annotations

from reddit_signal.clustering import quality_viability_llm


class _FakeCursor:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return [
            {"id": 21058, "label": "SaaS Retention", "member_count": 372},
        ]


class _FakeConn:
    def __init__(self):
        self.cursor_obj = _FakeCursor()

    def cursor(self, *args, **kwargs):
        return self.cursor_obj


def test_fetch_candidate_clusters_filters_to_cluster_ids(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(quality_viability_llm.db, "get_conn", lambda: conn)
    monkeypatch.setattr(quality_viability_llm.db, "put_conn", lambda c: None)

    rows = quality_viability_llm._fetch_candidate_clusters(
        13,
        6,
        cluster_ids=[20967, 21058],
    )

    sql, params = conn.cursor_obj.calls[0]
    assert "AND pc.id = ANY(%s)" in sql
    assert params == (13, 6, [20967, 21058])
    assert rows == [{"id": 21058, "label": "SaaS Retention", "member_count": 372}]
