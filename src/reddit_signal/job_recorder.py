"""Records the stages and events of one pipeline job in the database.

Replaces parsing the Bash scripts' stdout with regular expressions: the pipeline says what it is doing.
Without a job id (a pipeline run by hand) it only logs.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

from reddit_signal import pipeline_jobs

logger = logging.getLogger(__name__)
T = TypeVar("T")


class JobRecorder:
    def __init__(self, job_id: int | None) -> None:
        self.job_id = job_id
        self._stage: str | None = None

    def run(self, order: int, name: str, fn: Callable[[], T]) -> T:
        self._stage = name
        logger.info("=== stage %d: %s ===", order, name)
        self._safe(pipeline_jobs.start_step, self.job_id, name, stage_order=order)
        started = time.monotonic()
        try:
            result = fn()
        except BaseException as exc:
            self._safe(pipeline_jobs.finish_step, self.job_id, name, status="failed", exit_code=1,
                       error=f"{type(exc).__name__}: {exc}"[:2000])
            self._safe(pipeline_jobs.skip_remaining_steps, self.job_id, order)
            logger.error("stage %d failed after %.0fs: %s", order, time.monotonic() - started, exc)
            raise
        self._safe(pipeline_jobs.finish_step, self.job_id, name, status="succeeded", exit_code=0)
        logger.info("stage %d done in %.0fs", order, time.monotonic() - started)
        return result

    def note(self, message: str, *, event_type: str = "log", details: dict[str, Any] | None = None) -> None:
        level = logging.WARNING if event_type in {"warning", "error"} else logging.INFO
        logger.log(level, "%s", message)
        self._safe(pipeline_jobs.add_job_event, self.job_id, message=message, stage_name=self._stage,
                   event_type=event_type, details=details)

    def _safe(self, fn: Callable[..., Any], job_id: int | None, *args: Any, **kwargs: Any) -> None:
        """Bookkeeping must never break the pipeline itself."""
        if job_id is None:
            return
        try:
            fn(job_id, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not record %s for job %s: %s", fn.__name__, job_id, exc)
