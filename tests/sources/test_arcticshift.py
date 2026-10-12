import threading
import time

import pytest

from reddit_signal.data_ingestion import arcticshift
from reddit_signal.data_ingestion.arcticshift import retry_delay


@pytest.mark.parametrize(
    ("status", "headers", "attempt", "expected"),
    [
        # Throttling (422 is how Arctic Shift answers bursts): wait for the advertised reset.
        (422, {"x-ratelimit-reset": "41"}, 0, 41.0),
        (422, {"x-ratelimit-reset": "0"}, 0, 1.0),          # at least a second
        (422, {"x-ratelimit-reset": "600"}, 0, 60.0),       # capped
        (429, {"retry-after": "5"}, 0, 5.0),
        (422, {}, 0, 1.0),                                  # no header: exponential backoff
        (422, {}, 3, 8.0),
        (422, {"x-ratelimit-reset": "soon"}, 2, 4.0),       # unparsable header: backoff
        # Server errors: backoff.
        (503, {}, 1, 2.0),
        # Validation errors (400 with a message) and other client errors: do not retry.
        (400, {}, 0, None),
        (404, {}, 0, None),
        (200, {}, 0, None),
    ],
)
def test_retry_delay(status, headers, attempt, expected):
    assert retry_delay(status, headers, attempt) == expected


def test_rate_limiter_spaces_requests_across_threads():
    limiter = arcticshift._RateLimiter()
    limiter._rps = 20.0                      # 50 ms apart
    starts = []
    lock = threading.Lock()

    def worker():
        for _ in range(5):
            limiter.wait()
            with lock:
                starts.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    starts.sort()
    assert len(starts) == 20
    # 20 requests at 20/s cannot start within less than 19 x 50 ms (allowing for clock jitter).
    assert starts[-1] - starts[0] >= 19 * 0.05 * 0.9


def test_get_retries_422_then_succeeds(monkeypatch):
    responses = iter([(422, {"x-ratelimit-reset": "1"}), (422, {}), (200, {})])
    slept = []

    class Resp:
        def __init__(self, status, headers):
            self.status_code, self.headers = status, headers

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": []}

    monkeypatch.setattr(arcticshift.httpx, "get", lambda *a, **k: Resp(*next(responses)))
    monkeypatch.setattr(arcticshift.time, "sleep", slept.append)
    monkeypatch.setattr(arcticshift, "_limiter", type("L", (), {"wait": lambda self: None})())

    assert arcticshift._get("/posts/search", {}) == {"data": []}
    assert slept == [1.0, 2.0]


@pytest.mark.parametrize(
    ("status", "error", "expected"),
    [
        (422, "Timeout. Maybe slow down a bit", True),
        (422, "", False),                                   # plain throttling
        (400, "Timeout", False),
        (200, "", False),
    ],
)
def test_is_query_timeout(status, error, expected):
    assert arcticshift.is_query_timeout(status, error) is expected


def _fake_get(monkeypatch, bodies):
    """Serve (status, json body) pairs in order; return the list of sleeps."""
    slept = []

    class Resp:
        def __init__(self, status, body):
            self.status_code, self.headers, self._body, self.text = status, {"x-ratelimit-reset": "55"}, body, ""

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    responses = iter(bodies)
    monkeypatch.setattr(arcticshift.httpx, "get", lambda *a, **k: Resp(*next(responses)))
    monkeypatch.setattr(arcticshift.time, "sleep", slept.append)
    monkeypatch.setattr(arcticshift, "_limiter", type("L", (), {"wait": lambda self: None})())
    return slept


TIMEOUT = (422, {"data": None, "error": "Timeout. Maybe slow down a bit"})


def test_query_timeout_fails_fast_instead_of_waiting_for_the_rate_limit_reset(monkeypatch):
    slept = _fake_get(monkeypatch, [TIMEOUT] * 6)
    with pytest.raises(arcticshift.QueryTimeout):
        arcticshift._get("/comments/search", {})
    assert slept == [5.0, 10.0]                          # not 55 s per attempt, not six attempts


def test_query_timeout_can_recover(monkeypatch):
    slept = _fake_get(monkeypatch, [TIMEOUT, (200, {"data": []})])
    assert arcticshift._get("/comments/search", {}) == {"data": []}
    assert slept == [5.0]
