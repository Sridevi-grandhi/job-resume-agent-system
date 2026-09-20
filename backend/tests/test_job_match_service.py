"""Tests for app/services/job_match_service.py.

Covers: deterministic/stable scoring (#20), fitting/missing skills without
invented content (#22/#23), generic cross-role-family matching with no
hardcoded roles (#53), multi-user isolation (#24/#52), LLM failure never
breaking discovery (#54), and malformed LLM JSON handling (#55).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.models.discovered_job_model import DiscoveredJob
from app.services import job_match_service as matcher

JAVA_JOB_DESCRIPTION = "We need a Java Full Stack Developer with Spring Boot, React, PostgreSQL, and AWS experience."
JAVA_JOB_SKILLS = "Java, Spring Boot, React, PostgreSQL, AWS"

DATA_ENGINEER_DESCRIPTION = "We need a Data Engineer with Apache Spark, Airflow, Snowflake, and ETL pipeline experience."
DATA_ENGINEER_SKILLS = "Apache Spark, Airflow, Snowflake, ETL"


def test_deterministic_match_is_stable_across_repeated_calls(java_resume_text):
    first = matcher.deterministic_match(
        job_title="Java Full Stack Developer", job_description=JAVA_JOB_DESCRIPTION,
        job_skills_csv=JAVA_JOB_SKILLS, resume_text=java_resume_text,
    )
    second = matcher.deterministic_match(
        job_title="Java Full Stack Developer", job_description=JAVA_JOB_DESCRIPTION,
        job_skills_csv=JAVA_JOB_SKILLS, resume_text=java_resume_text,
    )
    assert first == second


def test_matching_role_family_scores_higher_than_mismatched_role(java_resume_text):
    matching = matcher.deterministic_match(
        job_title="Java Full Stack Developer", job_description=JAVA_JOB_DESCRIPTION,
        job_skills_csv=JAVA_JOB_SKILLS, resume_text=java_resume_text,
    )
    mismatched = matcher.deterministic_match(
        job_title="Data Engineer", job_description=DATA_ENGINEER_DESCRIPTION,
        job_skills_csv=DATA_ENGINEER_SKILLS, resume_text=java_resume_text,
    )
    assert matching["match_score"] > mismatched["match_score"]
    assert matching["role_alignment"] in {"STRONG", "PARTIAL"}
    assert mismatched["role_alignment"] in {"PARTIAL", "MISMATCH"}


def test_python_resume_scores_python_job_higher_than_java_resume_does(python_resume_text, java_resume_text):
    python_job_description = "Python Backend Developer role using Django, Flask, PostgreSQL, and Celery."
    python_job_skills = "Python, Django, Flask, PostgreSQL, Celery"

    python_result = matcher.deterministic_match(
        job_title="Python Backend Developer", job_description=python_job_description,
        job_skills_csv=python_job_skills, resume_text=python_resume_text,
    )
    java_resume_result = matcher.deterministic_match(
        job_title="Python Backend Developer", job_description=python_job_description,
        job_skills_csv=python_job_skills, resume_text=java_resume_text,
    )
    assert python_result["match_score"] > java_resume_result["match_score"]


def test_fitting_skills_never_include_skills_absent_from_resume(java_resume_text):
    result = matcher.deterministic_match(
        job_title="Java Full Stack Developer",
        job_description="Java Spring Boot Kubernetes Kafka Terraform role.",
        job_skills_csv="Java, Spring Boot, Kubernetes, Kafka, Terraform",
        resume_text=java_resume_text,
    )
    resume_lower = java_resume_text.lower()
    for skill in result["fitting_skills"]:
        assert skill.lower() in resume_lower or skill.lower() in resume_lower.replace(".", "")
    # Kubernetes/Kafka/Terraform are not in the Java fixture resume -> must be reported missing, not fitting.
    assert "Kubernetes" in result["missing_skills"] or "Kafka" in result["missing_skills"] or "Terraform" in result["missing_skills"]


def _seed_discovered_job(db_session) -> DiscoveredJob:
    job = DiscoveredJob(
        source="greenhouse", source_mode="mock", source_job_id="1", fingerprint="fp-1",
        company="Meridian Systems", title="Java Full Stack Developer", location="Remote",
        work_mode="Remote", job_type="Full-time", description=JAVA_JOB_DESCRIPTION,
        skills=JAVA_JOB_SKILLS, direct_url=None, status="ACTIVE", content_hash="hash-1",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def test_get_or_compute_match_returns_none_without_resume(db_session):
    job = _seed_discovered_job(db_session)
    result = matcher.get_or_compute_match(
        db_session, user_id=1, resume_id=None, resume_text=None, discovered_job=job,
    )
    assert result is None


def test_get_or_compute_match_caches_and_recomputes_only_when_stale(db_session, java_resume_text):
    job = _seed_discovered_job(db_session)
    first = matcher.get_or_compute_match(
        db_session, user_id=1, resume_id=10, resume_text=java_resume_text, discovered_job=job, use_llm=False,
    )
    assert first is not None
    first_calculated_at = first.calculated_at

    second = matcher.get_or_compute_match(
        db_session, user_id=1, resume_id=10, resume_text=java_resume_text, discovered_job=job, use_llm=False,
    )
    assert second.id == first.id
    assert second.calculated_at == first_calculated_at  # not recomputed

    third = matcher.get_or_compute_match(
        db_session, user_id=1, resume_id=11, resume_text="Different resume text.", discovered_job=job, use_llm=False,
    )
    assert third.id == first.id  # same cache row, updated in place
    assert third.resume_id == 11


def test_multi_user_matches_are_isolated(db_session, java_resume_text, python_resume_text):
    job = _seed_discovered_job(db_session)
    user_a_match = matcher.get_or_compute_match(
        db_session, user_id=1, resume_id=10, resume_text=java_resume_text, discovered_job=job, use_llm=False,
    )
    user_b_match = matcher.get_or_compute_match(
        db_session, user_id=2, resume_id=20, resume_text=python_resume_text, discovered_job=job, use_llm=False,
    )
    assert user_a_match.id != user_b_match.id
    assert user_a_match.user_id == 1
    assert user_b_match.user_id == 2
    assert user_a_match.match_score != user_b_match.match_score


def test_llm_disabled_falls_back_to_deterministic_verdict(monkeypatch):
    monkeypatch.setenv("JOB_MATCH_LLM_PROVIDER", "none")
    deterministic = matcher.deterministic_match(
        job_title="Java Full Stack Developer", job_description=JAVA_JOB_DESCRIPTION,
        job_skills_csv=JAVA_JOB_SKILLS, resume_text="Java Spring Boot experience.",
    )
    result = matcher.llm_explain(
        resume_text="Java Spring Boot experience.", job_title="Java Full Stack Developer",
        job_description=JAVA_JOB_DESCRIPTION, requirements="", deterministic=deterministic,
    )
    assert result["llm_used"] is False
    assert result["verdict"]


def test_llm_failure_falls_back_without_raising(monkeypatch):
    monkeypatch.setenv("JOB_MATCH_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    deterministic = matcher.deterministic_match(
        job_title="Java Full Stack Developer", job_description=JAVA_JOB_DESCRIPTION,
        job_skills_csv=JAVA_JOB_SKILLS, resume_text="Java Spring Boot experience.",
    )
    with patch("requests.post", side_effect=Exception("network down")):
        result = matcher.llm_explain(
            resume_text="Java Spring Boot experience.", job_title="Java Full Stack Developer",
            job_description=JAVA_JOB_DESCRIPTION, requirements="", deterministic=deterministic,
        )
    assert result["llm_used"] is False
    assert result["fitting_skills"] == deterministic["fitting_skills"]


def test_llm_malformed_json_response_is_rejected_safely(monkeypatch):
    monkeypatch.setenv("JOB_MATCH_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    deterministic = matcher.deterministic_match(
        job_title="Java Full Stack Developer", job_description=JAVA_JOB_DESCRIPTION,
        job_skills_csv=JAVA_JOB_SKILLS, resume_text="Java Spring Boot experience.",
    )
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"choices": [{"message": {"content": "not valid json at all"}}]}
    with patch("requests.post", return_value=response):
        result = matcher.llm_explain(
            resume_text="Java Spring Boot experience.", job_title="Java Full Stack Developer",
            job_description=JAVA_JOB_DESCRIPTION, requirements="", deterministic=deterministic,
        )
    assert result["llm_used"] is False  # never persisted as if it were a valid structured response


def test_llm_score_field_is_ignored_deterministic_score_stays_authoritative(monkeypatch):
    monkeypatch.setenv("JOB_MATCH_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    deterministic = matcher.deterministic_match(
        job_title="Java Full Stack Developer", job_description=JAVA_JOB_DESCRIPTION,
        job_skills_csv=JAVA_JOB_SKILLS, resume_text="Java Spring Boot experience.",
    )
    response = MagicMock()
    response.status_code = 200
    llm_payload = {
        "fitting_skills": ["Java"], "missing_skills": ["AWS"],
        "verdict": "Solid Java alignment.", "role_alignment": "STRONG",
        "match_score": 999,  # must be ignored entirely
    }
    import json
    response.json.return_value = {"choices": [{"message": {"content": json.dumps(llm_payload)}}]}
    with patch("requests.post", return_value=response):
        result = matcher.llm_explain(
            resume_text="Java Spring Boot experience.", job_title="Java Full Stack Developer",
            job_description=JAVA_JOB_DESCRIPTION, requirements="", deterministic=deterministic,
        )
    assert result["llm_used"] is True
    assert "match_score" not in result
    assert deterministic["match_score"] != 999
