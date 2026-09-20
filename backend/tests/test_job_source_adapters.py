"""Tests for app/job_sources/* adapters — company-centric behavior.

No real network calls — Greenhouse/Lever are exercised against explicit,
in-test `company_sources` lists (never real network), and their
no-fabrication behavior (empty when unconfigured and demo_mode is off) is
asserted directly. Workday/GenericFeed are always mock-capable; both must
stay silent unless demo_mode is explicitly on. Remotive's one real HTTP call
is mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.job_sources.generic_feed import GenericCareerFeedAdapter
from app.job_sources.greenhouse import GreenhouseAdapter
from app.job_sources.lever import LeverAdapter
from app.job_sources.remotive import RemotiveAdapter
from app.job_sources.workday import WorkdayAdapter
from app.services.job_normalization_service import NORMALIZED_JOB_FIELDS


def _assert_normalized_shape(job: dict):
    for field in NORMALIZED_JOB_FIELDS:
        assert field in job


# ---------------------------------------------------------------------------
# No silent mock fallback — the core new requirement
# ---------------------------------------------------------------------------

def test_greenhouse_adapter_produces_no_jobs_when_unconfigured_and_not_demo_mode():
    adapter = GreenhouseAdapter(company_sources=[], demo_mode=False)
    assert adapter.live_enabled is False
    assert adapter.fetch_jobs(limit=10) == []
    health = adapter.health_check()
    assert health.mode == "unavailable"
    assert health.healthy is False


def test_lever_adapter_produces_no_jobs_when_unconfigured_and_not_demo_mode():
    adapter = LeverAdapter(company_sources=[], demo_mode=False)
    assert adapter.fetch_jobs(limit=10) == []
    assert adapter.health_check().mode == "unavailable"


def test_workday_adapter_produces_no_jobs_when_not_demo_mode():
    adapter = WorkdayAdapter(demo_mode=False)
    assert adapter.fetch_jobs(limit=10) == []
    health = adapter.health_check()
    assert health.mode == "unavailable"
    assert health.healthy is False


def test_greenhouse_source_failure_does_not_fabricate_a_job(monkeypatch):
    adapter = GreenhouseAdapter(
        company_sources=[{"company_name": "Real Employer", "source_identifier": "real-employer", "company_logo_url": None, "career_page_url": None}],
        demo_mode=False,
    )
    with patch("requests.Session.get", side_effect=Exception("network down")):
        jobs = adapter.fetch_jobs(limit=10)
    assert jobs == []  # never invents a "Real Employer" job when the fetch failed


# ---------------------------------------------------------------------------
# Demo mode — mock catalog only, never claiming to be a real company
# ---------------------------------------------------------------------------

def test_workday_adapter_demo_mode_produces_mock_catalog():
    adapter = WorkdayAdapter(demo_mode=True)
    jobs = adapter.fetch_normalized_jobs(limit=5)
    assert len(jobs) == 5
    for job in jobs:
        _assert_normalized_shape(job)
        assert job["source_mode"] == "mock"
        assert job["direct_url"] is None
        assert job["application_url"] is None  # never fabricate an apply link


def test_generic_feed_requires_both_enabled_and_demo_mode(monkeypatch):
    monkeypatch.setenv("GENERIC_FEED_ENABLED", "true")
    assert GenericCareerFeedAdapter(demo_mode=False).fetch_jobs() == []
    assert len(GenericCareerFeedAdapter(demo_mode=True).fetch_jobs()) > 0
    monkeypatch.setenv("GENERIC_FEED_ENABLED", "false")
    assert GenericCareerFeedAdapter(demo_mode=True).fetch_jobs() == []


# ---------------------------------------------------------------------------
# Real, configured companies — the company name comes from configuration,
# never guessed from the connector's own slug/token
# ---------------------------------------------------------------------------

def test_greenhouse_adapter_live_mode_uses_configured_company_name():
    adapter = GreenhouseAdapter(
        company_sources=[{"company_name": "Example Employer", "source_identifier": "example-employer", "company_logo_url": "https://logos.example.test/e.png", "career_page_url": "https://example.test/careers"}],
        demo_mode=False,
    )
    assert adapter.live_enabled is True
    raw_job = {
        "id": 555,
        "title": "Senior SDET",
        "location": {"name": "Remote - US"},
        "content": "<p>We need a <b>Selenium</b> and TestNG expert.</p>",
        "departments": [{"name": "Quality Engineering"}],
        "updated_at": "2026-01-15T10:00:00Z",
        "absolute_url": "https://boards.greenhouse.io/example-employer/jobs/555",
        "_company_name": "Example Employer",
        "_company_logo_url": "https://logos.example.test/e.png",
        "_board_token": "example-employer",
    }
    normalized = adapter.normalize_job(raw_job)
    _assert_normalized_shape(normalized)
    assert normalized["company"] == "Example Employer"  # not "example-employer" / "Example-Employer"
    assert normalized["company_logo_url"] == "https://logos.example.test/e.png"
    assert normalized["source_mode"] == "live"
    assert normalized["source_job_id"] == "555"
    assert normalized["direct_url"] == "https://boards.greenhouse.io/example-employer/jobs/555"
    assert normalized["application_url"] == "https://boards.greenhouse.io/example-employer/jobs/555"


def test_greenhouse_adapter_fetch_jobs_isolates_per_company_failure():
    adapter = GreenhouseAdapter(
        company_sources=[
            {"company_name": "Failing Co", "source_identifier": "failing-co", "company_logo_url": None, "career_page_url": None},
            {"company_name": "Working Co", "source_identifier": "working-co", "company_logo_url": None, "career_page_url": None},
        ],
        demo_mode=False,
    )
    good_response = MagicMock()
    good_response.status_code = 200
    good_response.is_redirect = False
    good_response.raise_for_status.return_value = None
    good_response.json.return_value = {"jobs": [{"id": 1, "title": "Engineer", "absolute_url": "https://boards.greenhouse.io/working-co/jobs/1"}]}

    def fake_get(self, url, **kwargs):
        if "failing-co" in url:
            raise Exception("boom")
        return good_response

    with patch("requests.Session.get", fake_get):
        jobs = adapter.fetch_jobs(limit=10)
    assert len(jobs) == 1
    assert jobs[0]["_company_name"] == "Working Co"


def test_lever_adapter_live_mode_uses_configured_company_name():
    adapter = LeverAdapter(
        company_sources=[{"company_name": "Example Employer", "source_identifier": "example-employer", "company_logo_url": None, "career_page_url": None}],
        demo_mode=False,
    )
    raw_job = {
        "id": "abc123",
        "text": "Data Engineer",
        "categories": {"location": "Remote", "commitment": "Full-time", "team": "Data Platform"},
        "descriptionPlain": "Build Spark and Airflow pipelines on Snowflake.",
        "hostedUrl": "https://jobs.lever.co/example-employer/abc123",
        "createdAt": 1750000000000,
        "_company_name": "Example Employer",
        "_company_slug": "example-employer",
    }
    normalized = adapter.normalize_job(raw_job)
    _assert_normalized_shape(normalized)
    assert normalized["company"] == "Example Employer"
    assert normalized["title"] == "Data Engineer"
    assert normalized["application_url"] == "https://jobs.lever.co/example-employer/abc123"


def test_lever_adapter_prefers_explicit_apply_url_over_hosted_url():
    adapter = LeverAdapter(company_sources=[{"company_name": "Example Employer", "source_identifier": "example-employer"}], demo_mode=False)
    raw_job = {
        "id": "abc123", "text": "Data Engineer",
        "categories": {}, "descriptionPlain": "Build data pipelines.",
        "hostedUrl": "https://jobs.lever.co/example-employer/abc123",
        "applyUrl": "https://jobs.lever.co/example-employer/abc123/apply",
        "_company_name": "Example Employer", "_company_slug": "example-employer",
    }
    normalized = adapter.normalize_job(raw_job)
    assert normalized["application_url"] == "https://jobs.lever.co/example-employer/abc123/apply"


# ---------------------------------------------------------------------------
# Remotive — already a real, generic source; company comes straight from
# the API's own company_name field
# ---------------------------------------------------------------------------

def test_remotive_adapter_rejects_jobs_without_valid_url():
    adapter = RemotiveAdapter()
    assert adapter.normalize_job({"title": "X", "company_name": "Y", "url": ""}) is None
    assert adapter.normalize_job({"title": "X", "company_name": "Y", "url": "not-a-url"}) is None


def test_remotive_adapter_normalizes_real_shaped_payload():
    adapter = RemotiveAdapter()
    raw = {
        "id": 999,
        "title": "Java Full Stack Developer",
        "company_name": "Remote Co",
        "candidate_required_location": "USA",
        "job_type": "full_time",
        "description": "<p>Java Spring Boot React role.</p>",
        "url": "https://remotive.com/remote-jobs/software-dev/java-999",
        "tags": ["java", "spring boot"],
        "publication_date": "2026-01-01T00:00:00",
    }
    normalized = adapter.normalize_job(raw)
    _assert_normalized_shape(normalized)
    assert normalized["company"] == "Remote Co"
    assert normalized["source_job_id"] == "999"
    assert normalized["job_type"] == "Full Time"
    assert normalized["posted_at"] is not None
    # Regression: Remotive's API only ever exposes ITS OWN job page — never a
    # distinct employer/original-application URL. That must be classified as
    # a source listing, never as a direct application destination (which
    # previously made Apply silently open remotive.com instead of the
    # employer's real application page).
    assert normalized["source_url"] == raw["url"]
    assert normalized["direct_url"] is None
    assert normalized["application_url"] is None


def test_remotive_adapter_fetch_jobs_uses_mocked_http(monkeypatch):
    adapter = RemotiveAdapter()
    response = MagicMock()
    response.status_code = 200
    response.is_redirect = False
    response.json.return_value = {"jobs": [{"id": 1, "title": "T", "company_name": "C", "url": "https://remotive.com/remote-jobs/x-1"}]}
    with patch("requests.Session.get", return_value=response):
        jobs = adapter.fetch_jobs(limit=10)
    assert len(jobs) == 1
