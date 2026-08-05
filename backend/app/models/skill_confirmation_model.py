from sqlalchemy import Boolean, Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.sql import func

from app.database import Base


class JobRequirementAnalysis(Base):
    """Cached structured requirement extraction for a (user, job) pair."""

    __tablename__ = "job_requirement_analyses"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False, index=True)

    analysis_json = Column(Text, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class UserConfirmedSkill(Base):
    """A user's truthful confirmation of a JD-required skill, with structured evidence."""

    __tablename__ = "user_confirmed_skills"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False, index=True)
    resume_id = Column(Integer, ForeignKey("resumes.id"), nullable=True)

    skill_name = Column(String(120), nullable=False)
    normalized_skill_name = Column(String(120), nullable=False, index=True)

    requirement_type = Column(String(20), nullable=True)  # MANDATORY | PREFERRED

    confirmed = Column(Boolean, nullable=False, default=False)

    evidence_type = Column(String(60), nullable=True)
    evidence_status = Column(String(40), nullable=False, default="INCOMPLETE")

    # Simplified default form (current). "Beginner"/"Intermediate"/"Advanced" and a
    # bucketed range — not required unless the user has already answered "Yes".
    experience_level = Column(String(20), nullable=True)
    experience_range = Column(String(30), nullable=True)
    supporting_context = Column(String(300), nullable=True)
    confirmed_accurate = Column(Boolean, nullable=True)

    # Detailed/legacy fields — nullable, only required when evidence_type is a
    # project/certification/training type, or when the user explicitly flags a new
    # employment record not already present in the resume (is_new_employment).
    employer_name = Column(String(200), nullable=True)
    role_title = Column(String(200), nullable=True)
    project_name = Column(String(200), nullable=True)
    certification_name = Column(String(200), nullable=True)
    provider_name = Column(String(200), nullable=True)

    start_date = Column(String(20), nullable=True)
    end_date = Column(String(20), nullable=True)
    currently_using = Column(Boolean, nullable=True)

    years_experience = Column(Integer, nullable=True)
    last_used = Column(String(20), nullable=True)

    usage_description = Column(Text, nullable=True)
    work_completed = Column(Text, nullable=True)
    related_technologies = Column(String(300), nullable=True)
    impact_description = Column(Text, nullable=True)
    skill_modules = Column(String(400), nullable=True)  # comma-separated sub-topics, e.g. "Producer, Consumer, DLQ"

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
