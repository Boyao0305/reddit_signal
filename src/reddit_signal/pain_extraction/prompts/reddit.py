"""Reddit prompt template.

Wraps the legacy ``reddit_signal.pain_extraction.extraction.build_prompt`` so behaviour
is byte-identical to v1. The only difference is the input shape: this function
takes an EventBundle and converts it into the legacy ``PostWithComments`` view.
"""
from __future__ import annotations

from reddit_signal.pain_extraction.models import PostWithComments
from reddit_signal.sources.base import EventBundle


def bundle_to_postwithcomments(bundle: EventBundle) -> PostWithComments:
    """Convert an EventBundle (parent post + child comments) into the legacy
    PostWithComments dataclass that `build_prompt`/`parse_pain_json` expect."""
    p = bundle.parent
    meta = p.raw_meta or {}
    return PostWithComments(
        post_db_id=int(p.source_row_id),
        post_reddit_id=meta.get("reddit_id", ""),
        subreddit_name=p.surface or "",
        title=p.title or "",
        selftext=p.text or "",
        author=p.author or "[deleted]",
        score=int(meta.get("score") or 0),
        num_comments=int(meta.get("num_comments") or len(bundle.children)),
        post_created=p.event_time,
        comments=[
            {
                "reddit_id":   (c.raw_meta or {}).get("reddit_id", ""),
                "id":          int(c.source_row_id),
                "db_id":       int(c.source_row_id),
                "author":      c.author or "[deleted]",
                "body":        c.text or "",
                "score":       int((c.raw_meta or {}).get("score") or 0),
                "depth":       int((c.raw_meta or {}).get("depth") or 0),
                "created_utc": c.event_time,
            }
            for c in bundle.children
        ],
        source_name=p.source,
        source_table=p.source_table,
        source_row_id=p.source_row_id,
    )


def build_reddit_prompt(bundle: EventBundle) -> str:
    """Build the Reddit pain-extraction user prompt from an EventBundle.

    Delegates to the v1 ``build_prompt`` so that the prompt text is identical
    to the legacy path — preserving extraction behaviour during the transition.
    """
    from reddit_signal.pain_extraction.extraction import build_prompt as v1_build_prompt
    return v1_build_prompt(bundle_to_postwithcomments(bundle))
