"""Additive schema setup for Updated Job Search (career/ATS tracker) tables.

Follows the same lightweight, no-Alembic convention as
app.services.schema_upgrades.ensure_ats_schema: new tables via
metadata.create_all with an explicit table list, plus additive
ALTER TABLE ADD COLUMN for columns added to a table that may already exist
from an earlier deployment of this feature — never touches any
pre-existing production table outside this feature.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text

logger = logging.getLogger(__name__)

# table -> {column: DDL}. Additive only; applied only when the column is
# missing (discovered_jobs already existed before company_logo_url /
# application_url were added).
TABLE_COLUMN_DDL = {
    "discovered_jobs": {
        "company_logo_url": "ALTER TABLE discovered_jobs ADD COLUMN company_logo_url TEXT",
        "application_url": "ALTER TABLE discovered_jobs ADD COLUMN application_url TEXT",
        "candidate_application_type": "ALTER TABLE discovered_jobs ADD COLUMN candidate_application_type VARCHAR(40)",
        "source_provenance": "ALTER TABLE discovered_jobs ADD COLUMN source_provenance TEXT",
        "verified_destination_url": "ALTER TABLE discovered_jobs ADD COLUMN verified_destination_url TEXT",
        "enrichment_checked_at": "ALTER TABLE discovered_jobs ADD COLUMN enrichment_checked_at TIMESTAMP WITH TIME ZONE",
        "enrichment_input_hash": "ALTER TABLE discovered_jobs ADD COLUMN enrichment_input_hash VARCHAR(64)",
        "enrichment_status": "ALTER TABLE discovered_jobs ADD COLUMN enrichment_status VARCHAR(40)",
        "enrichment_diagnostics": "ALTER TABLE discovered_jobs ADD COLUMN enrichment_diagnostics TEXT",
        "career_page_url": "ALTER TABLE discovered_jobs ADD COLUMN career_page_url TEXT",
        "career_metadata": "ALTER TABLE discovered_jobs ADD COLUMN career_metadata TEXT",
    },
    "job_source_runs": {
        "user_id": "ALTER TABLE job_source_runs ADD COLUMN user_id INTEGER",
        "outcome": "ALTER TABLE job_source_runs ADD COLUMN outcome VARCHAR(30)",
        "diagnostics": "ALTER TABLE job_source_runs ADD COLUMN diagnostics TEXT",
    },
}

# table -> {column: minimum VARCHAR length}. job_sources.mode originally
# shipped as VARCHAR(10) — one character too narrow for the legitimate value
# "unavailable" (11 chars), which every adapter's health_check() reports for
# an intentionally-unconfigured/unavailable source (e.g. Greenhouse with no
# configured boards, Workday in production). SQLite never enforces VARCHAR
# length, so this was invisible to the test suite; Postgres does, turning a
# routine "not configured" status write into a DataError that got logged as
# JOB_SOURCE_FAILED — see job_source_registry_service.record_health. Widened
# with headroom for any future status string.
TABLE_COLUMN_WIDEN = {
    "job_sources": {"mode": 20},
    # discovered_jobs.source_job_id originally shipped as VARCHAR(160) —
    # fine for Greenhouse/Lever's short numeric/slug ids, but a real,
    # live-verified JSearch job_id is an opaque ~400-character base64
    # token. external_id ("{source}:{source_job_id}") widened to match.
    "discovered_jobs": {"source_job_id": 512, "external_id": 600, "direct_url_source": 80},
}


def _widen_varchar_columns(connection, inspector, existing_tables: set[str]) -> None:
    if connection.engine.dialect.name != "postgresql":
        return  # SQLite doesn't enforce VARCHAR length — nothing to widen or fix
    for table_name, columns in TABLE_COLUMN_WIDEN.items():
        if table_name not in existing_tables:
            continue
        existing_columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        for column_name, min_length in columns.items():
            column = existing_columns.get(column_name)
            if column is None:
                continue
            current_length = getattr(column["type"], "length", None)
            if current_length is not None and current_length < min_length:
                connection.execute(text(f"ALTER TABLE {table_name} ALTER COLUMN {column_name} TYPE VARCHAR({min_length})"))
                logger.info(
                    "SCHEMA_COLUMN_WIDENED table=%s column=%s from=%s to=%s",
                    table_name, column_name, current_length, min_length,
                )


def ensure_job_discovery_schema() -> None:
    try:
        from app.database import Base, engine
        from app.models.discovered_job_model import DiscoveredJob
        from app.models.job_source_model import JobSource, JobSourceRun
        from app.models.job_match_model import JobMatch
        from app.models.company_source_model import CompanySource
        from app.models.job_apply_event_model import JobApplyEvent

        Base.metadata.create_all(
            bind=engine,
            tables=[
                DiscoveredJob.__table__,
                JobSource.__table__,
                JobSourceRun.__table__,
                JobMatch.__table__,
                CompanySource.__table__,
                JobApplyEvent.__table__,
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

            _widen_varchar_columns(connection, inspector, existing_tables)

        logger.info(
            "Job discovery schema verified (discovered_jobs, job_sources, job_source_runs, "
            "job_matches, company_sources, job_apply_events)"
        )
    except Exception:
        logger.exception("Job discovery schema ensure failed")
        raise
