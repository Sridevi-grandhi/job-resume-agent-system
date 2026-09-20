"""Automatic Gmail scanning for Email Summary — kept logically separate from
Job Search / ATS / Daily Summary / Selected Jobs, but reuses the ONE shared
APScheduler instance already started by app.scheduler.daily_scheduler (so we
never run a second background scheduler thread).

Only ever calls app.services.job_email_scan_service.scan_for_job_emails —
the exact same pipeline the manual "Scan Job Emails" button uses. That
pipeline already guarantees every newly scanned event stays
NEEDS_CONFIRMATION (see its own module docstring); this scheduler adds
nothing on top except *when* it runs and per-user overlap protection so a
slow/stuck scan can never run twice concurrently for the same user. It never
updates ApplicationTracking, never sends email, and never creates a Gmail
draft.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.triggers.interval import IntervalTrigger

from app.models.email_connection_model import EmailConnection
from app.models.email_summary_preference_model import EmailSummaryPreference
from app.scheduler.daily_scheduler import scheduler
from app.services import gmail_service, job_email_scan_service

logger = logging.getLogger(__name__)

FREQUENCY_TO_HOURS = {
    "daily": 24,
    "every_12_hours": 12,
    "every_6_hours": 6,
}
DEFAULT_FREQUENCY = "daily"

# A run that's held the lock longer than this is assumed crashed/killed
# rather than genuinely still in progress — the lock is treated as stale and
# a new run is allowed, so one wedged process can never permanently block a
# user's automatic scans.
STALE_LOCK_MINUTES = 15


def run_automatic_scan_for_user(user_id: int) -> str:
    """One user's automatic scan. Never raises — any failure is caught and
    logged so it can never take down the scheduler thread or block another
    user's job."""
    # Lazy import (not a module-level `from app.database import SessionLocal`):
    # this function is also reachable indirectly from a request (the
    # preferences route re-syncs the scheduler after every save), so it must
    # resolve app.database.SessionLocal fresh at call time to pick up a
    # test's patched session — see app.services.ats_optimization_workflow_service
    # / updated_job_discovery_service for the same convention.
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        connection = db.query(EmailConnection).filter(EmailConnection.user_id == user_id).first()
        if connection is None or connection.status != "CONNECTED":
            return "skipped_not_connected"

        preference = db.query(EmailSummaryPreference).filter(EmailSummaryPreference.user_id == user_id).first()
        if preference is None or not preference.automatic_gmail_scan_enabled:
            return "skipped_disabled"

        now = datetime.now(timezone.utc)
        if connection.auto_scan_running and connection.auto_scan_started_at:
            started_at = connection.auto_scan_started_at
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            if now - started_at < timedelta(minutes=STALE_LOCK_MINUTES):
                return "skipped_overlap"

        connection.auto_scan_running = True
        connection.auto_scan_started_at = now
        db.commit()
        try:
            job_email_scan_service.scan_for_job_emails(db, user_id=user_id)
            return "scanned"
        except gmail_service.GmailAuthError:
            return "skipped_not_connected"
        except gmail_service.GmailApiError:
            logger.warning("automatic_gmail_scan_api_error user_id=%s", user_id)
            return "failed"
        finally:
            connection.auto_scan_running = False
            db.commit()
    except Exception:
        logger.exception("automatic_gmail_scan_failed user_id=%s", user_id)
        return "failed"
    finally:
        db.close()


def schedule_gmail_auto_scans() -> None:
    """Re-syncs one APScheduler job per opted-in, connected user onto the
    shared scheduler. Safe to call repeatedly (e.g. after every preferences
    save) — stale registrations are removed first, same pattern as
    daily_scheduler.schedule_daily_summary_jobs()."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        for (user_id,) in db.query(EmailSummaryPreference.user_id).all():
            job_id = f"gmail_auto_scan_user_{user_id}"
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)

        rows = (
            db.query(EmailSummaryPreference)
            .filter(EmailSummaryPreference.automatic_gmail_scan_enabled.is_(True))
            .all()
        )
        for preference in rows:
            hours = FREQUENCY_TO_HOURS.get(preference.gmail_scan_frequency or DEFAULT_FREQUENCY, 24)
            job_id = f"gmail_auto_scan_user_{preference.user_id}"
            scheduler.add_job(
                run_automatic_scan_for_user,
                trigger=IntervalTrigger(hours=hours),
                id=job_id,
                args=[preference.user_id],
                replace_existing=True,
            )
    finally:
        db.close()
