from sqlalchemy import Boolean, Column, Integer, String, Text, DateTime, ForeignKey, Float
from sqlalchemy.sql import func

from app.database import Base


class AtsOptimizationWorkflow(Base):
    """
    Combined ATS Resume Optimizer & Match Analyzer workflow record.

    Tracks background processing status and stores both score analyses.
    """

    __tablename__ = "ats_optimization_workflows"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    resume_id = Column(Integer, ForeignKey("resumes.id"), nullable=False, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False, index=True)
    optimized_resume_id = Column(Integer, ForeignKey("optimized_resumes.id"), nullable=True)

    workflow_status = Column(String(60), nullable=False, default="PENDING", index=True)
    content_hash = Column(String(128), nullable=True, index=True)

    original_match_score = Column(Integer, nullable=True)
    optimized_match_score = Column(Integer, nullable=True)
    score_improvement = Column(Integer, nullable=True)

    original_analysis_json = Column(Text, nullable=True)
    optimized_analysis_json = Column(Text, nullable=True)
    validation_report_json = Column(Text, nullable=True)

    generated_resume_plain_text = Column(Text, nullable=True)
    generated_resume_markdown = Column(Text, nullable=True)

    llm_provider = Column(String(80), nullable=True)
    llm_model = Column(String(120), nullable=True)
    llm_service = Column(String(40), nullable=True)
    generation_method = Column(String(60), nullable=True)
    llm_used = Column(Boolean, nullable=True)
    fallback_used = Column(Boolean, nullable=True)
    fallback_reason = Column(String(80), nullable=True)
    generation_duration_ms = Column(Integer, nullable=True)
    generation_diagnostics_json = Column(Text, nullable=True)
    prompt_version = Column(String(80), nullable=True)
    # Must fit current ANALYSIS_VERSION (48+) and future version strings.
    analysis_version = Column(String(120), nullable=True)

    error_message = Column(Text, nullable=True)
    missing_job_fields = Column(Text, nullable=True)

    original_resume_hash = Column(String(64), nullable=True)
    optimized_resume_hash = Column(String(64), nullable=True)
    original_text_length = Column(Integer, nullable=True)
    optimized_text_length = Column(Integer, nullable=True)
    content_changed = Column(Boolean, nullable=True)
    optimization_status = Column(String(40), nullable=True)
    optimization_allowed = Column(Boolean, nullable=True)
    eligibility_reason = Column(Text, nullable=True)
    eligibility_tier = Column(String(80), nullable=True)
    improvement_report_json = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_activity_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
