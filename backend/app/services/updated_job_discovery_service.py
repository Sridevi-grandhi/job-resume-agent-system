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
from datetime import datetime, timezone
from typing import Any

from app.job_sources.base import SourceHealth
from app.models.job_source_model import JobSourceRun
from app.services import job_change_detection_service as change_detection
from app.services import job_source_registry_service as registry

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
    text = str(error)[:300]
    for env_var in ("JSEARCH_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LLM_API_KEY"):
        secret = (os.getenv(env_var) or "").strip()
        if secret and secret in text:
            text = text.replace(secret, "[REDACTED]")
    return text


def poll_status() -> dict[str, Any]:
    return {"running": _poll_running, "last_summary": _last_summary}


def run_poll_cycle(triggered_by: str = "scheduler") -> dict[str, Any]:
    global _poll_running, _last_summary

    if not _poll_lock.acquire(blocking=False):
        logger.info("UPDATED_JOB_POLL_ALREADY_RUNNING triggered_by=%s", triggered_by)
        return {"already_running": True, **poll_status()}

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
            adapters = [adapter for adapter in registry.get_adapters(db) if getattr(adapter, "enabled", True)]
            limit = _max_jobs_per_source()

            for adapter in adapters:
                run_started_at = datetime.now(timezone.utc)
                logger.info("JOB_SOURCE_STARTED source=%s", adapter.source_name)
                run_row = JobSourceRun(
                    source_name=adapter.source_name,
                    triggered_by=triggered_by,
                    run_started_at=run_started_at,
                )
                db.add(run_row)
                db.flush()

                stage = "fetch"
                try:
                    normalized_jobs = adapter.fetch_normalized_jobs(limit=limit)
                    for job in normalized_jobs:
                        logger.debug("JOB_NORMALIZED source=%s title=%r", adapter.source_name, job["title"][:120])

                    stage = "persist"
                    counts = change_detection.process_source_jobs(db, adapter.source_name, normalized_jobs)
                    closed = change_detection.close_stale_jobs(db, adapter.source_name, counts["seen_ids"])

                    stage = "health_check"
                    health = adapter.health_check()
                    registry.record_health(db, health)

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
                        "error": None,
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
        }
        _last_summary = summary
        logger.info(
            "UPDATED_JOB_POLL_COMPLETED triggered_by=%s total_fetched=%s total_new=%s total_updated=%s",
            triggered_by, summary["total_fetched"], summary["total_new"], summary["total_updated"],
        )
        return summary
    finally:
        _poll_running = False
        _poll_lock.release()
