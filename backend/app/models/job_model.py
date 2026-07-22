from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.sql import func

from app.database import Base


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    title = Column(String(200), nullable=False)
    company = Column(String(200), nullable=False)
    location = Column(String(200), nullable=False)
    work_mode = Column(String(50), nullable=False)
    job_type = Column(String(50), nullable=False)

    description = Column(Text, nullable=False)
    skills_required = Column(Text, nullable=False)

    source = Column(String(100), nullable=False, default="Mock Data")
    job_url = Column(String(500), nullable=True)

    discovered_at = Column(DateTime(timezone=True), server_default=func.now())