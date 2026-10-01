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
import hashlib
import json
from types import SimpleNamespace
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.discovered_job_model import DiscoveredJob
from app.services.job_enrichment_service import apply_enrichment_result, enrich_job, enrich_job_with_fallback
from app.services.job_normalization_service import compute_content_hash, compute_fingerprint, resolve_apply_action
from app.services.provider_apply_resolver import outbound_application_candidate
from app.services.job_enrichment_context import current_budget, enrichment_scope, record_event

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
    from app.job_sources.career_sources.persistence import find_duplicate
    official_duplicate = find_duplicate(db, job)
    if official_duplicate is not None:
        return official_duplicate
    source_job_id = job.get("source_job_id")
    if source_job_id:
        exact = (
            db.query(DiscoveredJob)
            .filter(DiscoveredJob.source == source, DiscoveredJob.source_job_id == source_job_id)
            .first()
        )
        if exact is not None:
            return exact
        # Different broad aggregators assign different IDs to the same
        # employer posting. Only those providers may fall through after an
        # ID miss: ATS boards' IDs are authoritative requisitions and must
        # never be merged merely because title/company/location coincide.
        if source in {"jsearch", "adzuna", "apify_dice"}:
            # Provider listing URLs/IDs are not the canonical cross-provider identity.
            from sqlalchemy import func
            return db.query(DiscoveredJob).filter(
                DiscoveredJob.source.in_(["jsearch", "adzuna", "apify_dice"]),
                func.lower(DiscoveredJob.company) == job["company"].strip().lower(),
                func.lower(DiscoveredJob.title) == job["title"].strip().lower(),
                func.lower(DiscoveredJob.location) == (job.get("location") or "").strip().lower(),
            ).first()
        return None
    return (
        db.query(DiscoveredJob)
        .filter(DiscoveredJob.source == source, DiscoveredJob.fingerprint == fingerprint)
        .first()
    )


def _enrich_unverified_application_candidate(row: DiscoveredJob) -> None:
    """Enrich a listing or provider-supplied candidate within the refresh budget.
    The candidate is never a direct
    destination by itself; only ``apply_enrichment_result`` may promote it.

    This is deliberately per-row failure-isolated.  Discovery must retain a
    safe aggregator listing even if optional enrichment is unavailable.
    """
    budget = current_budget.get()
    if row.source_mode == "mock" or row.candidate_application_type == "APPLY_TO_EMAIL":
        return
    action = resolve_apply_action(row)
    if action["action_label"] == "Apply" and (not row.unverified_application_url or row.unverified_application_url == action["url"]):
        record_event("skipped_trusted_direct")
        return
    if not row.company or not row.title or not action["url"]:
        return
    identity = (row.company.casefold(), row.title.casefold(), (row.location or "").casefold())
    verification_candidate = outbound_application_candidate(row)
    input_hash = hashlib.sha256(json.dumps([*identity, verification_candidate, row.candidate_application_type]).encode()).hexdigest()
    checked_at = row.enrichment_checked_at
    if checked_at and checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    if checked_at and row.enrichment_input_hash == input_hash and (datetime.now(timezone.utc) - checked_at).total_seconds() < 6 * 3600:
        record_event("skipped_cooldown")
        return
    record_event("eligible_jobs")
    if budget is not None:
        # A non-empty unverified_application_url means a provider itself
        # supplied a candidate apply URL (e.g. Dice) — distinct from a
        # listing-only job (e.g. Adzuna) that only ever has a source_url.
        # Direct candidates get a reserved share of the shared refresh
        # budget so a listing-only source processed earlier can never
        # starve them out — see EnrichmentBudget.reserve.
        is_direct_candidate = bool(verification_candidate)
        if not budget.reserve(identity, is_direct=is_direct_candidate):
            record_event("skipped_budget_or_duplicate")
            return
    events_start = len(budget.events) if budget else 0
    try:
        evaluation_job = row
        if action["is_direct"]:
            evaluation_job = SimpleNamespace(**{field: getattr(row, field) for field in (
                "company", "title", "location", "source", "source_url", "unverified_application_url")},
                direct_url=None, application_url=None)
        # A changed candidate still needs verification, but an existing verified
        # Apply destination makes further provider searches unnecessary.
        from app.job_sources.career_sources.persistence import metadata
        official = metadata(row)
        if official and verification_candidate:
            from app.services.job_enrichment_service import EnrichmentCandidateResult
            from app.job_sources.career_sources.detector import detect, same_board
            # An unrelated ATS tenant cannot borrow this board's employer
            # evidence merely because its URL appeared in an apply link.
            confirmed_company = row.company if (
                detect(verification_candidate).provider == "custom"
                or same_board(official.get("board_url") or official["career_url"], verification_candidate)
            ) else None
            # The configured public company board supplies company/title
            # evidence. Every existing verification/availability gate remains.
            result = enrich_job(
                evaluation_job, include_local=False,
                employer_website=official.get("career_url"),
                provider=lambda _: [EnrichmentCandidateResult(
                    url=verification_candidate, matched_company=confirmed_company,
                    matched_title=row.title, matched_location=row.location,
                    provider_name="company_direct",
                )],
            )
        else:
            result = (enrich_job(evaluation_job, provider=lambda _: [])
                      if action["action_label"] == "Apply"
                      else enrich_job_with_fallback(evaluation_job))
        apply_enrichment_result(row, result)
        row.enrichment_status = result.status
        row.enrichment_diagnostics = json.dumps({"candidates": result.diagnostics, "events": budget.events[events_start:] if budget else []})
    except Exception as error:  # noqa: BLE001 - enrichment is optional
        row.enrichment_status = "PROVIDER_ERROR"
        row.enrichment_diagnostics = json.dumps({"error_class": type(error).__name__})
        logger.warning(
            "JOB_ENRICHMENT_FAILED job_id=%s source=%s error_class=%s",
            row.id, row.source, type(error).__name__,
        )
    row.enrichment_checked_at = datetime.now(timezone.utc)
    row.enrichment_input_hash = input_hash


def _attempt_jsearch_direct_lookup(row: DiscoveredJob) -> None:
    """Updated Job Search only: a targeted JSearch lookup for a job still
    resolving to a provider/aggregator listing after the enrichment pass
    above — tries to promote a Dice/Adzuna/LinkedIn-only listing to a real
    employer/ATS destination (see app.job_sources.jsearch.lookup_direct_apply).

    Deliberately a SEPARATE call site from _enrich_unverified_application_candidate
    (not nested inside its enrich_job_with_fallback call), so it still runs
    for APPLY_TO_EMAIL/Easy-Apply Dice rows, which that function intentionally
    skips before ever calling enrichment. Bounded by a per-cycle cap (shared
    EnrichmentBudget counters — no dedicated budget field needed) and a 7-day
    per-job cooldown recorded in enrichment_diagnostics (no schema change);
    stops entirely for the rest of this cycle on a 429.
    """
    if row.career_metadata or row.source_mode == "mock" or not row.company or not row.title:
        return
    action = resolve_apply_action(row)
    if action.get("destination_tier") not in ("PROVIDER_JOB_APPLY", "GENERIC_LISTING"):
        return

    try:
        diagnostics = json.loads(row.enrichment_diagnostics or "{}")
    except ValueError:
        diagnostics = {}
    if not isinstance(diagnostics, dict):
        diagnostics = {}
    last_attempt = diagnostics.get("jsearch_lookup_at")
    if last_attempt:
        try:
            last_attempt_at = datetime.fromisoformat(last_attempt)
        except ValueError:
            last_attempt_at = None
        if last_attempt_at and (datetime.now(timezone.utc) - last_attempt_at).total_seconds() < 7 * 24 * 3600:
            return

    from app.job_sources import jsearch

    budget = current_budget.get()
    if budget is not None:
        if budget.counters["jsearch_lookup_attempt"] >= jsearch.lookup_max_per_cycle():
            return
        record_event("jsearch_lookup_attempt")

    result = jsearch.lookup_direct_apply(row.title, row.company, row.location or "")
    if result.get("rate_limited"):
        if budget is not None:
            budget.counters["jsearch_lookup_attempt"] = jsearch.lookup_max_per_cycle()
        record_event("jsearch_lookup_rate_limited")
        return

    if result.get("url"):
        row.unverified_application_url = result["url"]
        record_event("jsearch_lookup_hit")
    else:
        record_event("jsearch_lookup_miss")

    diagnostics["jsearch_lookup_at"] = datetime.now(timezone.utc).isoformat()
    row.enrichment_diagnostics = json.dumps(diagnostics)


def _career_page(db, job: dict) -> str | None:
    from app.services.employer_career_service import career_page_for, official_career_url, remember_career_page

    page = official_career_url(job) or career_page_for(db, job.get("company") or "")
    if page:
        remember_career_page(job.get("company") or "", page)
    return page


def process_source_jobs(db: Session, source: str, normalized_jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """Persist one poll's normalized jobs for a single source.

    Returns counts + the set of DiscoveredJob ids seen this poll (for the
    caller to run close_stale_jobs against).
    """
    if current_budget.get() is None:
        with enrichment_scope():
            return process_source_jobs(db, source, normalized_jobs)
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
        candidate = outbound_application_candidate(job)
        application_type = job.get("candidate_application_type")
        if application_type == "APPLY_TO_EMAIL":
            candidate = None
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
                direct_url=None,
                source_url=job.get("source_url") or job.get("direct_url") or job.get("application_url"),
                application_url=None,
                unverified_application_url=candidate,
                candidate_application_type=application_type,
                source_provenance=json.dumps([{"source": source, "source_job_id": job.get("source_job_id")}]),
                career_page_url=_career_page(db, job),
                career_metadata=job.get("career_metadata"),
                status="ACTIVE",
                missed_poll_count=0,
                content_hash=content_hash,
            )
            db.add(row)
            if job.get("career_metadata"):
                from app.job_sources.career_sources.persistence import apply_metadata
                apply_metadata(row, job)
            db.flush()
            _enrich_unverified_application_candidate(row)
            _attempt_jsearch_direct_lookup(row)
            seen_ids.add(row.id)
            counts["new"] += 1
            logger.info("NEW_JOB_DETECTED source=%s job_id=%s title=%r", source, row.id, row.title[:120])
            continue

        seen_ids.add(existing.id)
        preserve_official = bool(existing.career_metadata) and not job.get("career_metadata")
        content_changed = not preserve_official and existing.content_hash != content_hash
        existing.last_seen_at = now
        existing.last_checked_at = now
        # Candidate-only changes must reach verification even when JD is unchanged.
        # They never overwrite an already verified destination.
        if not preserve_official and (candidate or job.get("source_url")):
            from app.services.job_normalization_service import incoming_destination_wins

            if candidate and incoming_destination_wins(candidate, existing.unverified_application_url):
                existing.unverified_application_url = candidate
                existing.candidate_application_type = application_type
            incoming_source = job.get("source_url")
            if incoming_source and incoming_destination_wins(incoming_source, existing.source_url):
                existing.source_url = incoming_source
            if not existing.career_page_url:
                page = _career_page(db, job)
                if page:
                    existing.career_page_url = page
        elif application_type and not existing.unverified_application_url:
            existing.candidate_application_type = application_type
        provenance = json.loads(existing.source_provenance or "[]")
        entry = {"source": source, "source_job_id": job.get("source_job_id")}
        if entry not in provenance:
            provenance.append(entry)
        existing.source_provenance = json.dumps(provenance)
        if job.get("career_metadata"):
            from app.job_sources.career_sources.persistence import apply_metadata
            apply_metadata(existing, job)
        if not preserve_official:
            existing.missed_poll_count = 0
        if not preserve_official and existing.status != "ACTIVE":
            existing.status = "ACTIVE"
        if content_changed:
            existing.description = job["description"]
            existing.requirements = job.get("requirements") or None
            existing.skills = job.get("skills") or None
            existing.location = job.get("location")
            existing.work_mode = job.get("work_mode")
            existing.job_type = job.get("job_type")
            # An aggregator refresh (notably Dice) has no verified direct URL
            # and must never erase a destination previously promoted by the
            # shared verifier.  A newly supplied direct URL may still update
            # its respective field through the normal source path.
            existing.company_logo_url = job.get("company_logo_url") or existing.company_logo_url
            existing.content_hash = content_hash
            existing.updated_detected_at = now
            counts["updated"] += 1
            logger.info("JOB_UPDATED source=%s job_id=%s title=%r", source, existing.id, existing.title[:120])
        else:
            counts["duplicates"] += 1
            logger.debug("DUPLICATE_SKIPPED source=%s job_id=%s", source, existing.id)

        _enrich_unverified_application_candidate(existing)
        _attempt_jsearch_direct_lookup(existing)

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
