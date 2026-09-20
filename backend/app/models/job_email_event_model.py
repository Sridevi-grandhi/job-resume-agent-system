"""Minimized job-related Gmail message metadata (data minimization — see
Part 12: only what Email Summary needs, never the whole inbox).

Idempotency (Part 21): (user_id, message_id, event_type) is unique — the
same Gmail message classified/reprocessed twice never creates a duplicate
event, duplicate status history, or duplicate log entry.
"""

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func

from app.database import Base

EVENT_TYPES = (
    "APPLICATION_CONFIRMATION",
    "APPLICATION_UPDATE",
    "RECRUITER_OUTREACH",
    "INTERVIEW_INVITE",
    "ASSESSMENT_REQUEST",
    "OFFER",
    "REJECTION",
    "OTHER",
)


class JobEmailEvent(Base):
    __tablename__ = "job_email_events"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    connection_id = Column(Integer, ForeignKey("email_connections.id"), nullable=False, index=True)

    message_id = Column(String(200), nullable=False)
    thread_id = Column(String(200), nullable=True)
    sender = Column(String(255), nullable=False)
    subject = Column(String(500), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=True)

    event_type = Column(String(30), nullable=False, default="OTHER")
    # Blended classifier-confidence x match-confidence (see
    # job_email_scan_service._process_message) — shown to the user as-is,
    # even when no reliable application match was found, so the email's own
    # classification confidence stays visible and honest.
    confidence = Column(Float, nullable=True)

    # The RAW application_tracking_service._match_score() result for the
    # winning candidate (0.0-1.0) — kept separate from `confidence` above so
    # backend validation (see email_routes.resolve_job_email_event) can
    # independently verify a "confirm" request is against a candidate that
    # actually cleared RELIABLE_MATCH_CONFIDENCE_THRESHOLD, instead of
    # trusting mere presence of matched_application_id (see CURRENT
    # VERIFIED BUG this field was added to fix — a weak, title-overlap-only
    # 0.135 match was confirmable via matched_application_id alone).
    match_confidence = Column(Float, nullable=True)

    matched_job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    matched_application_id = Column(Integer, ForeignKey("application_tracking.id"), nullable=True)
    # NEEDS_CONFIRMATION | CONFIRMED | IGNORED | AUTO_APPLIED — see
    # application_tracking_service's confidence-threshold gate. Only ever
    # NEEDS_CONFIRMATION when matched_application_id is set but the match
    # was not reliable — matched_job_id/matched_application_id are None
    # whenever the winning candidate did not clear
    # RELIABLE_MATCH_CONFIDENCE_THRESHOLD (see job_email_scan_service.py).
    resolution = Column(String(30), nullable=False, default="NEEDS_CONFIRMATION")

    # Retained only when needed for recruiter-draft generation/audit (Part
    # 12) — never the raw full message, and never unrelated (non-job) mail.
    body_snippet = Column(Text, nullable=True)

    # Display-only sibling of body_snippet: preserves the sender's original
    # line breaks/paragraph structure (see gmail_service._extract_display_body)
    # for Email Summary's View Details full email body. Nullable so a row
    # from before this field existed — or one whose provider never populated
    # ProviderMessage.body_display — just falls back to body_snippet's
    # flattened text at display time (see email_routes._event_payload).
    # Never read by classification/matching, which use body_snippet only.
    body_formatted = Column(Text, nullable=True)

    processed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # CareerBoost-local only (Email Summary redesign) — Gmail stays
    # read-only, so these never call a Gmail write/modify API. "Mark as
    # Read"/star in the UI flip these columns only; Gmail's own
    # read/unread and starred state are never touched.
    is_read = Column(Boolean, nullable=False, default=False)
    is_starred = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("user_id", "message_id", "event_type", name="uq_job_email_event_user_message_type"),
    )
