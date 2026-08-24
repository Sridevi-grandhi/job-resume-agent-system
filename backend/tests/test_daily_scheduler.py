"""Tests for app/scheduler/daily_scheduler.py.

Every test monkeypatches SessionLocal to the shared in-memory sqlite test
engine so nothing here ever opens a connection to the real Postgres database,
and the scheduler's own BackgroundScheduler is never started (schedule_user_jobs
only registers/replaces cron jobs — it never invokes them synchronously).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import app.scheduler.daily_scheduler as scheduler_module


@pytest.fixture(autouse=True)
def _use_test_session_local(db_session, monkeypatch):
    engine = db_session.get_bind()
    from sqlalchemy.orm import sessionmaker

    test_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(scheduler_module, "SessionLocal", test_session_local)
    yield


def test_run_workflow_for_user_missing_user_returns_early(db_session):
    scheduler_module.run_workflow_for_user(999999)  # must not raise


def test_run_workflow_for_user_missing_preferences_returns_early(db_session, test_user):
    scheduler_module.run_workflow_for_user(test_user.id)  # must not raise


def test_run_workflow_for_user_missing_resume_returns_early(db_session, test_user, test_preferences):
    scheduler_module.run_workflow_for_user(test_user.id)  # must not raise


def test_run_workflow_for_user_happy_path_creates_jobs_and_email(db_session, test_user, test_preferences, test_resume):
    from app.models.job_model import Job
    from app.models.email_log_model import EmailLog

    scheduler_module.run_workflow_for_user(test_user.id)

    jobs = db_session.query(Job).filter(Job.user_id == test_user.id).all()
    assert len(jobs) > 0

    logs = db_session.query(EmailLog).filter(EmailLog.user_id == test_user.id).all()
    assert len(logs) == 1


def test_run_workflow_for_user_ats_failure_does_not_abort_run(db_session, test_user, test_preferences, test_resume):
    from app.models.email_log_model import EmailLog

    with patch.object(scheduler_module, "start_or_get_workflow", side_effect=RuntimeError("boom")):
        scheduler_module.run_workflow_for_user(test_user.id)

    # Email is still generated even though the ATS workflow step failed.
    logs = db_session.query(EmailLog).filter(EmailLog.user_id == test_user.id).all()
    assert len(logs) == 1


def test_run_workflow_for_user_builds_email_from_existing_match_scores(
    db_session, test_user, test_preferences, test_resume, java_job
):
    from app.models.match_model import MatchScore
    from app.models.email_log_model import EmailLog

    db_session.add(MatchScore(
        user_id=test_user.id, resume_id=test_resume.id, job_id=java_job.id,
        match_percentage=72, matched_keywords="Java, Spring Boot", missing_keywords="AWS",
        recommendation="Apply", resume_version="original",
    ))
    db_session.add(MatchScore(
        user_id=test_user.id, resume_id=test_resume.id, job_id=java_job.id,
        match_percentage=85, matched_keywords="Java, Spring Boot, AWS", missing_keywords="",
        recommendation="Apply", resume_version="optimized",
    ))
    db_session.commit()

    scheduler_module.run_workflow_for_user(test_user.id)

    log = db_session.query(EmailLog).filter(EmailLog.user_id == test_user.id).order_by(EmailLog.id.desc()).first()
    assert "Optimized ATS Score" in log.body


def test_run_workflow_for_user_swallows_unexpected_exception(db_session, test_user, test_preferences, test_resume):
    with patch.object(scheduler_module, "get_mock_jobs", side_effect=RuntimeError("boom")):
        scheduler_module.run_workflow_for_user(test_user.id)  # must not raise


def test_schedule_user_jobs_registers_cron_job_per_preference(db_session, test_user, test_preferences):
    scheduler_module.schedule_user_jobs()
    job_id = f"daily_workflow_user_{test_user.id}"
    assert scheduler_module.scheduler.get_job(job_id) is not None
    scheduler_module.scheduler.remove_job(job_id)


def test_schedule_user_jobs_replaces_existing_job_for_same_user(db_session, test_user, test_preferences):
    scheduler_module.schedule_user_jobs()
    job_id = f"daily_workflow_user_{test_user.id}"
    first_job = scheduler_module.scheduler.get_job(job_id)
    assert first_job is not None

    scheduler_module.schedule_user_jobs()
    second_job = scheduler_module.scheduler.get_job(job_id)
    assert second_job is not None
    scheduler_module.scheduler.remove_job(job_id)


def test_start_daily_scheduler_starts_scheduler_once_and_registers_jobs(db_session, test_user, test_preferences):
    assert scheduler_module.scheduler.running is False
    try:
        scheduler_module.start_daily_scheduler()
        assert scheduler_module.scheduler.running is True
        job_id = f"daily_workflow_user_{test_user.id}"
        assert scheduler_module.scheduler.get_job(job_id) is not None

        # Calling start again must not raise or create a second scheduler thread.
        scheduler_module.start_daily_scheduler()
        assert scheduler_module.scheduler.running is True
    finally:
        scheduler_module.stop_daily_scheduler()
        assert scheduler_module.scheduler.running is False


def test_stop_daily_scheduler_is_safe_when_not_running():
    assert scheduler_module.scheduler.running is False
    scheduler_module.stop_daily_scheduler()  # must not raise
    assert scheduler_module.scheduler.running is False
