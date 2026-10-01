"""Orchestrates one Gmail scan (Part 11): incremental via Gmail's history
API after the first connection, a single bounded recent-messages scan on
first connection. Ties together the provider, the classifier, matching, and
idempotent event persistence (Part 21). Gmail is read-only for Email
Summary: scanning only ever detects/proposes a possible application update
(every JobEmailEvent it creates stays NEEDS_CONFIRMATION) — it never
changes ApplicationTracking itself, regardless of confidence; only the
user's explicit Confirm action can do that (see
application_tracking_service.apply_email_status_update). This is what both
the manual "Scan Job Emails" button and the scheduled automatic trigger
call — there is only one scan pipeline.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.email_connection_model import EmailConnection
from app.models.email_log_model import EmailLog
from app.models.job_email_event_model import JobEmailEvent
from app.services import application_tracking_service as tracking
from app.services import gmail_service
from app.services.application_email_classifier import classify_email, classify_email_rules_only

logger = logging.getLogger(__name__)

JOB_SEARCH_QUERY = (
    '(interview OR application OR recruiter OR "thank you for applying" '
    'OR assessment OR "job offer" OR "not moving forward")'
)

_BODY_SNIPPET_MAX_CHARS = 600

# Email Summary Settings -> "Email Types to Scan" category keys (frontend
# checkboxes). Never used to request additional Gmail scopes — these only
# gate which already-job-related, already-classified JobEmailEvent rows get
# persisted at all, both for the manual "Scan Job Emails" button and the
# automatic scheduler (both funnel through scan_for_job_emails/_process_message,
# the one scan pipeline).
CATEGORY_JOB_OPPORTUNITIES = "job_opportunities"
CATEGORY_RECRUITER_MESSAGES = "recruiter_messages"
CATEGORY_APPLICATION_UPDATES = "application_updates"
CATEGORY_INTERVIEW_INVITATIONS = "interview_invitations"

ALL_EMAIL_CATEGORIES = (
    CATEGORY_JOB_OPPORTUNITIES,
    CATEGORY_RECRUITER_MESSAGES,
    CATEGORY_APPLICATION_UPDATES,
    CATEGORY_INTERVIEW_INVITATIONS,
)

# Each classified type has one scan preference. Recruiter outreach requires
# recruiter_messages even when job_opportunities remains enabled.
_EVENT_TYPE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "RECRUITER_OUTREACH": (CATEGORY_RECRUITER_MESSAGES,),
    "OTHER": (CATEGORY_JOB_OPPORTUNITIES,),
    "APPLICATION_CONFIRMATION": (CATEGORY_APPLICATION_UPDATES,),
    "APPLICATION_UPDATE": (CATEGORY_APPLICATION_UPDATES,),
    "ASSESSMENT_REQUEST": (CATEGORY_APPLICATION_UPDATES,),
    "OFFER": (CATEGORY_APPLICATION_UPDATES,),
    "REJECTION": (CATEGORY_APPLICATION_UPDATES,),
    "INTERVIEW_INVITE": (CATEGORY_INTERVIEW_INVITATIONS,),
}


def _enabled_categories(db: Session, *, user_id: int) -> set[str] | None:
    """The user's enabled "Email Types to Scan" categories. None means "all
    enabled" — the default, and also what a user who has never saved this
    preference (or a row from before this field existed) gets, so existing
    scans/tests are unaffected by this gate."""
    from app.models.email_summary_preference_model import EmailSummaryPreference

    preference = db.query(EmailSummaryPreference).filter(EmailSummaryPreference.user_id == user_id).first()
    if preference is None or preference.email_types_to_scan is None:
        return None
    try:
        values = json.loads(preference.email_types_to_scan)
    except (TypeError, ValueError):
        return None
    if not isinstance(values, list):
        return None
    enabled = {value for value in values if value in ALL_EMAIL_CATEGORIES}
    return enabled


def _category_enabled(event_type: str, enabled_categories: set[str] | None) -> bool:
    if enabled_categories is None:
        return True
    return any(category in enabled_categories for category in _EVENT_TYPE_CATEGORIES.get(event_type, ()))


def _scan_max_messages() -> int:
    return max(1, min(100, int(os.getenv("GMAIL_SCAN_MAX_MESSAGES") or "25")))


def _initial_scan_days() -> int:
    return max(1, int(os.getenv("GMAIL_INITIAL_SCAN_DAYS") or "30"))


def _log(db: Session, *, user_id: int, event_type: str, subject: str, body: str, status: str = "ok") -> None:
    from app.models.user_model import User

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


def _message_ids_from_history(history_payload: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for record in history_payload.get("history") or []:
        for added in record.get("messagesAdded") or []:
            message_id = (added.get("message") or {}).get("id")
            if message_id:
                ids.append(message_id)
    return ids


def is_legacy_event_unrelated(event: Any) -> bool:
    """Defense-in-depth for JobEmailEvent rows persisted before the
    job-relevance fix (the classifier that decided their event_type/
    job_related verdict at the time may have been too permissive — see
    CURRENT VERIFIED BUG). Never deletes anything; callers (email_routes'
    list/resolve endpoints) use this to exclude/reject at read time only.

    Only ever re-checks event_type="OTHER" rows: the six specific event
    types (APPLICATION_CONFIRMATION, INTERVIEW_INVITE, ASSESSMENT_REQUEST,
    OFFER, REJECTION, RECRUITER_OUTREACH, APPLICATION_UPDATE) are matched by
    strong, low-false-positive phrase rules that were never the source of
    this bug — only the OTHER fallback was. Deterministic rules only
    (classify_email_rules_only, never the LLM-assist path) so this stays
    fast and network-independent for a plain list/resolve call.
    """
    if getattr(event, "event_type", None) != "OTHER":
        return False
    result = classify_email_rules_only(
        subject=getattr(event, "subject", "") or "",
        sender=getattr(event, "sender", "") or "",
        body=getattr(event, "body_snippet", "") or "",
    )
    return not result["job_related"]


def _process_message(
    db: Session, *, user_id: int, connection_id: int, message_id: str, provider,
    enabled_categories: set[str] | None = None,
) -> str:
    """Returns a short outcome tag for the scan summary. Idempotent: a
    message already recorded for a given event_type is never reprocessed
    into a duplicate event/history/log (Part 21) — but a message can
    legitimately match a NEW event_type on a later scan (e.g. first seen as
    RECRUITER_OUTREACH, later as INTERVIEW_INVITE in the same thread), each
    tracked as its own event.
    """
    message = provider.get_message(message_id)
    if message is None:
        return "fetch_failed"

    classification = classify_email(subject=message.subject, sender=message.sender, body=message.body)
    if not classification["job_related"]:
        # Data minimization (Part 12): stops here, before match_email_to_
        # application(), JobEmailEvent creation, status updates, status
        # history, or recruiter drafting — nothing about this message is
        # persisted. "unrelated_ignored" is a diagnostics-only outcome tag
        # (see scan_for_job_emails' summary/outcomes dict) for observability
        # into how many fetched messages were correctly filtered out —
        # never itself a stored record of the message's content.
        return "unrelated_ignored"

    event_type = classification["event_type"] or "OTHER"

    if not _category_enabled(event_type, enabled_categories):
        # Email Summary Settings -> "Email Types to Scan": the user opted
        # this category out. Same data-minimization stance as the
        # unrelated_ignored branch above — nothing about this message is
        # persisted, just a diagnostics-only outcome tag.
        return "category_disabled_skipped"

    existing = (
        db.query(JobEmailEvent)
        .filter(JobEmailEvent.user_id == user_id, JobEmailEvent.message_id == message_id, JobEmailEvent.event_type == event_type)
        .first()
    )
    if existing is not None:
        return "duplicate_skipped"

    matched_application, match_confidence = tracking.match_email_to_application(
        db, user_id=user_id, sender=message.sender, subject=message.subject, body=message.body,
    )
    overall_confidence = classification["confidence"] * (match_confidence if matched_application else 0.5)

    # A candidate must independently clear RELIABLE_MATCH_CONFIDENCE_THRESHOLD
    # on its own match evidence (company name / sender domain), not merely be
    # "the best-scoring application available" — see CURRENT VERIFIED BUG: a
    # generic job-title overlap alone (no company/domain evidence) produced a
    # 0.135 combined-confidence match that was still exposed as if reliable.
    # Below the bar, the candidate is dropped entirely rather than shown —
    # matched_job_id/matched_application_id stay None, so the existing
    # "no matched_application_id -> Confirm unavailable" checks (frontend
    # PossibleUpdateCard and the /resolve route) correctly block it, and the
    # raw match_confidence is still stored (below) so the reason is auditable.
    #
    # Requirement 7's semantic reliability gate is checked explicitly here
    # too (not just implied by the score composition) — clearing 0.5
    # numerically must never be sufficient on its own; the winning
    # candidate must also carry independently strong, deterministic
    # identity evidence (company name / sender domain), never title overlap
    # alone. See CURRENT LIVE BUG: a Computer Merchant SDET assessment email
    # matched an unrelated "SES" application only because "ses" is a raw
    # substring of "Assessment" — _match_score's word-boundary fix already
    # prevents that specific case from scoring at all, but this gate is the
    # deliberate, explicit defense against any future scoring change that
    # might otherwise let title overlap alone reach the threshold.
    reliable_match = (
        matched_application is not None
        and match_confidence >= tracking.RELIABLE_MATCH_CONFIDENCE_THRESHOLD
        and tracking.has_strong_identity_evidence(
            company=matched_application.company, sender=message.sender, subject=message.subject, body=message.body,
        )
    )

    event = JobEmailEvent(
        user_id=user_id,
        connection_id=connection_id,
        message_id=message_id,
        thread_id=message.thread_id,
        sender=message.sender[:255],
        subject=(message.subject or "")[:500],
        received_at=message.received_at,  # the message's actual receipt time — see gmail_service.parse_message()
        event_type=event_type,
        confidence=round(overall_confidence, 4),
        match_confidence=round(match_confidence, 4) if matched_application else None,
        matched_job_id=matched_application.job_id if reliable_match else None,
        matched_application_id=matched_application.id if reliable_match else None,
        # Root cause (this task): Gmail's OWN server-generated `snippet`
        # field strips HTML tags without inserting a separating space at
        # the boundary — unlike our own gmail_service._extract_body/
        # _html_to_text pipeline, which already treats every tag/<br>/
        # block-boundary as a word separator (see CURRENT VERIFIED BUG:
        # "Thank youfor applying"). Gmail's raw snippet is what produced
        # examples like "position atComputer Merchant" and "SQL,AWS" — and
        # since Gmail supplies a non-empty snippet for virtually every
        # message, preferring it (as this line previously did) meant the
        # properly-normalized `message.body` almost never actually reached
        # the stored/displayed body_snippet. `message.body` is preferred
        # here; the raw snippet is only a last-resort fallback for the rare
        # message with no extractable body at all.
        body_snippet=(message.body or message.snippet or "")[:_BODY_SNIPPET_MAX_CHARS],
        # Display-only — preserves the original paragraph structure for
        # Email Summary's View Details full email body (never read by
        # classification/matching, which use body_snippet above). Falls
        # back to body_snippet's flattened text when a provider hasn't
        # populated body_display (e.g. an older test double).
        body_formatted=(message.body_display or message.body or message.snippet or "")[:_BODY_SNIPPET_MAX_CHARS],
        processed_at=datetime.now(timezone.utc),
    )

    # Gmail is read-only for CareerBoost Email Summary (this task): scanning
    # only ever detects/proposes a possible application update — it must
    # NEVER itself change ApplicationTracking or write a status-history
    # row, no matter how high classification/match confidence is. Every
    # newly scanned job-related event — reliable match or not — is created
    # as NEEDS_CONFIRMATION; only the user's explicit Confirm action (see
    # email_routes.resolve_job_email_event) may ever change a tracked
    # application's status. SAFE_AUTO_UPDATE_THRESHOLD remains defined on
    # application_tracking_service for any other feature that might use it,
    # but Gmail scanning itself no longer reads it.
    event.resolution = "NEEDS_CONFIRMATION"
    db.add(event)
    db.commit()

    return event.resolution.lower()


def scan_for_job_emails(db: Session, *, user_id: int) -> dict[str, Any]:
    connection = gmail_service.get_connection(db, user_id)
    if connection is None or connection.status != "CONNECTED":
        raise gmail_service.GmailAuthError("not_connected")

    provider = gmail_service.get_provider_for_user(db, user_id)
    if provider is None:
        raise gmail_service.GmailAuthError("not_connected")

    outcomes: dict[str, int] = {}
    message_ids: list[str] = []
    new_history_id: str | None = None
    need_bounded_scan = True

    if connection.last_history_id:
        try:
            history = gmail_service.get_history(
                provider.access_token, start_history_id=connection.last_history_id,
                max_results=_scan_max_messages(),
            )
            message_ids = _message_ids_from_history(history)
            new_history_id = history.get("historyId") or connection.last_history_id
            need_bounded_scan = False  # incremental call succeeded — even zero new messages is a valid result
        except gmail_service.GmailApiError:
            # A stale/expired historyId (Gmail expires them after ~a week)
            # falls back to a bounded recent scan rather than failing the
            # whole operation.
            need_bounded_scan = True

    if need_bounded_scan:
        query = f"{JOB_SEARCH_QUERY} newer_than:{_initial_scan_days()}d"
        stubs = provider.list_recent_messages(query=query, max_results=_scan_max_messages())
        message_ids = [stub.get("id") for stub in stubs if stub.get("id")]
        profile = gmail_service.get_profile(provider.access_token)
        new_history_id = profile.get("historyId")

    enabled_categories = _enabled_categories(db, user_id=user_id)
    for message_id in message_ids:
        outcome = _process_message(
            db, user_id=user_id, connection_id=connection.id, message_id=message_id, provider=provider,
            enabled_categories=enabled_categories,
        )
        outcomes[outcome] = outcomes.get(outcome, 0) + 1

    connection.last_history_id = new_history_id or connection.last_history_id
    connection.last_scanned_at = datetime.now(timezone.utc)
    connection.last_error = None
    db.commit()

    summary = {"messages_checked": len(message_ids), "outcomes": outcomes}
    _log(
        db, user_id=user_id, event_type="scan_completed",
        subject="Gmail scan completed",
        body=f"Checked {len(message_ids)} message(s). {outcomes}",
    )
    return summary
