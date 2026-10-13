"""Database-backed runtime settings with environment fallback.

DATABASE_URL remains the bootstrap setting. Operational keys such as LLM
credentials, relay URL, model ids, and scheduler times can be edited in
PostgreSQL and take effect for new requests/jobs without redeploying.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Iterable

import psycopg2
import psycopg2.extras

from reddit_signal import config, db

logger = logging.getLogger(__name__)


SETTINGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    is_secret BOOLEAN NOT NULL DEFAULT FALSE,
    description TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by TEXT
)
"""


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    group: str
    default: str
    is_secret: bool = False
    description: str = ""


SETTING_SPECS: tuple[SettingSpec, ...] = (
    # --- LLM -----------------------------------------------------------------------------------------
    SettingSpec("OPENROUTER_API_KEY", "OpenRouter API key", "LLM", "", is_secret=True,
                description="Key for all LLM calls (extraction, labels, audience, viability)."),
    SettingSpec("OPENROUTER_MODEL", "Extraction model", "LLM", "openai/gpt-oss-120b",
                description="Model used to extract pain points from posts."),
    SettingSpec("OPENROUTER_PROVIDER_ORDER", "Extraction provider order", "LLM", "",
                description="Comma-separated OpenRouter providers to try first, e.g. 'venice'. Empty = default routing."),
    SettingSpec("OPENROUTER_ALLOW_FALLBACKS", "Allow provider fallback", "LLM", "true",
                description="If the preferred providers cannot serve, fall back to OpenRouter's default routing."),
    SettingSpec("OPENROUTER_PROVIDER_ONLY", "Extraction providers (load-balanced)", "LLM", "",
                description="Comma-separated OpenRouter providers to spread calls over, e.g. venice/int4,deepinfra/bf16."),
    SettingSpec("OPENROUTER_MAX_PRICE", "Extraction max price", "LLM", "",
                description="USD per million tokens, 'prompt,completion' (e.g. 0.04,0.19); pricier providers are skipped."),
    SettingSpec("EXTRACTION_MAX_TOKENS", "Extraction max output tokens", "LLM", "2048",
                description="Output budget per post; cut-off answers are retried with double, up to 8192."),
    SettingSpec("EXTRACTION_REASONING_EFFORT", "Extraction reasoning effort", "LLM", "low",
                description="OpenRouter reasoning.effort for reasoning models (low/medium/high); empty = model default."),
    SettingSpec("AUDIENCE_CLASSIFIER_MODEL", "Audience classifier model", "LLM", "google/gemini-2.5-flash-lite",
                description="Monthly B2B/B2C classification of clusters."),
    SettingSpec("VIABILITY_MODEL", "Viability model", "LLM", "minimax/minimax-m2.7",
                description="Monthly is_real_pain / is_software_solvable scoring of candidate clusters."),
    # --- Schedule ------------------------------------------------------------------------------------
    SettingSpec("REDDIT_SIGNAL_DAILY_UTC", "Daily run time (UTC)", "Schedule", "",
                description="HH:MM. Empty = 04:05."),
    SettingSpec("REDDIT_SIGNAL_MONTHLY_UTC", "Monthly run time (UTC, 1st of month)", "Schedule", "",
                description="HH:MM. Empty = 05:00, on the 1st of the month."),
    SettingSpec("DASHBOARD_SNAPSHOT_REFRESH_ENABLED", "Refresh dashboard snapshots", "Schedule", "true"),
    SettingSpec("DASHBOARD_SNAPSHOT_REFRESH_MINUTES", "Snapshot refresh interval (minutes)", "Schedule", "60"),
    SettingSpec("SOURCE_BACKFILL_WORKER_ENABLED", "Process source backfill chunks", "Schedule", "true"),
    # --- Pipeline ------------------------------------------------------------------------------------
    SettingSpec("USE_SIGNAL_EVENTS", "Extract from signal_events", "Pipeline", "true"),
    SettingSpec("DAILY_EXTRACTION_LIMIT", "Daily extraction limit (posts)", "Pipeline", "1000",
                description="Posts per daily run across sources; 0 = no limit."),
    SettingSpec("EXTRACTION_RECENCY_DAYS", "Daily extraction: oldest post age (days)", "Pipeline", "14",
                description="Older unprocessed posts are backlog (extracted only by explicit backlog jobs)."),
    SettingSpec("EXTRACTION_MATURITY_DAYS", "Daily extraction: youngest post age (days)", "Pipeline", "2",
                description="Younger posts wait for their comments (~95% arrive within 2 days)."),
    SettingSpec("ASSIGN_THRESHOLD", "Daily centroid-assignment threshold", "Pipeline", "0.70",
                description="Cosine similarity needed to assign a new pain to an existing cluster (LD-#23)."),
    SettingSpec("ARCTIC_SHIFT_MAX_RPS", "Arctic Shift requests per second", "Data sources", "1.0",
                description="Shared across fetch threads; the API answers bursts with HTTP 422."),
    # --- Dashboard -----------------------------------------------------------------------------------
)

SETTING_SPECS_BY_KEY = {spec.key: spec for spec in SETTING_SPECS}
EDITABLE_SETTING_KEYS = set(SETTING_SPECS_BY_KEY)

_CACHE: dict[str, str] | None = None
_CACHE_EXPIRES_AT = 0.0
_CACHE_TTL_SECONDS = 15.0


def _dsn() -> str | None:
    url = config.get().database_url
    return url.get_secret_value() if url is not None else None


def _connect():
    return db.connect()


def clear_cache() -> None:
    global _CACHE, _CACHE_EXPIRES_AT
    _CACHE = None
    _CACHE_EXPIRES_AT = 0.0


def ensure_settings_table() -> None:
    if not _dsn():
        return
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(SETTINGS_TABLE_SQL)


def seed_from_environment(keys: Iterable[str] | None = None) -> None:
    """Insert missing settings from current env/defaults.

    Existing DB values are never overwritten.
    """
    if not _dsn():
        return
    ensure_settings_table()
    selected = [SETTING_SPECS_BY_KEY[k] for k in keys] if keys else list(SETTING_SPECS)
    with _connect() as conn:
        with conn.cursor() as cur:
            for spec in selected:
                value = os.environ.get(spec.key, spec.default)
                if value is None:
                    value = ""
                cur.execute(
                    """
                    INSERT INTO system_settings
                        (key, value, is_secret, description, updated_by)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (key) DO UPDATE SET
                        is_secret = EXCLUDED.is_secret,
                        description = EXCLUDED.description
                    WHERE system_settings.is_secret IS DISTINCT FROM EXCLUDED.is_secret
                       OR system_settings.description IS DISTINCT FROM EXCLUDED.description
                    """,
                    (spec.key, value, spec.is_secret, spec.description, "env-seed"),
                )
    clear_cache()


def load_settings(force: bool = False) -> dict[str, str]:
    global _CACHE, _CACHE_EXPIRES_AT
    now = time.time()
    if not force and _CACHE is not None and now < _CACHE_EXPIRES_AT:
        return dict(_CACHE)

    values: dict[str, str] = {}
    dsn = _dsn()
    if dsn:
        # Reads never create the table (migration v2_021 does). If the database cannot be read, keep the
        # last values we had; with none, fail loudly rather than silently run on built-in defaults
        # (wrong model, wrong limits).
        try:
            with _connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT key, value FROM system_settings")
                    values = {row["key"]: row["value"] for row in cur.fetchall()}
        except Exception as exc:
            if _CACHE is not None:
                logger.warning("could not refresh system_settings, keeping previous values: %s", exc)
                _CACHE_EXPIRES_AT = now + _CACHE_TTL_SECONDS
                return dict(_CACHE)
            raise RuntimeError(f"could not read system_settings: {exc}") from exc

    _CACHE = values
    _CACHE_EXPIRES_AT = now + _CACHE_TTL_SECONDS
    return dict(values)


def get_setting(key: str, default: str = "") -> str:
    value = load_settings().get(key)
    if value is not None and value != "":
        return value
    env_value = os.environ.get(key)
    if env_value is not None and env_value != "":
        return env_value
    spec = SETTING_SPECS_BY_KEY.get(key)
    if spec:
        return spec.default
    return default


def get_openrouter_api_key() -> str:
    return get_setting("OPENROUTER_API_KEY")


def apply_settings_to_environment(force: bool = False) -> None:
    """Populate os.environ for legacy modules that still read env vars."""
    settings = load_settings(force=force)
    for spec in SETTING_SPECS:
        value = settings.get(spec.key)
        if value is not None and value != "":
            os.environ[spec.key] = value
        elif spec.key not in os.environ and spec.default:
            os.environ[spec.key] = spec.default


def list_settings(mask_secrets: bool = True) -> list[dict]:
    settings = load_settings(force=True)
    rows: list[dict] = []
    for spec in SETTING_SPECS:
        value = settings.get(spec.key)
        source = "database" if value is not None else "environment/default"
        if value is None:
            value = os.environ.get(spec.key, spec.default)
        rows.append(
            {
                "key": spec.key,
                "label": spec.label,
                "group": spec.group,
                "value": "********" if mask_secrets and spec.is_secret and value else value,
                "is_secret": spec.is_secret,
                "description": spec.description,
                "source": source,
            }
        )
    return rows


def update_settings(
    values: dict[str, str], updated_by: str = "admin", mask_secrets: bool = True
) -> list[dict]:
    unknown = sorted(set(values) - EDITABLE_SETTING_KEYS)
    if unknown:
        raise ValueError(f"Unknown or non-editable setting(s): {', '.join(unknown)}")
    ensure_settings_table()
    with _connect() as conn:
        with conn.cursor() as cur:
            for key, value in values.items():
                spec = SETTING_SPECS_BY_KEY[key]
                cur.execute(
                    """
                    INSERT INTO system_settings
                        (key, value, is_secret, description, updated_by)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (key) DO UPDATE SET
                        value = EXCLUDED.value,
                        is_secret = EXCLUDED.is_secret,
                        description = EXCLUDED.description,
                        updated_by = EXCLUDED.updated_by
                    """,
                    (key, value, spec.is_secret, spec.description, updated_by),
                )
    clear_cache()
    apply_settings_to_environment(force=True)
    return list_settings(mask_secrets=mask_secrets)
