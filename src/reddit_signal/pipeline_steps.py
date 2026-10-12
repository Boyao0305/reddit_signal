"""Real implementations of the pipeline Protocols in ``reddit_signal.worker.pipelines``.

Each method is a thin call into a domain module. Settings come from ``system_settings`` through
``runtime_settings``; defaults match the previous Bash scripts.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from reddit_signal import runtime_settings
from reddit_signal.clustering import (
    assign_to_centroid,
    audience_classifier,
    candidate_gate,
    lineage,
    quality_metrics,
    quality_viability_llm,
    rolling,
)
from reddit_signal.core.extraction_policy import DEFAULT_MATURITY_DAYS, DEFAULT_RECENCY_DAYS, ExtractionPlan
from reddit_signal.core.promotion import RunStats
from reddit_signal.emerging_opportunities.runner import run_emerging_opportunities
from reddit_signal.job_recorder import JobRecorder
from reddit_signal.pain_extraction import extraction, labeling
from reddit_signal.pain_extraction import main as pain_extraction
from reddit_signal.repositories import analysis_runs
from reddit_signal.schemas import DailySettings, MonthlySettings
from reddit_signal.sources import daily as sources_daily
from reddit_signal.sources.runtime_config import enabled_sources_for_extract
from reddit_signal.temporal import aggregate_daily, aggregate_daily_subreddit, aggregate_weekly

# The daily has always assigned at 0.70 (LD-#23), not the module default of 0.55.
DEFAULT_ASSIGN_THRESHOLD = 0.70
DEFAULT_DAILY_EXTRACTION_LIMIT = 1000
DEFAULT_EXTRACTION_WORKERS = 20


def _int_setting(key: str, default: int) -> int:
    raw = runtime_settings.get_setting(key, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _float_setting(key: str, default: float) -> float:
    raw = runtime_settings.get_setting(key, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


class PipelineSteps:
    def __init__(self, recorder: JobRecorder) -> None:
        self._note = recorder.note

    # --- settings ---------------------------------------------------------------------------------
    def daily_settings(self) -> DailySettings:
        limit = _int_setting("DAILY_EXTRACTION_LIMIT", DEFAULT_DAILY_EXTRACTION_LIMIT)
        return DailySettings(
            extraction_limit=None if limit <= 0 else limit,          # 0 has always meant "no cap"
            recency_days=_int_setting("EXTRACTION_RECENCY_DAYS", DEFAULT_RECENCY_DAYS),
            maturity_days=_int_setting("EXTRACTION_MATURITY_DAYS", DEFAULT_MATURITY_DAYS),
            assign_threshold=_float_setting("ASSIGN_THRESHOLD", DEFAULT_ASSIGN_THRESHOLD),
        )

    # --- daily ------------------------------------------------------------------------------------
    def fetch_sources(self) -> dict[str, Any]:
        return sources_daily.fetch_enabled_sources(note=self._note)

    def project_sources(self) -> dict[str, Any]:
        return sources_daily.project_enabled_sources(note=self._note)

    def check_extraction_model(self) -> None:
        extraction.smoke_check_model()

    def extract(self, plan: ExtractionPlan, *, workers: int | None = None) -> None:
        pain_extraction.cmd_ingest(
            limit=plan.limit,
            workers=workers or DEFAULT_EXTRACTION_WORKERS,
            sources=enabled_sources_for_extract(),
            limit_is_total=True,
            window=(plan.window_start, plan.window_end),
        )

    def live_run_id(self) -> int:
        return analysis_runs.live_run_id()

    def assign_new_pains(self, run_id: int, *, since: datetime, threshold: float) -> dict[str, Any]:
        return assign_to_centroid.assign_to_centroid(run_id, threshold=threshold, since=since.isoformat())

    def sync_member_counts(self) -> int:
        return analysis_runs.sync_member_counts()

    def aggregate(self, run_id: int, mode: str) -> None:
        aggregate_weekly.aggregate(run_id, mode)
        aggregate_daily.aggregate(run_id, mode)
        aggregate_daily_subreddit.aggregate(run_id, mode)
        aggregate_daily.aggregate_corpus(run_id, mode)

    def emerging_opportunities(self, run_id: int) -> dict[str, Any]:
        return run_emerging_opportunities(cluster_run_id=run_id, lookback_days=7, min_size=8)

    # --- monthly ----------------------------------------------------------------------------------
    def live_run_id_or_none(self) -> int | None:
        try:
            return analysis_runs.live_run_id()
        except RuntimeError:
            return None

    def build_rolling_run(self, *, now: datetime, settings: MonthlySettings) -> int:
        return rolling.build_rolling_run(
            now=now,
            window_days=settings.window_days,
            min_cluster_size=settings.min_cluster_size,
            min_samples=settings.min_samples,
        )

    def mark_building(self, run_id: int) -> None:
        analysis_runs.mark_building(run_id)

    def mark_failed(self, run_id: int) -> None:
        analysis_runs.mark_failed(run_id)

    def label_clusters(self, run_id: int, *, workers: int) -> None:
        labeling.label_all_clusters(max_workers=workers, run_id=run_id)

    def classify_audience(self, run_id: int, *, workers: int) -> None:
        audience_classifier.classify_run(run_id, workers=workers)

    def compute_quality_metrics(self, run_id: int) -> None:
        quality_metrics.compute_run(run_id)

    def apply_candidate_gate(self, run_id: int) -> dict[str, Any]:
        return candidate_gate.calibrate_and_apply(run_id)

    def score_viability(self, run_id: int) -> int:
        result = quality_viability_llm.score_run(run_id)
        viability_run_id = result.get("viability_run_id") or analysis_runs.latest_viability_run_id(run_id)
        if viability_run_id is None:
            raise RuntimeError(f"viability scoring produced no viability run for cluster run {run_id}")
        return int(viability_run_id)

    def compute_lineage(self, parent_run_id: int, child_run_id: int) -> dict[str, Any]:
        return lineage.compute_lineage(parent_run_id, child_run_id)

    def calibrate_assignment(self, run_id: int) -> dict[str, Any]:
        return assign_to_centroid.calibrate(run_id)

    def run_stats(self, run_id: int) -> RunStats:
        return analysis_runs.run_stats(run_id)

    def recent_promoted_stats(self, *, before_run_id: int) -> list[RunStats]:
        return analysis_runs.recent_promoted_stats(before_run_id=before_run_id)

    def promote(self, run_id: int) -> None:
        analysis_runs.promote(run_id)
