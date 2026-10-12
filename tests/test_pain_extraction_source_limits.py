from reddit_signal.pain_extraction.main import _compute_source_limits


def test_compute_source_limits_even_split():
    limits = _compute_source_limits(
        sources=["reddit", "hackernews", "facebook", "youtube", "twitter"],
        limit=1000,
        limit_is_total=True,
    )

    assert limits == {
        "reddit": 200,
        "hackernews": 200,
        "facebook": 200,
        "youtube": 200,
        "twitter": 200,
    }


def test_compute_source_limits_distributes_remainder():
    limits = _compute_source_limits(
        sources=["reddit", "hackernews", "facebook"],
        limit=10,
        limit_is_total=True,
    )

    assert limits == {
        "reddit": 4,
        "hackernews": 3,
        "facebook": 3,
    }


def test_compute_source_limits_keeps_per_source_limit_when_not_total():
    limits = _compute_source_limits(
        sources=["reddit", "youtube"],
        limit=500,
        limit_is_total=False,
    )

    assert limits == {
        "reddit": 500,
        "youtube": 500,
    }


def _run_ingest(monkeypatch, pending: dict[str, int], limit: int, sources: list[str]) -> list[tuple[str, int]]:
    """cmd_ingest with fake sources holding `pending` unprocessed posts; returns (source, limit) calls."""
    from reddit_signal.pain_extraction import main

    calls = []
    left = dict(pending)

    def extract_source(*, source, limit, **_):
        calls.append((source, limit))
        done = min(limit, left[source])
        left[source] -= done
        return {"processed": done}

    monkeypatch.setattr(main, "_cmd_ingest_extract_source", extract_source)
    monkeypatch.setattr(main.extraction, "smoke_check_model", lambda: None)
    monkeypatch.setattr(main, "cmd_score", lambda: None)
    monkeypatch.setattr(main, "cmd_embed", lambda: None)
    monkeypatch.setattr(main, "_job_event", lambda **_: None)
    main.cmd_ingest(limit=limit, workers=1, sources=sources, limit_is_total=True)
    return calls


def test_budget_left_by_a_later_source_goes_back_to_the_capped_one(monkeypatch):
    # Daily job 111: reddit first and capped at 5,000; hackernews needed 133 of its 5,000.
    calls = _run_ingest(monkeypatch, {"reddit": 95_000, "hackernews": 133}, 10_000, ["reddit", "hackernews"])
    assert calls == [("reddit", 5_000), ("hackernews", 5_000), ("reddit", 4_867)]


def test_no_second_pass_when_every_source_ran_dry(monkeypatch):
    calls = _run_ingest(monkeypatch, {"reddit": 10, "hackernews": 5}, 100, ["reddit", "hackernews"])
    assert calls == [("reddit", 50), ("hackernews", 90)]   # reddit's unused 40 rolls forward; no pass 2
