"""Shared vocabulary: data shapes that cross a component or container boundary (CONVENTIONS.md §6).

Imports nothing from the package. Data only.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DailySettings(BaseModel):
    """Operator settings for the daily pipeline (from ``system_settings``)."""

    model_config = ConfigDict(frozen=True)

    extraction_limit: int | None = Field(description="posts per day across sources; None = no cap")
    recency_days: int
    maturity_days: int
    assign_threshold: float


class MonthlySettings(BaseModel):
    """Parameters of the monthly re-cluster."""

    model_config = ConfigDict(frozen=True)

    window_days: int = 90
    min_cluster_size: int = 30
    min_samples: int = 10
    label_workers: int = 4
    audience_workers: int = 6
