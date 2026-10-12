"""
Dataclasses for Step 4 pain extraction pipeline.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class PainObject:
    """A single pain signal extracted from a post or comment.

    v2 changes: post_id/comment_id/subreddit_name are now optional (Reddit-only).
    Every PainObject ties to a signal_events row via ``signal_event_id``.
    """

    # LLM-extracted fields (mandatory — these are the actual content)
    pain_statement: str             # Normalized 1-2 sentence description
    raw_quote: str                  # Exact source text
    mentioned_tools: List[str]      # Products/services mentioned
    is_workaround: bool             # Whether this describes a workaround
    source_author: Optional[str]    # username/handle

    # Source references (Reddit-specific; nullable for other sources)
    post_id: Optional[int] = None
    comment_id: Optional[int] = None
    subreddit_name: Optional[str] = None

    # v2 generic linkage
    signal_event_id: Optional[int] = None
    prompt_version: str = "v1"

    # Computed scoring fields (filled by scoring.py)
    sentiment_score: Optional[float] = None   # VADER compound [-1, 1]
    specificity: Optional[float] = None       # Heuristic [0, 1]
    community_score: Optional[float] = None   # Upvote-weighted signal strength

    # Timestamps
    source_created: Optional[datetime] = None
    created_at: Optional[datetime] = None

    # Future fields (not used yet)
    embedding: None = None
    cluster_id: Optional[int] = None

    # DB id (set after insert)
    id: Optional[int] = None


@dataclass
class PostWithComments:
    """A Reddit post bundled with all its comments — unit of LLM extraction."""

    post_db_id: Optional[int]
    post_reddit_id: str
    subreddit_name: str
    title: str
    selftext: str
    author: str
    score: int
    num_comments: int
    post_created: datetime

    comments: List[dict] = field(default_factory=list)

    # Generic source provenance for non-Reddit signal_events.
    source_name: str = "reddit"
    source_table: Optional[str] = None
    source_row_id: Optional[str] = None
    signal_event_id: Optional[int] = None
