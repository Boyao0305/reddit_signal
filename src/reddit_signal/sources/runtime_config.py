"""Runtime configuration for source adapter execution.

The table is optional for local development: if it does not exist yet, the
framework falls back to the legacy reddit-only behavior.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2
import psycopg2.extras

from reddit_signal.data_ingestion.db import get_conn, put_conn

SUPPORTED_RUNTIME_SOURCES = frozenset({
    "hackernews",
    "reddit",
})


@dataclass(frozen=True)
class SourceRuntimeConfig:
    source: str
    enabled: bool = False
    fetch_enabled: bool = False
    project_enabled: bool = False
    extract_enabled: bool = False
    query: str = ""
    lookback_days: int = 7
    fetch_limit: int = 10_000
    priority: int = 100
    notes: str = ""
    updated_at: datetime | None = None

    @property
    def since_until(self) -> tuple[datetime | None, datetime]:
        until = datetime.now(timezone.utc)
        if self.lookback_days <= 0:
            return None, until
        return until - timedelta(days=self.lookback_days), until

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["updated_at"] = self.updated_at.isoformat() if self.updated_at else None
        return data


_LEGACY_REDDIT = SourceRuntimeConfig(
    source="reddit",
    enabled=True,
    fetch_enabled=True,
    project_enabled=True,
    extract_enabled=True,
    priority=10,
)


def list_source_configs(*, include_disabled: bool = False) -> list[SourceRuntimeConfig]:
    """Return configured sources ordered by priority.

    If source_runtime_config has not been migrated locally yet, return the
    legacy reddit-only config so dry-runs and existing scripts remain usable.
    """
    try:
        conn = get_conn()
    except Exception:
        return [_LEGACY_REDDIT] if not include_disabled else [_LEGACY_REDDIT]
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                where_sql = "" if include_disabled else "WHERE enabled IS TRUE"
                cur.execute(
                    f"""
                    SELECT source, enabled, fetch_enabled, project_enabled,
                           extract_enabled, query, lookback_days, fetch_limit,
                           priority, notes, updated_at
                    FROM source_runtime_config
                    {where_sql}
                    ORDER BY priority ASC, source ASC
                    """
                )
            except psycopg2.errors.UndefinedTable:
                conn.rollback()
                return [_LEGACY_REDDIT] if not include_disabled else [_LEGACY_REDDIT]
            rows = cur.fetchall()
    finally:
        put_conn(conn)

    configs = [
        SourceRuntimeConfig(
            source=row["source"],
            enabled=bool(row["enabled"]),
            fetch_enabled=bool(row["fetch_enabled"]),
            project_enabled=bool(row["project_enabled"]),
            extract_enabled=bool(row["extract_enabled"]),
            query=row["query"] or "",
            lookback_days=int(row["lookback_days"] or 0),
            fetch_limit=int(row["fetch_limit"] or 0),
            priority=int(row["priority"] or 100),
            notes=row["notes"] or "",
            updated_at=row["updated_at"],
        )
        for row in rows
    ]
    return [cfg for cfg in configs if cfg.source in SUPPORTED_RUNTIME_SOURCES]


def validate_source_update(values: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "enabled",
        "fetch_enabled",
        "project_enabled",
        "extract_enabled",
        "query",
        "lookback_days",
        "fetch_limit",
        "priority",
        "notes",
    }
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"Unknown source config field(s): {', '.join(sorted(unknown))}")

    out: dict[str, Any] = {}
    for key in ("enabled", "fetch_enabled", "project_enabled", "extract_enabled"):
        if key in values:
            if not isinstance(values[key], bool):
                raise ValueError(f"{key} must be a boolean")
            out[key] = values[key]

    if "query" in values:
        query = str(values["query"] or "")
        if len(query) > 2000:
            raise ValueError("query must be at most 2000 characters")
        out["query"] = query

    if "notes" in values:
        notes = str(values["notes"] or "")
        if len(notes) > 4000:
            raise ValueError("notes must be at most 4000 characters")
        out["notes"] = notes

    ranges = {
        "lookback_days": (0, 3650),
        "fetch_limit": (0, 100_000),
        "priority": (0, 10_000),
    }
    for key, (lo, hi) in ranges.items():
        if key not in values:
            continue
        try:
            value = int(values[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be an integer") from exc
        if value < lo or value > hi:
            raise ValueError(f"{key} must be between {lo} and {hi}")
        out[key] = value

    return out


def validate_source_payload(source: str, values: dict[str, Any]) -> dict[str, Any]:
    out = validate_source_update(values)
    if "query" in out:
        query = out["query"].strip()
        out["query"] = out["query"]
    return out


def update_source_config(source: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate, guard and upsert a single source's runtime config.

    Ensures that:
    1. The submitted fields are validated.
    2. An unregistered source cannot be enabled.
    3. The upsert is committed and the resulting config returned as a dict
       with a ``registered`` flag included.
    """
    values = validate_source_payload(source, payload)
    registered = set(registered_source_names())
    existing = get_source_config(source)

    wants_enable = any(
        values.get(key) is True
        for key in ("enabled", "fetch_enabled", "project_enabled", "extract_enabled")
    )
    if source not in registered and (existing is None or wants_enable):
        raise ValueError(
            f"Source {source!r} is not registered and cannot be enabled"
        )

    cfg = upsert_source_config(source, values)
    item = cfg.to_dict()
    item["registered"] = source in registered
    return item


def delete_source_config(source: str) -> bool:
    """Delete a source's runtime config row. Returns True if a row was deleted.

    The adapter code (registry) is unaffected.  After deletion the source
    will reappear in ``list_admin_source_configs()`` as a fully-disabled
    entry if it is still registered.
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM source_runtime_config WHERE source = %s",
                (source,),
            )
            deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def _row_to_config(row) -> SourceRuntimeConfig:
    return SourceRuntimeConfig(
        source=row["source"],
        enabled=bool(row["enabled"]),
        fetch_enabled=bool(row["fetch_enabled"]),
        project_enabled=bool(row["project_enabled"]),
        extract_enabled=bool(row["extract_enabled"]),
        query=row["query"] or "",
        lookback_days=int(row["lookback_days"] or 0),
        fetch_limit=int(row["fetch_limit"] or 0),
        priority=int(row["priority"] or 100),
        notes=row["notes"] or "",
        updated_at=row["updated_at"],
    )


def get_source_config(source: str) -> SourceRuntimeConfig | None:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT source, enabled, fetch_enabled, project_enabled,
                       extract_enabled, query, lookback_days, fetch_limit,
                       priority, notes, updated_at
                FROM source_runtime_config
                WHERE source = %s
                """,
                (source,),
            )
            row = cur.fetchone()
    finally:
        put_conn(conn)
    return _row_to_config(row) if row else None


def upsert_source_config(source: str, values: dict[str, Any]) -> SourceRuntimeConfig:
    updates = validate_source_payload(source, values)
    insert_values = {
        "enabled": updates.get("enabled", False),
        "fetch_enabled": updates.get("fetch_enabled", False),
        "project_enabled": updates.get("project_enabled", False),
        "extract_enabled": updates.get("extract_enabled", False),
        "query": updates.get("query", ""),
        "lookback_days": updates.get("lookback_days", 7),
        "fetch_limit": updates.get("fetch_limit", 10_000),
        "priority": updates.get("priority", 100),
        "notes": updates.get("notes", ""),
    }
    mutable_cols = [
        "enabled",
        "fetch_enabled",
        "project_enabled",
        "extract_enabled",
        "query",
        "lookback_days",
        "fetch_limit",
        "priority",
        "notes",
    ]
    update_sql = ",\n                    ".join(
        f"{col} = EXCLUDED.{col}" if col in updates else f"{col} = source_runtime_config.{col}"
        for col in mutable_cols
    )
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                INSERT INTO source_runtime_config (
                    source, enabled, fetch_enabled, project_enabled,
                    extract_enabled, query, lookback_days, fetch_limit,
                    priority, notes, updated_at
                ) VALUES (
                    %(source)s, %(enabled)s, %(fetch_enabled)s, %(project_enabled)s,
                    %(extract_enabled)s, %(query)s, %(lookback_days)s, %(fetch_limit)s,
                    %(priority)s, %(notes)s, NOW()
                )
                ON CONFLICT (source) DO UPDATE SET
                    {update_sql},
                    updated_at = NOW()
                RETURNING source, enabled, fetch_enabled, project_enabled,
                          extract_enabled, query, lookback_days, fetch_limit,
                          priority, notes, updated_at
                """,
                {"source": source, **insert_values},
            )
            row = cur.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)
    return _row_to_config(row)


def registered_source_names() -> list[str]:
    from reddit_signal.sources.registry import discover_adapters, list_adapters

    discover_adapters()
    return list_adapters()


def list_admin_source_configs() -> list[dict[str, Any]]:
    configs = {cfg.source: cfg for cfg in list_source_configs(include_disabled=True)}
    registered = set(registered_source_names())
    adapter_metas: dict[str, dict[str, Any]] = {}
    try:
        from reddit_signal.sources.registry import list_adapter_infos
        from dataclasses import asdict
        adapter_metas = {m.name: asdict(m) for m in list_adapter_infos()}
    except Exception:
        pass
    for source in registered - set(configs):
        configs[source] = SourceRuntimeConfig(source=source)
    rows = []
    for source, cfg in configs.items():
        item = cfg.to_dict()
        item["registered"] = source in registered
        if source in adapter_metas:
            item["adapter_meta"] = adapter_metas[source]
        rows.append(item)
    return sorted(rows, key=lambda row: (int(row["priority"]), str(row["source"])))


def preview_daily_plan() -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    order = 1
    configs = list_source_configs()
    for cfg in configs:
        if cfg.fetch_enabled:
            query = cfg.query
            lookback_days = cfg.lookback_days
            fetch_limit = cfg.fetch_limit
            if cfg.source == "reddit" and not query.strip():
                try:
                    from reddit_signal.sources.reddit_subreddits import active_subreddits

                    rows = active_subreddits()
                    query = f"managed active subreddits ({len(rows)})"
                    lookback_days = max(
                        (int(row.get("daily_lookback_days") or 7) for row in rows),
                        default=cfg.lookback_days,
                    )
                except Exception:
                    query = "managed active subreddits"
            steps.append({
                "order": order,
                "phase": "fetch",
                "source": cfg.source,
                "query": query,
                "lookback_days": lookback_days,
                "fetch_limit": fetch_limit,
            })
            order += 1
    for cfg in configs:
        if cfg.project_enabled:
            steps.append({"order": order, "phase": "project", "source": cfg.source})
            order += 1
    for cfg in configs:
        if cfg.extract_enabled:
            steps.append({"order": order, "phase": "extract", "source": cfg.source})
            order += 1
    return steps


def enabled_sources_for_extract() -> list[str]:
    return [cfg.source for cfg in list_source_configs() if cfg.extract_enabled]


def enabled_sources_for_project() -> list[str]:
    return [cfg.source for cfg in list_source_configs() if cfg.project_enabled]
