"""AI-suggested recruiter replies. draft_created only ever means a Gmail
DRAFT was created — this model (and every service that writes it) never
represents or implies a sent message. See recruiter_draft_service.

Lifecycle (Phase 10): PENDING -> QUEUED -> GENERATING -> READY, or FAILED /
NEEDS_REVIEW at any point after QUEUED. PENDING means "eligible, but no
queue was available to enqueue it" (see draft_queue.py's graceful
degradation) — the row still exists so the frontend can offer a manual
Generate Reply immediately rather than showing nothing. GENERATED/
SAVED_TO_GMAIL/ERROR are the pre-async-architecture legacy values: never
written by current code, kept only so any row persisted by an older build
still deserializes to a sane, known status rather than an unrecognized one.
"""

from sqlalchemy import Column, DateTime, Integer, String, Text, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func

from app.database import Base


DRAFT_STATUSES = ("PENDING", "QUEUED", "GENERATING", "READY", "FAILED", "NEEDS_REVIEW")
_LEGACY_DRAFT_STATUSES = ("GENERATED", "SAVED_TO_GMAIL", "ERROR")


class RecruiterDraft(Base):
    __tablename__ = "recruiter_drafts"
    __table_args__ = (
        # Idempotency (Phase 9): the same (user, event, draft category, prompt
        # version) must never accumulate more than one active row, no matter
        # how many times a scan re-enqueues it, a worker retries it, or a user
        # double-clicks Generate Reply. A future prompt_version bump is
        # deliberately NOT blocked by this constraint — that is a genuinely
        # new generation, not a duplicate of the old one.
        UniqueConstraint(
            "user_id", "job_email_event_id", "draft_type", "prompt_version",
            name="ux_recruiter_drafts_idempotency",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    job_email_event_id = Column(Integer, ForeignKey("job_email_events.id"), nullable=True)
    # Ticket/architecture docs refer to this as "matched_application_id" —
    # same field, kept under its original name to avoid an unnecessary
    # rename of a live production column.
    application_id = Column(Integer, ForeignKey("application_tracking.id"), nullable=True)

    # Deterministic category of reply this row is for, derived from the
    # source JobEmailEvent.event_type (see
    # recruiter_draft_service.draft_type_for_event) — never LLM-decided.
    draft_type = Column(String(40), nullable=True)
    tone = Column(String(20), nullable=False, default="professional")

    recruiter_email = Column(String(255), nullable=False)
    subject = Column(String(500), nullable=True)

    generated_body = Column(Text, nullable=True)
    edited_body = Column(Text, nullable=True)  # the user's own edits, if any, prior to saving

    status = Column(String(20), nullable=False, default="PENDING")
    gmail_draft_id = Column(String(200), nullable=True)  # legacy, never written

    # Which LLM gateway attempt actually produced generated_body — "template"
    # when every configured provider failed/was unconfigured and the
    # deterministic fallback was used instead (Phase 18).
    provider = Column(String(40), nullable=True)
    model = Column(String(120), nullable=True)
    prompt_version = Column(String(40), nullable=False, default="recruiter-draft-v1")

    generation_attempts = Column(Integer, nullable=False, default=0)
    last_error_code = Column(String(60), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    queued_at = Column(DateTime(timezone=True), nullable=True)
    generation_started_at = Column(DateTime(timezone=True), nullable=True)
    generated_at = Column(DateTime(timezone=True), nullable=True)
