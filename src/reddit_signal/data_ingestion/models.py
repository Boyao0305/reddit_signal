"""
Lightweight dataclass models for the Reddit pipeline.

These are plain Python dataclasses — no ORM dependency.
They act as typed containers between the fetcher and the DB layer.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class Subreddit:
    name: str
    display_name: Optional[str] = None
    subscribers: Optional[int] = None
    description: Optional[str] = None


@dataclass
class Post:
    reddit_id: str          # e.g. '1i80djz'
    subreddit: str          # subreddit name (resolved to subreddit_id on insert)
    title: str
    selftext: Optional[str] = None
    author: Optional[str] = None
    score: int = 0
    upvote_ratio: Optional[float] = None
    num_comments: int = 0
    permalink: Optional[str] = None
    url: Optional[str] = None
    link_flair_text: Optional[str] = None
    is_self: bool = True
    over_18: bool = False
    created_utc: Optional[datetime] = None
    raw_json: Optional[dict] = field(default=None, repr=False)


@dataclass
class Comment:
    reddit_id: str
    post_reddit_id: str     # the parent post's reddit_id (resolved to post_id on insert)
    parent_reddit_id: Optional[str] = None   # t1_xxx or t3_xxx
    author: Optional[str] = None
    body: Optional[str] = None
    score: int = 0
    is_submitter: bool = False
    depth: int = 0
    created_utc: Optional[datetime] = None
    raw_json: Optional[dict] = field(default=None, repr=False)
