"""Email Summary's own application-tracking record.

Deliberately separate from the existing Job (Selected Jobs) table — this
never moves, renames, or mutates a saved-job row. `job_id` references an
existing Job when one can be matched, but is nullable so a tracked
application can exist purely from email evidence (company/title only).
"""

from sqlalchemy import Column, DateTime, Integer, String, ForeignKey, Index
from sqlalchemy.sql import func

from app.database import Base

APPLICATION_STATUSES = (
    "SAVED",
    "APPLIED",
    "APPLICATION_CONFIRMED",
    "UNDER_REVIEW",
    "RECRUITER_CONTACTED",
    "ASSESSMENT_REQUESTED",
    "INTERVIEW_REQUESTED",
    "INTERVIEW_SCHEDULED",
    "OFFER",
    "REJECTED",
    "WITHDRAWN",
)


class ApplicationTracking(Base):
    __tablename__ = "application_tracking"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True, index=True)

    # Denormalized so a tracking row can exist even without a matched Job
    # (e.g. a recruiter email about a role never saved in CareerBoost).
    company = Column(String(200), nullable=False)
    title = Column(String(300), nullable=False)

    status = Column(String(30), nullable=False, default="APPLIED")
    applied_at = Column(DateTime(timezone=True), nullable=True)
    last_status_update_at = Column(DateTime(timezone=True), nullable=True)
    status_source = Column(String(20), nullable=False, default="manual")  # manual | email | system

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


Index("ix_application_tracking_user_job", ApplicationTracking.user_id, ApplicationTracking.job_id)
