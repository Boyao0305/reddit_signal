"""Orchestration tests with fake steps: order of stages, failure handling, promotion wiring."""
from datetime import datetime, timedelta, timezone

import pytest

from reddit_signal.core.promotion import RunStats
from reddit_signal.schemas import DailySettings
from reddit_signal.worker.pipelines import (
    PromotionBlocked,
    extraction_plan_from_params,
    run_daily,
    run_extraction,
    run_monthly,
)

NOW = datetime(2026, 10, 11, 4, 5, tzinfo=timezone.utc)
SETTINGS = DailySettings(extraction_limit=5000, recency_days=14, maturity_days=2, assign_threshold=0.70)


class FakeStages:
    def __init__(self):
        self.stages, self.notes = [], []

    def run(self, order, name, fn):
        self.stages.append((order, name))
        return fn()

    def note(self, message, *, event_type="log", details=None):
        self.notes.append((event_type, message))


def stats(run_id, clusters, candidates, members):
    return RunStats(run_id, clusters, candidates, members, clusters, clusters, max(candidates, 1),
                    max(candidates, 1), True)


class FakeSteps:
    """Records every call; individual steps can be made to fail."""

    def __init__(self, *, fail=None, live=17, new_run_stats=None, history=None):
        self.calls, self.fail, self.live = [], fail or set(), live
        self.new_run_stats = new_run_stats or stats(18, 650, 300, 55_000)
        self.history = history if history is not None else [
            stats(17, 33, 1, 2_017), stats(16, 672, 333, 56_231), stats(15, 683, 362, 62_649)]

    def _call(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if name in self.fail:
            raise RuntimeError(f"{name} exploded")

    # daily
    def daily_settings(self): return SETTINGS
    def fetch_sources(self): self._call("fetch"); return {"fetched": 10}
    def project_sources(self): self._call("project"); return {"projected": 10}
    def check_extraction_model(self): self._call("check_model")
    def extract(self, plan, *, workers=None): self._call("extract", plan, workers=workers)
    def live_run_id(self): self._call("live_run_id"); return self.live
    def assign_new_pains(self, run_id, *, since, threshold): self._call("assign", run_id, since=since, threshold=threshold); return {}
    def sync_member_counts(self): self._call("sync"); return 0
    def aggregate(self, run_id, mode): self._call("aggregate", run_id, mode)
    def emerging_opportunities(self, run_id): self._call("emerging", run_id); return {}
    # monthly
    def live_run_id_or_none(self): return self.live
    def build_rolling_run(self, *, now, settings): self._call("build"); return 18
    def mark_building(self, run_id): self._call("mark_building", run_id)
    def mark_failed(self, run_id): self._call("mark_failed", run_id)
    def label_clusters(self, run_id, *, workers): self._call("label", run_id)
    def classify_audience(self, run_id, *, workers): self._call("audience", run_id)
    def compute_quality_metrics(self, run_id): self._call("quality", run_id)
    def apply_candidate_gate(self, run_id): self._call("gate", run_id); return {}
    def score_viability(self, run_id): self._call("viability", run_id); return 11
    def compute_lineage(self, parent, child): self._call("lineage", parent, child); return {}
    def calibrate_assignment(self, run_id): self._call("calibrate", run_id); return {"suggested_threshold": 0.71}
    def run_stats(self, run_id): return self.new_run_stats
    def recent_promoted_stats(self, *, before_run_id): return self.history
    def promote(self, run_id): self._call("promote", run_id)

    def names(self):
        return [c[0] for c in self.calls]


# --- daily -----------------------------------------------------------------------------------------

def test_daily_runs_every_stage_in_order():
    steps, stages = FakeSteps(), FakeStages()
    run_daily(steps, stages, now=NOW)
    assert steps.names() == ["fetch", "project", "check_model", "extract", "live_run_id", "assign", "sync",
                             "aggregate", "emerging"]
    assert [o for o, _ in stages.stages] == [1, 2, 3, 4, 5, 6, 7]


def test_daily_extracts_mature_recent_posts_newest_first():
    steps = FakeSteps()
    run_daily(steps, FakeStages(), now=NOW)
    plan = next(c[1][0] for c in steps.calls if c[0] == "extract")
    assert (plan.window_start, plan.window_end, plan.limit) == (NOW - timedelta(days=14), NOW - timedelta(days=2), 5000)


def test_daily_assigns_to_the_live_run_at_the_configured_threshold():
    steps = FakeSteps(live=17)
    run_daily(steps, FakeStages(), now=NOW)
    assign = next(c for c in steps.calls if c[0] == "assign")
    assert assign[1] == (17,)
    assert assign[2] == {"since": NOW - timedelta(hours=36), "threshold": 0.70}


def test_daily_stops_at_the_first_failing_stage():
    steps = FakeSteps(fail={"extract"})
    with pytest.raises(RuntimeError, match="extract exploded"):
        run_daily(steps, FakeStages(), now=NOW)
    assert "assign" not in steps.names()


def test_emerging_opportunities_failure_does_not_fail_the_daily():
    steps, stages = FakeSteps(fail={"emerging"}), FakeStages()
    run_daily(steps, stages, now=NOW)
    assert ("warning", "Emerging opportunities failed; daily continues: emerging exploded") in stages.notes


# --- monthly ---------------------------------------------------------------------------------------

def test_healthy_monthly_runs_all_stages_and_promotes():
    steps = FakeSteps()
    result = run_monthly(steps, FakeStages(), now=NOW)
    assert steps.names() == ["build", "mark_building", "label", "audience", "quality", "gate", "viability",
                             "aggregate", "lineage", "calibrate", "promote"]
    assert result == {"previous_run_id": 17, "new_run_id": 18, "viability_run_id": 11}


def test_collapsed_monthly_is_not_promoted_and_is_marked_failed():
    steps = FakeSteps(new_run_stats=stats(18, 33, 1, 2_017),
                      history=[stats(16, 672, 333, 56_231), stats(15, 683, 362, 62_649), stats(14, 1291, 937, 131_713)])
    with pytest.raises(PromotionBlocked) as err:
        run_monthly(steps, FakeStages(), now=NOW)
    assert "promote" not in steps.names()
    assert ("mark_failed", (18,), {}) in steps.calls
    assert len(err.value.decision.blocked_by) == 3


def test_force_promotes_a_collapse_and_records_what_was_overridden():
    steps, stages = FakeSteps(new_run_stats=stats(18, 33, 1, 2_017)), FakeStages()
    steps.history = [stats(16, 672, 333, 56_231), stats(15, 683, 362, 62_649), stats(14, 1291, 937, 131_713)]
    run_monthly(steps, stages, now=NOW, force_promote=True)
    assert "promote" in steps.names()
    assert sum(1 for kind, _ in stages.notes if kind == "warning") == 3


def test_a_failing_stage_marks_the_new_run_failed():
    steps = FakeSteps(fail={"viability"})
    with pytest.raises(RuntimeError, match="viability exploded"):
        run_monthly(steps, FakeStages(), now=NOW)
    assert steps.names()[-1] == "mark_failed"
    assert "promote" not in steps.names()


def test_failure_before_a_run_exists_marks_nothing():
    steps = FakeSteps(fail={"build"})
    with pytest.raises(RuntimeError):
        run_monthly(steps, FakeStages(), now=NOW)
    assert "mark_failed" not in steps.names()


def test_first_ever_monthly_skips_lineage():
    steps, stages = FakeSteps(live=None, history=[]), FakeStages()
    run_monthly(steps, stages, now=NOW)
    assert "lineage" not in steps.names()
    assert ("warning", "No previous live run: lineage skipped") in stages.notes


# --- extraction jobs -----------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("params", "expected"),
    [
        # Manual extraction without parameters uses the daily window and limit.
        ({}, ("daily", NOW - timedelta(days=14), NOW - timedelta(days=2), 5000)),
        ({"limit": 200}, ("daily", NOW - timedelta(days=14), NOW - timedelta(days=2), 200)),
        # Backlog: an explicit range with its own budget.
        ({"mode": "backlog", "since": "2026-07-01T00:00:00+00:00", "until": "2026-09-27T00:00:00+00:00",
          "limit": 50000},
         ("backlog", datetime(2026, 7, 1, tzinfo=timezone.utc), datetime(2026, 9, 27, tzinfo=timezone.utc), 50000)),
        ({"mode": "backlog", "since": "2026-07-01T00:00:00+00:00", "until": "2026-09-27T00:00:00+00:00",
          "limit": None},
         ("backlog", datetime(2026, 7, 1, tzinfo=timezone.utc), datetime(2026, 9, 27, tzinfo=timezone.utc), None)),
    ],
)
def test_extraction_plan_from_job_params(params, expected):
    plan = extraction_plan_from_params(params, now=NOW, settings=SETTINGS)
    assert (plan.mode, plan.window_start, plan.window_end, plan.limit) == expected


def test_extraction_job_passes_workers_through():
    steps = FakeSteps()
    run_extraction(steps, FakeStages(), now=NOW, params={"workers": 8})
    extract = next(c for c in steps.calls if c[0] == "extract")
    assert extract[2] == {"workers": 8}
