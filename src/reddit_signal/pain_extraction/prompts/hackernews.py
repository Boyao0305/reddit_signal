"""Hacker News prompt converter for signal_events bundles."""
from __future__ import annotations

from reddit_signal.pain_extraction.models import PostWithComments
from reddit_signal.sources.base import EventBundle


def bundle_to_postwithcomments(bundle: EventBundle) -> PostWithComments:
    parent = bundle.parent
    meta = parent.raw_meta or {}
    return PostWithComments(
        post_db_id=None,
        post_reddit_id=str(meta.get("hn_id") or parent.source_row_id),
        subreddit_name=parent.surface or "hn",
        title=parent.title or "",
        selftext=parent.text or "",
        author=parent.author or "[deleted]",
        score=int(parent.engagement_score or 0),
        num_comments=len(bundle.children),
        post_created=parent.event_time,
        comments=[
            {
                "reddit_id": str((child.raw_meta or {}).get("hn_id") or child.source_row_id),
                "id": None,
                "db_id": None,
                "author": child.author or "[deleted]",
                "body": child.text or "",
                "score": int(child.engagement_score or 0),
                "depth": int((child.raw_meta or {}).get("depth") or 0),
                "created_utc": child.event_time,
                "signal_event_id": child.id,
            }
            for child in bundle.children
        ],
        source_name=parent.source,
        source_table=parent.source_table,
        source_row_id=parent.source_row_id,
        signal_event_id=parent.id,
    )


def build_hackernews_prompt(bundle: EventBundle) -> str:
    from reddit_signal.pain_extraction.extraction import build_prompt
    return build_prompt(bundle_to_postwithcomments(bundle))
