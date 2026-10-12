"""The daily, monthly and extraction pipelines (orchestration only).

Replaces ``scripts/b_ops_1_daily.sh`` and ``scripts/b_ops_3_monthly.sh``. Each pipeline receives what it
needs through the Protocols below; ``worker/main.py`` wires the real implementations
(``reddit_signal.pipeline_steps``), tests wire fakes. Decisions come from ``core``.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Protocol, TypeVar

from reddit_signal.core.extraction_policy import ExtractionPlan, plan_backlog, plan_daily
from reddit_signal.core.promotion import PromotionDecision, RunStats, decide_promotion
from reddit_signal.schemas import DailySettings, MonthlySettings

T = TypeVar("T")

ASSIGN_LOOKBACK = timedelta(hours=36)


class Stages(Protocol):
    def run(self, order: int, name: str, fn: Callable[[], T]) -> T: ...
    def note(self, message: str, *, event_type: str = "log", details: dict[str, Any] | None = None) -> None: ...


# --- daily -----------------------------------------------------------------------------------------

class DailyDeps(Protocol):
    def daily_settings(self) -> DailySettings: ...
    def fetch_sources(self) -> dict[str, Any]: ...
    def project_sources(self) -> dict[str, Any]: ...
    def check_extraction_model(self) -> None: ...
    def extract(self, plan: ExtractionPlan) -> None: ...
    def live_run_id(self) -> int: ...
    def assign_new_pains(self, run_id: int, *, since: datetime, threshold: float) -> dict[str, Any]: ...
    def sync_member_counts(self) -> int: ...
    def aggregate(self, run_id: int, mode: str) -> None: ...
    def emerging_opportunities(self, run_id: int) -> dict[str, Any]: ...


def run_daily(deps: DailyDeps, stages: Stages, *, now: datetime) -> dict[str, Any]:
    settings = deps.daily_settings()
    plan = plan_daily(
        now,
        recency_days=settings.recency_days,
        maturity_days=settings.maturity_days,
        limit=settings.extraction_limit,
    )
    stages.note(
        f"Extraction window {plan.window_start:%Y-%m-%d %H:%M} .. {plan.window_end:%Y-%m-%d %H:%M} UTC, "
        f"newest first, limit {plan.limit if plan.limit is not None else 'none'}",
        details={"window_start": plan.window_start.isoformat(), "window_end": plan.window_end.isoformat(),
                 "limit": plan.limit},
    )
    fetched = stages.run(1, "Fetch enabled sources", deps.fetch_sources)
    stages.run(2, "Project new rows to signal_events", deps.project_sources)

    def extract() -> None:
        deps.check_extraction_model()
        deps.extract(plan)

    stages.run(3, "Extract, score and embed", extract)
    run_id = deps.live_run_id()
    stages.run(
        4,
        f"Assign new pains to live run {run_id}",
        lambda: deps.assign_new_pains(run_id, since=now - ASSIGN_LOOKBACK, threshold=settings.assign_threshold),
    )
    stages.run(5, "Sync cluster member counts", deps.sync_member_counts)
    stages.run(6, "Aggregate weekly and daily volumes", lambda: deps.aggregate(run_id, "incremental"))
    try:
        stages.run(7, "Build emerging opportunities", lambda: deps.emerging_opportunities(run_id))
    except Exception as exc:  # noqa: BLE001 - advisory output; must not fail the daily
        stages.note(f"Emerging opportunities failed; daily continues: {exc}", event_type="warning")
    return {"live_run_id": run_id, "fetch": fetched, "plan": plan}


# --- monthly ---------------------------------------------------------------------------------------

class MonthlyDeps(Protocol):
    def live_run_id_or_none(self) -> int | None: ...
    def build_rolling_run(self, *, now: datetime, settings: MonthlySettings) -> int: ...
    def mark_building(self, run_id: int) -> None: ...
    def mark_failed(self, run_id: int) -> None: ...
    def label_clusters(self, run_id: int, *, workers: int) -> None: ...
    def classify_audience(self, run_id: int, *, workers: int) -> None: ...
    def compute_quality_metrics(self, run_id: int) -> None: ...
    def apply_candidate_gate(self, run_id: int) -> dict[str, Any]: ...
    def score_viability(self, run_id: int) -> int: ...
    def aggregate(self, run_id: int, mode: str) -> None: ...
    def compute_lineage(self, parent_run_id: int, child_run_id: int) -> dict[str, Any]: ...
    def calibrate_assignment(self, run_id: int) -> dict[str, Any]: ...
    def run_stats(self, run_id: int) -> RunStats: ...
    def recent_promoted_stats(self, *, before_run_id: int) -> list[RunStats]: ...
    def promote(self, run_id: int) -> None: ...


class PromotionBlocked(RuntimeError):
    def __init__(self, run_id: int, decision: PromotionDecision) -> None:
        self.run_id = run_id
        self.decision = decision
        super().__init__(f"run {run_id} not promoted: " + "; ".join(decision.blocked_by))


def run_monthly(
    deps: MonthlyDeps,
    stages: Stages,
    *,
    now: datetime,
    settings: MonthlySettings = MonthlySettings(),
    force_promote: bool = False,
) -> dict[str, Any]:
    previous = deps.live_run_id_or_none()
    new_run: int | None = None
    try:
        new_run = stages.run(
            1, f"Build {settings.window_days}-day rolling cluster run",
            lambda: deps.build_rolling_run(now=now, settings=settings),
        )
        deps.mark_building(new_run)
        run_id = new_run
        stages.run(2, "Label clusters", lambda: deps.label_clusters(run_id, workers=settings.label_workers))
        stages.run(3, "Classify audience", lambda: deps.classify_audience(run_id, workers=settings.audience_workers))
        stages.run(4, "Compute quality metrics", lambda: deps.compute_quality_metrics(run_id))
        stages.run(5, "Calibrate and apply candidate gate", lambda: deps.apply_candidate_gate(run_id))
        viability_run = stages.run(6, "Score viability", lambda: deps.score_viability(run_id))
        stages.run(7, "Rebuild weekly and daily volumes", lambda: deps.aggregate(run_id, "rebuild"))
        if previous is None:
            stages.note("No previous live run: lineage skipped", event_type="warning")
        else:
            stages.run(8, f"Compute lineage {previous} -> {run_id}", lambda: deps.compute_lineage(previous, run_id))
        calibration = stages.run(9, "Calibrate centroid-assignment threshold (reported, not applied)",
                                 lambda: deps.calibrate_assignment(run_id))
        stages.note(f"Suggested assignment threshold: {calibration.get('suggested_threshold')}",
                    details=calibration)

        def check_and_promote() -> PromotionDecision:
            decision = decide_promotion(
                deps.run_stats(run_id),
                deps.recent_promoted_stats(before_run_id=run_id),
                force=force_promote,
            )
            for reason in decision.overridden:
                stages.note(f"Overridden by force: {reason}", event_type="warning")
            if not decision.promote:
                raise PromotionBlocked(run_id, decision)
            deps.promote(run_id)
            return decision

        stages.run(10, "Check and promote run", check_and_promote)
    except BaseException:
        if new_run is not None:
            deps.mark_failed(new_run)
        raise
    return {"previous_run_id": previous, "new_run_id": new_run, "viability_run_id": viability_run}


# --- extraction (manual or backlog) ------------------------------------------------------------------

class ExtractionDeps(Protocol):
    def daily_settings(self) -> DailySettings: ...
    def check_extraction_model(self) -> None: ...
    def extract(self, plan: ExtractionPlan, *, workers: int | None = None) -> None: ...


def extraction_plan_from_params(params: dict[str, Any], *, now: datetime, settings: DailySettings) -> ExtractionPlan:
    """``{"mode": "backlog", "since": ISO, "until": ISO, "limit": N}`` or the daily window (default)."""
    limit = params.get("limit", settings.extraction_limit)
    limit = None if limit in (None, "") else int(limit)
    if params.get("mode") == "backlog":
        return plan_backlog(
            since=datetime.fromisoformat(str(params["since"])),
            until=datetime.fromisoformat(str(params["until"])),
            limit=limit,
        )
    return plan_daily(now, recency_days=settings.recency_days, maturity_days=settings.maturity_days, limit=limit)


def run_extraction(deps: ExtractionDeps, stages: Stages, *, now: datetime, params: dict[str, Any]) -> ExtractionPlan:
    plan = extraction_plan_from_params(params, now=now, settings=deps.daily_settings())
    workers = params.get("workers")
    stages.note(
        f"{plan.mode} extraction {plan.window_start:%Y-%m-%d} .. {plan.window_end:%Y-%m-%d}, newest first, "
        f"limit {plan.limit if plan.limit is not None else 'none'}",
        details={"mode": plan.mode, "limit": plan.limit, "workers": workers},
    )

    def extract() -> None:
        deps.check_extraction_model()
        deps.extract(plan, workers=None if workers is None else int(workers))

    stages.run(1, "Extract, score and embed", extract)
    return plan
