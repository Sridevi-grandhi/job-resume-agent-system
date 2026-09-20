"""Tests for app/scheduler/updated_job_scheduler_service.py.

Covers: reuses the existing shared APScheduler instance (never a second,
competing scheduler), env-driven enable/interval, idempotent
(re)registration, and that both the scheduler and manual Refresh Jobs call
the exact same run_poll_cycle — see also
test_updated_job_discovery_service.py's overlapping-poll-guard coverage.
"""

from __future__ import annotations

from unittest.mock import patch

from app.scheduler.daily_scheduler import scheduler as shared_scheduler
from app.scheduler.updated_job_scheduler_service import (
    JOB_ID,
    poll_interval_minutes,
    schedule_updated_job_poll,
    stop_updated_job_poll,
    tracker_enabled,
)


def teardown_function():
    stop_updated_job_poll()


def test_tracker_enabled_defaults_true(monkeypatch):
    monkeypatch.delenv("UPDATED_JOB_TRACKER_ENABLED", raising=False)
    assert tracker_enabled() is True


def test_tracker_can_be_disabled_via_env(monkeypatch):
    monkeypatch.setenv("UPDATED_JOB_TRACKER_ENABLED", "false")
    assert tracker_enabled() is False


def test_poll_interval_defaults_to_15_minutes(monkeypatch):
    monkeypatch.delenv("UPDATED_JOB_POLL_INTERVAL_MINUTES", raising=False)
    assert poll_interval_minutes() == 15


def test_poll_interval_reads_env_and_has_a_floor_of_one_minute(monkeypatch):
    monkeypatch.setenv("UPDATED_JOB_POLL_INTERVAL_MINUTES", "5")
    assert poll_interval_minutes() == 5
    monkeypatch.setenv("UPDATED_JOB_POLL_INTERVAL_MINUTES", "0")
    assert poll_interval_minutes() == 1


def test_schedule_registers_on_the_shared_scheduler_not_a_new_one(monkeypatch):
    """Reuses app.scheduler.daily_scheduler.scheduler — never a second
    BackgroundScheduler instance (see module docstring)."""
    monkeypatch.setenv("UPDATED_JOB_TRACKER_ENABLED", "true")
    monkeypatch.setenv("UPDATED_JOB_POLL_INTERVAL_MINUTES", "15")
    schedule_updated_job_poll()
    job = shared_scheduler.get_job(JOB_ID)
    assert job is not None
    assert job.trigger.interval.total_seconds() == 15 * 60


def test_schedule_disabled_registers_nothing(monkeypatch):
    monkeypatch.setenv("UPDATED_JOB_TRACKER_ENABLED", "false")
    schedule_updated_job_poll()
    assert shared_scheduler.get_job(JOB_ID) is None


def test_schedule_is_idempotent_replaces_not_duplicates(monkeypatch):
    monkeypatch.setenv("UPDATED_JOB_TRACKER_ENABLED", "true")
    monkeypatch.setenv("UPDATED_JOB_POLL_INTERVAL_MINUTES", "15")
    schedule_updated_job_poll()
    schedule_updated_job_poll()
    schedule_updated_job_poll()
    jobs = [job for job in shared_scheduler.get_jobs() if job.id == JOB_ID]
    assert len(jobs) == 1


def test_stop_removes_the_job(monkeypatch):
    monkeypatch.setenv("UPDATED_JOB_TRACKER_ENABLED", "true")
    schedule_updated_job_poll()
    assert shared_scheduler.get_job(JOB_ID) is not None
    stop_updated_job_poll()
    assert shared_scheduler.get_job(JOB_ID) is None


def test_stop_when_never_scheduled_does_not_raise():
    stop_updated_job_poll()  # no-op, must not raise


def test_scheduled_poll_calls_the_same_run_poll_cycle_as_manual_refresh(monkeypatch):
    """The scheduler's internal callback and the manual /refresh route both
    ultimately call updated_job_discovery_service.run_poll_cycle — the
    single shared discovery pipeline (never a competing implementation)."""
    from app.scheduler import updated_job_scheduler_service as scheduler_service

    with patch.object(scheduler_service, "run_poll_cycle") as mocked_run:
        scheduler_service._run_scheduled_poll()
    mocked_run.assert_called_once_with(triggered_by="scheduler")


def test_scheduled_poll_never_raises_even_if_run_poll_cycle_fails(monkeypatch):
    from app.scheduler import updated_job_scheduler_service as scheduler_service

    with patch.object(scheduler_service, "run_poll_cycle", side_effect=RuntimeError("boom")):
        scheduler_service._run_scheduled_poll()  # must not raise — a scheduled job that raises stops re-firing
