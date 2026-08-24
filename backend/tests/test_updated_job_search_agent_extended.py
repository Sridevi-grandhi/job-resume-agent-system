"""Extended tests for app/agents/updated_job_search_agent.py.

tests/test_updated_job_search_agent.py already covers some of the relevance
pipeline; this file targets the remaining gaps: fetch_jobs_from_remotive's
HTTP error branches, clean_html, extract_valid_job_url edge cases, and the
full get_updated_jobs_from_remotive orchestration (mocking only the network
boundary — session.get — never the real Remotive API).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

import app.agents.updated_job_search_agent as agent


PREFERENCE = SimpleNamespace(
    job_title="Java Full Stack Developer", location="Remote", experience="5",
    work_mode="Remote", job_type="Full-time", skills="Java, Spring Boot, React",
)


@pytest.fixture(autouse=True)
def _reset_session():
    agent._SESSION = None
    yield
    agent._SESSION = None


def _mock_response(status_code=200, json_body=None, raise_json_error=False):
    response = MagicMock()
    response.status_code = status_code
    if raise_json_error:
        response.json.side_effect = ValueError("bad json")
    else:
        response.json.return_value = json_body if json_body is not None else {}
    return response


def test_fetch_jobs_success():
    response = _mock_response(200, {"jobs": [{"title": "Java Developer"}]})
    with patch.object(requests.Session, "get", return_value=response):
        jobs, error, status = agent.fetch_jobs_from_remotive("java developer")
    assert error is None
    assert status == 200
    assert jobs == [{"title": "Java Developer"}]


def test_fetch_jobs_timeout():
    with patch.object(requests.Session, "get", side_effect=requests.Timeout("timed out")):
        jobs, error, status = agent.fetch_jobs_from_remotive("java developer")
    assert jobs == []
    assert error == "timeout"
    assert status is None


def test_fetch_jobs_connection_error():
    with patch.object(requests.Session, "get", side_effect=requests.ConnectionError("refused")):
        jobs, error, status = agent.fetch_jobs_from_remotive("java developer")
    assert error == "connection_error"


def test_fetch_jobs_generic_request_exception():
    with patch.object(requests.Session, "get", side_effect=requests.RequestException("boom")):
        jobs, error, status = agent.fetch_jobs_from_remotive("java developer")
    assert error == "request_error"


def test_fetch_jobs_non_200_status():
    response = _mock_response(500)
    with patch.object(requests.Session, "get", return_value=response):
        jobs, error, status = agent.fetch_jobs_from_remotive("java developer")
    assert error == "http_500"
    assert status == 500


def test_fetch_jobs_invalid_json():
    response = _mock_response(200, raise_json_error=True)
    with patch.object(requests.Session, "get", return_value=response):
        jobs, error, status = agent.fetch_jobs_from_remotive("java developer")
    assert error == "invalid_json"


def test_fetch_jobs_missing_jobs_array():
    response = _mock_response(200, {"unexpected": "shape"})
    with patch.object(requests.Session, "get", return_value=response):
        jobs, error, status = agent.fetch_jobs_from_remotive("java developer")
    assert error == "missing_jobs"


def test_fetch_jobs_filters_non_dict_entries():
    response = _mock_response(200, {"jobs": [{"title": "Java"}, "not-a-dict", 42]})
    with patch.object(requests.Session, "get", return_value=response):
        jobs, error, status = agent.fetch_jobs_from_remotive("java developer")
    assert jobs == [{"title": "Java"}]


def test_get_session_reuses_singleton():
    first = agent._get_session()
    second = agent._get_session()
    assert first is second
    assert first.headers["User-Agent"] == agent.USER_AGENT


def test_clean_html_removes_script_and_style_and_converts_breaks():
    html = "<p>Java developer</p><br><script>evil()</script><style>.x{}</style><li>Spring Boot</li>"
    result = agent.clean_html(html)
    assert "evil()" not in result
    assert "Java developer" in result
    assert "Spring Boot" in result


def test_clean_html_handles_none_and_empty():
    assert agent.clean_html(None) == ""
    assert agent.clean_html("") == ""


def test_clean_html_unescapes_entities():
    result = agent.clean_html("Java &amp; Spring &nbsp; Boot")
    assert "&amp;" not in result
    assert "Java & Spring" in result


def test_extract_valid_job_url_prefers_url_over_job_url():
    job = {"url": "https://remotive.com/remote-jobs/software-dev/java-1", "job_url": "https://other.com/x"}
    assert agent.extract_valid_job_url(job) == job["url"]


def test_extract_valid_job_url_falls_back_to_job_url_field():
    job = {"job_url": "https://remotive.com/remote-jobs/software-dev/java-2"}
    assert agent.extract_valid_job_url(job) == job["job_url"]


def test_extract_valid_job_url_rejects_missing_url():
    assert agent.extract_valid_job_url({}) is None


def test_extract_valid_job_url_rejects_non_http_scheme():
    assert agent.extract_valid_job_url({"url": "ftp://example.com/job"}) is None


def test_extract_valid_job_url_rejects_example_com():
    assert agent.extract_valid_job_url({"url": "https://example.com/jobs/1"}) is None


def test_extract_valid_job_url_rejects_remotive_search_pages():
    assert agent.extract_valid_job_url({"url": "https://remotive.com/remote-jobs?search=java"}) is None
    assert agent.extract_valid_job_url({"url": "https://remotive.com/remote-jobs"}) is None
    assert agent.extract_valid_job_url({"url": "https://remotive.com/remote-jobs/categories/software-dev"}) is None


def test_extract_valid_job_url_accepts_remotive_detail_page():
    url = "https://remotive.com/remote-jobs/software-dev/java-developer-12345"
    assert agent.extract_valid_job_url({"url": url}) == url


def test_extract_valid_job_url_accepts_third_party_urls():
    url = "https://company.com/careers/java-developer"
    assert agent.extract_valid_job_url({"url": url}) == url


def test_normalize_remotive_job_returns_none_without_valid_url():
    assert agent.normalize_remotive_job({"title": "Java Dev", "company_name": "Acme"}, PREFERENCE) is None


def test_normalize_remotive_job_returns_none_without_title_or_company():
    job = {"url": "https://company.com/careers/1", "title": "", "company_name": "Acme"}
    assert agent.normalize_remotive_job(job, PREFERENCE) is None


def test_normalize_remotive_job_builds_expected_shape():
    job = {
        "url": "https://company.com/careers/java-dev",
        "title": "Java Full Stack Developer",
        "company_name": "Acme",
        "candidate_required_location": "USA",
        "description": "<p>Java Spring Boot React</p>",
        "tags": ["java", "spring boot"],
        "job_type": "full_time",
    }
    normalized = agent.normalize_remotive_job(job, PREFERENCE)
    assert normalized["title"] == "Java Full Stack Developer"
    assert normalized["company"] == "Acme"
    assert normalized["source"] == "Remotive API"
    assert normalized["skills_required"] == "java, spring boot"
    assert normalized["job_type"] == "Full-time"


def test_normalize_remotive_job_defaults_location_and_description():
    job = {
        "url": "https://company.com/careers/java-dev",
        "title": "Java Developer", "company_name": "Acme",
        "candidate_required_location": "", "description": "", "tags": [],
    }
    normalized = agent.normalize_remotive_job(job, PREFERENCE)
    assert normalized["location"] == "Remote"
    assert normalized["description"] == "No description provided."


REMOTIVE_JOB = {
    "url": "https://remotive.com/remote-jobs/software-dev/java-full-stack-1",
    "title": "Java Full Stack Developer",
    "company_name": "Remote Co",
    "candidate_required_location": "Anywhere",
    "description": "<p>Java Spring Boot React PostgreSQL AWS Docker</p>",
    "tags": ["java", "spring boot", "react"],
    "job_type": "full_time",
}


def test_get_updated_jobs_from_remotive_happy_path_accepts_relevant_job():
    with patch.object(agent, "fetch_jobs_from_remotive", return_value=([REMOTIVE_JOB], None, 200)):
        result = agent.get_updated_jobs_from_remotive(PREFERENCE, max_jobs=5)
    assert result["api_status"] == "ok"
    assert len(result["jobs"]) >= 1
    assert result["jobs"][0]["source"] == "Remotive API"


def test_get_updated_jobs_from_remotive_deduplicates_same_url_across_queries():
    with patch.object(agent, "fetch_jobs_from_remotive", return_value=([REMOTIVE_JOB, dict(REMOTIVE_JOB)], None, 200)):
        result = agent.get_updated_jobs_from_remotive(PREFERENCE, max_jobs=5)
    assert len(result["jobs"]) == 1


def test_get_updated_jobs_from_remotive_all_queries_error_reports_unavailable():
    with patch.object(agent, "fetch_jobs_from_remotive", return_value=([], "timeout", None)):
        result = agent.get_updated_jobs_from_remotive(PREFERENCE, max_jobs=5)
    assert result["api_status"] == "unavailable"
    assert result["jobs"] == []
    assert result["api_error"] == "timeout"


def test_get_updated_jobs_from_remotive_no_relevant_jobs_after_filtering():
    irrelevant_job = dict(REMOTIVE_JOB, title="Marketing Manager", tags=["marketing"], description="Marketing role.")
    with patch.object(agent, "fetch_jobs_from_remotive", return_value=([irrelevant_job], None, 200)):
        result = agent.get_updated_jobs_from_remotive(PREFERENCE, max_jobs=5)
    assert result["api_status"] == "no_relevant_jobs"
    assert result["jobs"] == []
    assert result["diagnostics"]["zero_count_stage"] is not None


def test_get_updated_jobs_from_remotive_invalid_url_job_is_rejected_and_sampled():
    bad_job = dict(REMOTIVE_JOB, url="https://example.com/fake")
    with patch.object(agent, "fetch_jobs_from_remotive", return_value=([bad_job], None, 200)):
        result = agent.get_updated_jobs_from_remotive(PREFERENCE, max_jobs=5)
    assert result["jobs"] == []
    assert any(s["reason"] == "invalid_url" for s in result["diagnostics"]["rejection_samples"])


def test_get_updated_jobs_from_remotive_stops_once_max_jobs_reached():
    jobs = [dict(REMOTIVE_JOB, url=f"https://company{i}.com/careers/java-{i}") for i in range(3)]
    with patch.object(agent, "fetch_jobs_from_remotive", return_value=(jobs, None, 200)):
        result = agent.get_updated_jobs_from_remotive(PREFERENCE, max_jobs=2)
    assert len(result["jobs"]) <= 2


def test_build_search_queries_uses_relevance_filter_aliases():
    queries = agent.build_search_queries(PREFERENCE)
    assert isinstance(queries, list)
    assert len(queries) > 0


def test_evaluate_remotive_job_returns_none_for_blank_preference_title():
    blank_pref = SimpleNamespace(job_title="", location="Remote", experience="", work_mode="Remote", job_type="", skills="")
    assert agent.evaluate_remotive_job(REMOTIVE_JOB, blank_pref, []) is None


def test_evaluate_remotive_job_returns_ranked_payload_for_relevant_job():
    aliases = agent.get_role_aliases(PREFERENCE.job_title)
    result = agent.evaluate_remotive_job(REMOTIVE_JOB, PREFERENCE, aliases)
    assert result is not None
    assert "relevance_tier" in result
    assert "relevance_score" in result
