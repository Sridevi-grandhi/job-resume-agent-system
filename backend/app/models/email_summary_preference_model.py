"""Opt-in settings for Email Summary automation.

Kept separate from job-search preferences because an Email Summary user may
not yet have the required job-search preference fields populated.
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from app.database import Base


class EmailSummaryPreference(Base):
    __tablename__ = "email_summary_preferences"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    daily_summary_delivery_enabled = Column(Boolean, nullable=False, default=False)
    automatic_gmail_scan_enabled = Column(Boolean, nullable=False, default=False)
    daily_summary_recipient = Column(String(255), nullable=True)

    # Email Summary redesign: how often the automatic Gmail scan scheduler
    # (app.scheduler.gmail_auto_scan_scheduler) runs for this user — only
    # consulted while automatic_gmail_scan_enabled is True. "daily" is the
    # safe default so an existing opted-in row from before this field
    # existed keeps its prior (implicit) cadence expectation.
    gmail_scan_frequency = Column(String(20), nullable=False, default="daily")

    # JSON-encoded list of category keys ("job_opportunities",
    # "recruiter_messages", "application_updates", "interview_invitations")
    # the user wants CareerBoost to surface/process from Gmail. NULL means
    # "all four" — the same effective behavior as before this field
    # existed, while an explicit [] disables all categories. Never used to request
    # additional Gmail scopes; it only filters which classified JobEmailEvent
    # rows job_email_scan_service persists.
    email_types_to_scan = Column(Text, nullable=True)

    # Visibility in the existing in-app notification bell. Defaults preserve
    # every notification users received before these controls existed.
    job_alerts_enabled = Column(Boolean, nullable=False, default=True)
    application_updates_enabled = Column(Boolean, nullable=False, default=True)
    interview_alerts_enabled = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
