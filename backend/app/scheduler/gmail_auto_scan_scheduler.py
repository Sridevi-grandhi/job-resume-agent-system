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
from sqlalchemy import or_

from app.models.email_connection_model import EmailConnection
from app.models.email_summary_preference_model import EmailSummaryPreference
from app.scheduler.daily_scheduler import scheduler
from app.services import gmail_service, job_email_scan_service

logger = logging.getLogger("uvicorn.error")

FREQUENCY_TO_HOURS = {
    "every_1_hour": 1,
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
FIRST_SCAN_DELAY = timedelta(minutes=1)


def _log_skip(user_id: int, reason: str) -> str:
    logger.info("AUTO_GMAIL_SCAN_SKIPPED user_id=%s reason=%s", user_id, reason)
    return f"skipped_{reason}"


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
    claimed = False
    started_at = datetime.now(timezone.utc)
    try:
        logger.info("AUTO_GMAIL_SCAN_STARTED user_id=%s scheduled_at=%s", user_id, started_at.isoformat())
        connection = db.query(EmailConnection).filter(EmailConnection.user_id == user_id).first()
        if connection is None or connection.status != "CONNECTED":
            return _log_skip(user_id, "not_connected")

        preference = db.query(EmailSummaryPreference).filter(EmailSummaryPreference.user_id == user_id).first()
        if preference is None or not preference.automatic_gmail_scan_enabled:
            return _log_skip(user_id, "disabled")
        if not connection.encrypted_access_token or not connection.encrypted_refresh_token:
            return _log_skip(user_id, "missing_token")

        # Claim the row in one UPDATE so two backend instances cannot both
        # observe an unlocked connection and start the same user's scan.
        stale_before = started_at - timedelta(minutes=STALE_LOCK_MINUTES)
        claimed = bool(db.query(EmailConnection).filter(
            EmailConnection.id == connection.id,
            EmailConnection.status == "CONNECTED",
            or_(
                EmailConnection.auto_scan_running.is_(False),
                EmailConnection.auto_scan_running.is_(None),
                EmailConnection.auto_scan_started_at.is_(None),
                EmailConnection.auto_scan_started_at < stale_before,
            ),
        ).update({
            EmailConnection.auto_scan_running: True,
            EmailConnection.auto_scan_started_at: started_at,
        }, synchronize_session=False))
        db.commit()
        if not claimed:
            return _log_skip(user_id, "overlap")
        try:
            summary = job_email_scan_service.scan_for_job_emails(db, user_id=user_id)
            outcomes = summary.get("outcomes") or {}
            related = sum(count for key, count in outcomes.items() if key not in {
                "unrelated_ignored", "category_disabled_skipped", "duplicate_skipped", "fetch_failed",
            })
            logger.info(
                "AUTO_GMAIL_SCAN_FETCHED user_id=%s count=%s", user_id, summary.get("messages_checked", 0),
            )
            logger.info("AUTO_GMAIL_SCAN_JOB_RELATED user_id=%s count=%s", user_id, related)
            logger.info(
                "AUTO_GMAIL_SCAN_IGNORED user_id=%s count=%s", user_id,
                outcomes.get("unrelated_ignored", 0) + outcomes.get("category_disabled_skipped", 0),
            )
            logger.info("AUTO_GMAIL_SCAN_DUPLICATE user_id=%s count=%s", user_id, outcomes.get("duplicate_skipped", 0))
            logger.info(
                "AUTO_GMAIL_SCAN_COMPLETED user_id=%s job_related=%s ignored=%s duplicate=%s fetch_failed=%s",
                user_id, related, outcomes.get("unrelated_ignored", 0) + outcomes.get("category_disabled_skipped", 0),
                outcomes.get("duplicate_skipped", 0), outcomes.get("fetch_failed", 0),
            )
            return "scanned"
        except gmail_service.GmailAuthError:
            connection.status = "ERROR"
            connection.last_error = "gmail_authorization_failed"
            db.commit()
            logger.warning("AUTO_GMAIL_SCAN_FAILED user_id=%s reason=authorization", user_id)
            return "failed"
        except gmail_service.GmailApiError:
            logger.warning("AUTO_GMAIL_SCAN_FAILED user_id=%s reason=gmail_api", user_id)
            return "failed"
    except Exception as error:
        db.rollback()
        logger.error("AUTO_GMAIL_SCAN_FAILED user_id=%s reason=%s", user_id, type(error).__name__)
        return "failed"
    finally:
        if claimed:
            try:
                db.query(EmailConnection).filter(
                    EmailConnection.user_id == user_id,
                    EmailConnection.auto_scan_started_at == started_at,
                ).update({EmailConnection.auto_scan_running: False, EmailConnection.auto_scan_started_at: None}, synchronize_session=False)
                db.commit()
            except Exception:
                db.rollback()
                logger.error("AUTO_GMAIL_SCAN_FAILED user_id=%s reason=lock_release", user_id)
        db.close()


def schedule_gmail_auto_scans() -> None:
    """Re-syncs one APScheduler job per opted-in, connected user onto the
    shared scheduler. Safe to call repeatedly (e.g. after every preferences
    save). Existing jobs retain their next run; newly eligible accounts get
    a prompt first scan, then run at the selected interval."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        rows = (
            db.query(EmailSummaryPreference, EmailConnection)
            .outerjoin(EmailConnection, EmailConnection.user_id == EmailSummaryPreference.user_id)
            .filter(EmailSummaryPreference.automatic_gmail_scan_enabled.is_(True))
            .all()
        )
        eligible_ids = set()
        now = datetime.now(timezone.utc)
        for preference, connection in rows:
            if connection is None or connection.status != "CONNECTED":
                _log_skip(preference.user_id, "not_connected")
                continue
            if not connection.encrypted_access_token or not connection.encrypted_refresh_token:
                _log_skip(preference.user_id, "missing_token")
                continue
            hours = FREQUENCY_TO_HOURS.get(preference.gmail_scan_frequency or DEFAULT_FREQUENCY, 24)
            job_id = f"gmail_auto_scan_user_{preference.user_id}"
            eligible_ids.add(job_id)
            existing = scheduler.get_job(job_id)
            if existing and getattr(existing.trigger, "interval", None) == timedelta(hours=hours):
                continue
            next_run_at = now + FIRST_SCAN_DELAY
            scheduler.add_job(
                run_automatic_scan_for_user,
                trigger=IntervalTrigger(hours=hours),
                id=job_id,
                args=[preference.user_id],
                replace_existing=True,
                next_run_time=next_run_at,
            )
            logger.info(
                "AUTO_GMAIL_SCAN_USER user_id=%s interval_hours=%s next_run_at=%s",
                preference.user_id, hours, next_run_at.isoformat(),
            )
        for job in scheduler.get_jobs():
            if job.id.startswith("gmail_auto_scan_user_") and job.id not in eligible_ids:
                scheduler.remove_job(job.id)
                logger.info("AUTO_GMAIL_SCAN_SKIPPED user_id=%s reason=not_eligible", job.id.removeprefix("gmail_auto_scan_user_"))
        logger.info("GMAIL_AUTO_SCAN_SCHEDULER_STARTED eligible_users=%s", len(eligible_ids))
    finally:
        db.close()
