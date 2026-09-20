"""Phase-3 scheduler tests.

Daily Summary is intentionally disabled (Email Summary redesign):
run_scheduled_daily_summary_for_user() always returns "disabled" as its very
first statement, before any of the old opt-in/timezone/generation/delivery
logic runs. These tests verify that disabled behavior stays disabled for
every scenario the old suite covered (opted-in, opted-out, invalid
timezone/recipient, repeated runs, SMTP failure) — never generates an
EmailLog, ScheduledDailySummary claim, or SummaryDeliveryAttempt, and never
calls SMTP. Job registration itself (schedule_daily_summary_jobs) is a
separate, still-functioning concern covered by
test_scheduler_registration_uses_existing_schedule_time_and_timezone below."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import sessionmaker

import app.scheduler.daily_scheduler as scheduler_module
from app.models.email_log_model import EmailLog
from app.models.email_summary_preference_model import EmailSummaryPreference
from app.models.scheduled_daily_summary_model import ScheduledDailySummary
from app.models.summary_delivery_model import SummaryDeliveryAttempt


@pytest.fixture(autouse=True)
def _use_test_session_local(db_session, monkeypatch):
    factory = sessionmaker(autocommit=False, autoflush=False, bind=db_session.get_bind())
    monkeypatch.setattr(scheduler_module, "SessionLocal", factory)
    yield
    for job in list(scheduler_module.scheduler.get_jobs()):
        if job.id.startswith("daily_summary_user_"):
            scheduler_module.scheduler.remove_job(job.id)


def _enable(db, user_id, *, recipient=None):
    row = EmailSummaryPreference(
        user_id=user_id, daily_summary_delivery_enabled=True,
        automatic_gmail_scan_enabled=False, daily_summary_recipient=recipient,
    )
    db.add(row)
    db.commit()
    return row


def _run_at():
    return datetime(2026, 9, 18, 8, 0, tzinfo=ZoneInfo("America/Chicago"))


def _seed_match(db, user, resume, job):
    from app.models.match_model import MatchScore
    db.add(MatchScore(
        user_id=user.id, resume_id=resume.id, job_id=job.id, match_percentage=82,
        matched_keywords="Java", missing_keywords="", recommendation="Apply", resume_version="original",
    ))
    db.commit()


def test_scheduled_daily_summary_stays_disabled_regardless_of_preference_state(db_session, test_user, test_preferences, test_resume, java_job):
    _seed_match(db_session, test_user, test_resume, java_job)
    assert scheduler_module.run_scheduled_daily_summary_for_user(test_user.id, now=_run_at()) == "disabled"
    _enable(db_session, test_user.id)
    assert scheduler_module.run_scheduled_daily_summary_for_user(test_user.id, now=_run_at()) == "disabled"
    db_session.query(EmailSummaryPreference).filter_by(user_id=test_user.id).update({"daily_summary_delivery_enabled": False})
    db_session.commit()
    assert scheduler_module.run_scheduled_daily_summary_for_user(test_user.id, now=_run_at()) == "disabled"
    assert db_session.query(EmailLog).count() == 0


def test_scheduled_daily_summary_stays_disabled_even_for_a_fully_opted_in_user(db_session, test_user, test_preferences, test_resume, java_job):
    """A user who is fully opted in, has a valid timezone, and has real
    matches still gets "disabled" — no log, no claim, no delivery attempt,
    no SMTP call. Mirrors the old "opted-in user" happy path, now asserting
    the intentionally disabled outcome instead."""
    _seed_match(db_session, test_user, test_resume, java_job)
    _enable(db_session, test_user.id)
    with patch("app.services.daily_summary_delivery_service.send_email_message", return_value={"sent": True, "reason": None}) as smtp:
        result = scheduler_module.run_scheduled_daily_summary_for_user(test_user.id, now=_run_at())
    assert result == "disabled"
    assert db_session.query(EmailLog).count() == 0
    assert db_session.query(ScheduledDailySummary).count() == 0
    assert db_session.query(SummaryDeliveryAttempt).count() == 0
    smtp.assert_not_called()


def test_scheduler_registration_uses_existing_schedule_time_and_timezone(db_session, test_user, test_preferences):
    _enable(db_session, test_user.id)
    scheduler_module.schedule_daily_summary_jobs()
    job = scheduler_module.scheduler.get_job(f"daily_summary_user_{test_user.id}")
    assert job is not None
    assert "America/Chicago" in str(job.trigger.timezone)
    assert str(test_preferences.schedule_time).startswith("08:00")


def test_scheduled_daily_summary_stays_disabled_for_every_user_valid_or_not(db_session, test_user, second_user, test_preferences, test_resume, java_job):
    """Both an invalid-timezone/invalid-recipient user and an otherwise
    valid second user get the same "disabled" outcome — one user's bad
    config still can't cause anything to run for anyone."""
    _seed_match(db_session, test_user, test_resume, java_job)
    test_preferences.timezone = "Not/A_Timezone"
    db_session.commit()
    _enable(db_session, test_user.id, recipient="bad-email")
    assert scheduler_module.run_scheduled_daily_summary_for_user(test_user.id, now=_run_at()) == "disabled"

    from app.models.user_preference_model import UserPreference
    from datetime import time
    other_pref = UserPreference(user_id=second_user.id, job_title="Java", location="Remote", experience="5", work_mode="Remote", job_type="Full-time", skills="Java", schedule_time=time(8), timezone="America/Chicago")
    db_session.add(other_pref)
    db_session.commit()
    _seed_match(db_session, second_user, test_resume, java_job)
    _enable(db_session, second_user.id)
    assert scheduler_module.run_scheduled_daily_summary_for_user(second_user.id, now=_run_at()) == "disabled"


def test_running_the_scheduled_job_twice_stays_disabled_and_creates_nothing(db_session, test_user, test_preferences, test_resume, java_job):
    _seed_match(db_session, test_user, test_resume, java_job)
    _enable(db_session, test_user.id, recipient="digest@example.com")
    with patch("app.services.daily_summary_delivery_service.send_email_message", return_value={"sent": True, "reason": None}) as smtp:
        assert scheduler_module.run_scheduled_daily_summary_for_user(test_user.id, now=_run_at()) == "disabled"
        assert scheduler_module.run_scheduled_daily_summary_for_user(test_user.id, now=_run_at()) == "disabled"
    assert db_session.query(EmailLog).count() == 0
    assert db_session.query(ScheduledDailySummary).count() == 0
    smtp.assert_not_called()


def test_scheduled_daily_summary_stays_disabled_even_when_smtp_would_fail(db_session, test_user, test_preferences, test_resume, java_job, monkeypatch):
    monkeypatch.setenv("DAILY_SUMMARY_DELIVERY_MAX_ATTEMPTS", "1")
    _seed_match(db_session, test_user, test_resume, java_job)
    _enable(db_session, test_user.id)
    with patch("app.services.daily_summary_delivery_service.send_email_message", return_value={"sent": False, "reason": "provider_error"}) as smtp:
        assert scheduler_module.run_scheduled_daily_summary_for_user(test_user.id, now=_run_at()) == "disabled"
        assert scheduler_module.run_scheduled_daily_summary_for_user(test_user.id, now=_run_at()) == "disabled"
    assert db_session.query(EmailLog).count() == 0
    assert db_session.query(SummaryDeliveryAttempt).count() == 0
    smtp.assert_not_called()


def test_scheduled_path_never_invokes_legacy_job_search_or_ats(db_session, test_user, test_preferences, test_resume, java_job):
    _seed_match(db_session, test_user, test_resume, java_job)
    _enable(db_session, test_user.id)
    with patch.object(scheduler_module, "get_mock_jobs") as jobs, patch.object(scheduler_module, "start_or_get_workflow") as ats, patch(
        "app.services.daily_summary_delivery_service.send_email_message", return_value={"sent": True, "reason": None}
    ):
        scheduler_module.run_scheduled_daily_summary_for_user(test_user.id, now=_run_at())
    jobs.assert_not_called()
    ats.assert_not_called()
