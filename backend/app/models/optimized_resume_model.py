from sqlalchemy import Boolean, Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.sql import func

from app.database import Base


class OptimizedResume(Base):
    __tablename__ = "optimized_resumes"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    resume_id = Column(Integer, ForeignKey("resumes.id"), nullable=False)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)

    optimized_text = Column(Text, nullable=False)
    optimization_notes = Column(Text, nullable=True)
    generated_resume_text = Column(Text, nullable=True)
    edited_resume_text = Column(Text, nullable=True)
    final_resume_text = Column(Text, nullable=True)
    generated_resume_json = Column(Text, nullable=True)
    edited_resume_json = Column(Text, nullable=True)
    original_score = Column(Integer, nullable=True)
    generated_score = Column(Integer, nullable=True)
    edited_score = Column(Integer, nullable=True)
    template_preserved = Column(Boolean, nullable=False, default=False)
    source_file_type = Column(String(40), nullable=True)
    generated_file_path = Column(Text, nullable=True)
    final_file_path = Column(Text, nullable=True)
    user_modified = Column(Boolean, nullable=False, default=False)
    validation_status = Column(String(30), nullable=True)
    last_edited_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
