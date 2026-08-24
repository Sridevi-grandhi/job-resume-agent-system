"""Generic capability/contextual evidence regressions across roles."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.ats_scoring import calculate_weighted_ats_score
from app.services.requirement_extraction_service import group_canonical_score_evidence
from app.services.skill_analysis_service import get_skill_evidence, normalize_skill_name


SDET_RESUME = """
Jane Doe
Senior SDET

Technical Skills
Selenium, TestNG, JUnit, Java, JavaScript, SQL, Jenkins, Maven, CI/CD, GitHub Actions, Agile, Postman

Professional Experience
Acme | Senior SDET | 2020-Present
- Developed Selenium WebDriver automation suites using TestNG and Page Object Model for regression testing
- Executed SQL queries against Oracle, MySQL and PostgreSQL to validate application data, transaction results, record updates and data consistency
- Performed API testing using Postman for REST services
- Integrated automated regression suites with Jenkins and GitHub Actions
"""

SDET_JOB = SimpleNamespace(
    id=101,
    title="Senior SDET / QA Automation Engineer",
    description=(
        "Required: Selenium, TestNG, JUnit, Java, Test Automation, Database Validation, "
        "API Testing, Postman, Jenkins, CI/CD, SQL, Agile, Angular, Spring, Hibernate, "
        "AWS, Azure, Docker, Kubernetes, SOAP, Azure DevOps, Rest Assured\n"
        "Preferred: Playwright"
    ),
    skills_required="",
    location="Remote",
)


def test_sdet_test_automation_and_database_validation_are_verified_contextual():
    auto = get_skill_evidence("Test Automation", SDET_RESUME)
    dbv = get_skill_evidence("Database Validation", SDET_RESUME)
    assert auto["resume_status"] == "VERIFIED"
    assert auto["classification"] == "VERIFIED_CONTEXTUAL"
    assert auto["match_type"] == "CONTEXTUAL"
    assert dbv["resume_status"] == "VERIFIED"
    assert dbv["classification"] == "VERIFIED_CONTEXTUAL"

    score = calculate_weighted_ats_score(SDET_RESUME, SDET_JOB)
    grouped = group_canonical_score_evidence(score, SDET_JOB.id)
    missing = {item["skill"] for item in grouped["missing_mandatory_skills"]}
    verified = {item["skill"] for item in grouped["verified_resume_skills"]}

    assert "Test Automation" in verified
    assert "Database Validation" in verified
    assert "Test Automation" not in missing
    assert "Database Validation" not in missing

    # Vendor technologies remain missing when unsupported.
    for skill in [
        "Angular", "Spring", "Hibernate", "AWS", "Azure", "Docker",
        "Kubernetes", "SOAP", "Azure DevOps", "Rest Assured",
    ]:
        evidence = get_skill_evidence(skill, SDET_RESUME)
        assert evidence["classification"] == "NOT_FOUND", skill
        assert evidence["resume_status"] == "MISSING", skill


def test_false_positives_related_is_not_equivalent():
    resume = """
Skills: Postman, Selenium, AWS, Jenkins, SQL, REST APIs
Experience
- Built Selenium UI suites
- Used Postman for API testing
- Deployed services on AWS with Jenkins CI/CD
"""
    assert get_skill_evidence("Rest Assured", resume)["classification"] == "NOT_FOUND"
    assert get_skill_evidence("Cypress", resume)["classification"] == "NOT_FOUND"
    assert get_skill_evidence("Azure", resume)["classification"] == "NOT_FOUND"
    assert get_skill_evidence("SOAP", resume)["classification"] == "NOT_FOUND"
    assert get_skill_evidence("Azure DevOps", resume)["classification"] == "NOT_FOUND"
    # SQL alone does not verify Database Validation without validation verbs.
    sql_only = get_skill_evidence("Database Validation", "Skills: SQL, Oracle, MySQL")
    assert sql_only["classification"] in {"POSSIBLE_EVIDENCE", "NOT_FOUND"}
    assert sql_only["resume_status"] != "VERIFIED"


def test_devops_infrastructure_as_code_contextual():
    resume = """
Alex Rivera
DevOps Engineer
Experience
CloudCo | DevOps Engineer | 2019-Present
- Provisioned AWS infrastructure using Terraform modules for multi-account networking
- Administered Kubernetes clusters and Docker workloads
"""
    evidence = get_skill_evidence("Infrastructure as Code", resume)
    assert evidence["resume_status"] == "VERIFIED"
    assert evidence["classification"] == "VERIFIED_CONTEXTUAL"
    assert get_skill_evidence("Azure", resume)["classification"] == "NOT_FOUND"


def test_java_rest_api_development_contextual():
    resume = """
Sam Patel
Java Backend Engineer
Experience
FinTech | Java Backend Engineer | 2021-Present
- Designed Spring Boot microservices exposing RESTful endpoints for payment processing
"""
    evidence = get_skill_evidence("REST API development", resume)
    assert normalize_skill_name("REST API development") == "rest apis"
    assert evidence["resume_status"] == "VERIFIED"
    assert evidence["classification"] in {"VERIFIED_CONTEXTUAL", "VERIFIED_EXACT"}
    assert get_skill_evidence("Hibernate", resume)["classification"] == "NOT_FOUND"


def test_python_and_data_engineer_contextual():
    python_resume = """
Priya Shah
Python Backend Engineer
- Built FastAPI services and REST APIs backed by PostgreSQL
"""
    assert get_skill_evidence("REST APIs", python_resume)["resume_status"] == "VERIFIED"

    data_resume = """
Riley Chen
Data Engineer
- Built PySpark pipelines to ingest, transform and load data into Snowflake
"""
    etl = get_skill_evidence("ETL pipeline development", data_resume)
    assert normalize_skill_name("ETL pipeline development") in {"etl", "etl pipeline development"}
    # Prefer synonym collapse to ETL when present.
    assert get_skill_evidence("ETL", data_resume)["resume_status"] == "VERIFIED"
    assert get_skill_evidence("ETL", data_resume)["classification"] == "VERIFIED_CONTEXTUAL"
    assert get_skill_evidence("Azure", data_resume)["classification"] == "NOT_FOUND"


def test_sql_validation_alias_resolves_to_database_validation():
    resume = "Executed SQL validation of transaction results and record updates"
    # Exact alias phrase should verify via synonym/alias path.
    evidence = get_skill_evidence("Database Validation", resume)
    assert evidence["resume_status"] == "VERIFIED"
    assert evidence["classification"] in {"VERIFIED_EXACT", "VERIFIED_CONTEXTUAL"}


def test_or_group_selenium_covers_playwright_cypress_alternatives():
    resume = """
Senior SDET
Technical Skills
Java, Selenium, TestNG
Professional Experience
Acme | SDET | 2020-Present
- Built Selenium WebDriver automation with TestNG
"""
    job = SimpleNamespace(
        id=202,
        title="QA Automation Engineer",
        description=(
            "Required Skills:\n"
            "- Selenium WebDriver, Playwright, OR Cypress\n"
            "- Java\n"
            "- TestNG\n"
        ),
        skills_required="",
        location="",
    )
    score = calculate_weighted_ats_score(resume, job)
    grouped = group_canonical_score_evidence(score, job.id)
    missing = {item["skill"] for item in grouped["missing_mandatory_skills"]}
    # Selenium satisfies the OR group — siblings must not remain mandatory-missing.
    assert "Playwright" not in missing
    assert "Cypress" not in missing
    assert frozenset({"Cypress", "Playwright", "Selenium"}) in {
        frozenset(group["skills"]) for group in score["requirements"]["or_groups"]
    }


def test_step2_and_scorer_share_canonical_sdet_evidence():
    before_like = get_skill_evidence("Test Automation", "Skills only: Java")
    assert before_like["classification"] == "NOT_FOUND"

    score = calculate_weighted_ats_score(SDET_RESUME, SDET_JOB)
    grouped = group_canonical_score_evidence(score, SDET_JOB.id)
    by_name = {item["skill"]: item for item in grouped["skills"]}
    assert by_name["Test Automation"]["resume_status"] == "VERIFIED"
    assert by_name["Database Validation"]["resume_status"] == "VERIFIED"
    assert by_name["Rest Assured"]["resume_status"] == "MISSING"
    # Scorer credit and Step 2 use the same requirement_evidence source.
    score_statuses = {
        item["skill"]: item["resume_status"]
        for item in score["requirement_evidence"]
    }
    assert score_statuses.get("Test Automation") == "VERIFIED"
    assert score_statuses.get("Database Validation") == "VERIFIED"
