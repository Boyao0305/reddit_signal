"""Bootstrap configuration: the only module that reads process environment variables.

Operational settings (models, schedules, limits, API keys) live in PostgreSQL ``system_settings``
and are read through :mod:`reddit_signal.runtime_settings`. This module only holds what is needed
before the database can be reached, plus per-process identity.

Entry points call :func:`configure` with a validated :class:`Config`. Code that still runs without an
explicit entry point (legacy ``python -m`` modules) gets one built from the environment on first use.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

# Old variable names keep working during the openclaw -> reddit_signal rename (ADR 0002).
_LEGACY_ENV_NAMES = {
    "REDDIT_SIGNAL_WORKER_ID": "OPENCLAW_WORKER_ID",
}


class ConfigError(RuntimeError):
    """Raised when required bootstrap configuration is missing or invalid."""


class Config(BaseModel):
    model_config = ConfigDict(frozen=True)

    database_url: SecretStr | None = None
    db_pool_max: int = Field(default=32, ge=1, le=200)
    worker_id: str | None = None
    pipeline_job_id: int | None = None
    static_dir: str | None = None          # built frontend (frontend/dist) served by the api container
    analytics_api: bool = False            # internal /api/v2 analytics endpoints; off in production (ADR 0003)

    @field_validator("database_url", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Config:
        env = os.environ if environ is None else environ

        def read(name: str) -> str | None:
            value = env.get(name)
            if value is None and name in _LEGACY_ENV_NAMES:
                value = env.get(_LEGACY_ENV_NAMES[name])
            return value.strip() if isinstance(value, str) and value.strip() else None

        job_id = read("PIPELINE_JOB_ID")
        pool_max = read("REDDIT_SIGNAL_DB_POOL_MAX")
        try:
            return cls(
                database_url=read("DATABASE_URL"),
                db_pool_max=int(pool_max) if pool_max else 32,
                worker_id=read("REDDIT_SIGNAL_WORKER_ID"),
                static_dir=read("REDDIT_SIGNAL_STATIC_DIR"),
                analytics_api=(read("REDDIT_SIGNAL_ANALYTICS_API") or "").lower() in {"1", "true", "yes"},
                # Set by the worker for the job's child process; a malformed value means "no job".
                pipeline_job_id=int(job_id) if job_id and job_id.isdigit() else None,
            )
        except ValueError as exc:
            raise ConfigError(f"invalid bootstrap configuration: {exc}") from exc

    def require_database_url(self) -> str:
        if self.database_url is None:
            raise ConfigError(
                "DATABASE_URL is not set. It is the only required bootstrap variable; "
                "all other settings are read from the database."
            )
        return self.database_url.get_secret_value()


_current: Config | None = None


def configure(config: Config) -> None:
    """Install the process-wide configuration. Called once by each entry point."""
    global _current
    _current = config


def get() -> Config:
    """Return the process configuration, building it from the environment on first use."""
    global _current
    if _current is None:
        _current = Config.from_env()
    return _current


def reset() -> None:
    """Forget the cached configuration (tests)."""
    global _current
    _current = None


_DSN_PASSWORD = re.compile(r"(?P<head>://[^:/@\s]+:)[^@\s]*(?P<at>@)")
_KV_PASSWORD = re.compile(r"(?P<head>\bpassword\s*=\s*)\S+", re.IGNORECASE)


def redact_dsn(dsn: str) -> str:
    """Hide the password in a connection string (URL or key=value form) for logs and errors."""
    return _KV_PASSWORD.sub(r"\g<head>***", _DSN_PASSWORD.sub(r"\g<head>***\g<at>", dsn))
