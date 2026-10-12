from datetime import datetime, timedelta, timezone

import pytest

from reddit_signal.core.job_recovery import Heartbeat, RunningJob, find_stale_jobs

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def ago(**kw):
    return NOW - timedelta(**kw)


# Each row: running job, heartbeats, expected reason fragment (None = not stale).
CASES = [
    # Healthy: worker heartbeats while running this very job.
    (RunningJob(1, "w1", ago(hours=2)), [Heartbeat("w1", ago(seconds=20), 1)], None),
    # The October 9 case: the database (and worker) died mid-job; heartbeat frozen for hours.
    (RunningJob(108, "w1", ago(hours=30)), [Heartbeat("w1", ago(hours=30), 108)], "last seen"),
    # Worker restarted and is now idle or on another job: it lost this one.
    (RunningJob(2, "w1", ago(hours=1)), [Heartbeat("w1", ago(seconds=5), None)], "is alive but running job None"),
    (RunningJob(3, "w1", ago(hours=1)), [Heartbeat("w1", ago(seconds=5), 9)], "running job 9"),
    # Never heartbeated: grace period first, then stale.
    (RunningJob(4, "w2", ago(minutes=1)), [], None),
    (RunningJob(5, "w2", ago(minutes=30)), [], "never sent a heartbeat"),
    (RunningJob(6, None, None), [], "never sent a heartbeat"),
]


@pytest.mark.parametrize(("job", "heartbeats", "reason"), CASES)
def test_find_stale_jobs(job, heartbeats, reason):
    stale = find_stale_jobs([job], heartbeats, now=NOW)
    if reason is None:
        assert stale == []
    else:
        assert len(stale) == 1 and stale[0].job_id == job.job_id
        assert reason in stale[0].reason
