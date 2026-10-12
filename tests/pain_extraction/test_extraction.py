"""Extraction request/response handling. Failure shapes come from experiments 0001 and 0005."""
from types import SimpleNamespace

import pytest

from reddit_signal.pain_extraction import extraction
from reddit_signal.pain_extraction.extraction import ExtractionError, UnusableOutput, load_pain_array
from reddit_signal.pain_extraction.models import PostWithComments

ITEM = '{"pain_statement": "Invoices take hours", "raw_quote": "I spend hours", "source": "post"}'


# --- load_pain_array: what counts as a usable answer ----------------------------------------------
@pytest.mark.parametrize(
    ("raw", "count"),
    [
        ("[]", 0),                                         # "no pains" is a real, usable answer
        (f"[{ITEM}]", 1),
        (f"```json\n[{ITEM}]\n```", 1),                    # code fences
        (f"Here are the pain signals:\n[{ITEM}]", 1),      # preamble (mistral-nemo, gemma in 0001)
        (f"[{ITEM}]\nLet me know if you need more.", 1),   # trailing chatter
        (f"[{ITEM},]", 1),                                 # trailing comma
        ('[{"pain_statement": "can\'t export", "raw_quote": "C:\\\\x", "source": "post"}]', 1),
        ('[{"pain_statement": "x", "raw_quote": "a \\q b", "source": "post"}]', 1),  # bare backslash
    ],
)
def test_usable_answers(raw, count):
    assert len(load_pain_array(raw)) == count


@pytest.mark.parametrize(
    "raw",
    [
        "",                                                # empty content
        "I could not find any pain signals.",              # prose only
        f"[{ITEM}, {{\"pain_statement\": \"cut of",        # truncated mid-array (flash-lite at 2048)
        '{"pain_statement": "x", "raw_quote": "y"}',       # an object, not an array
    ],
)
def test_unusable_answers_raise_instead_of_meaning_no_pains(raw):
    with pytest.raises(UnusableOutput):
        load_pain_array(raw)


# --- extract_for_post with a fake OpenRouter client ---------------------------------------------------
def pwc():
    return PostWithComments(post_db_id=1, post_reddit_id="abc", subreddit_name="SaaS", title="t",
                            selftext="body", author="me", score=1, num_comments=0, post_created=None,
                            comments=[])


def response(content=None, finish_reason="stop", choices=True):
    if not choices:
        return SimpleNamespace(choices=None)
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish_reason,
                                                    message=SimpleNamespace(content=content))])


@pytest.fixture
def fake(monkeypatch):
    state = {"responses": [], "calls": [], "settings": {}}

    def create(**kwargs):
        state["calls"].append(kwargs)
        return state["responses"].pop(0)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(extraction, "get_client", lambda: client)
    monkeypatch.setattr(extraction, "get_openrouter_model", lambda: "openai/gpt-oss-120b")
    monkeypatch.setattr(extraction.runtime_settings, "get_setting",
                        lambda key, default="": state["settings"].get(key, default))
    monkeypatch.setattr(extraction.time, "sleep", lambda s: None)
    return state


def test_cut_off_output_is_retried_with_a_bigger_budget(fake):
    fake["responses"] = [response("[{\"pain", finish_reason="length"), response(f"[{ITEM}]")]
    pains = extraction.extract_for_post(pwc())
    assert len(pains) == 1
    assert [c["max_tokens"] for c in fake["calls"]] == [2048, 4096]


def test_output_cut_off_even_at_the_ceiling_is_an_error(fake):
    fake["settings"]["EXTRACTION_MAX_TOKENS"] = "8192"
    fake["responses"] = [response("[{", finish_reason="length")]
    with pytest.raises(UnusableOutput, match="cut off"):
        extraction.extract_for_post(pwc())


def test_empty_choices_are_retried(fake):
    fake["responses"] = [response(choices=False), response("[]")]
    assert extraction.extract_for_post(pwc()) == []
    assert len(fake["calls"]) == 2


def test_empty_choices_every_time_is_an_error(fake):
    fake["responses"] = [response(choices=False)] * 3
    with pytest.raises(ExtractionError):
        extraction.extract_for_post(pwc())


def test_unparseable_answer_is_not_retried(fake):
    fake["responses"] = [response("Sorry, I can't help with that.")]
    with pytest.raises(UnusableOutput):
        extraction.extract_for_post(pwc())
    assert len(fake["calls"]) == 1


def test_provider_pinning_from_settings(fake):
    fake["settings"].update(OPENROUTER_PROVIDER_ORDER="venice", OPENROUTER_ALLOW_FALLBACKS="true")
    fake["responses"] = [response("[]")]
    extraction.extract_for_post(pwc())
    assert fake["calls"][0]["extra_body"] == {"provider": {"order": ["venice"], "allow_fallbacks": True},
                                              "reasoning": {"effort": "low"}}


def test_no_provider_setting_means_default_routing(fake):
    fake["responses"] = [response("[]")]
    extraction.extract_for_post(pwc())
    assert "provider" not in fake["calls"][0]["extra_body"]


def test_reasoning_effort_defaults_to_low_as_evaluated(fake):
    fake["responses"] = [response("[]")]
    extraction.extract_for_post(pwc())
    assert fake["calls"][0]["extra_body"]["reasoning"] == {"effort": "low"}


def test_empty_reasoning_effort_leaves_the_model_default(fake):
    fake["settings"]["EXTRACTION_REASONING_EFFORT"] = ""
    fake["responses"] = [response("[]")]
    extraction.extract_for_post(pwc())
    assert "extra_body" not in fake["calls"][0]


def test_batch_reports_failures_to_fail_fn(fake, monkeypatch):
    posts = [pwc(), pwc()]
    outcomes = iter([UnusableOutput("no JSON array"), []])

    def fake_extract(p):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(extraction, "extract_for_post", fake_extract)
    saved, failed = [], []
    stats = extraction.extract_batch_concurrent(posts, save_fn=lambda p, pains: saved.append(p),
                                                max_workers=1, fail_fn=lambda p, err: failed.append(err))
    assert stats["errors"] == 1 and len(saved) == 1
    assert failed == ["UnusableOutput: no JSON array"]
