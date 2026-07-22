from sqlalchemy import Column, Integer, String, Time, DateTime, ForeignKey
from sqlalchemy.sql import func

from app.database import Base


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)

    job_title = Column(String(150), nullable=False)
    location = Column(String(150), nullable=False)
    experience = Column(String(50), nullable=False)
    work_mode = Column(String(50), nullable=False)
    job_type = Column(String(50), nullable=False)
    skills = Column(String(1000), nullable=False)

    schedule_time = Column(Time, nullable=False)
    timezone = Column(String(100), nullable=False, default="America/Chicago")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())