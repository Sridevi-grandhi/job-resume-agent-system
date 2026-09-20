"""Configurable registry of real employers Updated Job Search monitors.

This is the company-centric configuration layer: it says WHICH employer to
retrieve jobs for and WHICH internal connector (Greenhouse/Lever/etc.)
retrieves them. Nothing here is hardcoded — rows are synced from environment
configuration (see job_source_registry_service.sync_company_sources_from_env)
naming only companies an operator has actually configured.
"""

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.database import Base

# Internal connector types. Never shown to ordinary users — see
# job_discovery_routes for how company identity stays the user-facing concept.
CONNECTOR_TYPES = (
    "GREENHOUSE",
    "LEVER",
    "WORKDAY",
    "PUBLIC_JSON",
    "PUBLIC_API",
    "RSS",
    "COMPANY_CAREER_PAGE",
    "OTHER",
)


class CompanySource(Base):
    __tablename__ = "company_sources"

    id = Column(Integer, primary_key=True, index=True)

    company_name = Column(String(200), nullable=False)
    company_slug = Column(String(200), nullable=False)
    company_logo_url = Column(Text, nullable=True)
    career_page_url = Column(Text, nullable=True)

    source_type = Column(String(30), nullable=False)  # one of CONNECTOR_TYPES
    source_identifier = Column(String(200), nullable=False)  # board token / company slug / feed id
    enabled = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
