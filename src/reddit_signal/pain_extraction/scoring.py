"""
Deterministic scoring for pain objects.

Three scores:
  sentiment_score  — VADER compound score on raw_quote [-1.0, 1.0]
  specificity      — heuristic: (named entities + concrete nouns) / total tokens [0, 1]
  community_score  — upvote signal: post.score * log(num_comments+2) for post-sourced,
                     comment.score for comment-sourced
"""

import logging
import math
from typing import List

logger = logging.getLogger(__name__)

# ─── Lazy-load heavy models ───────────────────────────────────────────────────

_vader = None
_nlp = None


def _get_vader():
    global _vader
    if _vader is None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        _vader = SentimentIntensityAnalyzer()
    return _vader


def _get_nlp():
    global _nlp
    if _nlp is None:
        import spacy
        try:
            _nlp = spacy.load("en_core_web_sm")
        except OSError:
            logger.warning(
                "spaCy model 'en_core_web_sm' not found. "
                "Run: python -m spacy download en_core_web_sm\n"
                "Falling back to blank English model — specificity scores will be 0."
            )
            _nlp = spacy.blank("en")
    return _nlp


# ─── Individual score functions ───────────────────────────────────────────────

def sentiment_score(text: str) -> float:
    """VADER compound score in [-1.0, 1.0]. Negative = more pain."""
    sia = _get_vader()
    return round(sia.polarity_scores(text)["compound"], 4)


def specificity_score(text: str) -> float:
    """
    Heuristic specificity: fraction of tokens that are named entities
    or concrete/proper nouns.

    spaCy POS tags considered concrete: NOUN, PROPN
    NER entities always count as specific.
    Score = (ent_tokens + non_ent_concrete_nouns) / max(total_tokens, 1)
    Clamped to [0.0, 1.0].
    """
    if not text or not text.strip():
        return 0.0

    nlp = _get_nlp()
    doc = nlp(text[:1000])  # cap to avoid very long strings

    total_tokens = len([t for t in doc if not t.is_space])
    if total_tokens == 0:
        return 0.0

    # Collect token indices covered by named entities
    ent_token_indices = set()
    for ent in doc.ents:
        for i in range(ent.start, ent.end):
            ent_token_indices.add(i)

    specific_count = len(ent_token_indices)
    for tok in doc:
        if tok.i not in ent_token_indices and tok.pos_ in ("NOUN", "PROPN"):
            specific_count += 1

    score = specific_count / total_tokens
    return round(min(score, 1.0), 4)


def community_score(
    is_comment_sourced: bool,
    post_score: int,
    num_comments: int,
    comment_score: int = 0,
) -> float:
    """
    Community engagement signal.

    Post-sourced:    post.score * (1 + log(num_comments + 1))
    Comment-sourced: comment.score (raw)
    """
    if is_comment_sourced:
        return float(comment_score)
    return round(post_score * (1 + math.log(num_comments + 1)), 2)


# ─── Batch scoring ────────────────────────────────────────────────────────────

def score_pain_objects(unscored_rows: List[dict]) -> List[dict]:
    """
    Compute scores for a list of raw DB rows (dicts from get_unscored_pain_objects).

    Each input dict must have:
      id, raw_quote, post_id, comment_id,
      post_score, num_comments, comment_score

    Returns list of dicts: {id, sentiment, specificity, community}
    """
    results = []
    for row in unscored_rows:
        raw_quote = row.get("raw_quote") or ""
        sent = sentiment_score(raw_quote)
        spec = specificity_score(raw_quote)

        is_comment = row.get("comment_id") is not None
        comm = community_score(
            is_comment_sourced=is_comment,
            post_score=row.get("post_score") or 0,
            num_comments=row.get("num_comments") or 0,
            comment_score=row.get("comment_score") or 0,
        )

        results.append({
            "id": row["id"],
            "sentiment": sent,
            "specificity": spec,
            "community": comm,
        })

    return results
