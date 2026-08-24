"""Tests for app/agents/matching_agent.py."""

from __future__ import annotations

from types import SimpleNamespace

from app.agents.matching_agent import calculate_match_score, extract_keywords


def test_calculate_match_score_with_job_object(java_resume_text):
    job = SimpleNamespace(
        title="Java Developer",
        description="Java Spring Boot React PostgreSQL AWS Docker role.",
        skills_required="Java, Spring Boot",
        location="Remote", work_mode="Remote", job_type="Full-time",
    )
    result = calculate_match_score(java_resume_text, job)
    assert 0 <= result["match_percentage"] <= 100
    assert "matched_keywords" in result
    assert "recommendation" in result


def test_calculate_match_score_with_job_missing_optional_fields(java_resume_text):
    # hasattr(str, "title") is always True (str.title() is a builtin method), so the
    # "combined text string" branch documented in the docstring is unreachable through
    # a plain string in practice; every real caller (match_routes.py) passes a Job ORM
    # object, which is what this covers instead.
    job = SimpleNamespace(title="", description="", skills_required="")
    result = calculate_match_score(java_resume_text, job)
    assert 0 <= result["match_percentage"] <= 100


def test_extract_keywords_returns_lowercase_set(java_resume_text):
    keywords = extract_keywords(java_resume_text)
    assert isinstance(keywords, set)
    assert all(k == k.lower() for k in keywords)
    assert "java" in keywords
