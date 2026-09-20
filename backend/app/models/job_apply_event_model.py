"""Apply-click tracking for Updated Job Search.

Recorded the moment a user clicks Apply — before the browser navigates to
the employer's application page. This does NOT mean the application was
submitted; it only means the user was sent to the employer's real
application destination (see job_discovery_routes.record_apply_click).
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from app.database import Base


class JobApplyEvent(Base):
    __tablename__ = "job_apply_events"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    discovered_job_id = Column(Integer, ForeignKey("discovered_jobs.id"), nullable=False)

    application_url = Column(Text, nullable=False)
    apply_started_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(30), nullable=False, default="APPLY_STARTED")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
