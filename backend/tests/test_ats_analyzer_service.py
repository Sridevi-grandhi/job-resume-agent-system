"""Tests for app/services/ats_analyzer_service.py (analyze_and_optimize_resume).

Uses the deterministic resume generator (no LLM involved on this code path),
so these tests run against a real SQLite test database with no mocking needed
for the scoring/generation pipeline itself.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.match_model import MatchScore
from app.models.optimized_resume_model import OptimizedResume
from app.models.resume_model import Resume
from app.services.ats_analyzer_service import analyze_and_optimize_resume


def test_exact_role_alignment_java_resume_java_job(db_session, test_user, test_resume, java_job):
    result = analyze_and_optimize_resume(test_user.id, java_job.id, db_session)

    assert result["role_alignment"] == "EXACT"
    assert result["user_id"] == test_user.id
    assert result["job_id"] == java_job.id
    assert result["resume_id"] == test_resume.id
    assert 0 <= result["original_analysis"]["match_percentage"] <= 100
    assert 0 <= result["optimized_analysis"]["match_percentage"] <= 100


def test_python_resume_vs_java_job_is_mismatch_or_blocked_by_validation(
    db_session, test_user, java_job, python_resume_text
):
    """A Python-only resume against a Java job either scores as a role mismatch,
    or — if the deterministic generator would need to introduce unsupported
    technology to close the gap — final validation truthfully blocks the save
    with a 422 rather than fabricating Java experience."""
    resume = Resume(
        user_id=test_user.id,
        original_file_name="python_resume.txt",
        stored_file_path="/tmp/python_resume.txt",
        file_type="txt",
        parsed_text=python_resume_text,
    )
    db_session.add(resume)
    db_session.commit()
    db_session.refresh(resume)

    try:
        result = analyze_and_optimize_resume(test_user.id, java_job.id, db_session, resume_id=resume.id)
    except HTTPException as error:
        assert error.status_code == 422
        assert "final_validation" in error.detail
        assert error.detail["final_validation"]["status"] == "failed"
        return

    assert result["role_alignment"] in {"MISMATCH", "PARTIAL_TRANSFERABLE", "UNKNOWN"}
    assert result["original_analysis"]["match_percentage"] < 60


def test_java_resume_vs_sdet_job_role_family_differs(db_session, test_user, test_resume, sdet_job):
    result = analyze_and_optimize_resume(test_user.id, sdet_job.id, db_session)
    assert result["target_job_role"] == "SDET / QA Automation"
    assert result["role_alignment"] != "EXACT"


def test_missing_user_raises_404(db_session, java_job):
    with pytest.raises(HTTPException) as excinfo:
        analyze_and_optimize_resume(999999, java_job.id, db_session)
    assert excinfo.value.status_code == 404
    assert "User not found" in excinfo.value.detail


def test_missing_resume_raises_404(db_session, test_user, java_job):
    with pytest.raises(HTTPException) as excinfo:
        analyze_and_optimize_resume(test_user.id, java_job.id, db_session)
    assert excinfo.value.status_code == 404
    assert "No resume found" in excinfo.value.detail


def test_resume_id_belonging_to_another_user_is_rejected(db_session, test_user, second_user, java_job):
    other_resume = Resume(
        user_id=second_user.id,
        original_file_name="other.txt",
        stored_file_path="/tmp/other.txt",
        file_type="txt",
        parsed_text="Some resume text",
    )
    db_session.add(other_resume)
    db_session.commit()
    db_session.refresh(other_resume)

    with pytest.raises(HTTPException) as excinfo:
        analyze_and_optimize_resume(test_user.id, java_job.id, db_session, resume_id=other_resume.id)
    assert excinfo.value.status_code == 404
    assert "No resume found" in excinfo.value.detail


def test_missing_job_raises_404(db_session, test_user, test_resume):
    with pytest.raises(HTTPException) as excinfo:
        analyze_and_optimize_resume(test_user.id, 999999, db_session)
    assert excinfo.value.status_code == 404
    assert "Job not found" in excinfo.value.detail


def test_job_belonging_to_another_user_is_rejected(db_session, test_user, second_user, test_resume, java_job):
    with pytest.raises(HTTPException) as excinfo:
        analyze_and_optimize_resume(second_user.id, java_job.id, db_session)
    assert excinfo.value.status_code in {404}


def test_generic_search_link_job_is_rejected(db_session, test_user, test_resume, generic_search_job):
    with pytest.raises(HTTPException) as excinfo:
        analyze_and_optimize_resume(test_user.id, generic_search_job.id, db_session)
    assert excinfo.value.status_code == 400


def test_empty_resume_text_raises_400(db_session, test_user, java_job):
    resume = Resume(
        user_id=test_user.id,
        original_file_name="blank.txt",
        stored_file_path="/tmp/blank.txt",
        file_type="txt",
        parsed_text="   ",
    )
    db_session.add(resume)
    db_session.commit()
    db_session.refresh(resume)

    with pytest.raises(HTTPException) as excinfo:
        analyze_and_optimize_resume(test_user.id, java_job.id, db_session, resume_id=resume.id)
    assert excinfo.value.status_code == 400
    assert "empty" in excinfo.value.detail.lower()


def test_missing_mandatory_skills_reported(db_session, test_user, java_job, python_resume_text):
    from app.services.ats_scoring import calculate_weighted_ats_score

    resume = Resume(
        user_id=test_user.id,
        original_file_name="python_resume.txt",
        stored_file_path="/tmp/python_resume.txt",
        file_type="txt",
        parsed_text=python_resume_text,
    )
    db_session.add(resume)
    db_session.commit()
    db_session.refresh(resume)

    # This asserts the underlying scoring contract directly (missing mandatory
    # skills are reported), independent of whether the orchestration layer's
    # deterministic generator/validator later blocks the save with a 422.
    score = calculate_weighted_ats_score(python_resume_text, java_job)
    assert "Java" in score["missing_mandatory_skills"]


def test_scores_remain_within_0_and_100(db_session, test_user, test_resume, java_job):
    result = analyze_and_optimize_resume(test_user.id, java_job.id, db_session)
    assert 0 <= result["original_analysis"]["match_percentage"] <= 100
    assert 0 <= result["optimized_analysis"]["match_percentage"] <= 100


def test_deterministic_repeated_execution_produces_same_score(db_session, test_user, test_resume, java_job):
    first = analyze_and_optimize_resume(test_user.id, java_job.id, db_session)
    second = analyze_and_optimize_resume(test_user.id, java_job.id, db_session)
    assert first["original_analysis"]["match_percentage"] == second["original_analysis"]["match_percentage"]


def test_persists_optimized_resume_and_both_match_scores(db_session, test_user, test_resume, java_job):
    analyze_and_optimize_resume(test_user.id, java_job.id, db_session)

    optimized_rows = (
        db_session.query(OptimizedResume)
        .filter(OptimizedResume.user_id == test_user.id, OptimizedResume.job_id == java_job.id)
        .all()
    )
    assert len(optimized_rows) == 1

    match_rows = (
        db_session.query(MatchScore)
        .filter(MatchScore.user_id == test_user.id, MatchScore.job_id == java_job.id)
        .all()
    )
    versions = {row.resume_version for row in match_rows}
    assert versions == {"original", "optimized"}


def test_repeated_calls_upsert_rather_than_duplicate(db_session, test_user, test_resume, java_job):
    analyze_and_optimize_resume(test_user.id, java_job.id, db_session)
    analyze_and_optimize_resume(test_user.id, java_job.id, db_session)

    optimized_rows = (
        db_session.query(OptimizedResume)
        .filter(OptimizedResume.user_id == test_user.id, OptimizedResume.job_id == java_job.id)
        .all()
    )
    assert len(optimized_rows) == 1

    match_rows = (
        db_session.query(MatchScore)
        .filter(MatchScore.user_id == test_user.id, MatchScore.job_id == java_job.id)
        .all()
    )
    assert len(match_rows) == 2


def test_all_expected_output_fields_are_present(db_session, test_user, test_resume, java_job):
    result = analyze_and_optimize_resume(test_user.id, java_job.id, db_session)
    expected_keys = {
        "message", "user_id", "job_id", "resume_id", "optimized_resume_id",
        "job", "original_analysis", "optimized_analysis", "role_alignment",
        "resume_role_families", "target_job_role", "optimization_allowed",
        "safe_to_apply", "score_status", "critical_missing_skills",
        "matched_core_skills", "matched_transferable_skills", "warning",
        "gap_analysis", "score_improvement", "technology_validation",
        "client_validation", "timeline_validation", "keyword_analysis",
        "final_validation", "generated_resume", "generated_resume_preview_markdown",
        "original_resume",
    }
    assert expected_keys.issubset(result.keys())


def test_uses_most_recent_resume_when_resume_id_not_specified(db_session, test_user, test_resume, java_job):
    from datetime import datetime, timedelta, timezone

    # uploaded_at defaults to func.now() at insert time; both rows could land in
    # the same second on a fast test run, so set explicit, unambiguous timestamps.
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

    result = analyze_and_optimize_resume(test_user.id, java_job.id, db_session)
    assert result["resume_id"] == newer.id
