"""
Base interfaces and dataclasses for the adapter framework.

A SignalEvent is the unified, source-agnostic event record.
An EventBundle is a parent event + its children (e.g. a post + its comments).
A BaseAdapter is the contract every source plug-in implements.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class AdapterCapabilities:
    """What kind of signals an adapter can produce."""
    produces_pain: bool = False           # post/comment/review style content
    produces_competitor: bool = False     # product listings / launches
    produces_traction: bool = False       # stars, installs, votes
    produces_temporal: bool = False       # has reliable event_time

    def __or__(self, other: "AdapterCapabilities") -> "AdapterCapabilities":
        return AdapterCapabilities(
            produces_pain=self.produces_pain or other.produces_pain,
            produces_competitor=self.produces_competitor or other.produces_competitor,
            produces_traction=self.produces_traction or other.produces_traction,
            produces_temporal=self.produces_temporal or other.produces_temporal,
        )


@dataclass(frozen=True)
class AdapterMeta:
    """Structured metadata about a registered adapter, surfaced to the UI."""
    name: str
    display_name: str
    description: str
    source_file: str
    capabilities: AdapterCapabilities
    prompt_registered: bool = False


@dataclass
class SignalEvent:
    """One row from signal_events. ``id`` is set after upsert."""
    source: str                                 # 'reddit', 'hackernews', ...
    source_table: str                           # 'posts', 'comments', 'g2_reviews'
    source_row_id: str                          # L1 PK as string

    event_type: str                             # 'post' | 'comment' | 'review' | ...
    event_time: datetime

    surface: Optional[str] = None
    author: Optional[str] = None

    title: Optional[str] = None
    text: Optional[str] = None
    engagement_score: Optional[float] = None

    parent_event_id: Optional[int] = None
    raw_meta: dict = field(default_factory=dict)

    # Populated after DB upsert
    id: Optional[int] = None


@dataclass
class EventBundle:
    """Parent event + child events. Generic replacement for PostWithComments."""
    parent: SignalEvent
    children: list[SignalEvent] = field(default_factory=list)

    @property
    def source(self) -> str:
        return self.parent.source


class BaseAdapter(ABC):
    """Contract for a data source adapter.

    Subclasses must set the ``name`` and ``capabilities`` class attributes
    and implement ``fetch``, ``project``, ``build_pain_prompt``, and
    ``community_score``.

    Optionally set ``display_name`` (human-readable) and ``description``
    (one-line explanation) for the dashboard UI.
    """

    name: str = ""
    display_name: str = ""
    description: str = ""
    capabilities: AdapterCapabilities = AdapterCapabilities()

    @classmethod
    def metadata(cls) -> AdapterMeta:
        import inspect
        try:
            src_file = inspect.getfile(cls)
        except TypeError:
            src_file = "<unknown>"
        return AdapterMeta(
            name=cls.name,
            display_name=cls.display_name or cls.name,
            description=cls.description,
            source_file=src_file,
            capabilities=cls.capabilities,
            prompt_registered=False,
        )

    @abstractmethod
    def fetch(
        self,
        query: str = "",
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 10_000,
    ) -> int:
        """Pull from the upstream API into the L1 raw table.

        Must be idempotent. Returns number of rows written/updated.
        """

    @abstractmethod
    def project(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> int:
        """Transform L1 rows into ``signal_events`` (L2). Idempotent.

        Returns number of events upserted.
        """

    def fetch_and_project(
        self,
        query: str = "",
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 10_000,
    ) -> tuple[int, int]:
        n_fetched = self.fetch(query, since, until, limit)
        n_projected = self.project(since, until)
        return n_fetched, n_projected

    def simulate_fetch(
        self,
        query: str = "",
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        """Run a small upstream request and return sample rows without writes."""
        raise NotImplementedError(f"{self.name or self.__class__.__name__} does not support simulate_fetch")

    @abstractmethod
    def build_pain_prompt(self, bundle: EventBundle) -> str:
        """Per-source LLM prompt for pain extraction."""

    @abstractmethod
    def community_score(self, event: SignalEvent) -> float:
        """Per-source engagement formula. Must be deterministic."""
