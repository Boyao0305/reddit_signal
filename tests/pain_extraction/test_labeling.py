"""Cluster labels: reasoning models need room to answer, and an empty answer is retried."""
from types import SimpleNamespace

from reddit_signal.pain_extraction import extraction, labeling


def reply(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_empty_answer_is_retried_and_request_uses_extraction_routing(monkeypatch):
    calls, answers = [], [reply(None), reply('"Invoice chasing: freelancers chase late payments"')]
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: calls.append(kw) or answers.pop(0))))
    monkeypatch.setattr(extraction, "request_options",
                        lambda: {"max_tokens": 2048, "extra_body": {"reasoning": {"effort": "low"}}})

    label = labeling._generate_label(client, 1, ["late invoices"], "openai/gpt-oss-120b")

    assert label == "Invoice chasing: freelancers chase late payments"
    assert len(calls) == 2
    assert calls[0]["max_tokens"] == labeling.LABEL_MAX_TOKENS
    assert calls[0]["extra_body"] == {"reasoning": {"effort": "low"}}


def test_no_answer_after_all_attempts_gives_an_empty_label(monkeypatch):
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: reply(None))))
    monkeypatch.setattr(extraction, "request_options", lambda: {"max_tokens": 2048})
    assert labeling._generate_label(client, 1, ["x"], "m") == ""
