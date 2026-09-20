from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.sql import func

from app.database import Base


class EmailLog(Base):
    __tablename__ = "email_logs"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    recipient_email = Column(String(150), nullable=False)
    subject = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)

    status = Column(String(50), nullable=False, default="generated")

    # Additive — nullable so existing rows (and daily_scheduler.py's own,
    # untouched EmailLog inserts, which never set this) stay valid. NULL is
    # treated as "summary_generated" by the reporting layer. See
    # EMAIL_SUMMARY_EVENT_TYPES below for the values Email Summary itself
    # writes (Part 19) — never a secret or token value.
    event_type = Column(String(40), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


EMAIL_SUMMARY_EVENT_TYPES = (
    "summary_generated",
    "gmail_connected",
    "gmail_disconnected",
    "scan_completed",
    "application_status_updated",
    "draft_generated",
    "gmail_draft_created",
    "error",
)