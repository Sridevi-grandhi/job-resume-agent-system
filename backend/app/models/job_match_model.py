"""User-specific, cached resume-to-job match result for a discovered job.

Deliberately separate from app.models.match_model.MatchScore (the protected
ATS Optimizer scoring model, keyed off the per-user `jobs` table and
resume_version). Discovered jobs are a global table shared across users, so
match results must stay isolated per (user, resume, discovered_job) here
instead of coupling into ATS scoring internals.
"""

from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.sql import func

from app.database import Base


class JobMatch(Base):
    __tablename__ = "job_matches"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    discovered_job_id = Column(Integer, ForeignKey("discovered_jobs.id"), nullable=False)
    resume_id = Column(Integer, ForeignKey("resumes.id"), nullable=True)

    match_score = Column(Integer, nullable=False)
    fitting_skills = Column(Text, nullable=True)   # JSON-encoded list[str]
    missing_skills = Column(Text, nullable=True)   # JSON-encoded list[str]
    verdict = Column(Text, nullable=True)
    role_alignment = Column(String(30), nullable=True)
    mandatory_coverage = Column(Text, nullable=True)  # JSON-encoded
    preferred_coverage = Column(Text, nullable=True)  # JSON-encoded
    confidence = Column(String(20), nullable=True)

    matcher_version = Column(String(30), nullable=False)
    llm_used = Column(Boolean, nullable=False, default=False)
    llm_provider = Column(String(30), nullable=True)

    calculated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())


Index("ix_job_matches_user_job", JobMatch.user_id, JobMatch.discovered_job_id)
