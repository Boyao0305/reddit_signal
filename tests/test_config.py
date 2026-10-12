import pytest

from reddit_signal.config import Config, ConfigError, redact_dsn

# --- Config.from_env: what the environment maps to ---------------------------------------------
# Each row: environment, expected (database_url, pool_max, worker_id, pipeline_job_id).
FROM_ENV_CASES = [
    # Only DATABASE_URL is required; everything else has a default.
    ({"DATABASE_URL": "postgresql://u:p@h/db"}, ("postgresql://u:p@h/db", 32, None, None)),
    # Blank values count as unset.
    ({"DATABASE_URL": "  "}, (None, 32, None, None)),
    ({}, (None, 32, None, None)),
    ({"REDDIT_SIGNAL_WORKER_ID": "w1"}, (None, 32, "w1", None)),
    # The worker sets PIPELINE_JOB_ID for its child process; malformed means "no job", not an error.
    ({"PIPELINE_JOB_ID": "108"}, (None, 32, None, 108)),
    ({"PIPELINE_JOB_ID": "abc"}, (None, 32, None, None)),
    ({"REDDIT_SIGNAL_DB_POOL_MAX": "8"}, (None, 8, None, None)),
]


@pytest.mark.parametrize(("env", "expected"), FROM_ENV_CASES)
def test_from_env(env, expected):
    cfg = Config.from_env(env)
    url = cfg.database_url.get_secret_value() if cfg.database_url else None
    assert (url, cfg.db_pool_max, cfg.worker_id, cfg.pipeline_job_id) == expected


@pytest.mark.parametrize("bad", ["0", "abc", "1000"])
def test_invalid_pool_size_is_a_config_error(bad):
    with pytest.raises(ConfigError):
        Config.from_env({"REDDIT_SIGNAL_DB_POOL_MAX": bad})


def test_missing_database_url_fails_loudly_instead_of_falling_back():
    with pytest.raises(ConfigError, match="DATABASE_URL is not set"):
        Config.from_env({}).require_database_url()


def test_database_url_is_hidden_in_repr():
    cfg = Config.from_env({"DATABASE_URL": "postgresql://u:secret@h/db"})
    assert "secret" not in repr(cfg)


# --- redact_dsn: passwords never reach logs ----------------------------------------------------
REDACT_CASES = [
    ("postgresql://user:s3cret@host:5432/db", "postgresql://user:***@host:5432/db"),
    ("postgresql://user:p%40ss@host/db?sslmode=require", "postgresql://user:***@host/db?sslmode=require"),
    ("host=h port=5432 user=u password=s3cret dbname=d", "host=h port=5432 user=u password=*** dbname=d"),
    # Nothing to hide.
    ("postgresql://user@host/db", "postgresql://user@host/db"),
    ("postgresql:///db", "postgresql:///db"),
]


@pytest.mark.parametrize(("dsn", "expected"), REDACT_CASES)
def test_redact_dsn(dsn, expected):
    assert redact_dsn(dsn) == expected
