"""Shared prompt converter for SocialCrawl-backed sources."""
from __future__ import annotations

from reddit_signal.pain_extraction.models import PostWithComments
from reddit_signal.sources.base import EventBundle


def bundle_to_postwithcomments_for_source(bundle: EventBundle) -> PostWithComments:
    parent = bundle.parent
    return PostWithComments(
        post_db_id=None,
        post_reddit_id=parent.source_row_id,
        subreddit_name=parent.surface or parent.source,
        title=parent.title or "",
        selftext=parent.text or "",
        author=parent.author or "[deleted]",
        score=int(parent.engagement_score or 0),
        num_comments=len(bundle.children),
        post_created=parent.event_time,
        comments=[
            {
                "reddit_id": child.source_row_id,
                "id": None,
                "db_id": None,
                "author": child.author or "[deleted]",
                "body": child.text or "",
                "score": int(child.engagement_score or 0),
                "depth": 0,
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
