import pytest

from reddit_signal import runtime_settings as rs


class DatabaseDown(Exception):
    pass


@pytest.fixture
def db_down(monkeypatch):
    monkeypatch.setattr(rs, "_dsn", lambda: "postgresql://x@h/db")

    def boom():
        raise DatabaseDown("connection refused")

    monkeypatch.setattr(rs, "_connect", boom)
    rs.clear_cache()
    yield
    rs.clear_cache()


def test_unreadable_settings_fail_loudly_instead_of_using_defaults(db_down):
    # Before: the error was swallowed and every setting silently became its built-in default.
    with pytest.raises(RuntimeError, match="could not read system_settings"):
        rs.get_setting("OPENROUTER_MODEL")


def test_a_failed_refresh_keeps_the_last_good_values(db_down):
    rs._CACHE = {"OPENROUTER_MODEL": "openai/gpt-oss-120b", "DAILY_EXTRACTION_LIMIT": "10000"}
    rs._CACHE_EXPIRES_AT = 0  # expired: the next read tries the database and fails
    assert rs.get_setting("DAILY_EXTRACTION_LIMIT") == "10000"


def test_new_schedule_keys_default_to_empty_so_legacy_values_apply():
    assert rs.SETTING_SPECS_BY_KEY["REDDIT_SIGNAL_DAILY_UTC"].default == ""
    assert rs.SETTING_SPECS_BY_KEY["REDDIT_SIGNAL_MONTHLY_UTC"].default == ""


def test_removed_features_have_no_settings():
    removed = {"ADMIN_API_TOKEN", "ANTHROPIC_RELAY_BASE_URL", "COMPETITION_MONTHLY_ENABLED",
               "SOCIALCRAWL_API_KEY", "REDDIT_BACKFILL_WORKER_ENABLED"}
    assert not removed & set(rs.SETTING_SPECS_BY_KEY)
