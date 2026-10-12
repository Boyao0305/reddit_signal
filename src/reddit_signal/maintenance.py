"""Operations shared by the worker and the CLI: stale-job recovery and dashboard snapshot refresh."""
from __future__ import annotations

import logging
from datetime import datetime

from reddit_signal import pipeline_jobs
from reddit_signal.core import job_recovery

logger = logging.getLogger(__name__)

ALL_JOB_TYPES = ("daily", "monthly", "extraction")


def recover_stale_jobs(job_types: tuple[str, ...] = ALL_JOB_TYPES, *, now: datetime) -> list[int]:
    """Mark ``running`` jobs whose worker is gone as failed; returns their ids."""
    running = [
        job_recovery.RunningJob(r["id"], r["worker_id"], r["started_at"])
        for r in pipeline_jobs.running_jobs(job_types)
    ]
    if not running:
        return []
    heartbeats = [
        job_recovery.Heartbeat(h["worker_id"], h["last_seen_at"], h["current_job_id"])
        for h in pipeline_jobs.all_heartbeats()
    ]
    recovered = []
    for stale in job_recovery.find_stale_jobs(running, heartbeats, now=now):
        if pipeline_jobs.fail_stale_job(stale.job_id, reason=stale.reason):
            logger.warning("recovered stale job %d: %s", stale.job_id, stale.reason)
            recovered.append(stale.job_id)
    return recovered


def refresh_snapshots(*, reason: str, all_targets: bool) -> None:
    from reddit_signal.dashboard.snapshot import refresh_market_snapshot
    from reddit_signal.repositories import analysis_runs

    targets = analysis_runs.snapshot_targets()
    if not all_targets:
        targets = targets[:1]
    for cluster_run_id, viability_run_id in targets:
        try:
            result = refresh_market_snapshot(cluster_run_id, viability_run_id, "v1")
            logger.info("snapshot %s run=%d viability=%d rows=%s", reason, cluster_run_id, viability_run_id,
                        result.get("refreshed_rows"))
        except Exception as exc:  # noqa: BLE001 - one target must not block the others
            logger.error("snapshot refresh failed run=%d viability=%d: %s", cluster_run_id, viability_run_id, exc)
