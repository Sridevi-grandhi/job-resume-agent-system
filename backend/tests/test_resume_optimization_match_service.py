"""Tests for app/services/resume_optimization_match_service.py."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.models.resume_model import Resume
from app.services.resume_optimization_match_service import (
    optimize_and_match,
    optimize_and_match_safe,
)


def test_optimize_and_match_returns_combined_contract(db_session, test_user, test_resume, java_job):
    result = optimize_and_match(test_user.id, java_job.id, db_session)

    assert result["job_id"] == java_job.id
    assert result["original_resume_id"] == test_resume.id
    assert "original_match_percentage" in result
    assert "optimized_match_percentage" in result
    assert "improvement_percentage" in result
    assert result["improvement_percentage"] == (
        result["optimized_match_percentage"] - result["original_match_percentage"]
    )


def test_original_and_optimized_scores_are_independent_ints(db_session, test_user, test_resume, java_job):
    result = optimize_and_match(test_user.id, java_job.id, db_session)
    assert isinstance(result["original_match_percentage"], int)
    assert isinstance(result["optimized_match_percentage"], int)


def test_optimize_and_match_propagates_missing_job(db_session, test_user, test_resume):
    with pytest.raises(HTTPException) as excinfo:
        optimize_and_match(test_user.id, 999999, db_session)
    assert excinfo.value.status_code == 404


def test_optimize_and_match_propagates_missing_resume(db_session, test_user, java_job):
    with pytest.raises(HTTPException) as excinfo:
        optimize_and_match(test_user.id, java_job.id, db_session)
    assert excinfo.value.status_code == 404


def test_warnings_flag_when_optimized_score_does_not_improve(db_session, test_user, test_resume, java_job):
    result = optimize_and_match(test_user.id, java_job.id, db_session)
    if result["optimized_match_percentage"] < result["original_match_percentage"]:
        assert any("did not improve" in w for w in result["validation_warnings"])


def test_optimize_and_match_safe_happy_path(db_session, test_user, test_resume, java_job):
    result = optimize_and_match_safe(test_user.id, java_job.id, db_session)

    assert result["job_saved"] is True
    assert result["optimization_completed"] is True
    assert result["reason"] is None
    assert result["analysis"]["optimized_resume_id"] is not None
    assert "full_analysis" in result


def test_optimize_and_match_safe_missing_resume_soft_fails(db_session, test_user, java_job):
    result = optimize_and_match_safe(test_user.id, java_job.id, db_session)

    assert result["job_saved"] is True
    assert result["optimization_completed"] is False
    assert "Upload a resume" in result["message"]
    assert result["analysis"] is None


def test_optimize_and_match_safe_resume_with_blank_text_soft_fails(db_session, test_user, java_job):
    resume = Resume(
        user_id=test_user.id,
        original_file_name="blank.txt",
        stored_file_path="/tmp/blank.txt",
        file_type="txt",
        parsed_text="   ",
    )
    db_session.add(resume)
    db_session.commit()

    result = optimize_and_match_safe(test_user.id, java_job.id, db_session)
    assert result["optimization_completed"] is False
    assert "Upload a resume" in result["message"]


def test_optimize_and_match_safe_missing_job_soft_fails(db_session, test_user, test_resume):
    result = optimize_and_match_safe(test_user.id, 999999, db_session)

    assert result["job_saved"] is True
    assert result["optimization_completed"] is False
    assert result["reason"] == "Job not found after save."


def test_optimize_and_match_safe_generic_search_job_soft_fails(db_session, test_user, test_resume, generic_search_job):
    result = optimize_and_match_safe(test_user.id, generic_search_job.id, db_session)

    assert result["job_saved"] is True
    assert result["optimization_completed"] is False
    assert result["reason"] is not None


def test_optimize_and_match_safe_wraps_http_exception_as_soft_failure(
    db_session, test_user, test_resume, java_job
):
    with patch(
        "app.services.resume_optimization_match_service.optimize_and_match",
        side_effect=HTTPException(status_code=422, detail={"message": "validation failed"}),
    ):
        result = optimize_and_match_safe(test_user.id, java_job.id, db_session)

    assert result["job_saved"] is True
    assert result["optimization_completed"] is False
    assert "validation failed" in result["message"]


def test_optimize_and_match_safe_wraps_unexpected_exception_as_soft_failure(
    db_session, test_user, test_resume, java_job
):
    with patch(
        "app.services.resume_optimization_match_service.optimize_and_match",
        side_effect=RuntimeError("boom"),
    ):
        result = optimize_and_match_safe(test_user.id, java_job.id, db_session)

    assert result["job_saved"] is True
    assert result["optimization_completed"] is False
    assert "unexpected server error" in result["message"].lower()


def test_optimize_and_match_safe_uses_most_recent_resume(db_session, test_user, test_resume, java_job):
    from datetime import datetime, timedelta, timezone

    test_resume.uploaded_at = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.add(test_resume)
    db_session.commit()

    newer = Resume(
        user_id=test_user.id,
        original_file_name="newer.txt",
        stored_file_path="/tmp/newer.txt",
        file_type="txt",
        parsed_text=test_resume.parsed_text,
        uploaded_at=datetime.now(timezone.utc),
    )
    db_session.add(newer)
    db_session.commit()
    db_session.refresh(newer)

    result = optimize_and_match_safe(test_user.id, java_job.id, db_session)
    assert result["full_analysis"]["original_resume_id"] == newer.id


def test_service_class_aliases_match_module_functions(db_session, test_user, test_resume, java_job):
    from app.services.resume_optimization_match_service import ResumeOptimizationMatchService

    result = ResumeOptimizationMatchService.optimize_and_match(test_user.id, java_job.id, db_session)
    assert result["job_id"] == java_job.id
