from pathlib import Path

import pytest

from reddit_signal.migrate import load_migrations, wraps_in_transaction

REPO_MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"


@pytest.mark.parametrize(
    ("sql", "wrap"),
    [
        ("CREATE TABLE t (id int);", True),
        ("ALTER TABLE t ADD COLUMN x int;\nUPDATE t SET x = 1;", True),
        # Files that manage their own transaction are run as written.
        ("BEGIN;\nCREATE TABLE t (id int);\nCOMMIT;", False),
        ("begin;\nselect 1;\ncommit;", False),
        # CONCURRENTLY cannot run inside a transaction block.
        ("CREATE INDEX CONCURRENTLY ix ON t (id);", False),
        # Explicit opt-out.
        ("-- migrate: no-transaction\nVACUUM t;", False),
        # The words inside comments or identifiers do not count.
        ("-- we used to BEGIN; here\nCREATE TABLE t (id int);", True),
        ("CREATE TABLE begin_log (id int);", True),
    ],
)
def test_wraps_in_transaction(sql, wrap):
    assert wraps_in_transaction(sql) is wrap


def test_real_migration_files():
    by_name = {m.filename: m for m in load_migrations(REPO_MIGRATIONS)}
    assert len(by_name) >= 51
    assert not by_name["v2_001_signal_events.sql"].wrap_in_transaction            # has BEGIN/COMMIT
    assert not by_name["v2_019_dashboard_read_indexes.sql"].wrap_in_transaction   # CONCURRENTLY
    assert not by_name["v2_047_signal_events_recent_parents_index.sql"].wrap_in_transaction
    assert not by_name["v2_048_comments_created_utc_index.sql"].wrap_in_transaction
    assert by_name["v2_046_source_backfill_fetch_only_default.sql"].wrap_in_transaction


def test_checksums_are_line_ending_sensitive_so_files_must_stay_lf():
    # The production ledger stores SHA-256 of the LF files (verified identical on 2026-10-10).
    for m in load_migrations(REPO_MIGRATIONS):
        assert b"\r\n" not in m.path.read_bytes(), f"{m.filename} has CRLF line endings"


def test_snapshot_header_names_the_last_included_migration():
    from reddit_signal.migrate import snapshot_through

    assert snapshot_through("-- x\n-- schema snapshot through: v2_048_a.sql\nCREATE TABLE t();") == "v2_048_a.sql"
    with pytest.raises(SystemExit):
        snapshot_through("CREATE TABLE t();")


def test_committed_snapshot_matches_a_real_migration():
    from reddit_signal.migrate import snapshot_through

    through = snapshot_through((REPO_MIGRATIONS.parent / "schema.sql").read_text(encoding="utf-8"))
    assert through in {m.filename for m in load_migrations(REPO_MIGRATIONS)}
