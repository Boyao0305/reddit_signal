"""The shared pool must block (not raise) when exhausted, and give slots back on every path."""
import threading
import time

import pytest

from reddit_signal import config, db


class FakePool:
    def __init__(self, minconn, maxconn, dsn):
        self.maxconn = maxconn
        self.out = 0
        self.max_out = 0
        self.lock = threading.Lock()

    def getconn(self):
        with self.lock:
            if self.out >= self.maxconn:  # what psycopg2 does: raise PoolError
                raise RuntimeError("connection pool exhausted")
            self.out += 1
            self.max_out = max(self.max_out, self.out)
        return object()

    def putconn(self, conn):
        with self.lock:
            self.out -= 1

    def closeall(self):
        pass


@pytest.fixture
def fake_pool(monkeypatch):
    monkeypatch.setattr(db, "ThreadedConnectionPool", FakePool)
    db.close_pool()
    config.configure(config.Config(database_url="postgresql://u:p@h/db", db_pool_max=2))
    yield
    db.close_pool()
    config.reset()


def test_more_threads_than_connections_wait_instead_of_failing(fake_pool):
    errors = []

    def work():
        try:
            conn = db.get_conn()
            time.sleep(0.02)
            db.put_conn(conn)
        except Exception as exc:  # pragma: no cover - the assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert db.get_pool().max_out <= 2


def test_slot_is_released_when_getconn_fails(fake_pool, monkeypatch):
    pool = db.get_pool()
    monkeypatch.setattr(pool, "getconn", lambda: (_ for _ in ()).throw(OSError("db down")))
    for _ in range(3):  # more attempts than slots: would deadlock if slots leaked
        with pytest.raises(OSError):
            db.get_conn()


def test_missing_database_url_is_reported_before_connecting(monkeypatch):
    monkeypatch.setattr(db, "ThreadedConnectionPool", FakePool)
    db.close_pool()
    config.configure(config.Config())
    try:
        with pytest.raises(config.ConfigError):
            db.get_conn()
    finally:
        config.reset()
