"""User-facing major_step must stay 5 while generate substages run."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.ats_optimization_workflow_service import (
    generation_substage_for_status,
    major_step_for_workflow,
    start_or_get_workflow,
    workflow_to_dict,
)
from app.services.ats_scoring import calculate_weighted_ats_score


def test_analyzing_original_is_step5_substage_not_wizard_step_2():
    workflow = SimpleNamespace(
        workflow_status="ANALYZING_ORIGINAL",
        optimized_resume_id=None,
        generated_resume_plain_text=None,
    )
    assert major_step_for_workflow(workflow) == 5
    assert generation_substage_for_status("ANALYZING_ORIGINAL") == "calculating_original_score"
    assert generation_substage_for_status("VALIDATING_RESUME") == "validating_resume"
    assert generation_substage_for_status("ANALYZING_OPTIMIZED") == "calculating_optimized_score"


def test_workflow_to_dict_keeps_major_step_5_during_generation(db_session, test_user, test_resume, java_job):
    workflow = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    for status, substage in (
        ("ANALYZING_ORIGINAL", "calculating_original_score"),
        ("GENERATING_RESUME", "generating_resume"),
        ("VALIDATING_RESUME", "validating_resume"),
        ("ANALYZING_OPTIMIZED", "calculating_optimized_score"),
        ("VALIDATION_FAILED", "failed"),
        ("FAILED", "failed"),
        ("COMPLETED", "completed"),
    ):
        workflow.workflow_status = status
        db_session.add(workflow)
        db_session.commit()
        payload = workflow_to_dict(workflow, java_job)
        assert payload["major_step"] == 5
        assert payload["user_workflow_step"] == 5
        assert payload["generation_substage"] == substage
        assert payload["current_step"] == status
        assert payload["workflow_status"] == status


def test_pending_workflow_without_generation_is_not_forced_to_step_5(db_session, test_user, test_resume, java_job):
    workflow = start_or_get_workflow(db_session, user_id=test_user.id, job_id=java_job.id)
    payload = workflow_to_dict(workflow, java_job)
    assert payload["workflow_status"] == "PENDING"
    assert payload["major_step"] is None
    assert payload["generation_substage"] == "generating_resume"


def test_identical_scoring_inputs_are_deterministic():
    resume = (
        "Pat Engineer\npat@example.com\n\nTechnical Skills\nJava, Spring Boot, SQL\n\n"
        "Experience\nAcme | Java Engineer | 2020 - Present\n- Built Spring Boot APIs.\n"
    )
    job = SimpleNamespace(
        title="Java Engineer",
        company="Acme",
        description="Java Spring Boot SQL REST APIs for backend services.",
        skills_required="Java, Spring Boot, SQL",
    )
    scores = [
        calculate_weighted_ats_score(resume, job, evidence_gated=False)["match_percentage"]
        for _ in range(5)
    ]
    assert len(set(scores)) == 1
