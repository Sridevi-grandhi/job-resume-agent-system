"""Lightweight schema upgrades for environments without Alembic."""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from app.database import Base, engine


logger = logging.getLogger(__name__)


TABLE_COLUMN_DDL = {
    "match_scores": {
        "resume_version": "ALTER TABLE match_scores ADD COLUMN resume_version VARCHAR(20) DEFAULT 'original'",
        "optimized_resume_id": "ALTER TABLE match_scores ADD COLUMN optimized_resume_id INTEGER",
        "score_breakdown": "ALTER TABLE match_scores ADD COLUMN score_breakdown TEXT",
        "suggestions": "ALTER TABLE match_scores ADD COLUMN suggestions TEXT",
    },
    "ats_optimization_workflows": {
        "original_resume_hash": "ALTER TABLE ats_optimization_workflows ADD COLUMN original_resume_hash VARCHAR(64)",
        "optimized_resume_hash": "ALTER TABLE ats_optimization_workflows ADD COLUMN optimized_resume_hash VARCHAR(64)",
        "original_text_length": "ALTER TABLE ats_optimization_workflows ADD COLUMN original_text_length INTEGER",
        "optimized_text_length": "ALTER TABLE ats_optimization_workflows ADD COLUMN optimized_text_length INTEGER",
        "content_changed": "ALTER TABLE ats_optimization_workflows ADD COLUMN content_changed BOOLEAN",
        "optimization_status": "ALTER TABLE ats_optimization_workflows ADD COLUMN optimization_status VARCHAR(40)",
        "improvement_report_json": "ALTER TABLE ats_optimization_workflows ADD COLUMN improvement_report_json TEXT",
        "optimization_allowed": "ALTER TABLE ats_optimization_workflows ADD COLUMN optimization_allowed BOOLEAN",
        "eligibility_reason": "ALTER TABLE ats_optimization_workflows ADD COLUMN eligibility_reason TEXT",
        "eligibility_tier": "ALTER TABLE ats_optimization_workflows ADD COLUMN eligibility_tier VARCHAR(80)",
        "llm_service": "ALTER TABLE ats_optimization_workflows ADD COLUMN llm_service VARCHAR(40)",
        "generation_method": "ALTER TABLE ats_optimization_workflows ADD COLUMN generation_method VARCHAR(60)",
        "llm_used": "ALTER TABLE ats_optimization_workflows ADD COLUMN llm_used BOOLEAN",
        "fallback_used": "ALTER TABLE ats_optimization_workflows ADD COLUMN fallback_used BOOLEAN",
        "fallback_reason": "ALTER TABLE ats_optimization_workflows ADD COLUMN fallback_reason VARCHAR(80)",
        "generation_duration_ms": "ALTER TABLE ats_optimization_workflows ADD COLUMN generation_duration_ms INTEGER",
        "generation_diagnostics_json": "ALTER TABLE ats_optimization_workflows ADD COLUMN generation_diagnostics_json TEXT",
        "last_activity_at": "ALTER TABLE ats_optimization_workflows ADD COLUMN last_activity_at TIMESTAMP WITH TIME ZONE",
    },
    "user_confirmed_skills": {
        "requirement_type": "ALTER TABLE user_confirmed_skills ADD COLUMN requirement_type VARCHAR(20)",
        "evidence_status": "ALTER TABLE user_confirmed_skills ADD COLUMN evidence_status VARCHAR(40)",
        "evidence_source": "ALTER TABLE user_confirmed_skills ADD COLUMN evidence_source VARCHAR(30) DEFAULT 'legacy_unknown'",
        "user_input_description": "ALTER TABLE user_confirmed_skills ADD COLUMN user_input_description TEXT",
        "ai_rewritten_description": "ALTER TABLE user_confirmed_skills ADD COLUMN ai_rewritten_description TEXT",
        "experience_level": "ALTER TABLE user_confirmed_skills ADD COLUMN experience_level VARCHAR(20)",
        "experience_range": "ALTER TABLE user_confirmed_skills ADD COLUMN experience_range VARCHAR(30)",
        "supporting_context": "ALTER TABLE user_confirmed_skills ADD COLUMN supporting_context VARCHAR(300)",
        "confirmed_accurate": "ALTER TABLE user_confirmed_skills ADD COLUMN confirmed_accurate BOOLEAN",
        "employer_name": "ALTER TABLE user_confirmed_skills ADD COLUMN employer_name VARCHAR(200)",
        "role_title": "ALTER TABLE user_confirmed_skills ADD COLUMN role_title VARCHAR(200)",
        "project_name": "ALTER TABLE user_confirmed_skills ADD COLUMN project_name VARCHAR(200)",
        "certification_name": "ALTER TABLE user_confirmed_skills ADD COLUMN certification_name VARCHAR(200)",
        "provider_name": "ALTER TABLE user_confirmed_skills ADD COLUMN provider_name VARCHAR(200)",
        "start_date": "ALTER TABLE user_confirmed_skills ADD COLUMN start_date VARCHAR(20)",
        "end_date": "ALTER TABLE user_confirmed_skills ADD COLUMN end_date VARCHAR(20)",
        "currently_using": "ALTER TABLE user_confirmed_skills ADD COLUMN currently_using BOOLEAN",
        "usage_description": "ALTER TABLE user_confirmed_skills ADD COLUMN usage_description TEXT",
        "work_completed": "ALTER TABLE user_confirmed_skills ADD COLUMN work_completed TEXT",
        "related_technologies": "ALTER TABLE user_confirmed_skills ADD COLUMN related_technologies VARCHAR(300)",
        "impact_description": "ALTER TABLE user_confirmed_skills ADD COLUMN impact_description TEXT",
        "skill_modules": "ALTER TABLE user_confirmed_skills ADD COLUMN skill_modules VARCHAR(400)",
        "workflow_run_id": "ALTER TABLE user_confirmed_skills ADD COLUMN workflow_run_id VARCHAR(120)",
        "is_active": "ALTER TABLE user_confirmed_skills ADD COLUMN is_active BOOLEAN DEFAULT TRUE",
        "superseded_at": "ALTER TABLE user_confirmed_skills ADD COLUMN superseded_at TIMESTAMP WITH TIME ZONE",
    },
    "discovered_jobs": {
        # ACTIVE | EXPIRED | UNKNOWN — see DiscoveredJob model +
        # job_availability_service. Existing rows must default to UNKNOWN
        # (never ACTIVE) since they were never actually checked.
        "availability_status": "ALTER TABLE discovered_jobs ADD COLUMN availability_status VARCHAR(20) NOT NULL DEFAULT 'UNKNOWN'",
        "availability_checked_at": "ALTER TABLE discovered_jobs ADD COLUMN availability_checked_at TIMESTAMP WITH TIME ZONE",
        "availability_reason": "ALTER TABLE discovered_jobs ADD COLUMN availability_reason VARCHAR(300)",
        # "provider" | "enrichment" | NULL — see DiscoveredJob model +
        # job_enrichment_service. Diagnostics only.
        "direct_url_source": "ALTER TABLE discovered_jobs ADD COLUMN direct_url_source VARCHAR(20)",
    },
    "optimized_resumes": {
        "generated_resume_text": "ALTER TABLE optimized_resumes ADD COLUMN generated_resume_text TEXT",
        "edited_resume_text": "ALTER TABLE optimized_resumes ADD COLUMN edited_resume_text TEXT",
        "final_resume_text": "ALTER TABLE optimized_resumes ADD COLUMN final_resume_text TEXT",
        "generated_resume_json": "ALTER TABLE optimized_resumes ADD COLUMN generated_resume_json TEXT",
        "edited_resume_json": "ALTER TABLE optimized_resumes ADD COLUMN edited_resume_json TEXT",
        "original_score": "ALTER TABLE optimized_resumes ADD COLUMN original_score INTEGER",
        "generated_score": "ALTER TABLE optimized_resumes ADD COLUMN generated_score INTEGER",
        "edited_score": "ALTER TABLE optimized_resumes ADD COLUMN edited_score INTEGER",
        "template_preserved": "ALTER TABLE optimized_resumes ADD COLUMN template_preserved BOOLEAN DEFAULT FALSE",
        "source_file_type": "ALTER TABLE optimized_resumes ADD COLUMN source_file_type VARCHAR(40)",
        "generated_file_path": "ALTER TABLE optimized_resumes ADD COLUMN generated_file_path TEXT",
        "final_file_path": "ALTER TABLE optimized_resumes ADD COLUMN final_file_path TEXT",
        "user_modified": "ALTER TABLE optimized_resumes ADD COLUMN user_modified BOOLEAN DEFAULT FALSE",
        "validation_status": "ALTER TABLE optimized_resumes ADD COLUMN validation_status VARCHAR(30)",
        "last_edited_at": "ALTER TABLE optimized_resumes ADD COLUMN last_edited_at TIMESTAMP WITH TIME ZONE",
    },
}

# Widen existing VARCHAR columns that were too short for current workflow values.
# Key: (table, column) -> (min_length_or_None_for_text, postgres_type_sql)
# ANALYSIS_VERSION "evidence-role-gated-v7-applicable-weight-scoring" is 48 chars and
# previously overflowed analysis_version VARCHAR(40), causing DataError on workflow start.
COLUMN_WIDEN_SPECS = {
    ("ats_optimization_workflows", "analysis_version"): (120, "VARCHAR(120)"),
    ("ats_optimization_workflows", "prompt_version"): (80, "VARCHAR(80)"),
    ("ats_optimization_workflows", "workflow_status"): (60, "VARCHAR(60)"),
    ("ats_optimization_workflows", "llm_provider"): (80, "VARCHAR(80)"),
    ("ats_optimization_workflows", "llm_service"): (40, "VARCHAR(40)"),
    ("ats_optimization_workflows", "generation_method"): (60, "VARCHAR(60)"),
    ("ats_optimization_workflows", "fallback_reason"): (80, "VARCHAR(80)"),
    ("ats_optimization_workflows", "optimization_status"): (40, "VARCHAR(40)"),
    ("ats_optimization_workflows", "eligibility_tier"): (80, "VARCHAR(80)"),
    ("ats_optimization_workflows", "eligibility_reason"): (None, "TEXT"),
    ("ats_optimization_workflows", "error_message"): (None, "TEXT"),
    ("optimized_resumes", "generated_file_path"): (None, "TEXT"),
    ("optimized_resumes", "final_file_path"): (None, "TEXT"),
    ("optimized_resumes", "source_file_type"): (40, "VARCHAR(40)"),
    ("user_confirmed_skills", "evidence_status"): (60, "VARCHAR(60)"),
    ("user_confirmed_skills", "workflow_run_id"): (160, "VARCHAR(160)"),
    ("jobs", "job_url"): (None, "TEXT"),
}

# Backward-compatible alias for any external callers of the old flat dict.
COLUMN_DDL = TABLE_COLUMN_DDL["match_scores"]


def _column_needs_widen(column_info: dict, min_length: int | None) -> bool:
    """Return True when an existing column is narrower than the target type."""
    data_type = (column_info.get("type") or "")
    type_name = str(data_type).upper()
    if min_length is None:
        # Target is TEXT — widen any non-text varchar/character type.
        return "CHAR" in type_name or "VARCHAR" in type_name
    length = getattr(data_type, "length", None)
    if length is None:
        return False
    return int(length) < int(min_length)


def _widen_columns(connection, inspector, existing_tables: set[str]) -> None:
    dialect = engine.dialect.name
    for (table_name, column_name), (min_length, postgres_type) in COLUMN_WIDEN_SPECS.items():
        if table_name not in existing_tables:
            continue
        columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        column_info = columns.get(column_name)
        if not column_info:
            continue
        if not _column_needs_widen(column_info, min_length):
            continue
        if dialect == "postgresql":
            connection.execute(
                text(
                    f"ALTER TABLE {table_name} ALTER COLUMN {column_name} TYPE {postgres_type} "
                    f"USING {column_name}::{postgres_type}"
                )
            )
            logger.info("Widened %s.%s to %s", table_name, column_name, postgres_type)
        elif dialect == "sqlite":
            # SQLite create_all path uses model metadata for new DBs; existing test DBs
            # typically recreate tables. No-op widen keeps upgrades idempotent.
            logger.info("SQLite widen skipped for %s.%s (recreate uses model types)", table_name, column_name)


def ensure_ats_schema() -> None:
    """Ensure ATS-related tables/columns exist: match_scores columns,
    ats_optimization_workflows (+ diagnostics columns), job_requirement_analyses,
    user_confirmed_skills.

    Important: never call inspect(engine) while a connection transaction is open.
    That opens a second connection and deadlocks against the open DDL/DML lock,
    which leaves FastAPI stuck on "Waiting for application startup" and clients
    see ERR_EMPTY_RESPONSE / Failed to fetch.
    """
    try:
        # Import models so metadata includes the new tables
        from app.models.ats_optimization_workflow_model import AtsOptimizationWorkflow  # noqa: F401
        from app.models.optimized_resume_model import OptimizedResume  # noqa: F401
        from app.models.skill_confirmation_model import (  # noqa: F401
            JobRequirementAnalysis,
            UserConfirmedSkill,
        )

        Base.metadata.create_all(
            bind=engine,
            tables=[
                AtsOptimizationWorkflow.__table__,
                JobRequirementAnalysis.__table__,
                UserConfirmedSkill.__table__,
                OptimizedResume.__table__,
            ],
        )

        # Phase 1: additive columns + safe backfills (single transaction).
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
            if "match_scores" in existing_tables:
                connection.execute(
                    text(
                        "UPDATE match_scores SET resume_version = 'original' "
                        "WHERE resume_version IS NULL OR resume_version = ''"
                    )
                )
            if "user_confirmed_skills" in existing_tables:
                # Re-inspect via the SAME connection after ADD COLUMN.
                inspector = inspect(connection)
                existing_columns = {c["name"] for c in inspector.get_columns("user_confirmed_skills")}
                if "verification_status" in existing_columns:
                    connection.execute(
                        text("ALTER TABLE user_confirmed_skills ALTER COLUMN verification_status DROP NOT NULL")
                    )
                    connection.execute(
                        text(
                            "UPDATE user_confirmed_skills SET evidence_status = verification_status "
                            "WHERE evidence_status IS NULL AND verification_status IS NOT NULL"
                        )
                    )
                if "evidence_source" not in existing_columns:
                    connection.execute(
                        text(
                            "UPDATE user_confirmed_skills SET evidence_source = 'legacy_unknown' "
                            "WHERE evidence_status NOT IN ('INCOMPLETE', 'REJECTED')"
                        )
                    )
                inspector = inspect(connection)
                existing_columns = {c["name"] for c in inspector.get_columns("user_confirmed_skills")}
                if "is_active" in existing_columns:
                    connection.execute(
                        text(
                            "UPDATE user_confirmed_skills SET is_active = TRUE "
                            "WHERE is_active IS NULL"
                        )
                    )
                    connection.execute(
                        text(
                            "UPDATE user_confirmed_skills SET is_active = FALSE "
                            "WHERE evidence_status = 'OBSOLETE' AND (is_active IS NULL OR is_active = TRUE)"
                        )
                    )

        # Phase 2: widen columns after phase-1 commit so introspection cannot deadlock.
        with engine.begin() as connection:
            inspector = inspect(connection)
            existing_tables = set(inspector.get_table_names())
            _widen_columns(connection, inspector, existing_tables)

        logger.info(
            "ATS schema verified (match_scores + ats_optimization_workflows + "
            "job_requirement_analyses + user_confirmed_skills)"
        )
    except Exception as error:
        logger.exception("ATS schema ensure failed: %s", type(error).__name__)
        raise
