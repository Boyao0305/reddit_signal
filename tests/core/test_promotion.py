"""Case table for the monthly promotion check. History rows are real (production, 2026-10-10)."""
import pytest

from reddit_signal.core.promotion import RunStats, decide_promotion


def run(run_id, clusters, candidates, members, **overrides):
    complete = dict(
        audience_rows=clusters,
        quality_rows=clusters,
        viability_attempted=max(candidates, 1),
        viability_succeeded=max(candidates, 1),
        has_weekly_volume=True,
    )
    complete.update(overrides)
    return RunStats(run_id=run_id, clusters=clusters, candidates=candidates, members=members, **complete)


# Promoted runs 9-17 as they exist in production (run 11 failed and was never promoted).
# members = clustering-time members (analysis_runs.total_points - noise_count).
HISTORY = {
    9: run(9, 1542, 691, 150_800),
    10: run(10, 2594, 1071, 270_502),
    12: run(12, 1707, 612, 170_745),
    13: run(13, 1519, 1160, 157_843),
    14: run(14, 1291, 937, 131_713),
    15: run(15, 683, 362, 62_649),
    16: run(16, 672, 333, 56_231),
    17: run(17, 33, 1, 2_017),
}


def promoted_before(run_id):
    return [HISTORY[r] for r in sorted(HISTORY, reverse=True) if r < run_id]


# --- Magnitude: replaying history, only the October collapse (run 17) is blocked ----------------
@pytest.mark.parametrize(
    ("run_id", "expected"),
    [
        (9, True),     # no earlier runs: magnitude cannot be judged, completeness passes
        (10, True),
        (12, True),
        (13, True),
        (14, True),
        (15, True),    # already degraded by the extraction backlog, but not a collapse
        (16, True),
        (17, False),   # 33 clusters / 1 candidate: the October collapse
    ],
)
def test_replaying_history_blocks_only_the_collapse(run_id, expected):
    decision = decide_promotion(HISTORY[run_id], promoted_before(run_id))
    assert decision.promote is expected, decision.blocked_by


def test_collapse_reports_every_metric():
    decision = decide_promotion(HISTORY[17], promoted_before(17))
    assert len(decision.blocked_by) == 3
    assert decision.reference == {"clusters": 683.0, "members": 62_649.0, "candidates": 362.0}


def test_a_bad_live_run_does_not_become_the_baseline():
    # Next month, with run 17 live: a second collapse is still blocked (median of 17, 16, 15),
    healthy = run(18, 650, 300, 55_000)
    collapsed = run(18, 40, 2, 2_500)
    assert decide_promotion(healthy, promoted_before(18)).promote
    assert not decide_promotion(collapsed, promoted_before(18)).promote


def test_force_overrides_magnitude_but_reports_it():
    decision = decide_promotion(HISTORY[17], promoted_before(17), force=True)
    assert decision.promote
    assert decision.blocked_by == ()
    assert len(decision.overridden) == 3


# --- Completeness: never overridable --------------------------------------------------------------
@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (dict(audience_rows=600), "audience classified for 600 of 672"),
        (dict(quality_rows=0), "quality metrics for 0 of 672"),
        (dict(viability_succeeded=0), "viability scoring produced no results"),
        (dict(viability_attempted=0), "viability scoring produced no results"),
        (dict(has_weekly_volume=False), "weekly volume not built"),
    ],
)
def test_incomplete_runs_are_blocked_even_with_force(overrides, reason):
    incomplete = run(16, 672, 333, 56_231, **overrides)
    decision = decide_promotion(incomplete, promoted_before(16), force=True)
    assert not decision.promote
    assert any(reason in r for r in decision.blocked_by)


def test_empty_run_is_blocked():
    decision = decide_promotion(run(18, 0, 0, 0), promoted_before(18), force=True)
    assert "run has no clusters" in decision.blocked_by


def test_the_run_itself_is_not_part_of_its_reference():
    decision = decide_promotion(HISTORY[17], [HISTORY[17], *promoted_before(17)])
    assert decision.reference["clusters"] == 683.0
