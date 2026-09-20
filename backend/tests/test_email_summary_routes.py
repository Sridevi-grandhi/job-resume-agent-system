"""Route-level tests for the new Email Summary endpoints: config-status
secret safety, cross-user isolation (Part 22), and production error
handling (Part 20). No real Gmail/Ollama call anywhere in this file."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.models.application_tracking_model import ApplicationTracking
from app.models.email_connection_model import EmailConnection
from app.models.job_email_event_model import JobEmailEvent
from app.services import gmail_service


def _seed_connection(db_session, user_id, **overrides):
    defaults = dict(user_id=user_id, provider="gmail", status="CONNECTED", email_address="candidate@gmail.com")
    defaults.update(overrides)
    connection = EmailConnection(**defaults)
    db_session.add(connection)
    db_session.commit()
    db_session.refresh(connection)
    return connection


def _seed_application(db_session, user_id, **overrides):
    defaults = dict(user_id=user_id, job_id=None, company="ABC Corp", title="Java Developer", status="APPLIED", status_source="system")
    defaults.update(overrides)
    application = ApplicationTracking(**defaults)
    db_session.add(application)
    db_session.commit()
    db_session.refresh(application)
    return application


def _seed_event(db_session, user_id, connection_id, **overrides):
    defaults = dict(
        user_id=user_id, connection_id=connection_id, message_id="m1", sender="hr@abccorp.com",
        subject="Thank you for applying", event_type="APPLICATION_CONFIRMATION", confidence=0.9,
        resolution="NEEDS_CONFIRMATION",
    )
    defaults.update(overrides)
    event = JobEmailEvent(**defaults)
    db_session.add(event)
    db_session.commit()
    db_session.refresh(event)
    return event


# ---------------------------------------------------------------------------
# config-status — never leaks secrets
# ---------------------------------------------------------------------------

def test_gmail_config_status_never_leaks_client_secret(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "super-secret-value")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/email/gmail/callback")
    response = client.get("/api/email/gmail/config-status")
    assert response.status_code == 200
    assert "super-secret-value" not in response.text
    assert response.json()["oauth_configured"] is True


def test_gmail_status_endpoint_reports_disconnected_by_default(client, test_user):
    response = client.get(f"/api/email/gmail/status/{test_user.id}")
    assert response.status_code == 200
    assert response.json()["connected"] is False


def test_gmail_status_endpoint_reports_connected(client, test_user, db_session):
    _seed_connection(db_session, test_user.id)
    response = client.get(f"/api/email/gmail/status/{test_user.id}")
    body = response.json()
    assert body["connected"] is True
    assert body["email_address"] == "candidate@gmail.com"


def test_gmail_connect_returns_503_when_not_configured(client, test_user, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    response = client.get(f"/api/email/gmail/connect/{test_user.id}")
    assert response.status_code == 503


# ---------------------------------------------------------------------------
# Cross-user isolation (Part 22)
# ---------------------------------------------------------------------------

def test_user_a_cannot_see_user_b_gmail_connection(client, test_user, second_user, db_session):
    _seed_connection(db_session, test_user.id)
    response = client.get(f"/api/email/gmail/status/{second_user.id}")
    assert response.json()["connected"] is False


def test_user_a_cannot_read_user_b_applications(client, test_user, second_user, db_session):
    _seed_application(db_session, test_user.id)
    response = client.get(f"/api/email/applications/{second_user.id}")
    assert response.json()["applications"] == []


def test_user_a_cannot_update_user_b_application_status(client, test_user, second_user, db_session):
    application = _seed_application(db_session, test_user.id)
    response = client.post(f"/api/email/applications/{second_user.id}/{application.id}/status", json={"new_status": "OFFER"})
    assert response.status_code == 404
    db_session.refresh(application)
    assert application.status == "APPLIED"  # untouched


def test_user_a_cannot_read_user_b_events(client, test_user, second_user, db_session):
    connection = _seed_connection(db_session, test_user.id)
    _seed_event(db_session, test_user.id, connection.id)
    response = client.get(f"/api/email/events/{second_user.id}")
    assert response.json()["events"] == []


def test_user_a_cannot_resolve_user_b_event(client, test_user, second_user, db_session):
    connection = _seed_connection(db_session, test_user.id)
    event = _seed_event(db_session, test_user.id, connection.id)
    response = client.post(f"/api/email/events/{second_user.id}/{event.id}/resolve", json={"action": "ignore"})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Application status updates (Part 18)
# ---------------------------------------------------------------------------

def test_manual_status_update_appends_history(client, test_user, db_session):
    application = _seed_application(db_session, test_user.id)
    response = client.post(f"/api/email/applications/{test_user.id}/{application.id}/status", json={"new_status": "interview_requested"})
    assert response.status_code == 200
    assert response.json()["status"] == "INTERVIEW_REQUESTED"

    history_response = client.get(f"/api/email/applications/{test_user.id}/{application.id}/history")
    history = history_response.json()["history"]
    assert len(history) == 1
    assert history[0]["source"] == "manual"


def test_manual_status_update_rejects_unknown_status(client, test_user, db_session):
    application = _seed_application(db_session, test_user.id)
    response = client.post(f"/api/email/applications/{test_user.id}/{application.id}/status", json={"new_status": "NOT_REAL"})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Event resolution (confirm / ignore / choose_job)
# ---------------------------------------------------------------------------

def test_resolve_event_ignore(client, test_user, db_session):
    connection = _seed_connection(db_session, test_user.id)
    event = _seed_event(db_session, test_user.id, connection.id)
    response = client.post(f"/api/email/events/{test_user.id}/{event.id}/resolve", json={"action": "ignore"})
    assert response.status_code == 200
    assert response.json()["resolution"] == "IGNORED"


def test_resolve_event_ignore_never_changes_application_status(client, test_user, db_session):
    """Item 22 (stabilization pass): Ignore must never touch
    ApplicationTracking, even when the event carries a reliable
    matched_application_id — Ignore is purely a dismissal of the event."""
    connection = _seed_connection(db_session, test_user.id)
    application = _seed_application(db_session, test_user.id, company="ABC Corp", title="Java Developer", status="APPLIED")
    event = _seed_event(
        db_session, test_user.id, connection.id, event_type="INTERVIEW_INVITE",
        matched_application_id=application.id, match_confidence=0.9, confidence=0.85,
    )
    response = client.post(f"/api/email/events/{test_user.id}/{event.id}/resolve", json={"action": "ignore"})
    assert response.status_code == 200
    assert response.json()["resolution"] == "IGNORED"

    db_session.refresh(application)
    assert application.status == "APPLIED"  # untouched by Ignore
    assert application.status_source == "system"


def test_resolve_event_reject_re_resolving_an_already_confirmed_event(client, test_user, db_session):
    """Item 24 (stabilization pass): a settled historical row must never be
    rewritten by a later resolve call — direct-API defense-in-depth beyond
    the frontend only showing Confirm/Ignore for NEEDS_CONFIRMATION rows."""
    connection = _seed_connection(db_session, test_user.id)
    application = _seed_application(db_session, test_user.id, status="INTERVIEW_REQUESTED")
    event = _seed_event(
        db_session, test_user.id, connection.id,
        matched_application_id=application.id, match_confidence=0.9, resolution="CONFIRMED",
    )
    response = client.post(f"/api/email/events/{test_user.id}/{event.id}/resolve", json={"action": "confirm"})
    assert response.status_code == 422

    response = client.post(f"/api/email/events/{test_user.id}/{event.id}/resolve", json={"action": "ignore"})
    assert response.status_code == 422

    db_session.refresh(event)
    assert event.resolution == "CONFIRMED"  # untouched
    db_session.refresh(application)
    assert application.status == "INTERVIEW_REQUESTED"  # untouched


def test_resolve_event_reject_re_resolving_an_already_ignored_event(client, test_user, db_session):
    """Item 24, IGNORED variant."""
    connection = _seed_connection(db_session, test_user.id)
    application = _seed_application(db_session, test_user.id, status="APPLIED")
    event = _seed_event(
        db_session, test_user.id, connection.id,
        matched_application_id=application.id, match_confidence=0.9, resolution="IGNORED",
    )
    response = client.post(f"/api/email/events/{test_user.id}/{event.id}/resolve", json={"action": "confirm"})
    assert response.status_code == 422
    db_session.refresh(event)
    assert event.resolution == "IGNORED"


def test_legacy_auto_applied_event_is_never_modified_by_events_list(client, test_user, db_session):
    """Item 24 — a historical AUTO_APPLIED row (from before Gmail scanning
    became read-only-detect-only) is a settled audit record: GET /events
    must return it exactly as stored, never re-validated or altered."""
    connection = _seed_connection(db_session, test_user.id)
    application = _seed_application(db_session, test_user.id, status="INTERVIEW_REQUESTED")
    event = _seed_event(
        db_session, test_user.id, connection.id, event_type="INTERVIEW_INVITE",
        matched_application_id=application.id, matched_job_id=None,
        match_confidence=0.9, confidence=0.8, resolution="AUTO_APPLIED",
    )
    response = client.get(f"/api/email/events/{test_user.id}")
    assert response.status_code == 200
    payload = response.json()["events"][0]
    assert payload["matched_application_id"] == application.id
    assert payload["resolution"] == "AUTO_APPLIED"

    db_session.refresh(event)
    assert event.matched_application_id == application.id
    assert event.resolution == "AUTO_APPLIED"


def test_resolve_event_confirm_preserves_terminal_status(client, test_user, db_session):
    """Item 23, at the route level: Confirm must never move a terminal
    (OFFER/REJECTED/WITHDRAWN) application backward/sideways via an email
    event — the event may still be marked CONFIRMED (the match itself was
    accepted), but the application's terminal status is protected."""
    connection = _seed_connection(db_session, test_user.id)
    application = _seed_application(db_session, test_user.id, status="REJECTED")
    event = _seed_event(
        db_session, test_user.id, connection.id, event_type="INTERVIEW_INVITE",
        matched_application_id=application.id, match_confidence=0.9, confidence=0.85,
    )
    response = client.post(f"/api/email/events/{test_user.id}/{event.id}/resolve", json={"action": "confirm"})
    assert response.status_code == 200

    db_session.refresh(application)
    assert application.status == "REJECTED"  # terminal status never moved backward/sideways


def test_resolve_event_choose_job_creates_tracking_and_updates_status(client, test_user, db_session, java_job):
    connection = _seed_connection(db_session, test_user.id)
    event = _seed_event(db_session, test_user.id, connection.id, event_type="INTERVIEW_INVITE")
    response = client.post(
        f"/api/email/events/{test_user.id}/{event.id}/resolve",
        json={"action": "choose_job", "job_id": java_job.id},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["resolution"] == "CONFIRMED"
    assert body["matched_job_id"] == java_job.id

    applications = client.get(f"/api/email/applications/{test_user.id}").json()["applications"]
    assert any(a["job_id"] == java_job.id and a["status"] == "INTERVIEW_REQUESTED" for a in applications)


def test_resolve_event_confirm_requires_a_matched_application(client, test_user, db_session):
    connection = _seed_connection(db_session, test_user.id)
    event = _seed_event(db_session, test_user.id, connection.id, matched_application_id=None)
    response = client.post(f"/api/email/events/{test_user.id}/{event.id}/resolve", json={"action": "confirm"})
    assert response.status_code == 422


def test_resolve_event_confirm_succeeds_for_reliable_match(client, test_user, db_session):
    """Requirement 9/D at the route level: a genuinely reliable match
    (company-name evidence, match_confidence above the threshold) must
    still be confirmable — the new backend check is not overly strict."""
    connection = _seed_connection(db_session, test_user.id)
    application = _seed_application(db_session, test_user.id, company="ABC Corp", title="Java Developer")
    event = _seed_event(
        db_session, test_user.id, connection.id,
        matched_application_id=application.id, match_confidence=0.8, confidence=0.75,
        event_type="INTERVIEW_INVITE",
    )
    response = client.post(f"/api/email/events/{test_user.id}/{event.id}/resolve", json={"action": "confirm"})
    assert response.status_code == 200
    assert response.json()["resolution"] == "CONFIRMED"

    db_session.refresh(application)
    assert application.status == "INTERVIEW_REQUESTED"


def test_resolve_event_confirm_rejects_unsafe_low_confidence_match(client, test_user, db_session):
    """Requirement F/8: backend-level defense in depth. Even a row where
    matched_application_id IS set (e.g. a pre-fix row, or a future bug that
    bypasses job_email_scan_service's own gate) must be rejected by the
    /resolve confirm endpoint itself when match_confidence is below
    RELIABLE_MATCH_CONFIDENCE_THRESHOLD — never trusting the frontend button
    alone. A direct API call must not be able to confirm it."""
    connection = _seed_connection(db_session, test_user.id)
    application = _seed_application(db_session, test_user.id, company="Computer Merchant", title="Software Development Engineer")
    event = _seed_event(
        db_session, test_user.id, connection.id,
        matched_application_id=application.id, match_confidence=0.2, confidence=0.135,
    )
    response = client.post(f"/api/email/events/{test_user.id}/{event.id}/resolve", json={"action": "confirm"})
    assert response.status_code == 422

    db_session.refresh(application)
    assert application.status == "APPLIED"  # never touched
    db_session.refresh(event)
    assert event.resolution == "NEEDS_CONFIRMATION"  # never silently confirmed


def test_resolve_event_confirm_rejects_legacy_row_with_null_match_confidence(client, test_user, db_session):
    """Requirement 1 — the exact reported bug: a JobEmailEvent row created
    before match_confidence existed has matched_application_id set but
    match_confidence=None. Confirmation must be rejected (422), and must
    NOT be inferred from the older, unrelated blended `confidence` field
    (0.135 here, deliberately set to prove the check isn't reading it)."""
    connection = _seed_connection(db_session, test_user.id)
    application = _seed_application(db_session, test_user.id, company="CareerBoost Test", title="Software Engineer")
    event = _seed_event(
        db_session, test_user.id, connection.id,
        subject="Application Received - CareerBoost Test",
        matched_application_id=application.id, match_confidence=None, confidence=0.135,
    )
    response = client.post(f"/api/email/events/{test_user.id}/{event.id}/resolve", json={"action": "confirm"})
    assert response.status_code == 422

    db_session.refresh(application)
    assert application.status == "APPLIED"  # never touched
    db_session.refresh(event)
    assert event.resolution == "NEEDS_CONFIRMATION"


def test_resolve_event_ignore_still_works_for_legacy_null_match_confidence_row(client, test_user, db_session):
    """Requirement 6."""
    connection = _seed_connection(db_session, test_user.id)
    application = _seed_application(db_session, test_user.id)
    event = _seed_event(
        db_session, test_user.id, connection.id,
        matched_application_id=application.id, match_confidence=None, confidence=0.135,
    )
    response = client.post(f"/api/email/events/{test_user.id}/{event.id}/resolve", json={"action": "ignore"})
    assert response.status_code == 200
    assert response.json()["resolution"] == "IGNORED"


# ---------------------------------------------------------------------------
# CURRENT LIVE BUG regression at the route level (Requirements F, G, 8, 9,
# 10): Confirm must update exactly the matched application, and must
# independently reject a stale/incorrect matched_application_id even when
# match_confidence nominally clears the threshold.
# ---------------------------------------------------------------------------

def _seed_ses_and_computer_merchant(db_session, user_id):
    ses = _seed_application(db_session, user_id, company="SES", title="Sr. Java Developer (Java 21, Apache Kafka, SQL, AWS)")
    computer_merchant = _seed_application(db_session, user_id, company="Computer Merchant", title="Software Development Engineer (SDET)")
    return ses, computer_merchant


_ASSESSMENT_SUBJECT = "Assessment Request - Software Development Engineer (SDET) at Computer Merchant"
_ASSESSMENT_BODY = (
    "Hi, thank you for applying to the Software Development Engineer (SDET) role at "
    "Computer Merchant. We would like you to complete an online technical assessment "
    "as the next step in our process."
)


def test_resolve_event_confirm_updates_exactly_the_matched_row_and_no_other(client, test_user, db_session):
    """Requirement F."""
    connection = _seed_connection(db_session, test_user.id)
    ses, computer_merchant = _seed_ses_and_computer_merchant(db_session, test_user.id)
    event = _seed_event(
        db_session, test_user.id, connection.id,
        sender="ats@computermerchant-noreply.example.com",
        subject=_ASSESSMENT_SUBJECT, body_snippet=_ASSESSMENT_BODY,
        event_type="ASSESSMENT_REQUEST",
        matched_application_id=computer_merchant.id, matched_job_id=None,
        match_confidence=0.8, confidence=0.75,
    )
    response = client.post(f"/api/email/events/{test_user.id}/{event.id}/resolve", json={"action": "confirm"})
    assert response.status_code == 200
    assert response.json()["resolution"] == "CONFIRMED"

    db_session.refresh(computer_merchant)
    db_session.refresh(ses)
    assert computer_merchant.status == "ASSESSMENT_REQUESTED"
    assert ses.status == "APPLIED"  # the other row is never touched


def test_resolve_event_confirm_rejects_stale_matched_application_despite_high_confidence(client, test_user, db_session):
    """Requirement G — the exact reported bug at the route level: a
    JobEmailEvent whose matched_application_id points at the WRONG
    application (SES) while match_confidence sits exactly at (or above) the
    reliable threshold must still be rejected, because the event's own
    recorded subject/body unambiguously names a different, known company
    (Computer Merchant). The independent re-check (is_match_still_reliable)
    must catch this even though the raw stored number alone would pass."""
    connection = _seed_connection(db_session, test_user.id)
    ses, computer_merchant = _seed_ses_and_computer_merchant(db_session, test_user.id)
    event = _seed_event(
        db_session, test_user.id, connection.id,
        sender="ats@computermerchant-noreply.example.com",
        subject=_ASSESSMENT_SUBJECT, body_snippet=_ASSESSMENT_BODY,
        event_type="ASSESSMENT_REQUEST",
        matched_application_id=ses.id, matched_job_id=None,
        match_confidence=0.5, confidence=0.5,
    )
    response = client.post(f"/api/email/events/{test_user.id}/{event.id}/resolve", json={"action": "confirm"})
    assert response.status_code == 422

    db_session.refresh(ses)
    assert ses.status == "APPLIED"  # never touched — this is the unsafe update the bug report describes

    db_session.refresh(event)
    assert event.matched_application_id is None
    assert event.matched_job_id is None
    assert event.resolution == "NEEDS_CONFIRMATION"


def test_events_list_exposes_matched_application_title_and_company_for_display(client, test_user, db_session):
    """Requirement 9: the frontend needs to show which application is being
    proposed before Confirm — the events list must carry the matched
    application's title/company, not just its id."""
    connection = _seed_connection(db_session, test_user.id)
    _, computer_merchant = _seed_ses_and_computer_merchant(db_session, test_user.id)
    _seed_event(
        db_session, test_user.id, connection.id,
        sender="ats@computermerchant-noreply.example.com",
        subject=_ASSESSMENT_SUBJECT, body_snippet=_ASSESSMENT_BODY,
        event_type="ASSESSMENT_REQUEST",
        matched_application_id=computer_merchant.id, match_confidence=0.8, confidence=0.75,
    )
    response = client.get(f"/api/email/events/{test_user.id}")
    assert response.status_code == 200
    event_payload = response.json()["events"][0]
    assert event_payload["matched_application_title"] == "Software Development Engineer (SDET)"
    assert event_payload["matched_application_company"] == "Computer Merchant"


# ---------------------------------------------------------------------------
# Stale/legacy stored-match presentation (this task): a JobEmailEvent row
# persisted before the matcher fixes existed can carry a matched_application_id
# that CURRENT matching logic would reject — GET /events must sanitize that
# presentation at read time without ever writing to the historical row
# (Requirements 1-6, A-H).
# ---------------------------------------------------------------------------

def _seed_legacy_stale_computer_merchant_event(db_session, user_id, connection_id, ses_application_id, **overrides):
    """Shaped exactly like the reported historical row: a Computer Merchant
    assessment email whose matched_application_id/matched_job_id/company
    were stored pointing at an unrelated SES application — the kind of
    value the OLD (pre-substring-fix) matcher could produce, sitting
    unresolved in the database today."""
    defaults = dict(
        sender="ats@computermerchant-noreply.example.com",
        subject=_ASSESSMENT_SUBJECT, body_snippet=_ASSESSMENT_BODY,
        event_type="ASSESSMENT_REQUEST",
        matched_application_id=ses_application_id, matched_job_id=1438,
        match_confidence=0.5, confidence=0.5,
        resolution="NEEDS_CONFIRMATION",
    )
    defaults.update(overrides)
    return _seed_event(db_session, user_id, connection_id, **defaults)


def test_legacy_stale_match_is_sanitized_in_events_list(client, test_user, db_session):
    """Requirements A/B: GET /events re-validates a stored NEEDS_CONFIRMATION
    match against CURRENT matching logic and nulls the unsafe fields in the
    API response — the exact reported historical Computer Merchant -> SES
    row must no longer be exposed as a confirmable match."""
    connection = _seed_connection(db_session, test_user.id)
    ses, _computer_merchant = _seed_ses_and_computer_merchant(db_session, test_user.id)
    event = _seed_legacy_stale_computer_merchant_event(db_session, test_user.id, connection.id, ses.id)

    response = client.get(f"/api/email/events/{test_user.id}")
    assert response.status_code == 200
    payload = response.json()["events"][0]
    assert payload["matched_application_id"] is None
    assert payload["matched_job_id"] is None
    assert payload["matched_application_title"] is None
    assert payload["matched_application_company"] is None
    # Requirement 3: the raw stored match_confidence is preserved for audit,
    # never fabricated or silently zeroed just because presentation was sanitized.
    assert payload["match_confidence"] == 0.5

    # Requirement 2: the underlying row itself is never modified by a read.
    db_session.refresh(event)
    assert event.matched_application_id == ses.id
    assert event.matched_job_id == 1438
    assert event.resolution == "NEEDS_CONFIRMATION"


def test_historical_confirmed_event_is_never_rewritten_by_events_list(client, test_user, db_session):
    """Requirement E: a settled CONFIRMED row (a real audit record) must be
    returned as-is on every GET /events call, even if its stored match would
    fail today's reliability check — historical audit records are never
    re-validated or altered at read time."""
    connection = _seed_connection(db_session, test_user.id)
    ses, _computer_merchant = _seed_ses_and_computer_merchant(db_session, test_user.id)
    event = _seed_legacy_stale_computer_merchant_event(
        db_session, test_user.id, connection.id, ses.id, resolution="CONFIRMED",
    )

    response = client.get(f"/api/email/events/{test_user.id}")
    assert response.status_code == 200
    payload = response.json()["events"][0]
    assert payload["matched_application_id"] == ses.id
    assert payload["matched_job_id"] == 1438
    assert payload["resolution"] == "CONFIRMED"

    db_session.refresh(event)
    assert event.matched_application_id == ses.id  # untouched in the database too


def test_confirm_endpoint_still_rejects_the_same_stale_match(client, test_user, db_session):
    """Requirement I: the /resolve confirm endpoint's own independent
    revalidation (added previously) must remain in force regardless of the
    new read-time sanitization — a direct confirm attempt against the stale
    row is still rejected and updates nothing."""
    connection = _seed_connection(db_session, test_user.id)
    ses, _computer_merchant = _seed_ses_and_computer_merchant(db_session, test_user.id)
    event = _seed_legacy_stale_computer_merchant_event(db_session, test_user.id, connection.id, ses.id)

    response = client.post(f"/api/email/events/{test_user.id}/{event.id}/resolve", json={"action": "confirm"})
    assert response.status_code == 422

    db_session.refresh(ses)
    assert ses.status == "APPLIED"


def test_current_valid_matches_still_display_correctly_alongside_sanitized_legacy_row(client, test_user, db_session):
    """Requirements F/G/H: current, genuinely reliable matches (System One,
    the canonicalized Computer Merchant/Ltd. match, and a direct SES
    recruiter match) must keep displaying their matched application, even
    in the same events list as a sanitized stale row."""
    from app.models.application_tracking_model import ApplicationTracking

    connection = _seed_connection(db_session, test_user.id)
    ses = _seed_application(db_session, test_user.id, company="SES", title="Sr. Java Developer (Java 21, Apache Kafka, SQL, AWS)")
    system_one = ApplicationTracking(
        user_id=test_user.id, job_id=None, company="System One",
        title="Java Backend Developer", status="APPLIED", status_source="system",
    )
    computer_merchant_ltd = ApplicationTracking(
        user_id=test_user.id, job_id=None, company="Computer Merchant, Ltd., The",
        title="Software Development Engineer (SDET)", status="APPLIED", status_source="system",
    )
    db_session.add_all([system_one, computer_merchant_ltd])
    db_session.commit()
    db_session.refresh(system_one)
    db_session.refresh(computer_merchant_ltd)

    # A stale legacy event (Computer Merchant email, stored match = SES).
    stale_event = _seed_legacy_stale_computer_merchant_event(db_session, test_user.id, connection.id, ses.id, message_id="legacy-1")

    # F: current, valid System One interview match.
    system_one_event = _seed_event(
        db_session, test_user.id, connection.id, message_id="sysone-1",
        sender="recruiter@systemone.com", subject="Interview Invitation - Java Backend Developer",
        body_snippet="We would like to schedule an interview with you for the Java Backend Developer role at System One.",
        event_type="INTERVIEW_INVITE", matched_application_id=system_one.id, matched_job_id=None,
        match_confidence=1.0, confidence=0.95,
    )

    # G: current, normalized Computer Merchant, Ltd. SDET match.
    cm_ltd_event = _seed_event(
        db_session, test_user.id, connection.id, message_id="cmltd-1",
        sender="ats@computermerchant-noreply.example.com", subject=_ASSESSMENT_SUBJECT, body_snippet=_ASSESSMENT_BODY,
        event_type="ASSESSMENT_REQUEST", matched_application_id=computer_merchant_ltd.id, matched_job_id=None,
        match_confidence=1.0, confidence=0.9,
    )

    # H: current, direct SES recruiter match (SES actually named in the email).
    ses_event = _seed_event(
        db_session, test_user.id, connection.id, message_id="ses-1",
        sender="recruiter@ses.com", subject="Interview Invitation - Sr. Java Developer",
        body_snippet="We would like to schedule an interview with you for the Sr. Java Developer role at SES.",
        event_type="INTERVIEW_INVITE", matched_application_id=ses.id, matched_job_id=None,
        match_confidence=1.0, confidence=0.95,
    )

    response = client.get(f"/api/email/events/{test_user.id}")
    assert response.status_code == 200
    by_id = {event["id"]: event for event in response.json()["events"]}

    assert by_id[stale_event.id]["matched_application_id"] is None

    assert by_id[system_one_event.id]["matched_application_id"] == system_one.id
    assert by_id[system_one_event.id]["matched_application_company"] == "System One"

    assert by_id[cm_ltd_event.id]["matched_application_id"] == computer_merchant_ltd.id
    assert by_id[cm_ltd_event.id]["matched_application_company"] == "Computer Merchant, Ltd., The"

    assert by_id[ses_event.id]["matched_application_id"] == ses.id
    assert by_id[ses_event.id]["matched_application_company"] == "SES"


# ---------------------------------------------------------------------------
# Company-name canonicalization (this task), at the route level: CareerBoost
# stores the messy legal name "Computer Merchant, Ltd., The" while the email
# only says "Computer Merchant" — Confirm must still accept it (Requirement H).
# ---------------------------------------------------------------------------

_FIX_TEST_SUBJECT = "Assessment Request Fix Test - Software Development Engineer (SDET) at Computer Merchant"
_FIX_TEST_BODY = (
    "Hi, thank you for applying to the Software Development Engineer (SDET) role at "
    "Computer Merchant. We would like you to complete an online technical assessment "
    "as the next step in our process."
)


def test_resolve_event_confirm_accepts_canonicalized_company_match(client, test_user, db_session):
    """Requirement H: Confirm re-validation (is_match_still_reliable) must
    accept a match where the stored company is the messy legal name and the
    email uses the short form — canonical company identity, not literal
    string equality, is what the confirm-route defense checks."""
    connection = _seed_connection(db_session, test_user.id)
    application = _seed_application(
        db_session, test_user.id, company="Computer Merchant, Ltd., The",
        title="Software Development Engineer (SDET)",
    )
    event = _seed_event(
        db_session, test_user.id, connection.id,
        sender="ats@computermerchant-noreply.example.com",
        subject=_FIX_TEST_SUBJECT, body_snippet=_FIX_TEST_BODY,
        event_type="ASSESSMENT_REQUEST",
        matched_application_id=application.id, matched_job_id=None,
        match_confidence=0.8, confidence=0.75,
    )
    response = client.post(f"/api/email/events/{test_user.id}/{event.id}/resolve", json={"action": "confirm"})
    assert response.status_code == 200
    assert response.json()["resolution"] == "CONFIRMED"

    db_session.refresh(application)
    assert application.status == "ASSESSMENT_REQUESTED"

    db_session.refresh(event)
    assert event.matched_application_id == application.id


# ---------------------------------------------------------------------------
# Legacy unrelated events (rows created before the job-relevance fix) —
# Requirements 5, O, P: backend prevention, never destructively deleted.
# ---------------------------------------------------------------------------

def _seed_legacy_unrelated_event(db_session, user_id, connection_id, **overrides):
    """A row shaped like what the OLD, too-permissive classifier could have
    persisted for a Shutterfly-style promotional email: event_type=OTHER,
    a matched_application_id somehow present, but the content is clearly
    not job-related under the current (fixed) classifier."""
    defaults = dict(
        message_id="promo-legacy-1",
        sender="Shutterfly <shutterfly@em.shutterfly.com>",
        subject="Here it is: Our holiday first look & 50% off ALL cards!",
        body_snippet="Shop our newest holiday cards now and save. This exclusive offer ends soon.",
        event_type="OTHER", confidence=0.2, match_confidence=0.6,
        resolution="NEEDS_CONFIRMATION",
    )
    defaults.update(overrides)
    return _seed_event(db_session, user_id, connection_id, **defaults)


def test_no_unrelated_message_can_be_confirmed_via_backend_api(client, test_user, db_session):
    """Requirement O."""
    connection = _seed_connection(db_session, test_user.id)
    application = _seed_application(db_session, test_user.id)
    event = _seed_legacy_unrelated_event(db_session, test_user.id, connection.id, matched_application_id=application.id)

    response = client.post(f"/api/email/events/{test_user.id}/{event.id}/resolve", json={"action": "confirm"})
    assert response.status_code == 422

    db_session.refresh(application)
    assert application.status == "APPLIED"  # untouched
    db_session.refresh(event)
    assert event.resolution == "NEEDS_CONFIRMATION"


def test_legacy_unrelated_event_excluded_from_events_list(client, test_user, db_session):
    """Requirement 5/P (backend half): the list endpoint itself must not
    return a clearly-unrelated legacy row — the frontend never even
    receives it to render. The underlying row is not deleted."""
    connection = _seed_connection(db_session, test_user.id)
    unrelated = _seed_legacy_unrelated_event(db_session, test_user.id, connection.id)
    related = _seed_event(db_session, test_user.id, connection.id, message_id="job-1", event_type="APPLICATION_CONFIRMATION")

    response = client.get(f"/api/email/events/{test_user.id}")
    assert response.status_code == 200
    event_ids = {event["id"] for event in response.json()["events"]}
    assert related.id in event_ids
    assert unrelated.id not in event_ids

    # Never destructively deleted — the row still exists in the database.
    assert db_session.query(JobEmailEvent).filter_by(id=unrelated.id).first() is not None


def test_events_list_returns_received_at(client, test_user, db_session):
    """Requirement C — the actual Gmail-received timestamp survives through
    to the API response, not null and not the scan/request time."""
    message_received_at = datetime(2024, 10, 15, 13, 20, 0, tzinfo=timezone.utc)
    connection = _seed_connection(db_session, test_user.id)
    _seed_event(db_session, test_user.id, connection.id, received_at=message_received_at)

    response = client.get(f"/api/email/events/{test_user.id}")
    assert response.status_code == 200
    events = response.json()["events"]
    assert len(events) == 1
    assert events[0]["received_at"] is not None
    assert events[0]["received_at"].startswith("2024-10-15T13:20:00")


def test_events_list_received_at_null_for_event_without_one(client, test_user, db_session):
    """Requirement E, at the API-response level: an event with no resolvable
    received_at reports null rather than fabricating a value."""
    connection = _seed_connection(db_session, test_user.id)
    _seed_event(db_session, test_user.id, connection.id, received_at=None)

    response = client.get(f"/api/email/events/{test_user.id}")
    assert response.json()["events"][0]["received_at"] is None


def test_resolve_event_invalid_action(client, test_user, db_session):
    connection = _seed_connection(db_session, test_user.id)
    event = _seed_event(db_session, test_user.id, connection.id)
    response = client.post(f"/api/email/events/{test_user.id}/{event.id}/resolve", json={"action": "bogus"})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Production error handling (Part 20)
# ---------------------------------------------------------------------------

def test_scan_returns_409_when_gmail_not_connected(client, test_user):
    response = client.post(f"/api/email/gmail/scan/{test_user.id}")
    assert response.status_code == 409


def test_scan_returns_502_on_gmail_api_failure(client, test_user, db_session):
    _seed_connection(db_session, test_user.id)
    with patch("app.routes.email_routes.job_email_scan_service.scan_for_job_emails", side_effect=gmail_service.GmailApiError("HTTP 500")):
        response = client.post(f"/api/email/gmail/scan/{test_user.id}")
    assert response.status_code == 502
    assert "stack" not in response.text.lower()
    assert "traceback" not in response.text.lower()


# ---------------------------------------------------------------------------
# CURRENT VERIFIED BUG fix: a single Gmail message id returning HTTP 404
# must not fail the whole "Scan Job Emails" request — the manual scan route
# must still return 200 with a normal summary, never a 500/"Failed to fetch".
# ---------------------------------------------------------------------------

def test_manual_scan_returns_200_when_one_message_is_missing_404(client, test_user, db_session):
    from unittest.mock import MagicMock

    connection = _seed_connection(db_session, test_user.id)
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
        response = client.post(f"/api/email/gmail/scan/{test_user.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["messages_checked"] == 1
    assert body["outcomes"].get("fetch_failed") == 1


# ---------------------------------------------------------------------------
# Security — Gmail stays read-only, no write endpoints, no secrets exposed
# ---------------------------------------------------------------------------

# Recruiter Draft / Suggested Reply feature removal: Gmail is read-only and
# the app could never actually send a generated reply, so the entire
# /api/email/drafts/* surface (list, generate, status, health, update) is
# gone — not merely disabled behind a flag.

def test_drafts_list_route_removed(client, test_user):
    assert client.get(f"/api/email/drafts/{test_user.id}").status_code == 404


def test_drafts_generate_route_removed(client, test_user):
    assert client.post(f"/api/email/drafts/{test_user.id}/generate", json={"job_email_event_id": 1}).status_code == 404


def test_drafts_status_route_removed(client, test_user):
    assert client.get(f"/api/email/drafts/{test_user.id}/1/status").status_code == 404


def test_drafts_health_route_removed(client, test_user):
    assert client.get(f"/api/email/drafts/{test_user.id}/health").status_code == 404


def test_drafts_update_route_removed(client, test_user):
    assert client.patch(f"/api/email/drafts/{test_user.id}/1", json={"edited_body": "x"}).status_code == 404


def test_gmail_scope_is_readonly_only():
    from app.services import gmail_service

    assert gmail_service.SCOPES == "https://www.googleapis.com/auth/gmail.readonly"
    assert "compose" not in gmail_service.SCOPES
    assert "send" not in gmail_service.SCOPES
    assert "modify" not in gmail_service.SCOPES


# ---------------------------------------------------------------------------
# Daily Summary stays disabled (Email Summary redesign — Part 12)
# ---------------------------------------------------------------------------

def test_generate_summary_stays_disabled_and_creates_no_log(client, test_user, test_resume, java_job):
    """Daily Summary generation is permanently disabled: /generate always
    returns 410 regardless of match state, and never creates an email_logs
    row — the redesigned Email Summary page has no "Generate Email Summary"
    control anywhere."""
    client.post(f"/api/match/calculate/{test_user.id}")
    generate_response = client.post(f"/api/email/generate/{test_user.id}")
    assert generate_response.status_code == 410

    logs_response = client.get(f"/api/email/logs/{test_user.id}")
    assert logs_response.status_code == 404


# ---------------------------------------------------------------------------
# CareerBoost-local read/star state (Email Summary redesign) — Gmail stays
# read-only, so these never call gmail_service or change resolution/matched_*.
# ---------------------------------------------------------------------------

def test_new_event_defaults_to_unread_and_unstarred(client, test_user, db_session):
    connection = _seed_connection(db_session, test_user.id)
    _seed_event(db_session, test_user.id, connection.id)
    response = client.get(f"/api/email/events/{test_user.id}")
    assert response.status_code == 200
    event_payload = response.json()["events"][0]
    assert event_payload["is_read"] is False
    assert event_payload["is_starred"] is False


def test_mark_read_sets_is_read_and_is_idempotent(client, test_user, db_session):
    connection = _seed_connection(db_session, test_user.id)
    event = _seed_event(db_session, test_user.id, connection.id)
    first = client.post(f"/api/email/events/{test_user.id}/{event.id}/mark-read")
    assert first.status_code == 200
    assert first.json()["is_read"] is True
    second = client.post(f"/api/email/events/{test_user.id}/{event.id}/mark-read")
    assert second.status_code == 200
    assert second.json()["is_read"] is True  # idempotent — never errors on an already-read event


def test_toggle_star_flips_is_starred_each_call(client, test_user, db_session):
    connection = _seed_connection(db_session, test_user.id)
    event = _seed_event(db_session, test_user.id, connection.id)
    first = client.post(f"/api/email/events/{test_user.id}/{event.id}/toggle-star")
    assert first.status_code == 200
    assert first.json()["is_starred"] is True
    second = client.post(f"/api/email/events/{test_user.id}/{event.id}/toggle-star")
    assert second.status_code == 200
    assert second.json()["is_starred"] is False


def test_mark_read_and_toggle_star_never_touch_resolution_or_match(client, test_user, db_session):
    connection = _seed_connection(db_session, test_user.id)
    event = _seed_event(db_session, test_user.id, connection.id, matched_application_id=None, resolution="NEEDS_CONFIRMATION")
    client.post(f"/api/email/events/{test_user.id}/{event.id}/mark-read")
    client.post(f"/api/email/events/{test_user.id}/{event.id}/toggle-star")
    db_session.refresh(event)
    assert event.resolution == "NEEDS_CONFIRMATION"
    assert event.matched_application_id is None


def test_mark_read_and_toggle_star_never_call_gmail(client, test_user, db_session):
    connection = _seed_connection(db_session, test_user.id)
    event = _seed_event(db_session, test_user.id, connection.id)
    with patch("app.services.gmail_service.get_provider_for_user") as get_provider:
        client.post(f"/api/email/events/{test_user.id}/{event.id}/mark-read")
        client.post(f"/api/email/events/{test_user.id}/{event.id}/toggle-star")
    get_provider.assert_not_called()


def test_user_a_cannot_mark_read_or_star_user_b_event(client, test_user, second_user, db_session):
    connection = _seed_connection(db_session, test_user.id)
    event = _seed_event(db_session, test_user.id, connection.id)
    assert client.post(f"/api/email/events/{second_user.id}/{event.id}/mark-read").status_code == 404
    assert client.post(f"/api/email/events/{second_user.id}/{event.id}/toggle-star").status_code == 404
    db_session.refresh(event)
    assert event.is_read is False
    assert event.is_starred is False


def test_mark_read_missing_event_returns_404(client, test_user):
    response = client.post(f"/api/email/events/{test_user.id}/999999/mark-read")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# body_formatted — paragraph-preserving text for Email Summary's View
# Details full email body (display-only; body_snippet is unchanged).
# ---------------------------------------------------------------------------

def test_events_list_exposes_body_formatted_separately_from_body_snippet(client, test_user, db_session):
    connection = _seed_connection(db_session, test_user.id)
    _seed_event(
        db_session, test_user.id, connection.id,
        body_snippet="Hi, Thank you for applying.",
        body_formatted="Hi,\n\nThank you for applying.",
    )
    response = client.get(f"/api/email/events/{test_user.id}")
    assert response.status_code == 200
    event_payload = response.json()["events"][0]
    assert event_payload["body_snippet"] == "Hi, Thank you for applying."
    assert event_payload["body_formatted"] == "Hi,\n\nThank you for applying."


def test_events_list_body_formatted_falls_back_to_body_snippet_for_a_legacy_row(client, test_user, db_session):
    connection = _seed_connection(db_session, test_user.id)
    _seed_event(
        db_session, test_user.id, connection.id,
        body_snippet="Hi, Thank you for applying.",
        body_formatted=None,  # a row scanned before this field existed
    )
    response = client.get(f"/api/email/events/{test_user.id}")
    event_payload = response.json()["events"][0]
    assert event_payload["body_formatted"] == "Hi, Thank you for applying."
