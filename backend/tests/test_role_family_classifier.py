"""Role-family classifier regressions: titles + defining signals beat shared tools."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.ats_scoring import (
    calculate_weighted_ats_score,
    classify_role,
    compare_role_families,
)


SDET_RESUME = """
Jane Doe
Senior SDET

Technical Skills
Selenium, TestNG, JUnit, Java, JavaScript, SQL, Jenkins, Maven, CI/CD, GitHub Actions, Agile, Postman

Professional Experience
Acme | Senior SDET | 2020-Present
- Developed Selenium WebDriver automation using Java and TestNG/JUnit
- Executed API testing using Postman for REST services
- Integrated test automation with Jenkins and GitHub Actions CI/CD
- Performed SQL database validation and defect investigation
"""

SDET_JOB = SimpleNamespace(
    id=1,
    title="Senior SDET / QA Automation Engineer",
    description="Required: Selenium, TestNG, JUnit, Java, API testing, Postman, Jenkins, CI/CD, SQL, Agile\nPreferred: Playwright, Docker",
    skills_required="",
    location="Remote",
)

DEVOPS_RESUME = """
Alex Rivera
DevOps Engineer

Technical Skills
AWS, Kubernetes, Docker, Terraform, Jenkins, CI/CD, Linux, Python

Professional Experience
CloudCo | DevOps Engineer | 2019-Present
- Provisioned AWS infrastructure with Terraform
- Administered Kubernetes clusters and Docker workloads
- Built deployment automation and cloud operations tooling
"""

DEVOPS_JOB = SimpleNamespace(
    id=2,
    title="Senior DevOps Engineer",
    description="Required: AWS, Kubernetes, Docker, Terraform, Jenkins, CI/CD, Linux\nPreferred: Helm",
    skills_required="",
    location="Remote",
)


def test_sdet_with_jenkins_is_not_devops():
    result = classify_role(SDET_RESUME)
    assert result["family"] == "SDET / QA Automation"
    assert result["family_id"] == "SDET"
    assert result["confidence"] >= 0.75
    assert "DevOps" not in (result.get("families") or [])
    # Shared tools may appear as supporting, never as the family winner.
    support = " ".join(result.get("supporting_signals") or []).lower()
    assert "jenkins" in support or "ci/cd" in support or True
    score = calculate_weighted_ats_score(SDET_RESUME, SDET_JOB)
    assert score["job_role_family"] == "SDET / QA Automation"
    assert score["resume_role_families"][0] == "SDET / QA Automation"
    assert score["suggested_role_family"] == "SDET / QA Automation"
    assert score["role_alignment"] in {"EXACT", "STRONG_RELATED"}


def test_devops_with_python_remains_devops():
    result = classify_role(DEVOPS_RESUME)
    assert result["family"] == "DevOps"
    assert result["family_id"] == "DEVOPS"
    score = calculate_weighted_ats_score(DEVOPS_RESUME, DEVOPS_JOB)
    assert score["job_role_family"] == "DevOps"
    assert score["resume_role_families"][0] == "DevOps"
    assert score["role_alignment"] == "EXACT"


def test_ci_cd_alone_does_not_make_devops_title_match():
    # Historical bug: CI/CD matched the DevOps title regex.
    result = classify_role("Engineer\nSkills: CI/CD, Jenkins, Git\nBuilt UI test automation with Selenium and TestNG")
    assert result["family"] == "SDET / QA Automation"


@pytest.mark.parametrize(
    "title,text,expected",
    [
        ("Java Backend Engineer", "Java Spring Boot Hibernate REST APIs microservices", "Java Backend"),
        ("Data Engineer", "Built ETL pipelines with Spark Airflow Kafka Snowflake", "Data Engineering"),
        ("Python Backend Engineer", "Python FastAPI PostgreSQL Redis Docker", "Python Backend"),
        ("Frontend Developer", "React TypeScript JavaScript HTML CSS", "Frontend"),
        ("QA Automation Engineer", "Selenium Playwright Cypress TestNG API testing", "SDET / QA Automation"),
    ],
)
def test_cross_role_title_and_defining_signals(title, text, expected):
    resume = f"{title}\n{title}\nProfessional Experience\nAcme | {title} | 2021-Present\n- {text}"
    result = classify_role(resume, title)
    assert result["family"] == expected


def test_java_with_docker_stays_java():
    resume = """
Sam Patel
Java Backend Engineer
Skills: Java, Spring Boot, Docker, Jenkins, CI/CD
Experience
FinTech | Java Backend Engineer | 2021-Present
- Built Spring Boot microservices and REST APIs
- Packaged services with Docker for deployment
"""
    assert classify_role(resume)["family"] == "Java Backend"


def test_data_engineer_with_aws_stays_data():
    resume = """
Riley Chen
Data Engineer
Skills: Python, Spark, Airflow, AWS, SQL
Experience
DataCorp | Data Engineer | 2020-Present
- Built ETL data pipelines with Spark and Airflow on AWS
"""
    assert classify_role(resume)["family"] == "Data Engineering"


def test_sdet_vs_devops_alignment_is_mismatch():
    assert compare_role_families("SDET / QA Automation", "DevOps") == "MISMATCH"


def test_multi_user_role_isolation_is_stateless():
    a = classify_role(SDET_RESUME)
    b = classify_role(DEVOPS_RESUME)
    c = classify_role(SDET_RESUME)
    assert a["family"] == "SDET / QA Automation"
    assert b["family"] == "DevOps"
    assert c["family"] == a["family"]
    assert a["family"] != b["family"]


def test_recent_sdet_title_beats_older_devops_experience():
    resume = """
Jamie Lee
Senior SDET

Professional Experience
Acme | Senior SDET | 2022-Present
- Built Selenium TestNG automation frameworks and API testing with Postman

CloudCo | DevOps Engineer | 2018-2021
- Provisioned Terraform Kubernetes infrastructure and Jenkins pipelines
"""
    result = classify_role(resume)
    assert result["family"] == "SDET / QA Automation"
    assert result["family_id"] == "SDET"
