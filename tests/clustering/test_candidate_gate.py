from __future__ import annotations

from reddit_signal.clustering import candidate_gate


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return self.rows


class _FakeConn:
    def __init__(self, rows):
        self.cursor_obj = _FakeCursor(rows)
        self.committed = False

    def cursor(self, *args, **kwargs):
        return self.cursor_obj

    def commit(self):
        self.committed = True


def _candidate_row(**overrides):
    row = {
        "id": 101,
        "member_count": 50,
        "coherence": 0.6,
        "prev": False,
        "audience_type": "B2B",
        "aud_conf": 0.8,
        "thread_diversity": 0.8,
        "author_diversity": 0.8,
        "specificity_mean": 0.3,
        "protected_source_pain_count": 0,
        "protected_sources": [],
    }
    row.update(overrides)
    return row


def _capture_execute_values(monkeypatch):
    calls = []

    def fake_execute_values(cur, sql, argslist, **kwargs):
        calls.append((sql, list(argslist), kwargs))
        cur.calls.append((sql, list(argslist)))

    monkeypatch.setattr(candidate_gate.psycopg2.extras, "execute_values", fake_execute_values)
    return calls


def test_default_thresholds_include_b2b_mixed_and_source_protection():
    assert candidate_gate.DEFAULT_THRESHOLDS["audiences"] == ["B2B", "B2C", "mixed"]
    assert "facebook" in candidate_gate.DEFAULT_THRESHOLDS["protected_sources"]
    assert "twitter" in candidate_gate.DEFAULT_THRESHOLDS["protected_sources"]
    assert "youtube" in candidate_gate.DEFAULT_THRESHOLDS["protected_sources"]


def test_compute_candidates_promotes_b2b_candidate(monkeypatch):
    conn = _FakeConn([_candidate_row(id=201, audience_type="B2B", aud_conf=0.7)])
    monkeypatch.setattr(candidate_gate.db, "get_conn", lambda: conn)
    monkeypatch.setattr(candidate_gate.db, "put_conn", lambda c: None)
    execute_values_calls = _capture_execute_values(monkeypatch)

    stats = candidate_gate.compute_candidates(13, candidate_gate.DEFAULT_THRESHOLDS)

    assert stats == {"total": 1, "promoted": 1, "demoted": 0, "unchanged": 0}
    assert conn.committed is True
    assert execute_values_calls[0][1] == [(True, 201)]


def test_compute_candidates_protects_recent_new_source_cluster(monkeypatch):
    row = _candidate_row(
        id=301,
        audience_type="unclear",
        aud_conf=0.4,
        thread_diversity=0.1,
        author_diversity=0.7,
        protected_source_pain_count=2,
        protected_sources=["facebook", "twitter"],
    )
    conn = _FakeConn([row])
    monkeypatch.setattr(candidate_gate.db, "get_conn", lambda: conn)
    monkeypatch.setattr(candidate_gate.db, "put_conn", lambda c: None)
    execute_values_calls = _capture_execute_values(monkeypatch)

    stats = candidate_gate.compute_candidates(13, candidate_gate.DEFAULT_THRESHOLDS)

    assert stats["promoted"] == 1
    insert_args = execute_values_calls[1][1]
    assert len(insert_args) == 1
    metrics_json = insert_args[0][2]
    assert '"protected_source_hit": true' in metrics_json
    assert '"protected_bypass_thread_diversity": true' in metrics_json
    assert '"facebook"' in metrics_json
