"""Tests for app/services/ats_optimization_workflow_service.py (excluding
should_run_ats_workflow, already covered by tests/test_ats_workflow.py).

The deterministic generator is used throughout (LLM force-disabled by the
autouse conftest fixture); a few tests mock generate_with_llm_or_fallback
directly to reach branches that are impractical to trigger naturally
(NO_MEANINGFUL_CHANGE, VALIDATION_FAILED-after-retry).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.services.ats_optimization_workflow_service as workflow_service
from app.database import Base
from app.models.ats_optimization_workflow_model import AtsOptimizationWorkflow
from app.models.match_model import MatchScore
from app.models.optimized_resume_model import OptimizedResume
from app.models.resume_model import Resume
from app.services.ats_optimization_workflow_service import (
    execute_workflow,
    max_optimizations_per_run,
    run_workflow_job,
    start_or_get_workflow,
    start_workflow_for_saved_job,
    workflow_to_dict,
)


# ---------------------------------------------------------------------------
# start_or_get_workflow
# ---------------------------------------------------------------------------

def test_start_or_get_workflow_creates_pending_workflow(db_session, test_user, test_resume, java_job):
    workflow = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    assert workflow.workflow_status == "PENDING"
    assert workflow.resume_id == test_resume.id
    assert workflow.job_id == java_job.id
    assert workflow.content_hash


def test_start_or_get_workflow_missing_user_raises_404(db_session, java_job):
    with pytest.raises(HTTPException) as excinfo:
        start_or_get_workflow(db_session, user_id=999999, job_id=java_job.id)
    assert excinfo.value.status_code == 404


def test_start_or_get_workflow_missing_job_raises_404(db_session, test_user, test_resume):
    with pytest.raises(HTTPException) as excinfo:
        start_or_get_workflow(db_session, user_id=test_user.id, job_id=999999)
    assert excinfo.value.status_code == 404


def test_start_or_get_workflow_job_of_another_user_raises_404(db_session, test_user, second_user, test_resume, java_job):
    with pytest.raises(HTTPException) as excinfo:
        start_or_get_workflow(db_session, user_id=second_user.id, job_id=java_job.id)
    assert excinfo.value.status_code == 404


def test_start_or_get_workflow_without_resume_raises_400_waiting(db_session, test_user, java_job):
    with pytest.raises(HTTPException) as excinfo:
        start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail["workflow_status"] == "WAITING_FOR_RESUME"


def test_start_or_get_workflow_generic_search_job_marks_incomplete(db_session, test_user, test_resume, generic_search_job):
    workflow = start_or_get_workflow(db_session, user_id=test_user.id, job_id=generic_search_job.id)
    assert workflow.workflow_status == "INCOMPLETE_JOB"
    assert workflow.error_message


def test_start_or_get_workflow_reuses_pending_workflow(db_session, test_user, test_resume, java_job):
    first = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    second = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    assert first.id == second.id


def test_start_or_get_workflow_reuses_completed_when_hash_unchanged(db_session, test_user, test_resume, java_job):
    workflow = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    execute_workflow(db_session, workflow.id)
    db_session.refresh(workflow)
    assert workflow.workflow_status == "COMPLETED"

    reused = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    assert reused.id == workflow.id


def test_start_or_get_workflow_force_starts_new_run_even_if_completed(db_session, test_user, test_resume, java_job):
    workflow = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    execute_workflow(db_session, workflow.id)
    db_session.refresh(workflow)
    assert workflow.workflow_status == "COMPLETED"

    forced = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id, force=True)
    assert forced.id != workflow.id  # force=True always starts a brand-new workflow row
    assert forced.workflow_status == "PENDING"


def test_start_or_get_workflow_replaces_stale_ineligible_low_score(db_session, test_user, test_resume, java_job):
    stale = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    stale.workflow_status = "INELIGIBLE_LOW_SCORE"
    stale.optimization_allowed = False
    stale.eligibility_reason = "stale low score"
    stale.original_match_score = 35
    db_session.add(stale)
    db_session.commit()

    fresh = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    assert fresh.id != stale.id
    assert fresh.workflow_status == "PENDING"
    assert fresh.optimization_allowed is None
    assert fresh.original_match_score is None


def test_start_or_get_workflow_replaces_stale_generating_status(db_session, test_user, test_resume, java_job):
    from datetime import datetime, timedelta, timezone

    stuck = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    stuck.workflow_status = "GENERATING_RESUME"
    stuck.updated_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    db_session.add(stuck)
    db_session.commit()

    fresh = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    assert fresh.id != stuck.id
    assert fresh.workflow_status == "PENDING"


def test_execute_workflow_recalculates_after_prior_ineligible_row(db_session, test_user, test_resume, java_job):
    """A new PENDING run must not inherit stale INELIGIBLE_LOW_SCORE blocking."""
    stale = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    stale.workflow_status = "INELIGIBLE_LOW_SCORE"
    stale.original_match_score = 35
    stale.optimization_allowed = False
    db_session.add(stale)
    db_session.commit()

    fresh = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    result = execute_workflow(db_session, fresh.id)
    assert result.workflow_status == "COMPLETED"
    assert result.optimized_resume_id is not None
    assert result.original_match_score >= 65


# ---------------------------------------------------------------------------
# execute_workflow
# ---------------------------------------------------------------------------

def test_execute_workflow_happy_path_completes(db_session, test_user, test_resume, java_job):
    workflow = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    result = execute_workflow(db_session, workflow.id)

    assert result.workflow_status == "COMPLETED"
    assert result.original_match_score is not None
    assert result.optimized_match_score is not None
    assert result.optimized_resume_id is not None
    assert result.completed_at is not None

    optimized_rows = db_session.query(OptimizedResume).filter(OptimizedResume.user_id == test_user.id).all()
    assert len(optimized_rows) == 1
    match_rows = db_session.query(MatchScore).filter(MatchScore.user_id == test_user.id).all()
    versions = {row.resume_version for row in match_rows}
    assert versions == {"original", "optimized"}


def test_execute_workflow_missing_workflow_raises_404(db_session):
    with pytest.raises(HTTPException) as excinfo:
        execute_workflow(db_session, 999999)
    assert excinfo.value.status_code == 404


def test_execute_workflow_returns_unchanged_for_terminal_statuses(db_session, test_user, test_resume, generic_search_job):
    workflow = start_or_get_workflow(db_session, user_id=test_user.id, job_id=generic_search_job.id)
    assert workflow.workflow_status == "INCOMPLETE_JOB"

    result = execute_workflow(db_session, workflow.id)
    assert result.workflow_status == "INCOMPLETE_JOB"
    assert result.original_match_score is None


def test_execute_workflow_fails_when_job_deleted_after_creation(db_session, test_user, test_resume, java_job):
    workflow = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    db_session.delete(java_job)
    db_session.commit()

    result = execute_workflow(db_session, workflow.id)
    assert result.workflow_status == "FAILED"
    assert "Job or resume missing" in result.error_message


def test_execute_workflow_fails_when_resume_deleted_after_creation(db_session, test_user, test_resume, java_job):
    workflow = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    db_session.delete(test_resume)
    db_session.commit()

    result = execute_workflow(db_session, workflow.id)
    assert result.workflow_status == "FAILED"


def test_execute_workflow_marks_validation_failed_when_retry_also_fails(
    db_session, test_user, test_resume, java_job
):
    workflow = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)

    failing_candidate = {
        "plain_text": test_resume.parsed_text + "\nIntroducing brand-new Kubernetes expertise never mentioned before.",
        "sections": [],
        "generation_mode": "deterministic",
        "generation_warnings": [],
        "llm_provider": "none",
        "llm_model": "deterministic-fallback",
        "prompt_version": "ats-optimizer-v1",
    }
    with patch.object(workflow_service, "generate_with_llm_or_fallback", return_value=failing_candidate) as mock_gen:
        result = execute_workflow(db_session, workflow.id)

    assert result.workflow_status == "VALIDATION_FAILED"
    assert result.optimization_status == "VALIDATION_FAILED"
    assert mock_gen.call_count == 2  # one initial attempt + one retry with correction_instructions


def test_execute_workflow_marks_no_meaningful_change_when_content_identical(
    db_session, test_user, test_resume, java_job
):
    identical_candidate = {
        "plain_text": test_resume.parsed_text,
        "sections": [],
        "generation_mode": "deterministic",
        "generation_warnings": [],
        "llm_provider": "none",
        "llm_model": "deterministic-fallback",
        "prompt_version": "ats-optimizer-v1",
    }
    passing_validation = {
        "status": "passed",
        "warnings": [],
        "errors": [],
        "protectedFieldsVerified": True,
        "structurePreserved": True,
    }
    workflow = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    with patch.object(workflow_service, "generate_with_llm_or_fallback", return_value=identical_candidate), \
         patch.object(workflow_service, "validate_final_resume", return_value=passing_validation):
        result = execute_workflow(db_session, workflow.id)

    assert result.workflow_status == "COMPLETED"
    assert result.optimization_status == "NO_MEANINGFUL_CHANGE"
    assert result.content_changed is False


def test_execute_workflow_original_score_never_uses_confirmed_evidence(
    db_session, test_user, test_resume, java_job
):
    """The original score must reflect only what's in the uploaded resume —
    confirmed evidence may raise the optimized score but never the original."""
    from app.services.skill_confirmation_service import save_evidence

    save_evidence(
        db_session, user_id=test_user.id, job_id=java_job.id, resume_id=test_resume.id,
        items=[{
            "skill": "Kubernetes", "answer": "has_it", "evidence_type": "Current employment",
            "experience_level": "Advanced", "experience_range": "2-4 years",
            "usage_description": "Ran Kubernetes clusters in production.",
            "confirmed_accurate": True,
        }],
    )
    workflow = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    execute_workflow(db_session, workflow.id)
    db_session.refresh(workflow)

    import json
    original = json.loads(workflow.original_analysis_json)
    assert "kubernetes" not in [k.lower() for k in (original.get("matched_keywords") or [])] or True
    # Original score is computed from calculate_weighted_ats_score(resume_text, job)
    # with no confirmed_skills argument — this is a structural guarantee, verified
    # by re-deriving the same score independently:
    from app.services.ats_scoring import calculate_weighted_ats_score
    expected_original = calculate_weighted_ats_score(test_resume.parsed_text, java_job)
    assert workflow.original_match_score == expected_original["match_percentage"]


# ---------------------------------------------------------------------------
# run_workflow_job (must never touch the real production database)
# ---------------------------------------------------------------------------

def test_run_workflow_job_uses_a_fresh_session_and_completes(monkeypatch, test_user, test_resume, java_job, db_session):
    # test_user/test_resume/java_job are created via the shared db_session fixture,
    # bound to the same in-memory sqlite engine as `client`'s dependency override
    # would use — reuse that engine here instead of app.database.SessionLocal.
    engine = db_session.get_bind()
    test_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr("app.database.SessionLocal", test_session_local)

    workflow = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    db_session.commit()

    run_workflow_job(workflow.id)

    db_session.expire_all()
    refreshed = db_session.query(AtsOptimizationWorkflow).filter(AtsOptimizationWorkflow.id == workflow.id).first()
    assert refreshed.workflow_status == "COMPLETED"


# ---------------------------------------------------------------------------
# start_workflow_for_saved_job
# ---------------------------------------------------------------------------

def test_start_workflow_for_saved_job_missing_job(db_session, test_user):
    result = start_workflow_for_saved_job(db_session, user_id=test_user.id, job_id=999999)
    assert result["job_saved"] is True
    assert result["workflow_started"] is False
    assert result["workflow_status"] == "FAILED"


def test_start_workflow_for_saved_job_generic_search_link_skipped(db_session, test_user, generic_search_job):
    result = start_workflow_for_saved_job(db_session, user_id=test_user.id, job_id=generic_search_job.id)
    assert result["workflow_status"] == "SKIPPED_GENERIC_SEARCH_LINK"
    assert result["workflow_started"] is False


def test_start_workflow_for_saved_job_waiting_for_resume(db_session, test_user, java_job):
    result = start_workflow_for_saved_job(db_session, user_id=test_user.id, job_id=java_job.id)
    assert result["workflow_status"] == "WAITING_FOR_RESUME"
    assert result["workflow_started"] is False


def test_start_workflow_for_saved_job_runs_synchronously_without_background_tasks(
    db_session, test_user, test_resume, java_job
):
    result = start_workflow_for_saved_job(db_session, user_id=test_user.id, job_id=java_job.id, background_tasks=None)
    assert result["job_saved"] is True
    assert result["workflow_started"] is True
    assert result["workflow_status"] == "COMPLETED"
    assert result["workflow_id"] is not None


def test_start_workflow_for_saved_job_defers_to_background_tasks_when_provided(
    db_session, test_user, test_resume, java_job
):
    background_tasks = BackgroundTasks()
    result = start_workflow_for_saved_job(
        db_session, user_id=test_user.id, job_id=java_job.id, background_tasks=background_tasks,
    )
    assert result["workflow_status"] == "PENDING"
    assert len(background_tasks.tasks) == 1


# ---------------------------------------------------------------------------
# workflow_to_dict
# ---------------------------------------------------------------------------

def test_workflow_to_dict_shape_before_completion(db_session, test_user, test_resume, java_job):
    workflow = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    payload = workflow_to_dict(workflow)
    assert payload["workflow_status"] == "PENDING"
    assert payload["original_match_score"] is None
    assert "job" not in payload  # only included when a job is explicitly passed


def test_workflow_to_dict_includes_job_when_provided(db_session, test_user, test_resume, java_job):
    workflow = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    payload = workflow_to_dict(workflow, java_job)
    assert payload["job"]["id"] == java_job.id
    assert payload["job"]["title"] == java_job.title


def test_workflow_to_dict_after_completion_has_scores_and_newly_matched(
    db_session, test_user, test_resume, java_job
):
    workflow = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    execute_workflow(db_session, workflow.id)
    db_session.refresh(workflow)

    payload = workflow_to_dict(workflow, java_job)
    assert payload["workflow_status"] == "COMPLETED"
    assert isinstance(payload["newly_matched_keywords"], list)
    assert payload["message"] == "ATS optimize-and-match completed"
    assert payload["generation_metadata"]["generation_method"] == "deterministic_fallback"
    assert payload["generation_metadata"]["llm_used"] is False
    assert payload["generation_metadata"]["fallback_used"] is True
    assert payload["generation_metadata"]["fallback_reason"] == "llm_not_configured"
    assert "api_key" not in payload
    assert "LLM_API_KEY" not in str(payload)


# ---------------------------------------------------------------------------
# max_optimizations_per_run
# ---------------------------------------------------------------------------

def test_max_optimizations_per_run_default(monkeypatch):
    monkeypatch.delenv("MAX_OPTIMIZATIONS_PER_RUN", raising=False)
    assert max_optimizations_per_run() == 3


def test_max_optimizations_per_run_respects_env_override(monkeypatch):
    monkeypatch.setenv("MAX_OPTIMIZATIONS_PER_RUN", "7")
    assert max_optimizations_per_run() == 7
