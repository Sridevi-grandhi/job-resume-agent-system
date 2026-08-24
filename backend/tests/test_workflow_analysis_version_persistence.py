"""Regression: long ANALYSIS_VERSION must persist without DataError."""

from __future__ import annotations

from app.services.ats_optimization_workflow_service import start_or_get_workflow, execute_workflow
from app.services.ats_scoring import ANALYSIS_VERSION
from app.models.ats_optimization_workflow_model import AtsOptimizationWorkflow


def test_analysis_version_longer_than_legacy_varchar40():
    assert len(ANALYSIS_VERSION) > 40


def test_start_workflow_persists_long_analysis_version(db_session, test_user, test_resume, java_job):
    workflow = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id, force=True)
    db_session.refresh(workflow)
    assert workflow.analysis_version == ANALYSIS_VERSION
    assert len(workflow.analysis_version) > 40


def test_execute_workflow_persists_generation_metadata_values(
    db_session, test_user, test_resume, java_job
):
    workflow = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id, force=True)
    result = execute_workflow(db_session, workflow.id)
    assert result.workflow_status == "COMPLETED"
    assert result.optimized_resume_id is not None
    # Deterministic path in tests (LLM disabled by conftest).
    assert result.generation_method in {None, "deterministic_fallback", "llm", "deterministic"}
    assert result.analysis_version == ANALYSIS_VERSION


def test_model_allows_long_eligibility_reason_and_error_message(db_session, test_user, test_resume, java_job):
    workflow = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id, force=True)
    long_reason = "R" * 500
    long_error = "E" * 800
    workflow.eligibility_reason = long_reason
    workflow.error_message = long_error
    workflow.llm_provider = "openai_compatible"
    workflow.llm_model = "llama3.1:8b"
    workflow.generation_method = "deterministic_fallback"
    workflow.fallback_reason = "connection_failed"
    db_session.add(workflow)
    db_session.commit()
    db_session.refresh(workflow)
    loaded = db_session.query(AtsOptimizationWorkflow).filter_by(id=workflow.id).one()
    assert loaded.eligibility_reason == long_reason
    assert loaded.error_message == long_error
    assert loaded.llm_provider == "openai_compatible"
    assert loaded.llm_model == "llama3.1:8b"
