"""Globally shared job discovered by the Updated Job Search career/ATS tracker.

One row per real-world posting (deduplicated by source + source_job_id, a
canonical direct_url, or a content fingerprint as a last resort). Never
user-scoped — user-specific match results live in JobMatch.
"""

from sqlalchemy import Column, Integer, String, Text, DateTime, Index
from sqlalchemy.sql import func

from app.database import Base


class DiscoveredJob(Base):
    __tablename__ = "discovered_jobs"

    id = Column(Integer, primary_key=True, index=True)

    source = Column(String(40), nullable=False)
    source_mode = Column(String(10), nullable=False, default="mock")  # live | mock
    # 512, not the original 160: real JSearch job_id values are opaque
    # ~400-char base64 tokens (verified against a live response), far
    # longer than an ATS board's short numeric/slug ids.
    source_job_id = Column(String(512), nullable=True, index=True)
    external_id = Column(String(600), nullable=True, index=True)  # f"{source}:{source_job_id}" — must fit the widened id above
    fingerprint = Column(String(64), nullable=False, index=True)

    company = Column(String(200), nullable=False)
    company_logo_url = Column(Text, nullable=True)
    title = Column(String(300), nullable=False)
    location = Column(String(200), nullable=True)
    work_mode = Column(String(50), nullable=True)
    job_type = Column(String(50), nullable=True)

    description = Column(Text, nullable=False)
    requirements = Column(Text, nullable=True)
    skills = Column(Text, nullable=True)

    posted_at = Column(DateTime(timezone=True), nullable=True)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_checked_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_detected_at = Column(DateTime(timezone=True), nullable=True)

    direct_url = Column(Text, nullable=True)
    source_url = Column(Text, nullable=True)
    application_url = Column(Text, nullable=True)
    # Provenance of direct_url/application_url — "provider" (supplied
    # directly by the discovery source, e.g. JSearch's own apply_options) or
    # "enrichment" (found afterward by job_enrichment_service for a job that
    # originally had only an aggregator listing). NULL for legacy rows and
    # any row never touched by enrichment. Exists purely for honest
    # diagnostics/coverage reporting — never changes Apply-vs-View-Listing
    # logic itself (resolve_application_destination doesn't read this).
    direct_url_source = Column(String(20), nullable=True)

    # Deliberately separate from `status` (ACTIVE/INACTIVE/CLOSED — whether the
    # SOURCE still returns this posting at all) and from destination TYPE
    # (DIRECT_EMPLOYER/DIRECT_ATS/AGGREGATOR_LISTING — resolve_application_destination,
    # whether the stored URL is a genuine employer/ATS page). This is a THIRD,
    # independent question: is that specific destination still open right now?
    # See job_availability_service. Existing rows default to UNKNOWN, never
    # ACTIVE — an un-checked job must never be presented as verified-open.
    availability_status = Column(String(20), nullable=False, default="UNKNOWN")  # ACTIVE | EXPIRED | UNKNOWN
    availability_checked_at = Column(DateTime(timezone=True), nullable=True)
    availability_reason = Column(String(300), nullable=True)

    status = Column(String(20), nullable=False, default="ACTIVE")  # ACTIVE | INACTIVE | CLOSED
    missed_poll_count = Column(Integer, nullable=False, default=0)
    content_hash = Column(String(64), nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


Index("ix_discovered_jobs_source_sourcejobid", DiscoveredJob.source, DiscoveredJob.source_job_id)
