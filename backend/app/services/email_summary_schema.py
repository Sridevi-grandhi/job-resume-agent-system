"""Additive schema setup for the Email Summary feature (Gmail connection,
application tracking, job email events, recruiter drafts). Same lightweight,
no-Alembic convention as app.services.job_discovery_schema /
app.services.schema_upgrades: new tables via metadata.create_all with an
explicit table list, plus additive ALTER TABLE ADD COLUMN for a column added
to a table that already existed before this feature (email_logs.event_type).
Never touches any table outside this feature's scope.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

TABLE_COLUMN_DDL = {
    "email_logs": {
        "event_type": "ALTER TABLE email_logs ADD COLUMN event_type VARCHAR(40)",
    },
    "job_email_events": {
        "match_confidence": "ALTER TABLE job_email_events ADD COLUMN match_confidence FLOAT",
        # Email Summary redesign — CareerBoost-local only, never Gmail state.
        "is_read": "ALTER TABLE job_email_events ADD COLUMN is_read BOOLEAN NOT NULL DEFAULT FALSE",
        "is_starred": "ALTER TABLE job_email_events ADD COLUMN is_starred BOOLEAN NOT NULL DEFAULT FALSE",
        # Display-only, paragraph-preserving sibling of body_snippet — see
        # the model's own docstring. Nullable: a pre-existing row just falls
        # back to body_snippet's flattened text at display time.
        "body_formatted": "ALTER TABLE job_email_events ADD COLUMN body_formatted TEXT",
    },
    "email_summary_preferences": {
        "gmail_scan_frequency": "ALTER TABLE email_summary_preferences ADD COLUMN gmail_scan_frequency VARCHAR(20) NOT NULL DEFAULT 'daily'",
        "email_types_to_scan": "ALTER TABLE email_summary_preferences ADD COLUMN email_types_to_scan TEXT",
        "job_alerts_enabled": "ALTER TABLE email_summary_preferences ADD COLUMN job_alerts_enabled BOOLEAN NOT NULL DEFAULT TRUE",
        "application_updates_enabled": "ALTER TABLE email_summary_preferences ADD COLUMN application_updates_enabled BOOLEAN NOT NULL DEFAULT TRUE",
        "interview_alerts_enabled": "ALTER TABLE email_summary_preferences ADD COLUMN interview_alerts_enabled BOOLEAN NOT NULL DEFAULT TRUE",
    },
    "email_connections": {
        "auto_scan_running": "ALTER TABLE email_connections ADD COLUMN auto_scan_running BOOLEAN NOT NULL DEFAULT FALSE",
        "auto_scan_started_at": "ALTER TABLE email_connections ADD COLUMN auto_scan_started_at TIMESTAMP WITH TIME ZONE",
    },
    # Production Recruiter Drafts async-architecture upgrade (see
    # recruiter_draft_model.py's lifecycle docstring): additive columns only
    # — no existing column is dropped or renamed, so any row written by the
    # pre-upgrade synchronous code path still reads back safely (its status
    # stays a legacy value; the new columns default to NULL/0 for it).
    "recruiter_drafts": {
        "draft_type": "ALTER TABLE recruiter_drafts ADD COLUMN draft_type VARCHAR(40)",
        "tone": "ALTER TABLE recruiter_drafts ADD COLUMN tone VARCHAR(20) NOT NULL DEFAULT 'professional'",
        "provider": "ALTER TABLE recruiter_drafts ADD COLUMN provider VARCHAR(40)",
        "model": "ALTER TABLE recruiter_drafts ADD COLUMN model VARCHAR(120)",
        "prompt_version": "ALTER TABLE recruiter_drafts ADD COLUMN prompt_version VARCHAR(40) NOT NULL DEFAULT 'recruiter-draft-v1'",
        "generation_attempts": "ALTER TABLE recruiter_drafts ADD COLUMN generation_attempts INTEGER NOT NULL DEFAULT 0",
        "last_error_code": "ALTER TABLE recruiter_drafts ADD COLUMN last_error_code VARCHAR(60)",
        "queued_at": "ALTER TABLE recruiter_drafts ADD COLUMN queued_at TIMESTAMP WITH TIME ZONE",
        "generation_started_at": "ALTER TABLE recruiter_drafts ADD COLUMN generation_started_at TIMESTAMP WITH TIME ZONE",
        "generated_at": "ALTER TABLE recruiter_drafts ADD COLUMN generated_at TIMESTAMP WITH TIME ZONE",
    },
}


def ensure_email_summary_schema() -> None:
    try:
        from app.database import Base, engine
        from app.models.email_connection_model import EmailConnection
        from app.models.application_tracking_model import ApplicationTracking
        from app.models.application_status_history_model import ApplicationStatusHistory
        from app.models.job_email_event_model import JobEmailEvent
        from app.models.recruiter_draft_model import RecruiterDraft
        from app.models.summary_delivery_model import SummaryDeliveryAttempt
        from app.models.email_summary_preference_model import EmailSummaryPreference
        from app.models.scheduled_daily_summary_model import ScheduledDailySummary

        Base.metadata.create_all(
            bind=engine,
            tables=[
                EmailConnection.__table__,
                ApplicationTracking.__table__,
                ApplicationStatusHistory.__table__,
                JobEmailEvent.__table__,
                RecruiterDraft.__table__,
                SummaryDeliveryAttempt.__table__,
                EmailSummaryPreference.__table__,
                ScheduledDailySummary.__table__,
            ],
        )

        with engine.begin() as connection:
            inspector = inspect(connection)
            existing_tables = set(inspector.get_table_names())
            for table_name, columns in TABLE_COLUMN_DDL.items():
                if table_name not in existing_tables:
                    continue
                existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
                for name, ddl in columns.items():
                    if name not in existing_columns:
                        connection.execute(text(ddl))

            if "recruiter_drafts" in existing_tables:
                # generated_body was NOT NULL pre-upgrade; a PENDING/QUEUED/
                # GENERATING row now legitimately has no body yet.
                columns_now = {column["name"]: column for column in inspect(connection).get_columns("recruiter_drafts")}
                body_column = columns_now.get("generated_body")
                if body_column is not None and not body_column.get("nullable", True) and engine.dialect.name == "postgresql":
                    connection.execute(text("ALTER TABLE recruiter_drafts ALTER COLUMN generated_body DROP NOT NULL"))
                # Any row written before this upgrade is already GENERATED/
                # SAVED_TO_GMAIL/ERROR (still valid legacy values) or NULL
                # (never happens pre-upgrade) — nothing to backfill.

        # Best-effort only: a pre-existing duplicate row from before this
        # upgrade (should not exist — the service has always reused/updated
        # the existing row per (user_id, job_email_event_id) — but this is
        # defense-in-depth) must never crash startup (Phase 35). Postgres
        # only; SQLite test DBs are created fresh from the model's
        # __table_args__ UniqueConstraint via create_all above already.
        if engine.dialect.name == "postgresql":
            try:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "CREATE UNIQUE INDEX IF NOT EXISTS ux_recruiter_drafts_idempotency "
                            "ON recruiter_drafts (user_id, job_email_event_id, draft_type, prompt_version)"
                        )
                    )
            except SQLAlchemyError:
                logger.warning(
                    "Could not create ux_recruiter_drafts_idempotency (likely pre-existing "
                    "duplicate rows) — draft generation still works, just without the extra "
                    "database-level safety net; application-level reuse-by-lookup still applies.",
                    exc_info=True,
                )

        logger.info(
            "Email Summary schema verified (email_connections, application_tracking, "
            "application_status_history, job_email_events, recruiter_drafts)"
        )
    except Exception:
        logger.exception("Email Summary schema ensure failed")
        raise
