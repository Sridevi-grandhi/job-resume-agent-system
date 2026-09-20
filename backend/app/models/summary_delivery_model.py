"""Durable delivery state for an already-generated Daily Summary.

This deliberately references ``email_logs`` instead of changing it: a
summary can exist without ever being queued or sent, and historical summary
logs retain their original meaning.
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from app.database import Base


DELIVERY_STATUSES = ("GENERATED", "QUEUED", "SENDING", "SENT", "FAILED")


class SummaryDeliveryAttempt(Base):
    __tablename__ = "summary_delivery_attempts"
    __table_args__ = (
        # One delivery lifecycle belongs to one generated summary. This is the
        # durable duplicate-send guard across requests and process restarts.
        UniqueConstraint("email_log_id", name="uq_summary_delivery_email_log"),
        UniqueConstraint("idempotency_key", name="uq_summary_delivery_idempotency"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    email_log_id = Column(Integer, ForeignKey("email_logs.id"), nullable=False, index=True)
    recipient_email = Column(String(255), nullable=False)
    delivery_status = Column(String(20), nullable=False, default="GENERATED")
    attempt_count = Column(Integer, nullable=False, default=0)
    idempotency_key = Column(String(128), nullable=False)

    queued_at = Column(DateTime(timezone=True), nullable=True)
    sending_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(80), nullable=True)
    last_error_message = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
