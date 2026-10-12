"""Find jobs marked ``running`` whose worker is gone.

A worker heartbeats every few seconds while idle *and while a job runs*, and reports the job it is
running. A ``running`` job is stale when its worker has not been seen within the grace period, or is
alive but reports a different job (it restarted and lost track of this one).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

DEFAULT_GRACE = timedelta(minutes=5)


@dataclass(frozen=True)
class RunningJob:
    job_id: int
    worker_id: str | None
    started_at: datetime | None


@dataclass(frozen=True)
class Heartbeat:
    worker_id: str
    last_seen_at: datetime
    current_job_id: int | None


@dataclass(frozen=True)
class StaleJob:
    job_id: int
    reason: str


def find_stale_jobs(
    running: list[RunningJob],
    heartbeats: list[Heartbeat],
    *,
    now: datetime,
    grace: timedelta = DEFAULT_GRACE,
) -> list[StaleJob]:
    by_worker = {hb.worker_id: hb for hb in heartbeats}
    stale = []
    for job in running:
        hb = by_worker.get(job.worker_id or "")
        if hb is None:
            # Just-claimed jobs get the grace period before the missing heartbeat counts.
            if job.started_at is None or now - job.started_at > grace:
                stale.append(StaleJob(job.job_id, f"worker {job.worker_id!r} has never sent a heartbeat"))
        elif now - hb.last_seen_at > grace:
            age = int((now - hb.last_seen_at).total_seconds())
            stale.append(StaleJob(job.job_id, f"worker {job.worker_id} last seen {age}s ago"))
        elif hb.current_job_id != job.job_id:
            stale.append(
                StaleJob(job.job_id, f"worker {job.worker_id} is alive but running job {hb.current_job_id}")
            )
    return stale
