"""Regression tests for the ATS Step 5 score-integrity / eligibility / download incident.

Incident: a low-score, role-mismatched job (original 35%, INELIGIBLE) showed
another job's completed result in Step 5 (88 / 88 / +0), and every
editor/download call used the other job's optimized_resume_id under this
job's path (404 → "Unable to prepare the resume download").

These tests pin the backend contracts that make that impossible to hide:
- the original baseline is computed once from the uploaded resume and is never
  overwritten by generation or editor rescoring;
- results, editor, and downloads are scoped to (user, job, optimized resume);
- ineligible combinations are rejected by the generation endpoint itself.

Scores come from the real canonical scorer against the shared fixtures
(java_job is an eligible EXACT match, sdet_job is a low-score MISMATCH for the
Java resume) — nothing here hardcodes a score value.
"""

from __future__ import annotations

from io import BytesIO
from unittest.mock import patch

from docx import Document

import app.routes.ats_optimizer_routes as ats_routes
from app.models.ats_optimization_workflow_model import AtsOptimizationWorkflow
from app.models.match_model import MatchScore
from app.models.optimized_resume_model import OptimizedResume
from app.services.ats_optimization_workflow_service import (
    MINIMUM_ORIGINAL_SCORE,
    execute_workflow,
    role_aware_optimization_eligibility,
    start_or_get_workflow,
)
from app.services.ats_scoring import calculate_weighted_ats_score


def _docx_source(db_session, resume, tmp_path):
    source = tmp_path / "source.docx"
    Document().save(source)
    resume.stored_file_path = str(source)
    resume.file_type = "docx"
    db_session.add(resume)
    db_session.commit()


def _complete_workflow(db_session, user, job):
    workflow = start_or_get_workflow(db_session, user_id=user.id, job_id=job.id)
    execute_workflow(db_session, workflow.id)
    db_session.refresh(workflow)
    return workflow


def _original_match_row(db_session, user, resume, job):
    return (
        db_session.query(MatchScore)
        .filter(
            MatchScore.user_id == user.id,
            MatchScore.resume_id == resume.id,
            MatchScore.job_id == job.id,
            MatchScore.resume_version == "original",
        )
        .first()
    )


# ---------------------------------------------------------------------------
# Score lifecycle (TESTS 1–6)
# ---------------------------------------------------------------------------

def test_original_baseline_is_canonical_and_separate_from_optimized(
    db_session, test_user, test_resume, java_job,
):
    canonical = calculate_weighted_ats_score(test_resume.parsed_text, java_job)["match_percentage"]
    workflow = _complete_workflow(db_session, test_user, java_job)

    assert workflow.workflow_status == "COMPLETED"
    assert workflow.optimized_resume_id
    assert workflow.original_match_score == canonical
    optimized = db_session.get(OptimizedResume, workflow.optimized_resume_id)
    assert optimized.original_score == canonical
    # Optimized score is its own field, and improvement is optimized - original.
    assert workflow.optimized_match_score is not None
    assert workflow.score_improvement == workflow.optimized_match_score - workflow.original_match_score


def test_result_reload_keeps_original_baseline(client, db_session, test_user, test_resume, java_job):
    workflow = _complete_workflow(db_session, test_user, java_job)
    original = workflow.original_match_score

    for _ in range(2):  # Step 5 page load + reload
        body = client.get(f"/api/ats-optimizer/result/{test_user.id}/{java_job.id}").json()
        assert body["workflow_id"] == workflow.id
        assert body["job_id"] == java_job.id
        assert body["original_match_percentage"] == original
        assert body["original_match_score"] == original
        assert body["optimized_match_percentage"] == workflow.optimized_match_score
        assert body["improvement_percentage"] == workflow.optimized_match_score - original


def test_editor_rescore_never_mutates_original_baseline(
    client, db_session, test_user, test_resume, java_job, tmp_path,
):
    _docx_source(db_session, test_resume, tmp_path)
    workflow = _complete_workflow(db_session, test_user, java_job)
    original = workflow.original_match_score
    optimized = db_session.get(OptimizedResume, workflow.optimized_resume_id)
    original_row_before = _original_match_row(db_session, test_user, test_resume, java_job).match_percentage

    edited = (optimized.final_resume_text or optimized.generated_resume_text) + "\n- Reviewed pull requests for the team."
    response = client.patch(
        f"/api/optimizer/resume/{test_user.id}/{java_job.id}",
        json={"optimized_resume_id": optimized.id, "edited_resume_text": edited, "action": "recalculate"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["edited_score"] is not None

    db_session.expire_all()
    workflow = db_session.get(AtsOptimizationWorkflow, workflow.id)
    optimized = db_session.get(OptimizedResume, optimized.id)
    assert workflow.original_match_score == original
    assert optimized.original_score == original
    assert optimized.edited_score == body["edited_score"]
    assert _original_match_row(db_session, test_user, test_resume, java_job).match_percentage == original_row_before

    editor = client.get(f"/api/optimizer/resume/{test_user.id}/{java_job.id}/{optimized.id}").json()
    assert editor["original_score"] == original


def test_selected_jobs_keeps_original_score_after_optimization(
    client, db_session, test_user, test_resume, java_job,
):
    workflow = _complete_workflow(db_session, test_user, java_job)
    jobs = client.get(f"/api/jobs/selected/{test_user.id}").json()
    jobs = jobs if isinstance(jobs, list) else jobs.get("jobs", [])
    card = next(job for job in jobs if job["id"] == java_job.id)
    assert card["original_match_percentage"] == workflow.original_match_score


def test_different_job_does_not_inherit_other_job_result(
    client, db_session, test_user, test_resume, java_job, sdet_job,
):
    """The exact incident: Job A completed later than Job B's ineligible run.
    Job B's result must be Job B's own workflow — never Job A's."""
    ineligible = _complete_workflow(db_session, test_user, sdet_job)
    completed = _complete_workflow(db_session, test_user, java_job)
    assert completed.id > ineligible.id

    body = client.get(f"/api/ats-optimizer/result/{test_user.id}/{sdet_job.id}").json()
    assert body["workflow_id"] == ineligible.id
    assert body["job_id"] == sdet_job.id
    assert body["workflow_status"].startswith("INELIGIBLE")
    assert body["optimized_resume_id"] is None
    assert body["original_match_percentage"] == calculate_weighted_ats_score(
        test_resume.parsed_text, sdet_job,
    )["match_percentage"]
    assert body["original_match_percentage"] != completed.original_match_score


def test_other_jobs_optimized_resume_is_not_served_under_this_job(
    client, db_session, test_user, test_resume, java_job, sdet_job, tmp_path,
):
    """Editor, save, improve, and download calls for Job B with Job A's
    optimized_resume_id return a safe 404 (what the stale UI hit)."""
    _docx_source(db_session, test_resume, tmp_path)
    completed = _complete_workflow(db_session, test_user, java_job)
    foreign_id = completed.optimized_resume_id
    base = f"/api/optimizer/resume/{test_user.id}/{sdet_job.id}"

    assert client.get(f"{base}/{foreign_id}").status_code == 404
    assert client.patch(base, json={"optimized_resume_id": foreign_id, "edited_resume_text": "x"}).status_code == 404
    assert client.post(f"{base}/improve-snippet", json={"optimized_resume_id": foreign_id, "text": "Built APIs."}).status_code == 404
    for fmt in ("docx", "pdf", "txt"):
        assert client.get(f"{base}/{foreign_id}/download?format={fmt}").status_code == 404


def test_other_user_cannot_read_results_or_download(
    client, db_session, test_user, second_user, test_resume, java_job, tmp_path,
):
    _docx_source(db_session, test_resume, tmp_path)
    completed = _complete_workflow(db_session, test_user, java_job)
    assert client.get(f"/api/ats-optimizer/result/{second_user.id}/{java_job.id}").status_code == 404
    response = client.get(
        f"/api/optimizer/resume/{second_user.id}/{java_job.id}/{completed.optimized_resume_id}/download?format=docx"
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Eligibility (TESTS 7–10)
# ---------------------------------------------------------------------------

def test_canonical_eligibility_rules():
    below = role_aware_optimization_eligibility(MINIMUM_ORIGINAL_SCORE - 1, "EXACT")
    assert below["optimization_allowed"] is False
    assert below["eligibility_status"] == "INELIGIBLE_LOW_SCORE"

    mismatch = role_aware_optimization_eligibility(MINIMUM_ORIGINAL_SCORE + 15, "MISMATCH")
    assert mismatch["optimization_allowed"] is False
    assert mismatch["eligibility_status"] == "INELIGIBLE_ROLE_MISMATCH"

    ok = role_aware_optimization_eligibility(MINIMUM_ORIGINAL_SCORE + 15, "EXACT")
    assert ok["optimization_allowed"] is True


def test_low_score_workflow_is_ineligible_and_generates_nothing(db_session, test_user, test_resume, sdet_job):
    workflow = _complete_workflow(db_session, test_user, sdet_job)
    assert workflow.original_match_score < MINIMUM_ORIGINAL_SCORE
    assert workflow.workflow_status == "INELIGIBLE_LOW_SCORE"
    assert workflow.optimized_resume_id is None
    assert workflow.generated_resume_plain_text in (None, "")
    assert db_session.query(OptimizedResume).filter(OptimizedResume.job_id == sdet_job.id).count() == 0


def test_generate_endpoint_rejects_low_score_direct_call(client, db_session, test_user, test_resume, sdet_job):
    response = client.post(f"/api/ats-optimizer/generate/{test_user.id}/{sdet_job.id}?force=true")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["can_generate_resume"] is False
    assert detail["blocking_reasons"][0]["code"] == "INELIGIBLE_LOW_SCORE"
    assert db_session.query(AtsOptimizationWorkflow).filter(AtsOptimizationWorkflow.job_id == sdet_job.id).count() == 0
    assert db_session.query(OptimizedResume).filter(OptimizedResume.job_id == sdet_job.id).count() == 0


def test_generate_endpoint_rejects_hard_role_mismatch_above_threshold(
    client, db_session, test_user, test_resume, java_job,
):
    real = ats_routes.calculate_weighted_ats_score

    def mismatched(resume_text, job, *args, **kwargs):
        # Real score (above threshold for this fixture) with a hard role mismatch.
        return {**real(resume_text, job, *args, **kwargs), "role_alignment": "MISMATCH"}

    with patch.object(ats_routes, "calculate_weighted_ats_score", side_effect=mismatched):
        response = client.post(f"/api/ats-optimizer/generate/{test_user.id}/{java_job.id}?force=true")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["original_score"] >= MINIMUM_ORIGINAL_SCORE
    assert detail["blocking_reasons"][0]["code"] == "INELIGIBLE_ROLE_ALIGNMENT"
    assert db_session.query(AtsOptimizationWorkflow).filter(AtsOptimizationWorkflow.job_id == java_job.id).count() == 0


def test_generate_endpoint_accepts_eligible_job(client, db_session, test_user, test_resume, java_job):
    response = client.post(f"/api/ats-optimizer/generate/{test_user.id}/{java_job.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["eligibility_status"] == "ELIGIBLE"
    assert body["workflow_id"]


def test_eligibility_endpoint_reports_ineligible_for_low_score(client, test_user, test_resume, sdet_job):
    body = client.get(f"/api/ats-optimizer/eligibility/{test_user.id}/{sdet_job.id}").json()
    assert body["optimization_allowed"] is False
    assert body["job_id"] == sdet_job.id
    assert body["eligibility_reason"]


# ---------------------------------------------------------------------------
# AI bullet Try Again truthfulness (TESTS 14–15)
# ---------------------------------------------------------------------------

def test_try_again_returns_new_suggestion_via_mocked_rewrite(client, db_session, test_user, test_resume, java_job):
    completed = _complete_workflow(db_session, test_user, java_job)
    with patch(
        "app.routes.optimizer_edit_routes.rewrite_bullet_with_ollama",
        return_value=("Designed and shipped Spring Boot services in Java that power customer account workflows.", "ollama", False),
    ):
        response = client.post(
            f"/api/optimizer/resume/{test_user.id}/{java_job.id}/improve-snippet",
            json={
                "optimized_resume_id": completed.optimized_resume_id,
                "text": "Developed REST APIs using Java and Spring Boot for account workflows.",
                "context": "bullet",
                "previous_suggestion": "Delivered REST APIs using Java and Spring Boot for account workflows.",
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["suggested"] == "Designed and shipped Spring Boot services in Java that power customer account workflows."
    assert body["used_llm"] is True
    assert body["retry_failed"] is False


def test_try_again_cannot_introduce_unsupported_skill(client, db_session, test_user, test_resume, java_job):
    completed = _complete_workflow(db_session, test_user, java_job)
    with patch(
        "app.routes.optimizer_edit_routes.rewrite_bullet_with_ollama",
        return_value=("Developed REST APIs using Java, Kubernetes and Terraform.", "ollama", False),
    ):
        response = client.post(
            f"/api/optimizer/resume/{test_user.id}/{java_job.id}/improve-snippet",
            json={
                "optimized_resume_id": completed.optimized_resume_id,
                "text": "Developed REST APIs using Java.",
                "context": "bullet",
                "previous_suggestion": "Delivered REST APIs using Java.",
            },
        )
    assert response.status_code == 200
    suggested = response.json()["suggested"]
    assert "Kubernetes" not in suggested
    assert "Terraform" not in suggested


# ---------------------------------------------------------------------------
# Download (TESTS 16–19)
# ---------------------------------------------------------------------------

def test_completed_workflow_docx_download_contains_saved_edits(
    client, db_session, test_user, test_resume, java_job, tmp_path,
):
    _docx_source(db_session, test_resume, tmp_path)
    completed = _complete_workflow(db_session, test_user, java_job)
    optimized = db_session.get(OptimizedResume, completed.optimized_resume_id)
    current = optimized.final_resume_text or optimized.generated_resume_text
    # Edit an existing Experience bullet (a saved user edit, not a new claim).
    bullet = next(line for line in current.splitlines() if "React components" in line)
    marker = "Built reusable React components for customer portals."
    edited = current.replace(bullet, f"- {marker}", 1)
    saved = client.patch(
        f"/api/optimizer/resume/{test_user.id}/{java_job.id}",
        json={"optimized_resume_id": optimized.id, "edited_resume_text": edited, "action": "autosave"},
    )
    assert saved.status_code == 200
    assert saved.json()["safe_to_download"] is True

    response = client.get(
        f"/api/optimizer/resume/{test_user.id}/{java_job.id}/{optimized.id}/download?format=docx"
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert "attachment" in response.headers["content-disposition"].lower()
    text = "\n".join(p.text for p in Document(BytesIO(response.content)).paragraphs)
    assert marker in text

    txt = client.get(f"/api/optimizer/resume/{test_user.id}/{java_job.id}/{optimized.id}/download?format=txt")
    assert txt.status_code == 200
    assert marker in txt.text


def test_completed_workflow_pdf_download_uses_converter_contract(
    client, db_session, test_user, test_resume, java_job, tmp_path,
):
    _docx_source(db_session, test_resume, tmp_path)
    completed = _complete_workflow(db_session, test_user, java_job)
    pdf = tmp_path / "converted.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% test\n")
    calls = {}

    def fake_convert(docx_path, destination):
        calls["docx"] = str(docx_path)
        return pdf

    with patch("app.routes.optimizer_edit_routes.convert_docx_to_pdf", side_effect=fake_convert), \
         patch("app.routes.optimizer_edit_routes.validate_pdf_export", return_value=None):
        response = client.get(
            f"/api/optimizer/resume/{test_user.id}/{java_job.id}/{completed.optimized_resume_id}/download?format=pdf"
        )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert calls["docx"].endswith(".docx")
    assert response.content.startswith(b"%PDF")


def test_download_for_unknown_or_ineligible_workflow_is_safe_4xx(client, db_session, test_user, test_resume, sdet_job):
    ineligible = _complete_workflow(db_session, test_user, sdet_job)
    assert client.get(f"/api/ats-optimizer/download/{ineligible.id}?format=txt").status_code == 400
    assert client.get("/api/ats-optimizer/download/999999?format=txt").status_code == 404
    assert client.get(
        f"/api/optimizer/resume/{test_user.id}/{sdet_job.id}/999999/download?format=docx"
    ).status_code == 404
