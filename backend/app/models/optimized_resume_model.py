from sqlalchemy import Column, Integer, Text, DateTime, ForeignKey
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

    created_at = Column(DateTime(timezone=True), server_default=func.now())