from __future__ import annotations

from reddit_signal import pipeline_jobs


def test_claim_next_job_filters_job_types_before_worker_param(monkeypatch):
    captured: dict[str, object] = {}

    class _Cursor:
        description = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params

        def fetchone(self):
            return None

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self, cursor_factory=None):
            return _Cursor()

    monkeypatch.setattr(pipeline_jobs, "connect", lambda: _Conn())

    assert pipeline_jobs.claim_next_job("worker-1", job_types=("daily", "monthly")) is None

    assert "job_type = ANY(%s)" in str(captured["sql"])
    assert captured["params"] == (["daily", "monthly"], "worker-1")
