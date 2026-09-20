"""Tests for app/scheduler/gmail_auto_scan_scheduler.py — the automatic
Gmail scan scheduler for Email Summary. Isolated from Job Search/ATS/Daily
Summary/Selected Jobs; reuses the shared daily_scheduler.scheduler instance
but never actually starts it here (jobs are added/inspected directly)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from unittest.mock import MagicMock

import app.database as database_module
import app.scheduler.gmail_auto_scan_scheduler as auto_scan_module
from app.models.application_tracking_model import ApplicationTracking
from app.models.email_connection_model import EmailConnection
from app.models.email_summary_preference_model import EmailSummaryPreference
from app.services import gmail_service


@pytest.fixture(autouse=True)
def _use_test_session_local(db_session, monkeypatch):
    # Both functions under test resolve SessionLocal via a lazy `from
    # app.database import SessionLocal` inside the function body (not a
    # module-level import) specifically so this works — see their docstrings.
    factory = sessionmaker(autocommit=False, autoflush=False, bind=db_session.get_bind())
    monkeypatch.setattr(database_module, "SessionLocal", factory)
    yield
    for job in list(auto_scan_module.scheduler.get_jobs()):
        if job.id.startswith("gmail_auto_scan_user_"):
            auto_scan_module.scheduler.remove_job(job.id)


def _connected(db, user_id, **overrides):
    defaults = dict(user_id=user_id, provider="gmail", status="CONNECTED", email_address="candidate@gmail.com")
    defaults.update(overrides)
    connection = EmailConnection(**defaults)
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return connection


def _opted_in(db, user_id, **overrides):
    defaults = dict(user_id=user_id, automatic_gmail_scan_enabled=True, gmail_scan_frequency="daily")
    defaults.update(overrides)
    preference = EmailSummaryPreference(**defaults)
    db.add(preference)
    db.commit()
    db.refresh(preference)
    return preference


# ---------------------------------------------------------------------------
# run_automatic_scan_for_user — the actual per-user run
# ---------------------------------------------------------------------------

def test_skips_when_not_connected(db_session, test_user):
    _opted_in(db_session, test_user.id)
    with patch.object(auto_scan_module.job_email_scan_service, "scan_for_job_emails") as scan:
        result = auto_scan_module.run_automatic_scan_for_user(test_user.id)
    assert result == "skipped_not_connected"
    scan.assert_not_called()


def test_skips_when_opted_out(db_session, test_user):
    _connected(db_session, test_user.id)
    _opted_in(db_session, test_user.id, automatic_gmail_scan_enabled=False)
    with patch.object(auto_scan_module.job_email_scan_service, "scan_for_job_emails") as scan:
        result = auto_scan_module.run_automatic_scan_for_user(test_user.id)
    assert result == "skipped_disabled"
    scan.assert_not_called()


def test_skips_when_no_preference_row_exists(db_session, test_user):
    _connected(db_session, test_user.id)
    with patch.object(auto_scan_module.job_email_scan_service, "scan_for_job_emails") as scan:
        result = auto_scan_module.run_automatic_scan_for_user(test_user.id)
    assert result == "skipped_disabled"
    scan.assert_not_called()


def test_runs_the_shared_scan_pipeline_and_clears_the_lock(db_session, test_user):
    connection = _connected(db_session, test_user.id)
    _opted_in(db_session, test_user.id)
    with patch.object(auto_scan_module.job_email_scan_service, "scan_for_job_emails") as scan:
        result = auto_scan_module.run_automatic_scan_for_user(test_user.id)
    scan.assert_called_once()
    assert scan.call_args.kwargs["user_id"] == test_user.id
    assert result == "scanned"
    db_session.refresh(connection)
    assert connection.auto_scan_running is False  # lock cleared after a successful run


def test_overlapping_run_is_skipped_while_lock_is_fresh(db_session, test_user):
    connection = _connected(db_session, test_user.id)
    _opted_in(db_session, test_user.id)
    connection.auto_scan_running = True
    connection.auto_scan_started_at = datetime.now(timezone.utc)
    db_session.commit()

    with patch.object(auto_scan_module.job_email_scan_service, "scan_for_job_emails") as scan:
        result = auto_scan_module.run_automatic_scan_for_user(test_user.id)
    assert result == "skipped_overlap"
    scan.assert_not_called()


def test_stale_lock_is_treated_as_crashed_and_a_new_run_proceeds(db_session, test_user):
    connection = _connected(db_session, test_user.id)
    _opted_in(db_session, test_user.id)
    connection.auto_scan_running = True
    connection.auto_scan_started_at = datetime.now(timezone.utc) - timedelta(minutes=auto_scan_module.STALE_LOCK_MINUTES + 1)
    db_session.commit()

    with patch.object(auto_scan_module.job_email_scan_service, "scan_for_job_emails") as scan:
        result = auto_scan_module.run_automatic_scan_for_user(test_user.id)
    assert result == "scanned"
    scan.assert_called_once()


def test_lock_is_cleared_even_when_the_scan_raises(db_session, test_user):
    connection = _connected(db_session, test_user.id)
    _opted_in(db_session, test_user.id)
    with patch.object(auto_scan_module.job_email_scan_service, "scan_for_job_emails", side_effect=RuntimeError("boom")):
        result = auto_scan_module.run_automatic_scan_for_user(test_user.id)
    assert result == "failed"
    db_session.refresh(connection)
    assert connection.auto_scan_running is False


def test_one_users_failure_never_affects_another_users_run(db_session, test_user, second_user):
    _connected(db_session, test_user.id)
    _opted_in(db_session, test_user.id)
    _connected(db_session, second_user.id)
    _opted_in(db_session, second_user.id)

    def side_effect(_db, *, user_id):
        if user_id == test_user.id:
            raise RuntimeError("boom")
        return {"messages_checked": 0, "outcomes": {}}

    with patch.object(auto_scan_module.job_email_scan_service, "scan_for_job_emails", side_effect=side_effect):
        first_result = auto_scan_module.run_automatic_scan_for_user(test_user.id)
        second_result = auto_scan_module.run_automatic_scan_for_user(second_user.id)
    assert first_result == "failed"
    assert second_result == "scanned"


def test_automatic_scan_never_touches_application_status(db_session, test_user, java_job):
    """The automatic scheduler is just a different trigger for the exact
    same read-only-detect-only scan pipeline — it must never itself change
    ApplicationTracking, matching the manual button's guarantee."""
    application = ApplicationTracking(user_id=test_user.id, job_id=java_job.id, company=java_job.company, title=java_job.title, status="APPLIED", status_source="system")
    db_session.add(application)
    db_session.commit()
    _connected(db_session, test_user.id)
    _opted_in(db_session, test_user.id)

    # scan_for_job_emails itself is covered exhaustively elsewhere
    # (test_job_email_scan_service.py) — here we only need to confirm the
    # scheduler calls into it rather than any other code path that could
    # bypass its NEEDS_CONFIRMATION guarantee.
    with patch.object(auto_scan_module.job_email_scan_service, "scan_for_job_emails") as scan:
        auto_scan_module.run_automatic_scan_for_user(test_user.id)
    scan.assert_called_once()
    db_session.refresh(application)
    assert application.status == "APPLIED"


# ---------------------------------------------------------------------------
# schedule_gmail_auto_scans — job (de)registration
# ---------------------------------------------------------------------------

def test_schedules_a_job_only_for_opted_in_users(db_session, test_user, second_user):
    _opted_in(db_session, test_user.id)
    _opted_in(db_session, second_user.id, automatic_gmail_scan_enabled=False)

    auto_scan_module.schedule_gmail_auto_scans()

    assert auto_scan_module.scheduler.get_job(f"gmail_auto_scan_user_{test_user.id}") is not None
    assert auto_scan_module.scheduler.get_job(f"gmail_auto_scan_user_{second_user.id}") is None


def test_opting_out_removes_the_previously_scheduled_job(db_session, test_user):
    preference = _opted_in(db_session, test_user.id)
    auto_scan_module.schedule_gmail_auto_scans()
    assert auto_scan_module.scheduler.get_job(f"gmail_auto_scan_user_{test_user.id}") is not None

    preference.automatic_gmail_scan_enabled = False
    db_session.commit()
    auto_scan_module.schedule_gmail_auto_scans()
    assert auto_scan_module.scheduler.get_job(f"gmail_auto_scan_user_{test_user.id}") is None


@pytest.mark.parametrize("frequency,hours", [("daily", 24), ("every_12_hours", 12), ("every_6_hours", 6)])
def test_job_interval_matches_configured_frequency(db_session, test_user, frequency, hours):
    _opted_in(db_session, test_user.id, gmail_scan_frequency=frequency)
    auto_scan_module.schedule_gmail_auto_scans()
    job = auto_scan_module.scheduler.get_job(f"gmail_auto_scan_user_{test_user.id}")
    assert job is not None
    assert job.trigger.interval == timedelta(hours=hours)


# CURRENT VERIFIED BUG fix, item 9: the automatic scheduler must benefit
# from the same safe per-message 404 handling as the manual "Scan Job
# Emails" button — both call the exact same job_email_scan_service.
# scan_for_job_emails, so a single missing Gmail message must not make the
# scheduler report "failed" for the whole user.
def test_automatic_scan_reports_scanned_not_failed_when_one_message_is_missing_404(db_session, test_user):
    connection = _connected(db_session, test_user.id)
    _opted_in(db_session, test_user.id)
    provider = gmail_service.GmailEmailProvider(db=db_session, connection=connection, access_token="fake-token")

    def http_response(status_code=200, payload=None):
        response = MagicMock()
        response.status_code = status_code
        response.json.return_value = payload or {}
        response.content = b"1" if payload is not None else b""
        response.raise_for_status.side_effect = None if status_code < 400 else Exception(f"HTTP {status_code}")
        return response

    def side_effect(method, url, **kwargs):
        if url.endswith("/profile"):
            return http_response(200, {"historyId": "1000"})
        if url.endswith("/messages"):
            return http_response(200, {"messages": [{"id": "m1"}]})
        if url.endswith("/messages/m1"):
            return http_response(404)
        raise AssertionError(f"unexpected Gmail call: {method} {url}")

    with patch("app.services.job_email_scan_service.gmail_service.get_provider_for_user", return_value=provider), \
         patch("app.services.gmail_service.requests.request", side_effect=side_effect):
        result = auto_scan_module.run_automatic_scan_for_user(test_user.id)

    assert result == "scanned"
    db_session.refresh(connection)
    assert connection.auto_scan_running is False  # lock still correctly cleared
