"""Worker container entry point.

``python -m reddit_signal.worker [--lane L]``
    Long-running loop: heartbeats, enqueues scheduled daily/monthly jobs, recovers jobs whose worker
    died, and runs queued work for its lane. Each job runs in a child process so memory is returned
    and a job can be cancelled by terminating its process group.

``python -m reddit_signal.worker run-job JOB_ID``
    Child process: runs one pipeline job and records its stages. Exit code 0 = succeeded.

``python -m reddit_signal.worker run-backfill-chunk``
    Child process: processes one queued source-backfill chunk.

Lanes keep job families from blocking each other: ``pipeline`` (daily, monthly, scheduler),
``extraction`` (manual/backlog extraction), ``source-backfill``, ``snapshot`` (periodic dashboard
snapshot refresh), ``all`` (everything, for a single small deployment).
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone

from reddit_signal import config, pipeline_jobs, runtime_settings
from reddit_signal.core import schedule
from reddit_signal.maintenance import recover_stale_jobs, refresh_snapshots

logger = logging.getLogger("reddit_signal.worker")

LANES = ("all", "pipeline", "extraction", "source-backfill", "snapshot")
LANE_JOB_TYPES = {
    "all": ("daily", "monthly", "extraction"),
    "pipeline": ("daily", "monthly"),
    "extraction": ("extraction",),
    "source-backfill": (),
    "snapshot": (),
}
HEARTBEAT_SECONDS = 10.0
CANCEL_GRACE_SECONDS = 20.0
LOG_TAIL_CHARS = 8000

_stop = threading.Event()


def _handle_stop(signum, frame):  # noqa: ANN001
    _stop.set()


def lane_runs(lane: str, what: str) -> bool:
    return {
        "scheduler": lane in {"all", "pipeline"},
        "after_job_snapshot": lane in {"all", "pipeline"},
        "source_backfill": lane in {"all", "source-backfill"},
        "periodic_snapshot": lane in {"all", "snapshot"},
    }[what]


# --- child-process supervision ----------------------------------------------------------------------

def _terminate(proc: subprocess.Popen, *, force: bool) -> None:
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL if force else signal.SIGTERM)
        elif force:
            proc.kill()
        else:
            proc.terminate()
    except ProcessLookupError:
        pass


def supervise(
    command: list[str],
    *,
    should_cancel,
    on_tick=None,
    env: dict[str, str] | None = None,
) -> tuple[int, bool, str]:
    """Run ``command``, stream its output, stop it when ``should_cancel()`` turns true.

    Returns ``(returncode, cancelled, log_tail)``. ``on_tick`` runs every heartbeat interval.
    """
    proc = subprocess.Popen(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        start_new_session=os.name == "posix",
    )
    tail: deque[str] = deque()
    tail_len = 0

    def pump() -> None:
        nonlocal tail_len
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            tail.append(line)
            tail_len += len(line)
            while tail_len > LOG_TAIL_CHARS and len(tail) > 1:
                tail_len -= len(tail.popleft())

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    cancel_sent: float | None = None
    next_tick = 0.0
    while proc.poll() is None:
        now = time.monotonic()
        if on_tick is not None and now >= next_tick:
            on_tick()
            next_tick = now + HEARTBEAT_SECONDS
        if cancel_sent is None and (_stop.is_set() or should_cancel()):
            _terminate(proc, force=False)
            cancel_sent = now
        elif cancel_sent is not None and now - cancel_sent >= CANCEL_GRACE_SECONDS:
            _terminate(proc, force=True)
        time.sleep(0.5)
    reader.join(timeout=5)
    return proc.returncode, cancel_sent is not None, "".join(tail)


# --- one loop iteration's pieces ------------------------------------------------------------------



def execute_job(job: pipeline_jobs.PipelineJob, *, worker_id: str, mode: str) -> bool:
    logger.info("starting job id=%d type=%s", job.id, job.job_type)
    pipeline_jobs.add_job_event(job.id, message=f"Worker {worker_id} started {job.job_type} job", event_type="worker")
    env = {**os.environ, "PIPELINE_JOB_ID": str(job.id), "PYTHONUNBUFFERED": "1"}

    def tick() -> None:
        try:
            pipeline_jobs.heartbeat(worker_id, mode=mode, current_job_id=job.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("heartbeat failed during job %d: %s", job.id, exc)

    returncode, cancelled, tail = supervise(
        [sys.executable, "-m", "reddit_signal.worker", "run-job", str(job.id)],
        should_cancel=lambda: pipeline_jobs.is_cancel_requested(job.id),
        on_tick=tick,
        env=env,
    )
    if cancelled:
        status, error = "cancelled", f"{job.job_type} cancelled"
        pipeline_jobs.cancel_running_steps(job.id, error=error)
    elif returncode == 0:
        status, error = "succeeded", None
    else:
        status = "failed"
        last = [line for line in tail.splitlines() if line.strip()]
        error = f"{job.job_type} failed (exit {returncode})" + (f": {last[-1][:500]}" if last else "")
    pipeline_jobs.finish_job(job.id, status=status, exit_code=returncode, error=error, log_tail=tail)
    pipeline_jobs.add_job_event(job.id, message=f"Job finished: {status} (exit {returncode})", event_type=status)
    logger.info("finished job id=%d status=%s exit=%d", job.id, status, returncode)
    return status == "succeeded"




def _setting_true(key: str, default: str = "true") -> bool:
    return runtime_settings.get_setting(key, default).strip().lower() == "true"


def _snapshot_interval() -> timedelta:
    raw = runtime_settings.get_setting("DASHBOARD_SNAPSHOT_REFRESH_MINUTES", "60").strip()
    return timedelta(minutes=max(1, int(raw) if raw.isdigit() else 60))


def _schedule_settings() -> tuple[str, str]:
    return (
        runtime_settings.get_setting("REDDIT_SIGNAL_DAILY_UTC") or runtime_settings.get_setting("OPENCLAW_DAILY_UTC", "04:05"),
        runtime_settings.get_setting("REDDIT_SIGNAL_MONTHLY_UTC") or runtime_settings.get_setting("OPENCLAW_MONTHLY_UTC", "05:00"),
    )


def _queued_source_backfill() -> bool:
    from reddit_signal.sources import source_backfill

    try:
        source_backfill.cancel_running_chunks_for_cancelled_jobs(error="cancelled before next worker cycle")
    except Exception as exc:  # noqa: BLE001
        logger.warning("source backfill cleanup failed: %s", exc)
    with pipeline_jobs.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM source_backfill_chunks c JOIN source_backfill_jobs j ON j.id = c.job_id
            WHERE c.status = 'queued' AND j.status <> 'cancelled' LIMIT 1
            """
        )
        return cur.fetchone() is not None


def process_one_backfill_chunk(*, worker_id: str, mode: str) -> None:
    from reddit_signal.sources import source_backfill

    returncode, cancelled, _ = supervise(
        [sys.executable, "-m", "reddit_signal.worker", "run-backfill-chunk"],
        should_cancel=source_backfill.has_cancel_requested_running_chunks,
        on_tick=lambda: pipeline_jobs.heartbeat(worker_id, mode=mode, current_job_id=None),
    )
    if cancelled:
        source_backfill.cancel_running_chunks_for_cancelled_jobs()
    elif returncode != 0:
        logger.error("source backfill chunk exited %d", returncode)


# --- loop ----------------------------------------------------------------------------------------

def run_loop(*, lane: str, poll_seconds: float) -> int:
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    worker_id = f"{pipeline_jobs.default_worker_id()}:{lane}"
    mode = f"worker:{lane}"
    job_types = LANE_JOB_TYPES[lane]
    logger.info("worker started id=%s lane=%s", worker_id, lane)

    now = datetime.now(timezone.utc)
    signature = _schedule_settings()
    next_daily, next_monthly = schedule.next_daily(now, signature[0]), schedule.next_monthly(now, signature[1])
    next_snapshot = now
    while not _stop.is_set():
        try:
            runtime_settings.clear_cache()
            now = datetime.now(timezone.utc)
            pipeline_jobs.heartbeat(worker_id, mode=mode, current_job_id=None)
            if job_types:
                recover_stale_jobs(job_types, now=now)

            if lane_runs(lane, "scheduler"):
                latest = _schedule_settings()
                if latest != signature:
                    signature = latest
                    next_daily, next_monthly = schedule.next_daily(now, latest[0]), schedule.next_monthly(now, latest[1])
                    logger.info("schedule changed: daily %s, monthly %s", next_daily, next_monthly)
                for job_type, due in (("daily", next_daily), ("monthly", next_monthly)):
                    if now >= due:
                        job = pipeline_jobs.create_job_once_per_window(
                            job_type, request_source="scheduler", window_key=schedule.window_key(job_type, due))
                        if job:
                            logger.info("scheduled %s job queued id=%d", job_type, job.id)
                next_daily = schedule.next_daily(now, signature[0]) if now >= next_daily else next_daily
                next_monthly = schedule.next_monthly(now, signature[1]) if now >= next_monthly else next_monthly

            job = pipeline_jobs.claim_next_job(worker_id, job_types=job_types) if job_types else None
            if job is not None:
                pipeline_jobs.heartbeat(worker_id, mode=mode, current_job_id=job.id)
                succeeded = execute_job(job, worker_id=worker_id, mode=mode)
                pipeline_jobs.heartbeat(worker_id, mode=mode, current_job_id=None)
                if succeeded and lane_runs(lane, "after_job_snapshot") and _setting_true("DASHBOARD_SNAPSHOT_REFRESH_ENABLED"):
                    refresh_snapshots(reason=f"after-{job.job_type}", all_targets=job.job_type == "monthly")
                continue  # look for more work immediately
            if (lane_runs(lane, "source_backfill") and _setting_true("SOURCE_BACKFILL_WORKER_ENABLED")
                    and _queued_source_backfill()):
                process_one_backfill_chunk(worker_id=worker_id, mode=mode)
                continue
            if (lane_runs(lane, "periodic_snapshot") and _setting_true("DASHBOARD_SNAPSHOT_REFRESH_ENABLED")
                    and now >= next_snapshot):
                refresh_snapshots(reason="periodic", all_targets=False)
                next_snapshot = now + _snapshot_interval()
        except Exception as exc:  # noqa: BLE001 - the loop must survive a database outage
            logger.error("worker loop error: %s", exc)
        _stop.wait(poll_seconds)
    try:
        pipeline_jobs.heartbeat(worker_id, mode=mode, current_job_id=None)
    except Exception:  # noqa: BLE001
        pass
    logger.info("worker stopped")
    return 0


# --- child commands --------------------------------------------------------------------------------

def run_job(job_id: int, *, force_promote: bool = False) -> int:
    from reddit_signal.job_recorder import JobRecorder
    from reddit_signal.pipeline_steps import PipelineSteps
    from reddit_signal.worker import pipelines

    job = pipeline_jobs.get_job_with_steps(job_id)[0]
    if job is None:
        logger.error("job %d not found", job_id)
        return 2
    recorder = JobRecorder(job_id)
    steps = PipelineSteps(recorder)
    now = datetime.now(timezone.utc)
    params = job.params or {}
    if job.job_type == "daily":
        pipelines.run_daily(steps, recorder, now=now)
    elif job.job_type == "monthly":
        pipelines.run_monthly(steps, recorder, now=now, force_promote=force_promote or bool(params.get("force_promote")))
    elif job.job_type == "extraction":
        pipelines.run_extraction(steps, recorder, now=now, params=params)
    else:
        logger.error("unsupported job type %r", job.job_type)
        return 2
    return 0


def run_backfill_chunk() -> int:
    from reddit_signal.sources import source_backfill

    processed = source_backfill.process_chunks(limit=1)
    logger.info("source backfill chunks processed: %d", processed)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m reddit_signal.worker", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command")
    loop = sub.add_parser("loop", help="run the worker loop (default)")
    for p in (parser, loop):
        p.add_argument("--lane", choices=LANES, default=os.environ.get("REDDIT_SIGNAL_WORKER_LANE")
                       or os.environ.get("OPENCLAW_WORKER_LANE") or "all")
        p.add_argument("--poll-seconds", type=float, default=10.0)
    job_p = sub.add_parser("run-job", help="run one pipeline job in this process")
    job_p.add_argument("job_id", type=int)
    job_p.add_argument("--force-promote", action="store_true")
    sub.add_parser("run-backfill-chunk", help="process one queued source-backfill chunk")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%Y-%m-%dT%H:%M:%S")
    config.configure(config.Config.from_env())
    config.get().require_database_url()
    if args.command == "run-job":
        return run_job(args.job_id, force_promote=args.force_promote)
    if args.command == "run-backfill-chunk":
        return run_backfill_chunk()
    return run_loop(lane=args.lane, poll_seconds=max(1.0, args.poll_seconds))
