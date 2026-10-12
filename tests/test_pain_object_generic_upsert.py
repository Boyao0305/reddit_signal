from reddit_signal.pain_extraction import db
from reddit_signal.pain_extraction.models import PainObject


class _Cursor:
    def __init__(self):
        self.sql = ""
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return (123,)


class _Conn:
    def __init__(self):
        self.cursor_obj = _Cursor()
        self.committed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        pass


def test_non_reddit_pain_upsert_uses_signal_event_key(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(db, "get_conn", lambda: conn)
    monkeypatch.setattr(db, "put_conn", lambda _conn: None)

    pain = PainObject(
        post_id=None,
        comment_id=None,
        subreddit_name=None,
        signal_event_id=42,
        prompt_version="v1",
        pain_statement="Exports fail silently.",
        raw_quote="The export workflow fails silently.",
        mentioned_tools=[],
        is_workaround=False,
        source_author="hn-user",
    )

    pain_id = db.upsert_pain_object(pain)

    assert pain_id == 123
    assert conn.committed is True
    assert "ON CONFLICT (signal_event_id, pain_statement, prompt_version)" in conn.cursor_obj.sql
    assert conn.cursor_obj.params[0] is None
    assert conn.cursor_obj.params[12] == 42
