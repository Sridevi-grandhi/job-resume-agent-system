from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.sql import func

from app.database import Base


class Resume(Base):
    __tablename__ = "resumes"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    original_file_name = Column(String(255), nullable=False)
    stored_file_path = Column(String(500), nullable=False)
    file_type = Column(String(20), nullable=False)

    parsed_text = Column(Text, nullable=False)

    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())