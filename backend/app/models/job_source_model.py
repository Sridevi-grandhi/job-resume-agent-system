"""Registry + run history for Updated Job Search career/ATS sources."""

from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.sql import func

from app.database import Base


class JobSource(Base):
    """Live health/registry row for one job source adapter."""

    __tablename__ = "job_sources"

    id = Column(Integer, primary_key=True, index=True)
    source_name = Column(String(60), nullable=False, unique=True)
    source_type = Column(String(30), nullable=False, default="ats_board")
    enabled = Column(Boolean, nullable=False, default=True)
    mode = Column(String(20), nullable=False, default="mock")  # live | mock | unavailable

    last_checked_at = Column(DateTime(timezone=True), nullable=True)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class JobSourceRun(Base):
    """One row per source per poll cycle — background polling status/history."""

    __tablename__ = "job_source_runs"

    id = Column(Integer, primary_key=True, index=True)
    source_name = Column(String(60), nullable=False, index=True)
    triggered_by = Column(String(20), nullable=False, default="scheduler")  # scheduler | manual

    run_started_at = Column(DateTime(timezone=True), nullable=False)
    run_completed_at = Column(DateTime(timezone=True), nullable=True)

    jobs_fetched = Column(Integer, nullable=False, default=0)
    jobs_new = Column(Integer, nullable=False, default=0)
    jobs_updated = Column(Integer, nullable=False, default=0)
    duplicates_skipped = Column(Integer, nullable=False, default=0)
    errors = Column(Integer, nullable=False, default=0)
    duration_ms = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
