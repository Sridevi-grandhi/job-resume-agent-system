"""SMTP delivery lifecycle for generated Daily Summaries only.

Generation remains owned by the Email Summary route/agent.  This service is
intentionally not scheduled or called by summary generation: delivery is an
explicit, separately persisted operation until preferences/scheduling are
introduced in later phases.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session
from sqlalchemy import update

from app.models.email_log_model import EmailLog
from app.models.summary_delivery_model import SummaryDeliveryAttempt
from app.services.auth_email_service import send_email_message


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def max_delivery_attempts() -> int:
    """Finite, environment-configurable limit; malformed values stay safe."""
    try:
        configured = int(os.getenv("DAILY_SUMMARY_DELIVERY_MAX_ATTEMPTS", "3"))
    except ValueError:
        configured = 3
    return max(1, min(configured, 10))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_recipient(recipient: str) -> str:
    candidate = (recipient or "").strip()
    if not _EMAIL_RE.fullmatch(candidate):
        raise ValueError("A valid delivery recipient is required.")
    return candidate


def validate_recipient(recipient: str) -> str:
    """Shared validation for explicit and scheduled delivery callers."""
    return _safe_recipient(recipient)


def _safe_idempotency_key(value: str | None, email_log_id: int) -> str:
    key = (value or f"daily-summary-{email_log_id}").strip()
    if not key or len(key) > 128 or not re.fullmatch(r"[A-Za-z0-9._:-]+", key):
        raise ValueError("Invalid idempotency key.")
    return key


def _retry_at(attempt_count: int) -> datetime:
    # Persist the retry schedule for a future worker/scheduler. No Phase-1
    # scheduler is added; an explicit retry request is still bounded below.
    delay_seconds = min(3600, 60 * (2 ** max(0, attempt_count - 1)))
    return _now() + timedelta(seconds=delay_seconds)


def get_or_create_delivery(
    db: Session, *, user_id: int, email_log: EmailLog, recipient_email: str | None = None,
    idempotency_key: str | None = None,
) -> SummaryDeliveryAttempt:
    if email_log.user_id != user_id:
        raise LookupError("Summary not found")
    recipient = _safe_recipient(recipient_email or email_log.recipient_email)
    key = _safe_idempotency_key(idempotency_key, email_log.id)

    existing = db.query(SummaryDeliveryAttempt).filter(
        SummaryDeliveryAttempt.email_log_id == email_log.id,
        SummaryDeliveryAttempt.user_id == user_id,
    ).first()
    if existing is not None:
        return existing

    delivery = SummaryDeliveryAttempt(
        user_id=user_id,
        email_log_id=email_log.id,
        recipient_email=recipient,
        delivery_status="GENERATED",
        idempotency_key=key,
    )
    db.add(delivery)
    try:
        db.commit()
    except Exception:
        # A unique-constraint race is resolved by reading the authoritative
        # row. The transaction is rolled back before the caller continues.
        db.rollback()
        delivery = db.query(SummaryDeliveryAttempt).filter(
            SummaryDeliveryAttempt.email_log_id == email_log.id,
            SummaryDeliveryAttempt.user_id == user_id,
        ).first()
        if delivery is None:
            raise
    db.refresh(delivery)
    return delivery


def deliver_generated_summary(
    db: Session, *, user_id: int, email_log: EmailLog, recipient_email: str | None = None,
    idempotency_key: str | None = None,
) -> SummaryDeliveryAttempt:
    """Attempt SMTP delivery without mutating the generated ``EmailLog``.

    Calls after SENT are no-ops. Failed attempts can be explicitly retried,
    but never beyond the persisted finite limit. Gmail is not referenced.
    """
    delivery = get_or_create_delivery(
        db, user_id=user_id, email_log=email_log, recipient_email=recipient_email,
        idempotency_key=idempotency_key,
    )
    if delivery.delivery_status == "SENT" or delivery.attempt_count >= max_delivery_attempts():
        return delivery

    now = _now()
    # A persisted SENDING state is intentionally fail-closed: a process may
    # have stopped after handing the message to SMTP but before recording its
    # result. Retrying it blindly could duplicate email. Recovery policy for
    # stale SENDING records belongs with a later scheduled worker phase.
    if delivery.delivery_status == "SENDING":
        return delivery
    if delivery.delivery_status == "FAILED" and delivery.next_retry_at:
        retry_at = delivery.next_retry_at
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        if now < retry_at:
            return delivery

    # Database compare-and-swap is the cross-process send claim. Two
    # schedulers may both read GENERATED, but only one can atomically change
    # it to SENDING; the other returns without calling SMTP.
    claimed = db.execute(
        update(SummaryDeliveryAttempt)
        .where(
            SummaryDeliveryAttempt.id == delivery.id,
            SummaryDeliveryAttempt.delivery_status.in_(("GENERATED", "FAILED")),
            SummaryDeliveryAttempt.attempt_count < max_delivery_attempts(),
        )
        .values(
            delivery_status="SENDING",
            queued_at=delivery.queued_at or now,
            sending_at=now,
            attempt_count=SummaryDeliveryAttempt.attempt_count + 1,
        )
    ).rowcount
    db.commit()
    db.refresh(delivery)
    if claimed != 1:
        return delivery

    try:
        result = send_email_message(
            to_email=delivery.recipient_email,
            subject=email_log.subject,
            text_body=email_log.body,
        )
        sent = bool(result.get("sent"))
        reason = result.get("reason")
    except Exception:
        sent = False
        reason = "provider_error"

    if sent:
        delivery.delivery_status = "SENT"
        delivery.sent_at = _now()
        delivery.failed_at = None
        delivery.next_retry_at = None
        delivery.last_error_code = None
        delivery.last_error_message = None
    else:
        delivery.delivery_status = "FAILED"
        delivery.failed_at = _now()
        delivery.last_error_code = str(reason or "provider_error")[:80]
        delivery.last_error_message = "Email delivery could not be completed."
        delivery.next_retry_at = _retry_at(delivery.attempt_count) if delivery.attempt_count < max_delivery_attempts() else None
    db.commit()
    db.refresh(delivery)
    return delivery


def delivery_payload(delivery: SummaryDeliveryAttempt) -> dict:
    return {
        "delivery_id": delivery.id,
        "email_log_id": delivery.email_log_id,
        "recipient_email": delivery.recipient_email,
        "delivery_status": delivery.delivery_status,
        "attempt_count": delivery.attempt_count,
        "queued_at": delivery.queued_at.isoformat() if delivery.queued_at else None,
        "sending_at": delivery.sending_at.isoformat() if delivery.sending_at else None,
        "sent_at": delivery.sent_at.isoformat() if delivery.sent_at else None,
        "failed_at": delivery.failed_at.isoformat() if delivery.failed_at else None,
        "next_retry_at": delivery.next_retry_at.isoformat() if delivery.next_retry_at else None,
        "last_error_code": delivery.last_error_code,
    }
