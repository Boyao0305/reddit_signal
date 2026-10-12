"""SQL migration runner: plan, apply, baseline, sync.

Migrations are append-only files ``db/migrations/v2_NNN_*.sql``. The ledger table ``schema_migrations``
records each applied file with a SHA-256 of its bytes; editing an applied file is reported as drift and
stops the run.

Each file is applied by ``psql`` inside a single transaction, so a failing file leaves no partial
schema behind — unless the file manages its own transaction (``BEGIN``/``COMMIT``), uses
``CONCURRENTLY`` (which Postgres forbids inside a transaction), or carries the marker
``-- migrate: no-transaction``.

New installations: on an empty database with an empty ledger, ``apply`` loads ``db/schema.sql`` (a schema
snapshot) and ``db/seed.sql``, records every migration up to the one named in the snapshot header as
applied, then applies newer migrations as usual. The early migrations assume tables that only the first
version of the project created, so they cannot build a database from nothing.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from reddit_signal import config, db

VAR_RE = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")
_OWN_TXN_RE = re.compile(r"^\s*(BEGIN|COMMIT|ROLLBACK|START\s+TRANSACTION)\b", re.IGNORECASE | re.MULTILINE)
_CONCURRENTLY_RE = re.compile(r"\bCONCURRENTLY\b", re.IGNORECASE)
_NO_TXN_MARKER = re.compile(r"^\s*--\s*migrate:\s*no-transaction\s*$", re.IGNORECASE | re.MULTILINE)
DEFAULT_BOOTSTRAP_THROUGH = "v2_022_pipeline_jobs.sql"
_SNAPSHOT_RE = re.compile(r"^--\s*schema snapshot through:\s*(\S+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Migration:
    path: Path
    filename: str
    checksum: str
    required_vars: tuple[str, ...]
    wrap_in_transaction: bool


def wraps_in_transaction(sql: str) -> bool:
    """Whether the runner should wrap this file in one transaction (pure; see module docstring)."""
    return not (
        _NO_TXN_MARKER.search(sql) or _OWN_TXN_RE.search(_strip_comments(sql)) or _CONCURRENTLY_RE.search(sql)
    )


def _strip_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def migrations_dir() -> Path:
    override = os.environ.get("REDDIT_SIGNAL_MIGRATIONS_DIR", "").strip()
    return Path(override) if override else Path.cwd() / "db" / "migrations"


def load_migrations(directory: Path) -> list[Migration]:
    if not directory.is_dir():
        raise SystemExit(f"migrations directory not found: {directory}")
    out = []
    for path in sorted(directory.glob("v2_*.sql")):
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        out.append(
            Migration(
                path=path,
                filename=path.name,
                checksum=hashlib.sha256(raw).hexdigest(),
                required_vars=tuple(sorted(set(VAR_RE.findall(_strip_comments(text))))),
                wrap_in_transaction=wraps_in_transaction(text),
            )
        )
    return out


def snapshot_through(sql: str) -> str:
    """The last migration contained in a schema snapshot, from its header (pure)."""
    match = _SNAPSHOT_RE.search(sql)
    if not match:
        raise SystemExit("schema snapshot has no '-- schema snapshot through: <migration>' header")
    return match.group(1)


def _database_is_empty() -> bool:
    """No tables in the public schema besides the ledger."""
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'schema_migrations'"
        )
        return cur.fetchone()[0] == 0


def _psql_file(path: Path) -> None:
    dsn = config.get().require_database_url()
    result = subprocess.run(["psql", dsn, "-v", "ON_ERROR_STOP=1", "-X", "-q", "--single-transaction", "-f", str(path)],
                            check=False)
    if result.returncode != 0:
        raise SystemExit(f"{path.name} failed (psql exit {result.returncode})")


def install_snapshot(migrations: list[Migration], applied: dict[str, str], db_dir: Path) -> None:
    """Create a new database from db/schema.sql + db/seed.sql and baseline the migrations it contains."""
    schema = db_dir / "schema.sql"
    through = snapshot_through(schema.read_text(encoding="utf-8"))
    print(f"empty database: loading {schema.name} (through {through})", flush=True)
    _psql_file(schema)
    seed = db_dir / "seed.sql"
    if seed.is_file():
        print(f"loading {seed.name}", flush=True)
        _psql_file(seed)
    baseline(migrations, applied, through=through)


def _ensure_table() -> None:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                checksum TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                execution_seconds NUMERIC(12, 3),
                applied_by TEXT NOT NULL DEFAULT CURRENT_USER,
                hostname TEXT
            )
            """
        )


def _load_applied() -> dict[str, str]:
    """The ledger; empty if it does not exist yet (read-only: never creates it)."""
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.schema_migrations') IS NOT NULL")
        if not cur.fetchone()[0]:
            return {}
        cur.execute("SELECT filename, checksum FROM schema_migrations")
        return dict(cur.fetchall())


def _record(migration: Migration, execution_seconds: float | None) -> None:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO schema_migrations (filename, checksum, execution_seconds, hostname)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (filename) DO UPDATE SET
                checksum = EXCLUDED.checksum,
                applied_at = NOW(),
                execution_seconds = EXCLUDED.execution_seconds,
                applied_by = CURRENT_USER,
                hostname = EXCLUDED.hostname
            """,
            (migration.filename, migration.checksum, execution_seconds, socket.gethostname()),
        )


def _check_drift(migration: Migration, applied: dict[str, str]) -> None:
    recorded = applied.get(migration.filename)
    if recorded is not None and recorded != migration.checksum:
        raise SystemExit(f"checksum drift for {migration.filename}; applied migrations must not be edited")


def plan(migrations: list[Migration], applied: dict[str, str]) -> int:
    pending = 0
    for m in migrations:
        recorded = applied.get(m.filename)
        if recorded is None:
            pending += 1
            notes = [] if m.wrap_in_transaction else ["no wrapping transaction"]
            if m.required_vars:
                notes.append("requires vars: " + ", ".join(m.required_vars))
            print(f"PENDING  {m.filename}" + (f"  ({'; '.join(notes)})" if notes else ""))
        elif recorded != m.checksum:
            print(f"DRIFT    {m.filename} recorded checksum differs")
        else:
            print(f"APPLIED  {m.filename}")
    print(f"\nSummary: {pending} pending, {len(applied)} recorded")
    return pending


def apply(migrations: list[Migration], applied: dict[str, str], variables: dict[str, str]) -> None:
    dsn = config.get().require_database_url()
    for m in migrations:
        _check_drift(m, applied)
        if m.filename in applied:
            continue
        missing = [k for k in m.required_vars if k not in variables]
        if missing:
            raise SystemExit(f"{m.filename} requires --var {missing[0]}=... before it can be applied")
        cmd = ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-X", "-q"]
        if m.wrap_in_transaction:
            cmd.append("--single-transaction")
        for key, value in variables.items():
            cmd += ["-v", f"{key}={value}"]
        cmd += ["-f", str(m.path)]
        print(f"apply {m.filename}{'' if m.wrap_in_transaction else ' (no wrapping transaction)'}", flush=True)
        started = time.monotonic()
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            raise SystemExit(f"migration {m.filename} failed (psql exit {result.returncode})")
        elapsed = time.monotonic() - started
        _record(m, elapsed)
        applied[m.filename] = m.checksum
        print(f"recorded {m.filename} ({elapsed:.3f}s)", flush=True)


def baseline(migrations: list[Migration], applied: dict[str, str], *, through: str | None = None) -> None:
    """Record files as applied without running them (an existing database that predates the ledger)."""
    names = [m.filename for m in migrations]
    if through is not None and through not in names:
        raise SystemExit(f"bootstrap cutoff {through!r} not found in migrations directory")
    for m in migrations:
        _check_drift(m, applied)
        if m.filename not in applied:
            _record(m, None)
            applied[m.filename] = m.checksum
            print(f"baselined {m.filename}")
        if m.filename == through:
            break


def _parse_vars(items: list[str]) -> dict[str, str]:
    out = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise SystemExit(f"--var expects key=value, got {item!r}")
        out[key] = value
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan, apply, or baseline SQL migrations.")
    parser.add_argument("action", choices=("plan", "apply", "baseline", "sync"), nargs="?", default="plan")
    parser.add_argument(
        "--bootstrap-through",
        default=os.environ.get("MIGRATION_BOOTSTRAP_THROUGH", DEFAULT_BOOTSTRAP_THROUGH),
        help="sync on an empty ledger: record files through this one as applied, then apply newer ones",
    )
    parser.add_argument("--var", action="append", default=[], help="psql variable, e.g. --var run_id=10")
    parser.add_argument("--dir", type=Path, default=None, help="migrations directory (default db/migrations)")
    args = parser.parse_args(argv)

    directory = args.dir or migrations_dir()
    migrations = load_migrations(directory)
    if args.action == "plan":  # read-only
        plan(migrations, _load_applied())
        return 0
    fresh = args.action == "apply" and not _load_applied() and _database_is_empty()
    _ensure_table()
    applied = _load_applied()
    if fresh and (directory.parent / "schema.sql").is_file():
        install_snapshot(migrations, applied, directory.parent)
    if args.action == "baseline":
        baseline(migrations, applied)
    elif args.action == "sync":
        if not applied:
            print(f"empty ledger: baselining through {args.bootstrap_through}")
            baseline(migrations, applied, through=args.bootstrap_through)
        apply(migrations, applied, _parse_vars(args.var))
    else:
        apply(migrations, applied, _parse_vars(args.var))
    return 0


if __name__ == "__main__":
    sys.exit(main())
