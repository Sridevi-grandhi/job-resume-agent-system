import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, StrictBool
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user_model import User
from app.models.job_model import Job
from app.models.email_log_model import EmailLog
from app.models.application_tracking_model import APPLICATION_STATUSES
from app.models.job_email_event_model import JobEmailEvent
from app.models.summary_delivery_model import SummaryDeliveryAttempt
from app.models.email_summary_preference_model import EmailSummaryPreference
from app.services import daily_summary_service
from app.services import application_tracking_service as tracking
from app.services import gmail_service
from app.services import job_email_scan_service
from app.services.email_token_crypto import is_configured as token_crypto_configured
from app.services import daily_summary_delivery_service
from app.services.job_email_scan_service import ALL_EMAIL_CATEGORIES

router = APIRouter(
    prefix="/api/email",
    tags=["Email"]
)


def _get_user(user_id: int, db: Session) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _log_event(db: Session, *, user_id: int, event_type: str, subject: str, body: str, status: str = "ok") -> None:
    user = db.query(User).filter(User.id == user_id).first()
    db.add(EmailLog(
        user_id=user_id,
        recipient_email=user.email if user else "unknown",
        subject=subject[:255],
        body=body[:4000],
        status=status,
        event_type=event_type,
    ))
    db.commit()


def _email_types_to_scan_payload(preference: EmailSummaryPreference | None) -> list[str]:
    """NULL/unset means "all enabled" — see EmailSummaryPreference.email_types_to_scan."""
    if preference is None or not preference.email_types_to_scan:
        return list(ALL_EMAIL_CATEGORIES)
    try:
        values = json.loads(preference.email_types_to_scan)
    except (TypeError, ValueError):
        return list(ALL_EMAIL_CATEGORIES)
    if not isinstance(values, list) or not values:
        return list(ALL_EMAIL_CATEGORIES)
    enabled = [value for value in values if value in ALL_EMAIL_CATEGORIES]
    return enabled or list(ALL_EMAIL_CATEGORIES)


def _email_summary_preferences_payload(preference: EmailSummaryPreference | None, user: User) -> dict:
    """Safe defaults make newly deployed settings inert for every user."""
    recipient = preference.daily_summary_recipient if preference else None
    return {
        "daily_summary_delivery_enabled": bool(preference.daily_summary_delivery_enabled) if preference else False,
        "automatic_gmail_scan_enabled": bool(preference.automatic_gmail_scan_enabled) if preference else False,
        "daily_summary_recipient": recipient,
        "effective_daily_summary_recipient": recipient or user.email,
        "gmail_scan_frequency": (preference.gmail_scan_frequency if preference else None) or "daily",
        "email_types_to_scan": _email_types_to_scan_payload(preference),
    }


class EmailSummaryPreferenceUpdate(BaseModel):
    daily_summary_delivery_enabled: StrictBool | None = None
    automatic_gmail_scan_enabled: StrictBool | None = None
    daily_summary_recipient: EmailStr | None = None
    gmail_scan_frequency: Literal["daily", "every_12_hours", "every_6_hours"] | None = None
    email_types_to_scan: list[Literal[
        "job_opportunities", "recruiter_messages", "application_updates", "interview_invitations",
    ]] | None = None


@router.get("/preferences/{user_id}")
def get_email_summary_preferences(user_id: int, db: Session = Depends(get_db)):
    user = _get_user(user_id, db)
    preference = db.query(EmailSummaryPreference).filter(EmailSummaryPreference.user_id == user_id).first()
    return _email_summary_preferences_payload(preference, user)


@router.patch("/preferences/{user_id}")
def update_email_summary_preferences(
    user_id: int, request: EmailSummaryPreferenceUpdate, db: Session = Depends(get_db),
):
    """Persist opt-ins only. Delivery/scans/scheduler are never invoked here."""
    user = _get_user(user_id, db)
    preference = db.query(EmailSummaryPreference).filter(EmailSummaryPreference.user_id == user_id).first()
    if preference is None:
        preference = EmailSummaryPreference(user_id=user_id)
        db.add(preference)

    values = request.model_dump(exclude_unset=True)
    for name, value in values.items():
        if name == "daily_summary_recipient" and value is not None:
            value = str(value)
        elif name == "email_types_to_scan" and value is not None:
            value = json.dumps(value)
        setattr(preference, name, value)
    db.commit()
    db.refresh(preference)

    # Applies a toggle/frequency change immediately rather than waiting for
    # the next backend restart. Never allowed to fail the settings save —
    # a scheduler hiccup here must not look like "your settings didn't save".
    try:
        from app.scheduler.gmail_auto_scan_scheduler import schedule_gmail_auto_scans

        schedule_gmail_auto_scans()
    except Exception:
        pass

    return _email_summary_preferences_payload(preference, user)


@router.post("/generate/{user_id}")
def generate_email_summary(user_id: int, db: Session = Depends(get_db)):
    raise HTTPException(status_code=410, detail="Daily Summary is currently disabled.")
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    try:
        email_content = daily_summary_service.build_summary_content(db, user=user)
    except daily_summary_service.DailySummaryUnavailable as error:
        detail = "No match scores found. Please calculate matches first." if error.reason == "no_match_scores" else "No jobs found for the match scores."
        raise HTTPException(status_code=404, detail=detail) from error
    email_log = daily_summary_service.new_generated_summary(user_id=user_id, recipient_email=user.email, content=email_content)

    db.add(email_log)
    db.commit()
    db.refresh(email_log)

    return {
        "message": "Email summary generated successfully",
        "email_log_id": email_log.id,
        "user_id": user_id,
        "recipient_email": email_log.recipient_email,
        "subject": email_log.subject,
        "status": email_log.status,
        "body_preview": email_log.body[:2000]
    }


@router.get("/logs/{user_id}")
def get_email_logs(user_id: int, db: Session = Depends(get_db)):
    logs = db.query(EmailLog).filter(
        EmailLog.user_id == user_id
    ).order_by(
        EmailLog.created_at.desc()
    ).all()

    if not logs:
        raise HTTPException(
            status_code=404,
            detail="No email logs found for this user"
        )

    delivery_by_summary = {
        delivery.email_log_id: delivery
        for delivery in db.query(SummaryDeliveryAttempt).filter(SummaryDeliveryAttempt.user_id == user_id).all()
    }
    return {
        "user_id": user_id,
        "total_logs": len(logs),
        "logs": [
            {
                "email_log_id": log.id,
                "recipient_email": log.recipient_email,
                "subject": log.subject,
                "status": log.status,
                "event_type": log.event_type or "summary_generated",
                "body_preview": log.body[:1000],
                "created_at": log.created_at,
                "delivery": daily_summary_delivery_service.delivery_payload(delivery_by_summary[log.id])
                if log.id in delivery_by_summary else None,
            }
            for log in logs
        ]
    }


class SummaryDeliveryRequest(BaseModel):
    recipient_email: str | None = None
    idempotency_key: str | None = None


@router.post("/delivery/{user_id}/{email_log_id}")
def deliver_email_summary(
    user_id: int, email_log_id: int, request: SummaryDeliveryRequest, db: Session = Depends(get_db),
):
    """Explicit Phase-1 delivery endpoint; generation never calls this."""
    raise HTTPException(status_code=410, detail="Daily Summary delivery is currently disabled.")
    _get_user(user_id, db)
    summary = db.query(EmailLog).filter(
        EmailLog.id == email_log_id,
        EmailLog.user_id == user_id,
        (EmailLog.event_type == "summary_generated") | (EmailLog.event_type.is_(None)),
    ).first()
    if summary is None:
        raise HTTPException(status_code=404, detail="Generated summary not found")
    try:
        delivery = daily_summary_delivery_service.deliver_generated_summary(
            db, user_id=user_id, email_log=summary, recipient_email=request.recipient_email,
            idempotency_key=request.idempotency_key,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except LookupError:
        raise HTTPException(status_code=404, detail="Generated summary not found")
    return daily_summary_delivery_service.delivery_payload(delivery)


# ---------------------------------------------------------------------------
# Gmail connection (Part 3)
# ---------------------------------------------------------------------------

@router.get("/gmail/config-status")
def gmail_config_status():
    """Safe, secret-free — never the client secret or a token."""
    return {
        "oauth_configured": gmail_service.is_oauth_configured(),
        "token_encryption_configured": token_crypto_configured(),
        "configured": gmail_service.is_fully_configured(),
    }


@router.get("/gmail/status/{user_id}")
def gmail_connection_status(user_id: int, db: Session = Depends(get_db)):
    _get_user(user_id, db)
    connection = gmail_service.get_connection(db, user_id)
    if connection is None or connection.status != "CONNECTED":
        return {
            "connected": False,
            "status": connection.status if connection else "DISCONNECTED",
            "email_address": None,
            "last_scanned_at": None,
            "configured": gmail_service.is_fully_configured(),
        }
    return {
        "connected": True,
        "status": connection.status,
        "email_address": connection.email_address,
        "last_scanned_at": connection.last_scanned_at.isoformat() if connection.last_scanned_at else None,
        "configured": gmail_service.is_fully_configured(),
    }


@router.get("/gmail/connect/{user_id}")
def gmail_connect(user_id: int, db: Session = Depends(get_db)):
    _get_user(user_id, db)
    try:
        authorization_url = gmail_service.start_oauth(db, user_id)
    except gmail_service.GmailNotConfigured as error:
        raise HTTPException(status_code=503, detail=str(error))
    return {"authorization_url": authorization_url}


@router.get("/gmail/callback")
def gmail_callback(code: str = "", state: str = "", error: str = "", db: Session = Depends(get_db)):
    """Google redirects the user's browser here — never an API fetch, so
    this always redirects back to the frontend rather than returning JSON."""
    from app.services.auth_email_service import frontend_base_url

    target = f"{frontend_base_url()}/emails"
    if error:
        return RedirectResponse(url=f"{target}?gmail=denied")

    try:
        connection = gmail_service.handle_oauth_callback(db, code, state)
    except gmail_service.GmailAuthError:
        return RedirectResponse(url=f"{target}?gmail=error")
    except gmail_service.GmailNotConfigured:
        return RedirectResponse(url=f"{target}?gmail=not_configured")
    except gmail_service.GmailApiError:
        return RedirectResponse(url=f"{target}?gmail=error")

    _log_event(
        db, user_id=connection.user_id, event_type="gmail_connected",
        subject="Gmail connected", body=f"Connected as {connection.email_address}.",
    )
    return RedirectResponse(url=f"{target}?gmail=connected")


@router.post("/gmail/disconnect/{user_id}")
def gmail_disconnect(user_id: int, db: Session = Depends(get_db)):
    _get_user(user_id, db)
    gmail_service.disconnect(db, user_id)
    _log_event(db, user_id=user_id, event_type="gmail_disconnected", subject="Gmail disconnected", body="")
    return {"disconnected": True}


@router.post("/gmail/scan/{user_id}")
def gmail_scan(user_id: int, db: Session = Depends(get_db)):
    _get_user(user_id, db)
    try:
        summary = job_email_scan_service.scan_for_job_emails(db, user_id=user_id)
    except gmail_service.GmailAuthError:
        raise HTTPException(status_code=409, detail="Gmail is not connected for this user.")
    except gmail_service.GmailApiError:
        _log_event(db, user_id=user_id, event_type="error", subject="Gmail scan failed", body="Gmail was unreachable.", status="error")
        raise HTTPException(status_code=502, detail="Gmail is temporarily unavailable. Please try again shortly.")
    return summary


# ---------------------------------------------------------------------------
# Application tracking (Parts 7-10, 18) — separate from Saved Jobs
# ---------------------------------------------------------------------------

def _application_payload(application) -> dict:
    return {
        "id": application.id,
        "job_id": application.job_id,
        "company": application.company,
        "title": application.title,
        "status": application.status,
        "applied_at": application.applied_at.isoformat() if application.applied_at else None,
        "last_status_update_at": application.last_status_update_at.isoformat() if application.last_status_update_at else None,
        "status_source": application.status_source,
    }


@router.get("/applications/{user_id}")
def list_applications(user_id: int, db: Session = Depends(get_db)):
    _get_user(user_id, db)
    applications = tracking.list_applications(db, user_id=user_id)
    return {"applications": [_application_payload(app) for app in applications]}


@router.get("/applications/{user_id}/{application_id}/history")
def application_history(user_id: int, application_id: int, db: Session = Depends(get_db)):
    _get_user(user_id, db)
    history = tracking.list_history(db, user_id=user_id, application_id=application_id)
    return {
        "history": [
            {
                "old_status": row.old_status,
                "new_status": row.new_status,
                "source": row.source,
                "confidence": row.confidence,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in history
        ]
    }


class StatusUpdateRequest(BaseModel):
    new_status: str


@router.post("/applications/{user_id}/{application_id}/status")
def update_application_status(user_id: int, application_id: int, request: StatusUpdateRequest, db: Session = Depends(get_db)):
    _get_user(user_id, db)
    new_status = (request.new_status or "").strip().upper()
    if new_status not in APPLICATION_STATUSES:
        raise HTTPException(status_code=422, detail=f"Unknown status. Must be one of: {', '.join(APPLICATION_STATUSES)}")
    try:
        application = tracking.apply_manual_status_update(db, user_id=user_id, application_id=application_id, new_status=new_status)
    except LookupError:
        raise HTTPException(status_code=404, detail="Application not found")
    _log_event(
        db, user_id=user_id, event_type="application_status_updated",
        subject=f"{application.company} — {application.title}",
        body=f"Status manually updated to {new_status}.",
    )
    return _application_payload(application)


# ---------------------------------------------------------------------------
# Job email events — the "possible application update" confirmation queue
# ---------------------------------------------------------------------------

def _reliable_matched_application(event: JobEmailEvent, db: Session):
    """Read-time re-validation for presentation only — NEVER writes to the
    JobEmailEvent row. A row's stored matched_application_id can predate a
    matcher fix (substring bug, company canonicalization, contradiction
    gate) and no longer be safe under CURRENT matching logic; GET /events
    must not keep exposing that stale target as if it were still reliable
    (see CURRENT VERIFIED BUG: a historical "Computer Merchant" event whose
    matched_application_company was stored as "SES" before those fixes).

    Only NEEDS_CONFIRMATION rows are re-checked — CONFIRMED/IGNORED/
    AUTO_APPLIED are settled historical audit records and are returned
    as-is, unrevalidated and unaltered (Requirement 4)."""
    if event.matched_application_id is None:
        return None
    from app.models.application_tracking_model import ApplicationTracking

    application = db.query(ApplicationTracking).filter(ApplicationTracking.id == event.matched_application_id).first()
    if application is None:
        return None
    if event.resolution != "NEEDS_CONFIRMATION":
        return application
    if not tracking.is_match_still_reliable(
        db, user_id=event.user_id, application=application,
        sender=event.sender, subject=event.subject or "", body=event.body_snippet or "",
    ):
        return None
    return application


def _event_payload(event: JobEmailEvent, db: Session | None = None) -> dict:
    # Requirement 9: expose which application a match is actually proposing
    # to update, so the Email Summary UI can show it before Confirm — a
    # visual "is this the right one?" check, scoped to Email Summary only.
    matched_application_id = event.matched_application_id
    matched_job_id = event.matched_job_id
    matched_application_title = None
    matched_application_company = None
    if db is not None and event.matched_application_id:
        application = _reliable_matched_application(event, db)
        if application is not None:
            matched_application_title = application.title
            matched_application_company = application.company
        else:
            # Stale/no-longer-reliable under current matching logic — never
            # presented as a confirmable target. The underlying JobEmailEvent
            # row is untouched; only this read's response is sanitized.
            matched_application_id = None
            matched_job_id = None
    return {
        "id": event.id,
        "message_id": event.message_id,
        "sender": event.sender,
        "subject": event.subject,
        "received_at": event.received_at.isoformat() if event.received_at else None,
        "event_type": event.event_type,
        "confidence": event.confidence,
        # Preserved exactly as stored for audit, regardless of whether the
        # match above was sanitized for presentation (Requirement 3) — never
        # fabricated or recomputed here.
        "match_confidence": event.match_confidence,
        "matched_job_id": matched_job_id,
        "matched_application_id": matched_application_id,
        "matched_application_title": matched_application_title,
        "matched_application_company": matched_application_company,
        "resolution": event.resolution,
        "body_snippet": event.body_snippet,
        # Paragraph-preserving text for Email Summary's View Details full
        # email body only — the compact list keeps using body_snippet
        # unchanged. Falls back to body_snippet for a row scanned before
        # this field existed, so View Details always has something to show.
        "body_formatted": event.body_formatted or event.body_snippet,
        "is_read": bool(event.is_read),
        "is_starred": bool(event.is_starred),
    }


@router.get("/events/{user_id}")
def list_job_email_events(user_id: int, db: Session = Depends(get_db)):
    _get_user(user_id, db)
    events = (
        db.query(JobEmailEvent)
        .filter(JobEmailEvent.user_id == user_id)
        .order_by(JobEmailEvent.created_at.desc())
        .limit(200)
        .all()
    )
    # Defense-in-depth (never rely on frontend hiding alone): rows created
    # before the job-relevance fix may still be genuinely unrelated
    # promotional/personal/newsletter mail — re-checked, never deleted.
    events = [event for event in events if not job_email_scan_service.is_legacy_event_unrelated(event)]
    return {"events": [_event_payload(event, db) for event in events]}


class ResolveEventRequest(BaseModel):
    action: str  # confirm | ignore | choose_job
    job_id: int | None = None


@router.post("/events/{user_id}/{event_id}/resolve")
def resolve_job_email_event(user_id: int, event_id: int, request: ResolveEventRequest, db: Session = Depends(get_db)):
    _get_user(user_id, db)
    event = db.query(JobEmailEvent).filter(JobEmailEvent.id == event_id, JobEmailEvent.user_id == user_id).first()
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")

    action = (request.action or "").strip().lower()

    # Historical audit records (CONFIRMED/IGNORED/AUTO_APPLIED) are settled
    # and must never be rewritten by a later resolve call — the frontend
    # only ever shows Confirm/Ignore for NEEDS_CONFIRMATION rows, so this is
    # defense-in-depth against a direct API call re-resolving an
    # already-resolved event (which could otherwise re-apply or overwrite a
    # status transition on a row that's supposed to be immutable history).
    # choose_job is deliberately exempt: it's the user's manual-correction
    # path (e.g. undoing a mistaken Ignore) and never NEEDS_CONFIRMATION's
    # own auto-populated match, so it isn't "rewriting" an automated result.
    if action in ("ignore", "confirm") and event.resolution != "NEEDS_CONFIRMATION":
        raise HTTPException(status_code=422, detail=f"This event is already resolved ({event.resolution}) and cannot be re-resolved.")

    if action == "ignore":
        event.resolution = "IGNORED"
        db.commit()
        return _event_payload(event, db)

    if action == "choose_job":
        if request.job_id is None:
            raise HTTPException(status_code=422, detail="job_id is required for choose_job.")
        job = db.query(Job).filter(Job.id == request.job_id, Job.user_id == user_id).first()
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        application = tracking.get_or_create_application(db, user_id=user_id, job_id=job.id, company=job.company, title=job.title)
    elif action == "confirm":
        if event.matched_application_id is None:
            raise HTTPException(status_code=422, detail="This event has no matched application — use choose_job instead.")
        # Defense in depth (never rely on the frontend button alone): even
        # if matched_application_id is set, a request to confirm it must be
        # rejected unless the underlying match itself was ever verified as
        # reliable. Protects rows written before this check existed and any
        # future code path that might set matched_application_id without
        # going through job_email_scan_service's reliability gate.
        if (event.match_confidence or 0.0) < tracking.RELIABLE_MATCH_CONFIDENCE_THRESHOLD:
            raise HTTPException(
                status_code=422,
                detail="This match is not reliable enough to confirm automatically. Use choose_job to link the correct application.",
            )
        # Defense in depth: a legacy OTHER-typed row from before the
        # job-relevance fix must never be confirmable either, even if it
        # somehow also carries a matched_application_id/match_confidence.
        if job_email_scan_service.is_legacy_event_unrelated(event):
            raise HTTPException(status_code=422, detail="This message does not appear to be job-related.")
        from app.models.application_tracking_model import ApplicationTracking
        application = db.query(ApplicationTracking).filter(
            ApplicationTracking.id == event.matched_application_id, ApplicationTracking.user_id == user_id,
        ).first()
        if application is None:
            raise HTTPException(status_code=404, detail="Matched application no longer exists")
        # Requirements 8/10/G: independently re-verify the stored match is
        # still safe before touching ApplicationTracking — never trust
        # matched_application_id's mere presence or match_confidence >= 0.5
        # alone. A stale row, a corrupted event, or a future bug that
        # bypasses job_email_scan_service's own gate must still be caught
        # here. If unsafe, the proposed match is cleared entirely (never
        # silently pointed at the wrong application) and Confirm is
        # rejected — the user must use choose_job instead.
        if not tracking.is_match_still_reliable(
            db, user_id=user_id, application=application,
            sender=event.sender, subject=event.subject or "", body=event.body_snippet or "",
        ):
            event.matched_application_id = None
            event.matched_job_id = None
            event.resolution = "NEEDS_CONFIRMATION"
            db.commit()
            raise HTTPException(
                status_code=422,
                detail="This match could not be independently verified as safe. Use choose_job to link the correct application.",
            )
    else:
        raise HTTPException(status_code=422, detail="action must be one of: confirm, ignore, choose_job")

    new_status = tracking.EVENT_TYPE_TO_STATUS.get(event.event_type)
    if new_status and tracking.can_auto_transition(application.status, new_status):
        tracking.apply_email_status_update(
            db, application=application, new_status=new_status,
            message_id=event.message_id, confidence=event.confidence or 0.0,
        )
    event.matched_application_id = application.id
    event.matched_job_id = application.job_id
    event.resolution = "CONFIRMED"
    db.commit()
    _log_event(
        db, user_id=user_id, event_type="application_status_updated",
        subject=f"{application.company} — {application.title}",
        body=f"Status confirmed to {new_status or application.status} from an email.",
    )
    return _event_payload(event, db)


# ---------------------------------------------------------------------------
# CareerBoost-local read/star state (Email Summary redesign) — Gmail is
# read-only, so these two endpoints only ever flip is_read/is_starred on our
# own JobEmailEvent row. They never call gmail_service, never touch
# resolution/matched_*, and never require any additional Gmail scope.
# ---------------------------------------------------------------------------

@router.post("/events/{user_id}/{event_id}/mark-read")
def mark_job_email_event_read(user_id: int, event_id: int, db: Session = Depends(get_db)):
    _get_user(user_id, db)
    event = db.query(JobEmailEvent).filter(JobEmailEvent.id == event_id, JobEmailEvent.user_id == user_id).first()
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    event.is_read = True
    db.commit()
    return _event_payload(event, db)


@router.post("/events/{user_id}/{event_id}/toggle-star")
def toggle_job_email_event_star(user_id: int, event_id: int, db: Session = Depends(get_db)):
    _get_user(user_id, db)
    event = db.query(JobEmailEvent).filter(JobEmailEvent.id == event_id, JobEmailEvent.user_id == user_id).first()
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    event.is_starred = not bool(event.is_starred)
    db.commit()
    return _event_payload(event, db)
