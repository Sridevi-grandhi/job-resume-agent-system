"""Wires Updated Job Search polling into the project's existing APScheduler
BackgroundScheduler (app.scheduler.daily_scheduler.scheduler) instead of
creating a second, competing scheduler instance.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from apscheduler.triggers.interval import IntervalTrigger

from app.scheduler.daily_scheduler import scheduler
from app.services.updated_job_discovery_service import run_poll_cycle

logger = logging.getLogger(__name__)

JOB_ID = "updated_job_search_poll"


def tracker_enabled() -> bool:
    return (os.getenv("UPDATED_JOB_TRACKER_ENABLED") or "true").strip().lower() == "true"


def poll_interval_minutes() -> int:
    return max(1, int(os.getenv("UPDATED_JOB_POLL_INTERVAL_MINUTES") or "15"))


def _run_scheduled_poll() -> None:
    try:
        run_poll_cycle(triggered_by="scheduler")
    except Exception:
        logger.exception("updated_job_scheduled_poll_failed")


def schedule_updated_job_poll() -> None:
    """Register (or refresh) the recurring poll job on the shared scheduler.
    Fires once immediately (next_run_time=now) so newly discovered jobs are
    available on first login, then repeats every poll_interval_minutes()."""
    if not tracker_enabled():
        logger.info("Updated Job Search tracker disabled (UPDATED_JOB_TRACKER_ENABLED=false)")
        return
    if scheduler.get_job(JOB_ID):
        scheduler.remove_job(JOB_ID)
    scheduler.add_job(
        _run_scheduled_poll,
        trigger=IntervalTrigger(minutes=poll_interval_minutes()),
        id=JOB_ID,
        replace_existing=True,
        next_run_time=datetime.now(),
    )
    logger.info("Updated Job Search polling scheduled every %s minute(s)", poll_interval_minutes())


def stop_updated_job_poll() -> None:
    if scheduler.get_job(JOB_ID):
        scheduler.remove_job(JOB_ID)
