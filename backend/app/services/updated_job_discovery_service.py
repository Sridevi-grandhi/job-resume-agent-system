"""Poll orchestrator for Updated Job Search.

Scheduled Poll -> load enabled sources -> fetch -> normalize -> compare with
DB -> detect new/updated -> persist -> (caller matches per user).

One failing source must never stop the others (section 40/50), and a poll
already running must return the in-flight status instead of starting a
duplicate (section 10) — both the scheduler and the manual Refresh button
call the exact same run_poll_cycle().
"""

from __future__ import annotations

import logging
import os
import threading
import json
from collections import Counter
from types import SimpleNamespace
from datetime import datetime, timezone
from typing import Any

from app.job_sources.base import SourceHealth
from app.models.job_source_model import JobSourceRun
from app.services import job_change_detection_service as change_detection
from app.services import job_source_registry_service as registry
from app.services.job_enrichment_context import enrichment_scope
from app.services.job_relevance_filter import evaluate_relevance
from app.services.job_normalization_service import resolve_apply_action
from app.models.user_preference_model import UserPreference
from app.models.discovered_job_model import DiscoveredJob

logger = logging.getLogger(__name__)

_poll_lock = threading.Lock()
_poll_running = False
_last_summary: dict[str, Any] | None = None


def _max_jobs_per_source() -> int:
    return max(1, int(os.getenv("JOB_DISCOVERY_MAX_JOBS_PER_SOURCE") or "100"))


def _safe_error_summary(error: Exception) -> str:
    """A truncated exception message safe for logs/JobSourceRun — never the
    full traceback (which stays in logger.exception-free warning logs only,
    never persisted), and defensively redacted in case any configured
    provider API key value ever ended up embedded in a library's exception
    text (e.g. a proxy/SSL error echoing request state)."""
    # Third-party exception text may contain credentials, URLs or request bodies.
    return type(error).__name__


def poll_status() -> dict[str, Any]:
    return {"running": _poll_running, "last_summary": _last_summary}


def run_poll_cycle(triggered_by: str = "scheduler", user_id: int | None = None) -> dict[str, Any]:
    global _poll_running, _last_summary

    if not _poll_lock.acquire(blocking=False):
        logger.info("UPDATED_JOB_POLL_ALREADY_RUNNING triggered_by=%s", triggered_by)
        return {"already_running": True, **poll_status()}

    enrichment_context = enrichment_scope()
    budget = enrichment_context.__enter__()
    try:
        _poll_running = True
        started_at = datetime.now(timezone.utc)
        logger.info("UPDATED_JOB_POLL_STARTED triggered_by=%s", triggered_by)

        # Imported here (not at module top-level) so test fixtures that
        # monkeypatch app.database.SessionLocal after this module is first
        # imported still take effect — same pattern as
        # ats_optimization_workflow_service.run_workflow_job.
        from app.database import SessionLocal

        db = SessionLocal()
        source_summaries: list[dict[str, Any]] = []
        try:
            registry.sync_registry(db)
            adapters = registry.get_adapters(db, user_id=user_id) if user_id is not None else registry.get_adapters(db)
            adapters = [adapter for adapter in adapters if getattr(adapter, "enabled", True)]
            preference = db.query(UserPreference).filter_by(user_id=user_id).first() if user_id is not None else None
            if user_id is not None and (preference is None or not (preference.job_title or "").strip()):
                adapters = []  # A manual refresh never falls back to another user's preferences.
            limit = _max_jobs_per_source()

            for adapter in adapters:
                run_started_at = datetime.now(timezone.utc)
                logger.info("JOB_SOURCE_STARTED source=%s", adapter.source_name)
                run_row = JobSourceRun(
                    source_name=adapter.source_name,
                    triggered_by=triggered_by,
                    run_started_at=run_started_at,
                    user_id=user_id,
                )
                db.add(run_row)
                db.flush()

                stage = "fetch"
                try:
                    normalized_jobs = adapter.fetch_normalized_jobs(limit=limit)
                    normalized_count = len(normalized_jobs)
                    if preference is not None:
                        normalized_jobs = [job for job in normalized_jobs if evaluate_relevance(SimpleNamespace(**job), preference).relevant]
                    before_enrichment = budget.counters.copy()
                    for job in normalized_jobs:
                        logger.debug("JOB_NORMALIZED source=%s title=%r", adapter.source_name, job["title"][:120])

                    stage = "persist"
                    counts = change_detection.process_source_jobs(db, adapter.source_name, normalized_jobs)
                    closed = 0

                    stage = "health_check"
                    health = adapter.health_check()
                    registry.record_health(db, health)
                    outcome = "SUCCESS" if health.healthy else ("UNAVAILABLE" if health.mode == "unavailable" else "RATE_LIMITED" if health.error == "rate_limited" else "FAILED")
                    # User-scoped searches are not an authoritative global board inventory.
                    if user_id is None and health.healthy and getattr(adapter, "complete_inventory", True):
                        closed = change_detection.close_stale_jobs(db, adapter.source_name, counts["seen_ids"])
                    final_jobs = db.query(DiscoveredJob).filter(DiscoveredJob.id.in_(counts["seen_ids"])).all()
                    diagnostics = {
                        "raw": getattr(adapter, "last_counts", {}).get("raw", normalized_count),
                        "normalized": normalized_count, "accepted": len(normalized_jobs),
                        "rejected": normalized_count - len(normalized_jobs) + getattr(adapter, "last_counts", {}).get("rejected", 0),
                        "new": counts["new"], "updated": counts["updated"], "duplicates": counts["duplicates"],
                        "outcome": outcome,
                        "enrichment": dict(budget.counters - before_enrichment),
                        "actions": dict(Counter(resolve_apply_action(row)["action_label"] for row in final_jobs)),
                    }
                    run_row.outcome = outcome
                    run_row.diagnostics = json.dumps(diagnostics)
                    run_row.errors = int(outcome in {"FAILED", "RATE_LIMITED"})
                    logger.info("JOB_SOURCE_DIAGNOSTICS source=%s data=%s", adapter.source_name, run_row.diagnostics)

                    run_row.run_completed_at = datetime.now(timezone.utc)
                    run_row.jobs_fetched = counts["fetched"]
                    run_row.jobs_new = counts["new"]
                    run_row.jobs_updated = counts["updated"]
                    run_row.duplicates_skipped = counts["duplicates"]
                    run_row.duration_ms = int((run_row.run_completed_at - run_started_at).total_seconds() * 1000)
                    db.commit()

                    logger.info(
                        "JOB_SOURCE_COMPLETED source=%s fetched=%s new=%s updated=%s duplicates=%s closed=%s mode=%s",
                        adapter.source_name, counts["fetched"], counts["new"], counts["updated"],
                        counts["duplicates"], closed, health.mode,
                    )
                    source_summaries.append({
                        "source": adapter.source_name,
                        "mode": health.mode,
                        "healthy": health.healthy,
                        "fetched": counts["fetched"],
                        "new": counts["new"],
                        "updated": counts["updated"],
                        "duplicates": counts["duplicates"],
                        "closed": closed,
                        "error": "temporarily unavailable" if outcome in {"FAILED", "RATE_LIMITED"} else None,
                        "outcome": outcome,
                        "diagnostics": diagnostics,
                    })
                except Exception as error:
                    db.rollback()
                    safe_summary = _safe_error_summary(error)
                    failed_run = JobSourceRun(
                        source_name=adapter.source_name,
                        triggered_by=triggered_by,
                        run_started_at=run_started_at,
                        run_completed_at=datetime.now(timezone.utc),
                        errors=1,
                        user_id=user_id,
                        outcome="FAILED",
                        error_message=f"{stage}: {type(error).__name__}: {safe_summary}"[:2000],
                    )
                    db.add(failed_run)
                    db.commit()

                    # mode="error" (never "unavailable") — a real exception
                    # occurred here, distinct from an adapter's own benign
                    # "not configured" self-report, so downstream status
                    # classification never confuses the two (see
                    # job_source_registry_service.classify_source_status).
                    unhealthy = SourceHealth(
                        adapter.source_name, adapter.source_type, "error", False,
                        datetime.now(timezone.utc).isoformat(), error=type(error).__name__,
                    )
                    try:
                        registry.record_health(db, unhealthy)
                    except Exception:
                        db.rollback()

                    logger.warning(
                        "JOB_SOURCE_FAILED source=%s stage=%s error_class=%s detail=%s",
                        adapter.source_name, stage, type(error).__name__, safe_summary,
                    )
                    source_summaries.append({
                        "source": adapter.source_name,
                        "mode": "error",
                        "healthy": False,
                        "fetched": 0, "new": 0, "updated": 0, "duplicates": 0, "closed": 0,
                        "error": "temporarily unavailable",
                    })
                    continue
        finally:
            db.close()

        completed_at = datetime.now(timezone.utc)
        summary = {
            "already_running": False,
            "triggered_by": triggered_by,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "sources": source_summaries,
            "total_fetched": sum(s["fetched"] for s in source_summaries),
            "total_new": sum(s["new"] for s in source_summaries),
            "total_updated": sum(s["updated"] for s in source_summaries),
            "enrichment": dict(budget.counters),
        }
        _last_summary = summary
        logger.info(
            "UPDATED_JOB_POLL_COMPLETED triggered_by=%s total_fetched=%s total_new=%s total_updated=%s",
            triggered_by, summary["total_fetched"], summary["total_new"], summary["total_updated"],
        )
        return summary
    finally:
        enrichment_context.__exit__(None, None, None)
        _poll_running = False
        _poll_lock.release()
