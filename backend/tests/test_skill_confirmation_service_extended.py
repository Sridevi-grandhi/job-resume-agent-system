"""Extended tests for app/services/skill_confirmation_service.py.

tests/test_skill_confirmation.py already covers the question-bank/basic path;
this file targets the remaining validation, classification, and persistence
branches (existing coverage was 53%).
"""

from __future__ import annotations

import pytest

from app.models.skill_confirmation_model import UserConfirmedSkill
from app.services.skill_confirmation_service import (
    classify_evidence_status,
    confirmed_skill_to_dict,
    credit_for_row,
    delete_confirmed_skill,
    load_confirmed_skills,
    required_fields_for,
    save_evidence,
    to_generation_evidence,
    to_scoring_evidence,
    validate_evidence_item,
)


BASE_ITEM = {
    "skill": "Kafka",
    "answer": "has_it",
    "evidence_type": "Current employment",
    "experience_level": "Advanced",
    "experience_range": "2-4 years",
    "usage_description": "Built event pipelines with Kafka.",
    "confirmed_accurate": True,
}


def test_existing_position_lookup_succeeds_without_new_employment_fields():
    item = dict(BASE_ITEM)  # is_new_employment defaults to False
    errors = validate_evidence_item(item, known_employers=["Acme Corp"])
    assert errors == []


def test_existing_position_lookup_fails_for_unknown_employer():
    item = dict(BASE_ITEM, employer_name="Nonexistent Inc")
    errors = validate_evidence_item(item, known_employers=["Acme Corp"])
    assert any("was not found in your resume" in e for e in errors)


def test_new_employment_requires_employer_role_and_start_date():
    item = dict(BASE_ITEM, is_new_employment=True)
    errors = validate_evidence_item(item)
    assert any("missing required evidence" in e for e in errors)


def test_new_employment_valid_with_all_fields_and_currently_using():
    item = dict(
        BASE_ITEM,
        is_new_employment=True,
        employer_name="New Co",
        role_title="Engineer",
        start_date="2022-01",
        currently_using=True,
    )
    errors = validate_evidence_item(item)
    assert errors == []


def test_new_employment_requires_end_date_when_not_currently_employed():
    item = dict(
        BASE_ITEM,
        is_new_employment=True,
        employer_name="New Co",
        role_title="Engineer",
        start_date="2022-01",
        currently_using=False,
    )
    errors = validate_evidence_item(item)
    assert any("end_date" in e for e in errors)


def test_personal_project_requires_project_name_and_work_completed():
    item = dict(BASE_ITEM, evidence_type="Personal project", employer_name=None)
    item.pop("employer_name", None)
    errors = validate_evidence_item(item)
    assert any("project_name" in e or "work_completed" in e for e in errors)


def test_personal_project_valid_with_required_fields():
    item = dict(
        BASE_ITEM,
        evidence_type="Personal project",
        project_name="Home automation",
        work_completed="Built a Kafka-based event bus.",
    )
    errors = validate_evidence_item(item)
    assert errors == []


def test_professional_project_valid_with_required_fields():
    item = dict(
        BASE_ITEM,
        evidence_type="Professional project",
        project_name="Payments migration",
        work_completed="Migrated batch jobs to Kafka streams.",
    )
    errors = validate_evidence_item(item)
    assert errors == []


def test_academic_project_valid_with_required_fields():
    item = dict(
        BASE_ITEM,
        evidence_type="Academic project",
        project_name="Capstone",
        work_completed="Built a Kafka consumer for coursework.",
    )
    errors = validate_evidence_item(item)
    assert errors == []


def test_certification_requires_name_and_provider():
    item = dict(BASE_ITEM, evidence_type="Certification")
    errors = validate_evidence_item(item)
    assert any("certification_name" in e or "provider_name" in e for e in errors)


def test_certification_valid_with_required_fields():
    item = dict(
        BASE_ITEM,
        evidence_type="Certification",
        certification_name="Confluent Kafka Developer",
        provider_name="Confluent",
    )
    errors = validate_evidence_item(item)
    assert errors == []


def test_training_requires_project_name_field():
    item = dict(BASE_ITEM, evidence_type="Training/coursework")
    errors = validate_evidence_item(item)
    assert any("project_name" in e for e in errors)


def test_training_valid_with_required_field():
    item = dict(BASE_ITEM, evidence_type="Training/coursework", project_name="Kafka Bootcamp")
    errors = validate_evidence_item(item)
    assert errors == []


def test_learning_only_never_requires_evidence_fields():
    item = {"skill": "Kafka", "answer": "learning_only"}
    assert validate_evidence_item(item) == []


def test_not_possessed_never_requires_evidence_fields():
    item = {"skill": "Kafka", "answer": "not_possessed"}
    assert validate_evidence_item(item) == []


def test_incomplete_evidence_missing_baseline_fields():
    item = {"skill": "Kafka", "answer": "has_it", "evidence_type": "Current employment"}
    errors = validate_evidence_item(item)
    assert any("missing required evidence" in e for e in errors)
    assert "experience_level" in errors[0]


def test_invalid_evidence_type_is_rejected():
    item = dict(BASE_ITEM, evidence_type="Not a real type")
    errors = validate_evidence_item(item)
    assert any("invalid evidence_type" in e for e in errors)


def test_missing_evidence_type_prompts_choose_message():
    item = {"skill": "Kafka", "answer": "has_it"}
    errors = validate_evidence_item(item)
    assert "choose how you gained this skill" in errors[0]


def test_invalid_experience_level_is_rejected():
    item = dict(BASE_ITEM, experience_level="Expert")
    errors = validate_evidence_item(item)
    assert any("invalid experience_level" in e for e in errors)


def test_invalid_experience_range_is_rejected():
    item = dict(BASE_ITEM, experience_range="10+ years")
    errors = validate_evidence_item(item)
    assert any("invalid experience_range" in e for e in errors)


def test_missing_accuracy_confirmation_is_flagged():
    item = dict(BASE_ITEM, confirmed_accurate=False)
    errors = validate_evidence_item(item)
    assert any("accuracy confirmation" in e for e in errors)


def test_required_fields_for_each_evidence_type():
    assert required_fields_for("Current employment") == []
    assert required_fields_for("Current employment", is_new_employment=True) == [
        "employer_name", "role_title", "start_date",
    ]
    assert set(required_fields_for("Professional project")) == {"project_name", "work_completed"}
    assert required_fields_for("Certification") == ["certification_name", "provider_name"]
    assert required_fields_for("Training/coursework") == ["project_name"]
    assert required_fields_for("Learning only") == []


def test_classify_evidence_status_not_possessed():
    status, credit = classify_evidence_status({"answer": "not_possessed"})
    assert status == "REJECTED"
    assert credit == 0.0


def test_classify_evidence_status_learning_only():
    status, credit = classify_evidence_status({"answer": "learning_only"})
    assert status == "LEARNING_ONLY"
    assert credit == 0.10


def test_classify_evidence_status_incomplete_answer():
    status, credit = classify_evidence_status({"answer": None})
    assert status == "INCOMPLETE"
    assert credit == 0.0


def test_classify_evidence_status_incomplete_when_validation_fails():
    status, credit = classify_evidence_status({"answer": "has_it"})
    assert status == "INCOMPLETE"
    assert credit == 0.0


def test_classify_evidence_status_credit_boundaries_by_type():
    cases = {
        "Current employment": ("USER_CONFIRMED_PROFESSIONAL", 0.85),
        "Previous employment": ("USER_CONFIRMED_PROFESSIONAL", 0.85),
        "Professional project": ("USER_CONFIRMED_PROJECT", 0.75),
        "Personal project": ("USER_CONFIRMED_PROJECT", 0.55),
        "Academic project": ("USER_CONFIRMED_PROJECT", 0.55),
        "Certification": ("USER_CONFIRMED_CERTIFICATION", 0.35),
        "Training/coursework": ("USER_CONFIRMED_TRAINING", 0.25),
    }
    for evidence_type, (expected_status, expected_credit) in cases.items():
        item = dict(BASE_ITEM, evidence_type=evidence_type)
        if evidence_type in ("Professional project", "Personal project", "Academic project"):
            item.update(project_name="P", work_completed="done")
        elif evidence_type == "Certification":
            item.update(certification_name="C", provider_name="P")
        elif evidence_type == "Training/coursework":
            item.update(project_name="Course")
        status, credit = classify_evidence_status(item, known_employers=["Acme Corp"])
        assert (status, credit) == (expected_status, expected_credit), evidence_type


def test_save_evidence_persists_and_returns_rows(db_session, test_user, java_job, test_resume):
    saved = save_evidence(
        db_session,
        user_id=test_user.id,
        job_id=java_job.id,
        resume_id=test_resume.id,
        items=[dict(BASE_ITEM)],
    )
    assert len(saved) == 1
    row = saved[0]
    assert row.evidence_status == "USER_CONFIRMED_PROFESSIONAL"
    assert row.skill_name == "Kafka"
    assert row.confirmed is True


def test_save_evidence_raises_valueerror_on_invalid_items(db_session, test_user, java_job):
    with pytest.raises(ValueError):
        save_evidence(
            db_session,
            user_id=test_user.id,
            job_id=java_job.id,
            resume_id=None,
            items=[{"skill": "Kafka", "answer": "has_it"}],
        )


def test_save_evidence_replaces_existing_row_for_same_skill(db_session, test_user, java_job):
    save_evidence(db_session, user_id=test_user.id, job_id=java_job.id, resume_id=None, items=[dict(BASE_ITEM)])
    updated_item = dict(BASE_ITEM, experience_level="Beginner")
    saved = save_evidence(db_session, user_id=test_user.id, job_id=java_job.id, resume_id=None, items=[updated_item])

    assert len(saved) == 1
    rows = load_confirmed_skills(db_session, user_id=test_user.id, job_id=java_job.id)
    assert len(rows) == 1
    assert rows[0].experience_level == "Beginner"


def test_save_evidence_not_possessed_answer_stores_rejected(db_session, test_user, java_job):
    saved = save_evidence(
        db_session, user_id=test_user.id, job_id=java_job.id, resume_id=None,
        items=[{"skill": "Kubernetes", "answer": "not_possessed"}],
    )
    assert saved[0].evidence_status == "REJECTED"
    assert saved[0].confirmed is False


def test_save_evidence_learning_only_answer_stores_learning_only(db_session, test_user, java_job):
    saved = save_evidence(
        db_session, user_id=test_user.id, job_id=java_job.id, resume_id=None,
        items=[{"skill": "Kubernetes", "answer": "learning_only"}],
    )
    assert saved[0].evidence_status == "LEARNING_ONLY"
    assert saved[0].evidence_type == "Learning only"
    assert saved[0].confirmed is True


def test_delete_confirmed_skill_removes_row(db_session, test_user, java_job):
    saved = save_evidence(db_session, user_id=test_user.id, job_id=java_job.id, resume_id=None, items=[dict(BASE_ITEM)])
    evidence_id = saved[0].id

    removed = delete_confirmed_skill(db_session, user_id=test_user.id, job_id=java_job.id, evidence_id=evidence_id)
    assert removed is True
    rows = load_confirmed_skills(db_session, user_id=test_user.id, job_id=java_job.id)
    assert len(rows) == 1
    assert rows[0].is_active is False
    assert rows[0].evidence_status == "OBSOLETE"


def test_delete_confirmed_skill_returns_false_when_not_found(db_session, test_user, java_job):
    removed = delete_confirmed_skill(db_session, user_id=test_user.id, job_id=java_job.id, evidence_id=999999)
    assert removed is False


def test_delete_confirmed_skill_cross_user_access_denied(db_session, test_user, second_user, java_job):
    saved = save_evidence(db_session, user_id=test_user.id, job_id=java_job.id, resume_id=None, items=[dict(BASE_ITEM)])
    evidence_id = saved[0].id

    removed = delete_confirmed_skill(db_session, user_id=second_user.id, job_id=java_job.id, evidence_id=evidence_id)
    assert removed is False
    # Row must still exist for the real owner.
    assert len(load_confirmed_skills(db_session, user_id=test_user.id, job_id=java_job.id)) == 1


def test_delete_confirmed_skill_wrong_job_denied(db_session, test_user, java_job, sdet_job):
    saved = save_evidence(db_session, user_id=test_user.id, job_id=java_job.id, resume_id=None, items=[dict(BASE_ITEM)])
    evidence_id = saved[0].id

    removed = delete_confirmed_skill(db_session, user_id=test_user.id, job_id=sdet_job.id, evidence_id=evidence_id)
    assert removed is False


def test_load_confirmed_skills_returns_only_for_user_and_job(db_session, test_user, second_user, java_job):
    save_evidence(db_session, user_id=test_user.id, job_id=java_job.id, resume_id=None, items=[dict(BASE_ITEM)])
    save_evidence(db_session, user_id=second_user.id, job_id=java_job.id, resume_id=None, items=[dict(BASE_ITEM, skill="Docker")])

    rows = load_confirmed_skills(db_session, user_id=test_user.id, job_id=java_job.id)
    assert len(rows) == 1
    assert rows[0].skill_name == "Kafka"


def test_credit_for_row_verified_from_resume():
    row = UserConfirmedSkill(user_id=1, job_id=1, skill_name="Java", normalized_skill_name="java",
                              evidence_status="VERIFIED_FROM_RESUME")
    assert credit_for_row(row) == 1.0


def test_credit_for_row_incomplete_and_rejected_are_zero():
    incomplete = UserConfirmedSkill(user_id=1, job_id=1, skill_name="Java", normalized_skill_name="java",
                                     evidence_status="INCOMPLETE")
    rejected = UserConfirmedSkill(user_id=1, job_id=1, skill_name="Java", normalized_skill_name="java",
                                   evidence_status="REJECTED")
    assert credit_for_row(incomplete) == 0.0
    assert credit_for_row(rejected) == 0.0


def test_to_scoring_evidence_excludes_incomplete_and_rejected(db_session, test_user, java_job):
    save_evidence(db_session, user_id=test_user.id, job_id=java_job.id, resume_id=None,
                  items=[dict(BASE_ITEM), {"skill": "Kubernetes", "answer": "not_possessed"}])
    rows = load_confirmed_skills(db_session, user_id=test_user.id, job_id=java_job.id)
    evidence = to_scoring_evidence(rows)
    assert "Kafka" in evidence
    assert "Kubernetes" not in evidence


def test_to_generation_evidence_excludes_incomplete_and_rejected(db_session, test_user, java_job):
    save_evidence(db_session, user_id=test_user.id, job_id=java_job.id, resume_id=None,
                  items=[dict(BASE_ITEM), {"skill": "Kubernetes", "answer": "not_possessed"}])
    rows = load_confirmed_skills(db_session, user_id=test_user.id, job_id=java_job.id)
    evidence = to_generation_evidence(rows)
    assert "Kafka" in evidence
    assert "Kubernetes" not in evidence
    assert evidence["Kafka"]["credit"] == 0.85


def test_legacy_unknown_evidence_is_not_scored_or_generated():
    row = UserConfirmedSkill(
        user_id=1, job_id=1, skill_name="Angular", normalized_skill_name="Angular",
        confirmed=True, evidence_type="Current employment",
        evidence_status="USER_CONFIRMED_PROFESSIONAL", evidence_source="legacy_unknown",
        experience_level="Advanced", usage_description="Unverifiable historical value",
    )
    assert to_scoring_evidence([row]) == {}
    assert to_generation_evidence([row]) == {}


def test_confirmed_skill_to_dict_includes_credit_and_metadata(db_session, test_user, java_job):
    saved = save_evidence(db_session, user_id=test_user.id, job_id=java_job.id, resume_id=None, items=[dict(BASE_ITEM)])
    payload = confirmed_skill_to_dict(saved[0])
    assert payload["skill"] == "Kafka"
    assert payload["credit"] == 0.85
    assert payload["evidence_status"] == "USER_CONFIRMED_PROFESSIONAL"
    assert payload["canonical_skill"] == "kafka"
    assert payload["confirmation_status"] == "user_confirmed"
    assert payload["experience_context"] == "professional"
    assert payload["evidence_strength"] == "strong"
    assert payload["source"] == "user_input"


def test_null_optional_fields_do_not_raise_in_confirmed_skill_to_dict(db_session, test_user, java_job):
    saved = save_evidence(
        db_session, user_id=test_user.id, job_id=java_job.id, resume_id=None,
        items=[{"skill": "Kubernetes", "answer": "not_possessed"}],
    )
    payload = confirmed_skill_to_dict(saved[0])
    assert payload["employer_name"] is None
    assert payload["credit"] == 0.0
    assert payload["confirmation_status"] == "user_denied"
    assert payload["evidence_strength"] == "unavailable"


def test_very_long_description_is_accepted():
    item = dict(BASE_ITEM, usage_description="x" * 5000)
    assert validate_evidence_item(item) == []


def test_compact_confirmation_requires_user_description():
    item = {
        "skill": "JPA", "answer": "has_it", "evidence_type": "Current employment",
        "experience_level": "Intermediate", "simple_confirmation": True,
    }
    assert "own words" in validate_evidence_item(item)[0]


def test_checkbox_selection_without_decision_is_rejected(db_session, test_user, java_job):
    """Draft stubs with answer=None must stay frontend-only — never persist INCOMPLETE rows."""
    import pytest
    with pytest.raises(ValueError, match="no confirmation decision"):
        save_evidence(
            db_session, user_id=test_user.id, job_id=java_job.id, resume_id=None,
            items=[{"skill": "Angular", "answer": None, "requirement_type": "MANDATORY"}],
        )


def test_user_entered_compact_evidence_records_user_input_source(db_session, test_user, java_job):
    saved = save_evidence(
        db_session, user_id=test_user.id, job_id=java_job.id, resume_id=None,
        items=[{
            "skill": "Angular", "answer": "has_it", "evidence_type": "Personal project",
            "experience_level": "Intermediate", "simple_confirmation": True,
            "usage_description": "Built Angular components for my personal project.",
        }],
    )[0]
    payload = confirmed_skill_to_dict(saved)
    assert payload["evidence_status"] == "USER_CONFIRMED_PROJECT"
    assert payload["evidence_source"] == "user_input"
    assert payload["user_input_description"] == "Built Angular components for my personal project."
    assert payload["ai_rewritten_description"] is None


def test_compact_confirmation_limits_description_to_300_characters():
    item = {
        "skill": "JPA", "answer": "has_it", "evidence_type": "Personal project",
        "experience_level": "Intermediate", "simple_confirmation": True,
        "usage_description": "x" * 301,
    }
    assert "300 characters" in validate_evidence_item(item)[0]
