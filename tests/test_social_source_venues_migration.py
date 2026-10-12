import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"


def test_social_source_venues_migration_seeds_defaults_idempotently():
    sql = (MIGRATIONS / "v2_038_social_source_venues.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS social_source_venues" in sql
    assert "ON CONFLICT (source, venue_type, url_or_id) DO NOTHING" in sql
    assert "https://www.facebook.com/groups/saasfoundersclub/" in sql
    assert "https://www.facebook.com/groups/1752388055008755/" in sql
    assert "https://x.com/i/communities/1471580197908586507" in sql
    assert "https://x.com/i/communities/1891501175075491943" in sql


def test_facebook_vanity_group_venues_are_paused_by_followup_migration():
    sql = (MIGRATIONS / "v2_039_pause_facebook_vanity_group_venues.sql").read_text(encoding="utf-8")

    assert "status = 'paused'" in sql
    assert "saasfoundersclub" in sql
    assert "foundersspace" in sql
    assert "6204556817" not in sql


def test_v2_043_facebook_expansion_uses_numeric_group_urls_only():
    sql = (MIGRATIONS / "v2_043_source_backfill_and_social_expansion.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS source_backfill_jobs" in sql
    assert "CREATE TABLE IF NOT EXISTS source_backfill_chunks" in sql
    assert "Founder.CEOs" not in sql
    assert "ON CONFLICT (source, venue_type, url_or_id) DO NOTHING" in sql
    assert "source = 'facebook'" in sql
    facebook_urls = re.findall(
        r"'(https://www\.facebook\.com/groups/[^']+)'",
        sql,
    )
    seeded_urls = [
        url for url in facebook_urls
        if url != "https://www.facebook.com/groups/1998012103811981/"
    ]
    assert len(seeded_urls) >= 25
    assert all(
        re.match(r"^https://www\.facebook\.com/groups/[0-9]+/$", url)
        for url in seeded_urls
    )


def test_v2_046_source_backfill_defaults_to_fetch_only():
    sql = (MIGRATIONS / "v2_046_source_backfill_fetch_only_default.sql").read_text(encoding="utf-8")

    assert "ALTER COLUMN mode SET DEFAULT 'fetch'" in sql
    assert "source_backfill_jobs" in sql
    assert "source_backfill_chunks" in sql
