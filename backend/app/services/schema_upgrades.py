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
        "optimization_status": "ALTER TABLE ats_optimization_workflows ADD COLUMN optimization_status VARCHAR(30)",
        "improvement_report_json": "ALTER TABLE ats_optimization_workflows ADD COLUMN improvement_report_json TEXT",
    },
    "user_confirmed_skills": {
        "requirement_type": "ALTER TABLE user_confirmed_skills ADD COLUMN requirement_type VARCHAR(20)",
        "evidence_status": "ALTER TABLE user_confirmed_skills ADD COLUMN evidence_status VARCHAR(40)",
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
    },
}

# Backward-compatible alias for any external callers of the old flat dict.
COLUMN_DDL = TABLE_COLUMN_DDL["match_scores"]


def ensure_ats_schema() -> None:
    """Ensure ATS-related tables/columns exist: match_scores columns,
    ats_optimization_workflows (+ diagnostics columns), job_requirement_analyses,
    user_confirmed_skills."""
    try:
        # Import models so metadata includes the new tables
        from app.models.ats_optimization_workflow_model import AtsOptimizationWorkflow  # noqa: F401
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
            ],
        )

        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
        with engine.begin() as connection:
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
                # Older rows (pre-restructure) used verification_status as a NOT NULL
                # column; new code never sets it, so it must be relaxed to nullable or
                # every new insert would violate that constraint. Existing rows are
                # preserved and their status is carried forward into evidence_status.
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
        logger.info(
            "ATS schema verified (match_scores + ats_optimization_workflows + "
            "job_requirement_analyses + user_confirmed_skills)"
        )
    except Exception as error:
        logger.error("ATS schema ensure failed: %s", type(error).__name__)
