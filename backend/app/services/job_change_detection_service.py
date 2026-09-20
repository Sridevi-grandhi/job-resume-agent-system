"""Change detection + deduplication for discovered jobs.

Identity rule (never title alone):
  1. (source, source_job_id) when the source provides a stable id.
  2. Otherwise a canonical-direct-url / content fingerprint (see
     job_normalization_service.compute_fingerprint).

A job's content_hash changes -> UPDATED (existing row refreshed, first_seen_at
preserved). Same content -> EXISTING (duplicate, no new row, timestamps
touched). Neither found -> NEW. Jobs that stop appearing in a source's
results are never deleted; after INACTIVE_AFTER_MISSED_POLLS consecutive
misses they flip to INACTIVE so anything a user already saved stays
historically accessible.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.discovered_job_model import DiscoveredJob
from app.services.job_normalization_service import compute_content_hash, compute_fingerprint

logger = logging.getLogger(__name__)


def _inactive_after_missed_polls() -> int:
    return max(1, int(os.getenv("INACTIVE_AFTER_MISSED_POLLS") or "3"))


def _find_existing(db: Session, source: str, job: dict[str, Any], fingerprint: str) -> DiscoveredJob | None:
    """Identity lookup: (source, source_job_id) when the source provides a
    stable id — and ONLY that, since a miss there means "not seen before",
    not "fall back to fingerprint". Falling through to a fingerprint match
    after a source_job_id miss would wrongly collide distinct jobs that
    happen to share a fingerprint basis (e.g. the same source_url — several
    ATS boards return one listings URL for every posting). Fingerprint
    lookup is only used when the source has no source_job_id at all."""
    source_job_id = job.get("source_job_id")
    if source_job_id:
        return (
            db.query(DiscoveredJob)
            .filter(DiscoveredJob.source == source, DiscoveredJob.source_job_id == source_job_id)
            .first()
        )
    return (
        db.query(DiscoveredJob)
        .filter(DiscoveredJob.source == source, DiscoveredJob.fingerprint == fingerprint)
        .first()
    )


def process_source_jobs(db: Session, source: str, normalized_jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """Persist one poll's normalized jobs for a single source.

    Returns counts + the set of DiscoveredJob ids seen this poll (for the
    caller to run close_stale_jobs against).
    """
    from app.services.job_source_registry_service import demo_mode_enabled

    now = datetime.now(timezone.utc)
    counts = {"fetched": len(normalized_jobs), "new": 0, "updated": 0, "duplicates": 0}
    seen_ids: set[int] = set()
    demo_mode = demo_mode_enabled()

    for job in normalized_jobs:
        if job.get("source_mode") == "mock" and not demo_mode:
            # Defense in depth — adapters already gate mock data behind
            # UPDATED_JOB_DEMO_MODE themselves, so this should be
            # unreachable in practice; it exists so a future adapter bug can
            # never persist fabricated jobs as if they were real.
            logger.warning("MOCK_JOB_REJECTED_IN_PRODUCTION source=%s title=%r", source, str(job.get("title", ""))[:120])
            continue
        fingerprint = compute_fingerprint(job)
        content_hash = compute_content_hash(job)
        existing = _find_existing(db, source, job, fingerprint)

        if existing is None:
            row = DiscoveredJob(
                source=job["source"],
                source_mode=job["source_mode"],
                source_job_id=job.get("source_job_id"),
                external_id=job.get("external_id"),
                fingerprint=fingerprint,
                company=job["company"],
                company_logo_url=job.get("company_logo_url"),
                title=job["title"],
                location=job.get("location"),
                work_mode=job.get("work_mode"),
                job_type=job.get("job_type"),
                description=job["description"],
                requirements=job.get("requirements") or None,
                skills=job.get("skills") or None,
                posted_at=job.get("posted_at"),
                first_seen_at=now,
                last_seen_at=now,
                last_checked_at=now,
                direct_url=job.get("direct_url"),
                source_url=job.get("source_url"),
                application_url=job.get("application_url"),
                status="ACTIVE",
                missed_poll_count=0,
                content_hash=content_hash,
            )
            db.add(row)
            db.flush()
            seen_ids.add(row.id)
            counts["new"] += 1
            logger.info("NEW_JOB_DETECTED source=%s job_id=%s title=%r", source, row.id, row.title[:120])
            continue

        seen_ids.add(existing.id)
        content_changed = existing.content_hash != content_hash
        existing.last_seen_at = now
        existing.last_checked_at = now
        existing.missed_poll_count = 0
        if existing.status != "ACTIVE":
            existing.status = "ACTIVE"
        if content_changed:
            existing.description = job["description"]
            existing.requirements = job.get("requirements") or None
            existing.skills = job.get("skills") or None
            existing.location = job.get("location")
            existing.work_mode = job.get("work_mode")
            existing.job_type = job.get("job_type")
            existing.direct_url = job.get("direct_url")
            existing.application_url = job.get("application_url")
            existing.company_logo_url = job.get("company_logo_url") or existing.company_logo_url
            existing.content_hash = content_hash
            existing.updated_detected_at = now
            counts["updated"] += 1
            logger.info("JOB_UPDATED source=%s job_id=%s title=%r", source, existing.id, existing.title[:120])
        else:
            counts["duplicates"] += 1
            logger.debug("DUPLICATE_SKIPPED source=%s job_id=%s", source, existing.id)

    db.commit()
    return {**counts, "seen_ids": seen_ids}


def close_stale_jobs(db: Session, source: str, seen_ids: set[int]) -> int:
    threshold = _inactive_after_missed_polls()
    stale = (
        db.query(DiscoveredJob)
        .filter(DiscoveredJob.source == source, DiscoveredJob.status == "ACTIVE")
        .filter(~DiscoveredJob.id.in_(seen_ids))
        .all()
    )
    closed = 0
    for job in stale:
        job.missed_poll_count = (job.missed_poll_count or 0) + 1
        if job.missed_poll_count >= threshold:
            job.status = "INACTIVE"
            closed += 1
    db.commit()
    return closed
