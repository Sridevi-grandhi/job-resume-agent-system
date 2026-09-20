"""One durable scheduled Daily Summary claim per user-local calendar day."""

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from app.database import Base


class ScheduledDailySummary(Base):
    __tablename__ = "scheduled_daily_summaries"
    __table_args__ = (
        UniqueConstraint("user_id", "local_date", name="uq_scheduled_daily_summary_user_date"),
        UniqueConstraint("email_log_id", name="uq_scheduled_daily_summary_email_log"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    local_date = Column(Date, nullable=False, index=True)
    email_log_id = Column(Integer, ForeignKey("email_logs.id"), nullable=False)
    status = Column(String(20), nullable=False, default="GENERATED")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
