"""Shared test fixtures.

Database-backed tests never use ``DATABASE_URL`` (that is the production bootstrap variable):

* ``REDDIT_SIGNAL_TEST_DATABASE_URL`` — a disposable database. Required by tests that write.
* ``REDDIT_SIGNAL_READONLY_DATABASE_URL`` — read-only access (e.g. the ``reddit_signal_ro`` role through
  an SSH tunnel). Accepted by tests that only read; the disposable database is used if it is set.

Without them, database tests are skipped.
"""
from __future__ import annotations

import os

import pytest

from reddit_signal import config, db


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


@pytest.fixture(scope="session")
def write_dsn() -> str:
    dsn = _env("REDDIT_SIGNAL_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("set REDDIT_SIGNAL_TEST_DATABASE_URL (a disposable database) to run write tests")
    return dsn


@pytest.fixture(scope="session")
def readonly_dsn() -> str:
    dsn = _env("REDDIT_SIGNAL_TEST_DATABASE_URL") or _env("REDDIT_SIGNAL_READONLY_DATABASE_URL")
    if not dsn:
        pytest.skip("set REDDIT_SIGNAL_READONLY_DATABASE_URL or REDDIT_SIGNAL_TEST_DATABASE_URL")
    return dsn


def _use_database(dsn: str):
    """Point the shared pool at ``dsn`` for the duration of a fixture, then restore."""
    previous = config._current
    db.close_pool()
    config.configure(config.Config(database_url=dsn, db_pool_max=4))
    try:
        yield dsn
    finally:
        db.close_pool()
        if previous is None:
            config.reset()
        else:
            config.configure(previous)


@pytest.fixture(scope="module")
def configured_readonly_db(readonly_dsn):
    yield from _use_database(readonly_dsn)


@pytest.fixture(scope="module")
def configured_write_db(write_dsn):
    yield from _use_database(write_dsn)
