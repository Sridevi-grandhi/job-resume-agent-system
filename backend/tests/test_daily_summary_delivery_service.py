"""Focused tests for the explicit SMTP Daily Summary delivery endpoint.

Daily Summary delivery is intentionally disabled (Email Summary redesign):
POST /api/email/delivery/{user_id}/{email_log_id} always returns 410 before
any of the old delivery logic runs — regardless of user ownership, existing
delivery state, or SMTP outcome. These tests verify that disabled behavior
stays disabled: SMTP is never invoked, no SummaryDeliveryAttempt row is ever
created, and any pre-existing historical row is left completely untouched.
"""

from __future__ import annotations

from unittest.mock import patch

from app.models.email_log_model import EmailLog
from app.models.summary_delivery_model import SummaryDeliveryAttempt


def _summary(db_session, user_id, *, recipient="jane@example.com"):
    row = EmailLog(
        user_id=user_id,
        recipient_email=recipient,
        subject="Your CareerBoost summary",
        body="Top matches for today.",
        status="generated",
        event_type="summary_generated",
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _deliver(client, user_id, summary_id, **payload):
    return client.post(f"/api/email/delivery/{user_id}/{summary_id}", json=payload)


def test_generate_stays_disabled_and_never_creates_a_delivery_attempt(client, test_user, test_resume, java_job, db_session):
    client.post(f"/api/match/calculate/{test_user.id}")
    response = client.post(f"/api/email/generate/{test_user.id}")
    assert response.status_code == 410
    assert db_session.query(SummaryDeliveryAttempt).count() == 0


def test_delivery_stays_disabled_and_never_calls_smtp(client, test_user, db_session):
    summary = _summary(db_session, test_user.id)
    with patch("app.services.daily_summary_delivery_service.send_email_message") as smtp:
        response = _deliver(client, test_user.id, summary.id, idempotency_key="summary-success-1")

    assert response.status_code == 410
    smtp.assert_not_called()
    assert db_session.query(EmailLog).filter_by(id=summary.id).one().status == "generated"
    assert db_session.query(SummaryDeliveryAttempt).filter_by(email_log_id=summary.id).count() == 0


def test_delivery_stays_disabled_even_when_smtp_would_fail(client, test_user, db_session):
    summary = _summary(db_session, test_user.id)
    with patch("app.services.daily_summary_delivery_service.send_email_message", return_value={"sent": False, "reason": "provider_error"}) as smtp:
        response = _deliver(client, test_user.id, summary.id)

    assert response.status_code == 410
    smtp.assert_not_called()
    assert db_session.query(EmailLog).filter_by(id=summary.id).one().body == "Top matches for today."
    assert db_session.query(SummaryDeliveryAttempt).filter_by(email_log_id=summary.id).count() == 0


def test_repeated_delivery_calls_all_stay_disabled_with_no_attempts_recorded(client, test_user, db_session, monkeypatch):
    monkeypatch.setenv("DAILY_SUMMARY_DELIVERY_MAX_ATTEMPTS", "2")
    summary = _summary(db_session, test_user.id)
    with patch("app.services.daily_summary_delivery_service.send_email_message", return_value={"sent": False, "reason": "provider_error"}) as smtp:
        first = _deliver(client, test_user.id, summary.id)
        second = _deliver(client, test_user.id, summary.id)
        third = _deliver(client, test_user.id, summary.id)

    assert first.status_code == 410
    assert second.status_code == 410
    assert third.status_code == 410
    smtp.assert_not_called()
    assert db_session.query(SummaryDeliveryAttempt).filter_by(email_log_id=summary.id).count() == 0


def test_duplicate_delivery_requests_all_stay_disabled_smtp_never_called(client, test_user, db_session):
    summary = _summary(db_session, test_user.id)
    with patch("app.services.daily_summary_delivery_service.send_email_message", return_value={"sent": True, "reason": None}) as smtp:
        first = _deliver(client, test_user.id, summary.id, idempotency_key="stable-request-key")
        second = _deliver(client, test_user.id, summary.id, idempotency_key="another-request-key")

    assert first.status_code == 410
    assert second.status_code == 410
    smtp.assert_not_called()
    assert db_session.query(SummaryDeliveryAttempt).filter_by(email_log_id=summary.id).count() == 0


def test_delivery_route_never_touches_a_pre_existing_attempt_row(client, test_user, db_session):
    """Historical Daily Summary delivery data is preserved, never mutated,
    by a call to the now-disabled route."""
    summary = _summary(db_session, test_user.id)
    record = SummaryDeliveryAttempt(
        user_id=test_user.id, email_log_id=summary.id, recipient_email=test_user.email,
        delivery_status="SENDING", attempt_count=1, idempotency_key="stuck-sending-summary",
    )
    db_session.add(record)
    db_session.commit()
    with patch("app.services.daily_summary_delivery_service.send_email_message") as smtp:
        response = _deliver(client, test_user.id, summary.id)

    assert response.status_code == 410
    smtp.assert_not_called()
    db_session.refresh(record)
    assert record.delivery_status == "SENDING"
    assert record.attempt_count == 1


def test_delivery_stays_disabled_regardless_of_which_user_calls_it(client, test_user, second_user, db_session):
    summary = _summary(db_session, test_user.id)
    response = _deliver(client, second_user.id, summary.id)
    assert response.status_code == 410
    assert db_session.query(SummaryDeliveryAttempt).count() == 0
