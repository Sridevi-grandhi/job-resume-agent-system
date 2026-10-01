"""Shared pytest fixtures.

Test database: SQLite in-memory (StaticPool, single shared connection per test)
-- never the real DATABASE_URL from .env. The LLM is force-disabled by default
so no test can reach a real provider unless it explicitly re-enables and mocks
`requests.post`. Remotive / job-URL-import tests must mock `requests` the same way.
"""

from __future__ import annotations

import os
from datetime import time as dt_time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as database_module
from app.database import Base, get_db
import app.main as main_module

from app.models.user_model import User
from app.models.user_preference_model import UserPreference
from app.models.resume_model import Resume
from app.models.job_model import Job
from app.models.match_model import MatchScore
from app.models.optimized_resume_model import OptimizedResume
from app.models.email_log_model import EmailLog
from app.models.password_reset_model import PasswordResetToken  # noqa: F401


@pytest.fixture(autouse=True)
def _clear_employer_career_cache():
    """Career metadata is process-local. Tests must not inherit another test's employer."""
    from app.services.employer_career_service import clear_career_cache

    clear_career_cache()
    yield
    clear_career_cache()


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    """Force the LLM off for every test unless a test explicitly re-enables +
    mocks it. Prevents accidental real API calls (rule: no real LLM in tests)."""
    monkeypatch.setenv("LLM_PROVIDER", "none")
    monkeypatch.setenv("LLM_API_KEY", "")
    yield


@pytest.fixture(autouse=True)
def _no_real_jsearch_lookup(monkeypatch):
    """Force JSEARCH_API_KEY off for every test unless a test explicitly
    re-enables + mocks app.job_sources.jsearch.url_safety.safe_get.

    job_change_detection_service._attempt_jsearch_direct_lookup runs for ANY
    provider-listing job persisted through process_source_jobs — not just
    jsearch-adapter tests — so without this, a developer's real
    JSEARCH_API_KEY in backend/.env (loaded via load_dotenv at import time)
    would make a real outbound API call during Adzuna/Dice/JSearch discovery
    tests anywhere in the suite."""
    monkeypatch.delenv("JSEARCH_API_KEY", raising=False)
    yield


@pytest.fixture()
def test_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def db_session(test_engine):
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(test_engine, monkeypatch):
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    main_module.app.dependency_overrides[get_db] = override_get_db

    # Several routes queue BackgroundTasks (e.g. run_workflow_job) that open a
    # *fresh* session via `from app.database import SessionLocal` rather than
    # the get_db dependency. TestClient runs BackgroundTasks synchronously
    # within the request/response cycle, so without this patch those tasks
    # would hit the real production Postgres database during route tests.
    monkeypatch.setattr(database_module, "SessionLocal", TestingSessionLocal)

    from fastapi.testclient import TestClient

    # Deliberately NOT entered as `with TestClient(...) as c:` — that would run
    # the real lifespan (Base.metadata.create_all against the production Postgres
    # engine, ensure_ats_schema(), and a real APScheduler background thread).
    # BackgroundTasks still execute synchronously as part of each request/response
    # cycle either way, so route behavior under test is unaffected.
    test_client = TestClient(main_module.app)
    yield test_client
    main_module.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Domain fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def test_user(db_session):
    user = User(name="Jane Developer", email="jane@example.com", password="hashed-pw")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def second_user(db_session):
    user = User(name="Second User", email="second@example.com", password="hashed-pw")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def test_preferences(db_session, test_user):
    pref = UserPreference(
        user_id=test_user.id,
        job_title="Java Full Stack Developer",
        location="Remote",
        experience="5",
        work_mode="Remote",
        job_type="Full-time",
        skills="Java, Spring Boot, React",
        schedule_time=dt_time(8, 0),
        timezone="America/Chicago",
    )
    db_session.add(pref)
    db_session.commit()
    db_session.refresh(pref)
    return pref


JAVA_RESUME_TEXT = """
Jane Developer
jane@example.com | 555-111-2222 | linkedin.com/in/jane | github.com/jane

Professional Summary
Java Full Stack Developer with experience building enterprise applications.

Technical Skills
Java, Spring Boot, React, PostgreSQL, AWS, Docker, Jenkins, Agile

Professional Experience
Acme Corp | Java Full Stack Developer | Jan 2018 - Present
- Developed REST APIs using Java and Spring Boot for account workflows.
- Built React components for customer portals.
- Deployed services with Docker and Jenkins on AWS.

Projects
Payments Portal
- Implemented transaction workflows with Java and PostgreSQL.

Education
B.S. Computer Science, Example University

Certifications
AWS Cloud Practitioner
""".strip()


PYTHON_RESUME_TEXT = """
Jane Developer
jane@example.com | 555-111-2222

Professional Summary
Python Backend Developer with Django and Flask experience.

Technical Skills
Python, Django, Flask, PostgreSQL, Celery, SQLAlchemy

Professional Experience
DataCo | Python Developer | Jan 2019 - Present
- Built REST APIs using Python and Django.
- Wrote background jobs with Celery.

Education
B.S. Computer Science, Example University
""".strip()


@pytest.fixture()
def java_resume_text():
    return JAVA_RESUME_TEXT


@pytest.fixture()
def python_resume_text():
    return PYTHON_RESUME_TEXT


@pytest.fixture()
def test_resume(db_session, test_user):
    resume = Resume(
        user_id=test_user.id,
        original_file_name="jane_resume.txt",
        stored_file_path="/tmp/jane_resume.txt",
        file_type="txt",
        parsed_text=JAVA_RESUME_TEXT,
    )
    db_session.add(resume)
    db_session.commit()
    db_session.refresh(resume)
    return resume


@pytest.fixture()
def java_job(db_session, test_user):
    job = Job(
        user_id=test_user.id,
        title="Java Full Stack Developer",
        company="Example Co",
        location="Remote",
        work_mode="Remote",
        job_type="Full-time",
        description=(
            "We need a Java Full Stack Developer with Spring Boot, React, PostgreSQL, "
            "AWS, and Docker experience building REST APIs for enterprise payments. "
            "5+ years of experience required."
        ),
        skills_required="Java, Spring Boot, React, PostgreSQL, AWS, Docker",
        source="User Selected Job",
        job_url="https://example.com/jobs/123",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


@pytest.fixture()
def sdet_job(db_session, test_user):
    job = Job(
        user_id=test_user.id,
        title="SDET Automation Engineer",
        company="Quality Corp",
        location="Remote",
        work_mode="Remote",
        job_type="Full-time",
        description=(
            "We need an SDET with Selenium, Cypress, TestNG, and CI/CD experience "
            "building automated test frameworks."
        ),
        skills_required="Selenium, Cypress, TestNG, CI/CD",
        source="User Selected Job",
        job_url="https://example.com/jobs/456",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


@pytest.fixture()
def generic_search_job(db_session, test_user):
    job = Job(
        user_id=test_user.id,
        title="Java Jobs",
        company="LinkedIn",
        location="Remote",
        work_mode="Remote",
        job_type="Full-time",
        description="Search Java openings on LinkedIn based on your preferred location and skills.",
        skills_required="",
        source="LinkedIn Jobs",
        job_url="https://www.linkedin.com/jobs/search/?keywords=Java",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


@pytest.fixture()
def mock_remotive_response():
    return {
        "jobs": [
            {
                "id": 111,
                "title": "Java Full Stack Developer",
                "company_name": "Remote Co",
                "candidate_required_location": "Anywhere",
                "job_type": "full_time",
                "description": "<p>Java Spring Boot React PostgreSQL AWS Docker</p>",
                "url": "https://remotive.com/remote-jobs/software-dev/java-fullstack-111",
                "tags": ["java", "spring boot", "react"],
                "publication_date": "2026-01-01T00:00:00",
            }
        ]
    }


@pytest.fixture()
def mock_llm_response():
    return {
        "plain_text": "Optimized resume plain text body.",
        "sections": [],
        "generation_mode": "llm",
        "generation_warnings": [],
    }
