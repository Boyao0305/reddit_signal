import psycopg2

from reddit_signal.sources import runtime_config


class _Cursor:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, *_):
        if self.error:
            raise self.error
        self.sql = _[0] if _ else ""
        self.params = _[1] if len(_) > 1 else None
        self.executed.append((self.sql, self.params))

    def fetchall(self):
        if "WHERE enabled IS TRUE" in getattr(self, "sql", ""):
            return [row for row in self.rows if row["enabled"] is True]
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _Conn:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error
        self.rolled_back = False
        self.committed = False

    def cursor(self, *_, **__):
        return _Cursor(self.rows, self.error)

    def rollback(self):
        self.rolled_back = True

    def commit(self):
        self.committed = True


def test_list_source_configs_falls_back_to_reddit(monkeypatch):
    conn = _Conn(error=psycopg2.errors.UndefinedTable())
    monkeypatch.setattr(runtime_config, "get_conn", lambda: conn)
    monkeypatch.setattr(runtime_config, "put_conn", lambda _conn: None)

    configs = runtime_config.list_source_configs()

    assert [cfg.source for cfg in configs] == ["reddit"]
    assert configs[0].extract_enabled is True
    assert conn.rolled_back is True


def test_list_source_configs_filters_enabled(monkeypatch):
    rows = [
        {
            "source": "reddit",
            "enabled": True,
            "fetch_enabled": True,
            "project_enabled": True,
            "extract_enabled": True,
            "query": "",
            "lookback_days": 7,
            "fetch_limit": 10000,
            "priority": 10,
            "notes": "default",
            "updated_at": None,
        },
        {
            "source": "facebook",
            "enabled": True,
            "fetch_enabled": True,
            "project_enabled": True,
            "extract_enabled": True,
            "query": "https://www.facebook.com/example",
            "lookback_days": 7,
            "fetch_limit": 100,
            "priority": 20,
            "notes": "facebook source",
            "updated_at": None,
        },
        {
            "source": "removed_source",
            "enabled": True,
            "fetch_enabled": True,
            "project_enabled": True,
            "extract_enabled": True,
            "query": "legacy query",
            "lookback_days": 7,
            "fetch_limit": 100,
            "priority": 30,
            "notes": "removed source",
            "updated_at": None,
        },
        {
            "source": "youtube",
            "enabled": True,
            "fetch_enabled": True,
            "project_enabled": True,
            "extract_enabled": True,
            "query": "small business software problems",
            "lookback_days": 7,
            "fetch_limit": 200,
            "priority": 40,
            "notes": "youtube source",
            "updated_at": None,
        },
        {
            "source": "twitter",
            "enabled": True,
            "fetch_enabled": True,
            "project_enabled": True,
            "extract_enabled": True,
            "query": "workflow pain",
            "lookback_days": 7,
            "fetch_limit": 100,
            "priority": 70,
            "notes": "twitter source",
            "updated_at": None,
        },
    ]
    monkeypatch.setattr(runtime_config, "get_conn", lambda: _Conn(rows))
    monkeypatch.setattr(runtime_config, "put_conn", lambda _conn: None)

    # Rows for dropped sources stay in the table but are ignored.
    configs = runtime_config.list_source_configs()

    assert [cfg.source for cfg in configs] == ["reddit"]
    all_configs = runtime_config.list_source_configs(include_disabled=True)
    assert [cfg.source for cfg in all_configs] == ["reddit"]
    assert all_configs[0].notes == "default"
    assert runtime_config.enabled_sources_for_extract() == ["reddit"]


def test_preview_daily_plan_orders_enabled_phases(monkeypatch):
    rows = [
        {
            "source": "hackernews",
            "enabled": True,
            "fetch_enabled": False,
            "project_enabled": True,
            "extract_enabled": True,
            "query": "new,ask",
            "lookback_days": 3,
            "fetch_limit": 100,
            "priority": 5,
            "notes": "",
            "updated_at": None,
        },
        {
            "source": "reddit",
            "enabled": True,
            "fetch_enabled": True,
            "project_enabled": True,
            "extract_enabled": True,
            "query": "",
            "lookback_days": 7,
            "fetch_limit": 10000,
            "priority": 10,
            "notes": "",
            "updated_at": None,
        },
        {
            "source": "disabled",
            "enabled": False,
            "fetch_enabled": True,
            "project_enabled": True,
            "extract_enabled": True,
            "query": "",
            "lookback_days": 7,
            "fetch_limit": 100,
            "priority": 1,
            "notes": "",
            "updated_at": None,
        },
    ]
    monkeypatch.setattr(runtime_config, "get_conn", lambda: _Conn(rows))
    monkeypatch.setattr(runtime_config, "put_conn", lambda _conn: None)

    steps = runtime_config.preview_daily_plan()

    assert [(s["phase"], s["source"]) for s in steps] == [
        ("fetch", "reddit"),
        ("project", "hackernews"),
        ("project", "reddit"),
        ("extract", "hackernews"),
        ("extract", "reddit"),
    ]


def test_validate_source_update_rejects_out_of_range_values():
    try:
        runtime_config.validate_source_update({"fetch_limit": 100001})
    except ValueError as exc:
        assert "fetch_limit" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_validate_source_payload_keeps_query_verbatim():
    payload = runtime_config.validate_source_payload("reddit", {"query": "SaaS\nstartups"})

    assert payload["query"] == "SaaS\nstartups"
