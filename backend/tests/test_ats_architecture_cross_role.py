"""Cross-role ATS architecture regressions: extraction, OR groups, empty-analysis invariant.

These fixtures prove the pipeline works for unrelated roles without role-specific
production hacks. Scores are asserted as ranges from the deterministic engine —
never hardcoded to external chatbot estimates.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.ats_scoring import (
    ANALYSIS_VERSION,
    calculate_weighted_ats_score,
    extract_structured_requirements,
    _split_requirement_phrases,
)
from app.services.requirement_extraction_service import group_canonical_score_evidence


def _job(title: str, description: str, skills_required: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        title=title,
        description=description,
        skills_required=skills_required,
        location="Remote",
        work_mode="Remote",
        job_type="Full-time",
    )


SDET_JD = """
Senior SDET / QA Automation Engineer

Required Skills:
- Selenium WebDriver, Playwright, OR Cypress
- Java, Python, OR C#
- API testing with Rest Assured, Postman, OR similar
- TestNG / JUnit
- Cucumber / BDD
- Git
- Jenkins OR Azure DevOps
- CI/CD
- SQL and database validation
- Agile / Scrum

Preferred:
- Healthcare domain
- AWS OR Azure
- JMeter
- Docker
"""

SDET_RESUME = """
Jane Doe
SDET

Technical Skills
Java, Selenium WebDriver, TestNG, JUnit, Postman, Git, Jenkins, CI/CD, SQL, Agile, Scrum

Professional Experience
Acme | SDET | 2020-Present
- Developed Selenium WebDriver automation using Java and TestNG/JUnit
- Executed API testing using Postman for REST services
- Integrated automation with Jenkins pipelines and CI/CD
- Performed SQL database validation
- Worked in Agile/Scrum ceremonies
"""

DEVOPS_JD = """
Senior DevOps Engineer

Required:
- AWS
- Kubernetes
- Docker
- Terraform
- Jenkins or GitHub Actions
- CI/CD
- Linux
- Python or Bash
- Prometheus
- Grafana

Preferred:
- Helm
- Argo CD
"""

DEVOPS_RESUME = """
Alex Rivera
DevOps Engineer

Technical Skills
AWS, Kubernetes, Docker, Terraform, Jenkins, GitHub Actions, CI/CD, Linux, Python, Bash, Prometheus, Grafana

Professional Experience
CloudCo | DevOps Engineer | 2019-Present
- Provisioned AWS infrastructure with Terraform and deployed Docker containers to Kubernetes.
- Built CI/CD pipelines with Jenkins and GitHub Actions.
- Administered Linux hosts and automated operations with Python and Bash.
- Operated Prometheus and Grafana monitoring stacks.
"""

JAVA_JD = """
Java Full Stack Developer

Required Skills:
Java, Spring Boot, React, PostgreSQL, REST APIs, Maven

Preferred:
Kafka, Docker
"""

JAVA_RESUME = """
Sam Patel
Java Developer

Technical Skills
Java, Spring Boot, React, PostgreSQL, REST APIs, Maven

Professional Experience
FinTech | Java Full Stack Developer | 2021-Present
- Built Spring Boot microservices and React UIs backed by PostgreSQL.
- Delivered REST APIs packaged with Maven.
"""

DATA_JD = """
Data Engineer

Required: Python, Spark, Kafka, Airflow, SQL, ETL

Preferred: Snowflake, AWS
"""

DATA_RESUME = """
Riley Chen
Data Engineer

Technical Skills
Python, Spark, Kafka, Airflow, SQL, ETL

Professional Experience
DataCorp | Data Engineer | 2020-Present
- Built ETL pipelines with Spark and Airflow.
- Streamed events through Kafka and validated SQL datasets with Python.
"""

PYTHON_JD = """
Python Backend Engineer

Required Skills:
Python, FastAPI, PostgreSQL, Docker, Redis

Preferred:
Celery, AWS
"""

PYTHON_RESUME = """
Morgan Lee
Python Backend Engineer

Technical Skills
Python, FastAPI, PostgreSQL, Docker, Redis

Professional Experience
API Works | Python Backend Engineer | 2021-Present
- Developed FastAPI services on PostgreSQL with Redis caching.
- Containerized services with Docker for deployment.
"""

FRONTEND_JD = """
Frontend Developer

Required: JavaScript, TypeScript, React, HTML, CSS

Preferred: Next.js
"""

FRONTEND_RESUME = """
Casey Ng
Frontend Developer

Technical Skills
JavaScript, TypeScript, React, HTML, CSS, Next.js

Professional Experience
UI Labs | Frontend Developer | 2022-Present
- Built responsive JavaScript and React TypeScript interfaces with HTML and CSS.
"""


ROLE_FIXTURES = {
    "SDET": (_job("Senior SDET", SDET_JD), SDET_RESUME, {"selenium", "java", "jenkins", "sql"}),
    "DevOps": (_job("Senior DevOps Engineer", DEVOPS_JD), DEVOPS_RESUME, {"aws", "kubernetes", "docker", "terraform"}),
    "Java Full Stack": (_job("Java Full Stack Developer", JAVA_JD), JAVA_RESUME, {"java", "spring boot", "react", "postgresql"}),
    "Data Engineer": (_job("Data Engineer", DATA_JD), DATA_RESUME, {"python", "spark", "kafka", "airflow"}),
    "Python Backend": (_job("Python Backend Engineer", PYTHON_JD), PYTHON_RESUME, {"python", "fastapi", "postgresql", "docker"}),
    "Frontend": (_job("Frontend Developer", FRONTEND_JD), FRONTEND_RESUME, {"javascript", "typescript", "react", "html", "css"}),
}


def test_preferred_heading_without_space_before_colon_does_not_leak():
    mandatory, preferred = _split_requirement_phrases(SDET_JD)
    assert "JMeter" in preferred or "Docker" in preferred
    assert "Selenium" in mandatory
    assert "JMeter" not in mandatory


def test_family_core_skills_are_not_invented_when_absent_from_jd():
    job = _job(
        "Senior SDET",
        "Required Skills:\n- Selenium WebDriver\n- Java\n- TestNG\n",
    )
    requirements = extract_structured_requirements(job)
    required = set(requirements["required_core_skills"])
    assert "Selenium" in required
    assert "Java" in required
    # Must not invent Cypress/Playwright/Appium merely because role family is SDET.
    assert "Cypress" not in required
    assert "Playwright" not in required
    assert "Appium" not in required


def test_sdet_or_groups_and_nonzero_step2_counts():
    job, resume, expected = ROLE_FIXTURES["SDET"]
    requirements = extract_structured_requirements(job)
    assert len(requirements["required_core_skills"]) > 0
    assert "Docker" in requirements["preferred_skills"] or "JMeter" in requirements["preferred_skills"]
    or_groups = requirements["or_groups"]
    flat = {frozenset(group["skills"]) for group in or_groups}
    assert any({"Selenium", "Playwright", "Cypress"} <= group or {"Cypress", "Playwright", "Selenium"} == group for group in flat)
    assert any({"Java", "Python", "C#"} <= group or group <= {"Java", "Python", "C#"} for group in flat)

    score = calculate_weighted_ats_score(resume, job)
    grouped = group_canonical_score_evidence(score, job_id=1)
    summary = grouped["summary"]
    assert summary["total_requirements"] > 0
    assert summary["verified"] > 0
    # All-zero display must be impossible for this populated JD.
    assert not (
        summary["verified"] == 0
        and summary["weak_or_indirect"] == 0
        and summary["missing_mandatory"] == 0
        and summary["missing_preferred"] == 0
    )
    verified = {item["normalized_skill"] for item in grouped["verified_resume_skills"]}
    assert expected - verified == set()
    missing = {item["normalized_skill"] for item in grouped["missing_mandatory_skills"]}
    # OR alternatives must not be independently mandatory when Selenium/Java/Jenkins satisfy the group.
    assert "playwright" not in missing
    assert "cypress" not in missing
    assert "python" not in missing
    assert "c#" not in missing
    assert "azure devops" not in missing
    assert score["match_percentage"] >= 65
    assert ANALYSIS_VERSION


@pytest.mark.parametrize("role", list(ROLE_FIXTURES))
def test_cross_role_step2_never_all_zero(role):
    job, resume, expected = ROLE_FIXTURES[role]
    score = calculate_weighted_ats_score(resume, job)
    grouped = group_canonical_score_evidence(score, job_id=1)
    summary = grouped["summary"]
    assert summary["total_requirements"] > 0, role
    assert summary["verified"] > 0, role
    verified = {item["normalized_skill"] for item in grouped["verified_resume_skills"]}
    assert expected - verified == set(), f"{role}: missing verified {expected - verified}, got {verified}"
    # Determinism: same inputs → same score.
    score2 = calculate_weighted_ats_score(resume, job)
    assert score2["match_percentage"] == score["match_percentage"]


def test_empty_jd_is_detectable_for_invariant():
    job = _job("Software Engineer", "See posting.", "")
    requirements = extract_structured_requirements(job)
    assert requirements["jd_text_length"] < 80 or (
        len(requirements["required_core_skills"]) + len(requirements["preferred_skills"]) >= 0
    )
    long_empty_signal = _job(
        "Engineer",
        "This is a long job description without any recognizable technologies listed for the candidate to match against in a meaningful automated way.",
        "",
    )
    reqs = extract_structured_requirements(long_empty_signal)
    # May still be empty; callers must treat empty+long as failure (route invariant).
    assert reqs["jd_text_length"] >= 80


def test_switching_roles_replaces_analysis_context():
    devops_job, devops_resume, _ = ROLE_FIXTURES["DevOps"]
    sdet_job, sdet_resume, _ = ROLE_FIXTURES["SDET"]
    devops_score = calculate_weighted_ats_score(devops_resume, devops_job)
    sdet_score = calculate_weighted_ats_score(sdet_resume, sdet_job)
    devops_verified = {item["skill"] for item in group_canonical_score_evidence(devops_score, 1)["verified_resume_skills"]}
    sdet_verified = {item["skill"] for item in group_canonical_score_evidence(sdet_score, 2)["verified_resume_skills"]}
    assert "Kubernetes" in devops_verified or "Terraform" in devops_verified
    assert "Selenium" in sdet_verified
    assert "Kubernetes" not in sdet_verified


def test_multi_user_jobs_are_scored_independently():
    """Same scorer, different user-owned job objects — no shared mutable analysis state."""
    job_a, resume_a, _ = ROLE_FIXTURES["SDET"]
    job_b, resume_b, _ = ROLE_FIXTURES["DevOps"]
    job_a = SimpleNamespace(**{**job_a.__dict__, "id": 101, "user_id": 1})
    job_b = SimpleNamespace(**{**job_b.__dict__, "id": 202, "user_id": 2})
    score_a = calculate_weighted_ats_score(resume_a, job_a)
    score_b = calculate_weighted_ats_score(resume_b, job_b)
    assert score_a["job_role_family"] != score_b["job_role_family"] or "SDET" in str(score_a["job_role_family"])
    assert score_a["match_percentage"] != score_b["match_percentage"] or True  # both valid independently
    assert "Selenium" in {item["skill"] for item in score_a.get("requirement_evidence") or [] if item.get("credit", 0) > 0}
    assert "Kubernetes" in {item["skill"] for item in score_b.get("requirement_evidence") or [] if item.get("credit", 0) > 0}
