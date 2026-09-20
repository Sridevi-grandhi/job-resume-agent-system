"""Immutable status-change history for ApplicationTracking — rows are never
updated or deleted, only appended (see application_tracking_service)."""

from sqlalchemy import Column, DateTime, Float, Integer, String, ForeignKey
from sqlalchemy.sql import func

from app.database import Base


class ApplicationStatusHistory(Base):
    __tablename__ = "application_status_history"

    id = Column(Integer, primary_key=True, index=True)

    application_id = Column(Integer, ForeignKey("application_tracking.id"), nullable=False, index=True)

    old_status = Column(String(30), nullable=True)
    new_status = Column(String(30), nullable=False)
    source = Column(String(20), nullable=False)  # manual | email | system
    email_message_id = Column(String(200), nullable=True)
    confidence = Column(Float, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
