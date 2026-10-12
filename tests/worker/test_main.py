"""Worker mechanics: child supervision, job status mapping, stale-job recovery."""
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

from reddit_signal.worker import main as worker

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def py(code):
    return [sys.executable, "-c", code]


# --- supervise: real child processes ---------------------------------------------------------------

def test_supervise_streams_output_and_returns_exit_code():
    code, cancelled, tail = worker.supervise(py("print('hello'); print('world'); raise SystemExit(3)"),
                                             should_cancel=lambda: False)
    assert (code, cancelled) == (3, False)
    assert tail.splitlines() == ["hello", "world"]


def test_supervise_cancels_a_running_child():
    started = time.monotonic()
    calls = {"n": 0}

    def cancel_after_a_moment():
        calls["n"] += 1
        return calls["n"] > 2

    code, cancelled, _ = worker.supervise(py("import time; time.sleep(60)"), should_cancel=cancel_after_a_moment)
    assert cancelled
    assert code != 0
    assert time.monotonic() - started < 15


def test_supervise_ticks_while_the_child_runs(monkeypatch):
    monkeypatch.setattr(worker, "HEARTBEAT_SECONDS", 0.2)
    ticks = []
    worker.supervise(py("import time; time.sleep(1.5)"), should_cancel=lambda: False,
                     on_tick=lambda: ticks.append(1))
    assert len(ticks) >= 3   # heartbeats keep flowing during long jobs (the old worker went silent)


def test_supervise_keeps_only_the_tail(monkeypatch):
    monkeypatch.setattr(worker, "LOG_TAIL_CHARS", 50)
    _, _, tail = worker.supervise(py("for i in range(200): print('line', i)"), should_cancel=lambda: False)
    assert len(tail) <= 60 and tail.rstrip().endswith("line 199")


# --- execute_job: status mapping ----------------------------------------------------------------------

class FakeQueue:
    def __init__(self):
        self.finished, self.events, self.cancelled_steps = None, [], []

    def add_job_event(self, job_id, *, message, event_type="log", **_):
        self.events.append((event_type, message))

    def finish_job(self, job_id, *, status, exit_code, error, log_tail):
        self.finished = (status, exit_code, error)

    def cancel_running_steps(self, job_id, *, error=None, running_status="cancelled"):
        self.cancelled_steps.append(error)

    def is_cancel_requested(self, job_id):
        return False

    def heartbeat(self, *a, **k):
        pass


class Job:
    id, job_type, params = 108, "daily", {}


@pytest.mark.parametrize(
    ("supervised", "expected_status", "error_fragment"),
    [
        ((0, False, "done\n"), "succeeded", None),
        ((1, False, "stage 3 failed\nRuntimeError: extract exploded\n"), "failed", "RuntimeError: extract exploded"),
        ((-15, True, ""), "cancelled", "daily cancelled"),
    ],
)
def test_execute_job_maps_outcome_to_status(monkeypatch, supervised, expected_status, error_fragment):
    queue = FakeQueue()
    for name in ("add_job_event", "finish_job", "cancel_running_steps", "is_cancel_requested", "heartbeat"):
        monkeypatch.setattr(worker.pipeline_jobs, name, getattr(queue, name))
    monkeypatch.setattr(worker, "supervise", lambda *a, **k: supervised)

    ok = worker.execute_job(Job(), worker_id="w1", mode="worker:pipeline")

    status, _, error = queue.finished
    assert status == expected_status and ok is (expected_status == "succeeded")
    if error_fragment is None:
        assert error is None
    else:
        assert error_fragment in error


# --- recover_stale_jobs --------------------------------------------------------------------------------

def test_recovery_fails_only_jobs_whose_worker_is_gone(monkeypatch):
    running = [
        {"id": 108, "worker_id": "dead", "started_at": NOW - timedelta(hours=30)},
        {"id": 109, "worker_id": "alive", "started_at": NOW - timedelta(hours=1)},
    ]
    heartbeats = [
        {"worker_id": "dead", "last_seen_at": NOW - timedelta(hours=30), "current_job_id": 108},
        {"worker_id": "alive", "last_seen_at": NOW - timedelta(seconds=5), "current_job_id": 109},
    ]
    failed = []
    from reddit_signal import maintenance

    monkeypatch.setattr(maintenance.pipeline_jobs, "running_jobs", lambda types: running)
    monkeypatch.setattr(maintenance.pipeline_jobs, "all_heartbeats", lambda: heartbeats)
    monkeypatch.setattr(maintenance.pipeline_jobs, "fail_stale_job", lambda job_id, *, reason: failed.append(job_id) or True)

    assert maintenance.recover_stale_jobs(("daily", "monthly"), now=NOW) == [108]
    assert failed == [108]


def test_lanes():
    assert worker.LANE_JOB_TYPES["pipeline"] == ("daily", "monthly")
    assert worker.LANE_JOB_TYPES["extraction"] == ("extraction",)
    assert worker.lane_runs("pipeline", "scheduler") and not worker.lane_runs("extraction", "scheduler")
    assert worker.lane_runs("source-backfill", "source_backfill")
    assert worker.lane_runs("all", "periodic_snapshot")
