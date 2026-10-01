"""Tests for the JSearch broad-provider adapter — app/job_sources/jsearch.py.

No real network/DNS is ever touched: `url_safety.safe_get` (which resolves
DNS before every call) is mocked directly, so these tests do not depend on
a live JSearch API or on this machine's internet connectivity — the
required no-live-dependency guarantee for the whole broad-provider feature.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from app.job_sources.jsearch import JSearchAdapter, _build_query_string, _pick_apply_urls, lookup_direct_apply
from app.models.discovered_job_model import DiscoveredJob
from app.services import job_change_detection_service as change_detection
from app.services.job_normalization_service import NORMALIZED_JOB_FIELDS, resolve_application_destination

SAMPLE_QUERY = {"job_title": "SDET Automation Engineer", "location": "Austin, TX", "work_mode": "Hybrid", "job_type": "Full-time"}

REAL_STYLE_JOB = {
    "job_id": "abc123xyz",
    "job_title": "Senior SDET Automation Engineer",
    "employer_name": "Capital One",
    "employer_logo": "https://logos.example.com/capitalone.png",
    "employer_website": "https://www.capitalone.com",
    "job_publisher": "Capital One Careers",
    "job_employment_type": "FULLTIME",
    "job_description": "<p>Build automated test frameworks using Selenium and Java.</p>",
    "job_city": "Austin",
    "job_state": "TX",
    "job_country": "US",
    "job_is_remote": False,
    "job_posted_at_timestamp": 1750000000,
    "job_apply_link": "https://www.capitalone.com/careers/jobs/12345",
    "job_apply_is_direct": True,
    "apply_options": [
        {"publisher": "Capital One Careers", "apply_link": "https://www.capitalone.com/careers/jobs/12345", "is_direct": True},
        {"publisher": "LinkedIn", "apply_link": "https://www.linkedin.com/jobs/view/12345", "is_direct": False},
    ],
}


def _response(status_code=200, payload=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload or {}
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status_code}")
    else:
        response.raise_for_status.side_effect = None
    return response


def _real_payload(jobs, cursor=None):
    """The REAL search-v2 response shape, verified against a live call:
    top-level {"status", "request_id", "parameters", "data"}, where `data`
    is an OBJECT — {"jobs": [...], "cursor": "..."} — not a bare list."""
    return {
        "status": "OK",
        "request_id": "test-request-id",
        "parameters": {},
        "data": {"jobs": jobs, "cursor": cursor},
    }


# ---------------------------------------------------------------------------
# Query building — dynamic, never hardcoded
# ---------------------------------------------------------------------------

def test_query_string_built_from_preference_spec_not_hardcoded():
    assert _build_query_string({"job_title": "Data Engineer", "location": "Chicago, IL"}) == "Data Engineer jobs in Chicago, IL"
    assert _build_query_string({"job_title": "DevOps Engineer", "location": "Remote"}) == "DevOps Engineer jobs"
    assert _build_query_string({"job_title": ""}) == ""


# ---------------------------------------------------------------------------
# Missing API key — must never crash the backend
# ---------------------------------------------------------------------------

def test_missing_api_key_returns_no_jobs_without_crashing(monkeypatch):
    monkeypatch.delenv("JSEARCH_API_KEY", raising=False)
    adapter = JSearchAdapter(preference_queries=[SAMPLE_QUERY])
    assert adapter.is_configured is False
    assert adapter.fetch_jobs(limit=10) == []
    health = adapter.health_check()
    assert health.mode == "unavailable"
    assert health.healthy is False
    assert health.error == "JSEARCH_NOT_CONFIGURED"


def test_no_preference_queries_returns_no_jobs(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    adapter = JSearchAdapter(preference_queries=[])
    assert adapter.fetch_jobs(limit=10) == []


# ---------------------------------------------------------------------------
# Real-style employer results + normalization field mapping
# ---------------------------------------------------------------------------

def test_fetch_jobs_returns_raw_records_when_configured(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    adapter = JSearchAdapter(preference_queries=[SAMPLE_QUERY])
    with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_response(200, _real_payload([REAL_STYLE_JOB]))):
        jobs = adapter.fetch_jobs(limit=10)
    assert len(jobs) == 1
    assert jobs[0]["employer_name"] == "Capital One"


def test_fetch_jobs_parses_real_search_v2_nested_data_jobs_shape(monkeypatch):
    """Root-cause regression test for the live-verified bug: `data` is an
    object with a `jobs` list, not itself a list of jobs."""
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    adapter = JSearchAdapter(preference_queries=[SAMPLE_QUERY])
    payload = _real_payload(
        [
            {**REAL_STYLE_JOB, "job_id": "job-1", "employer_name": "Capgemini", "job_title": "Full Stack Developer - Java/ React"},
            {**REAL_STYLE_JOB, "job_id": "job-2", "employer_name": "Allata", "job_title": "FullStack Developer (Java + Vue)"},
        ],
        cursor=None,
    )
    with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_response(200, payload)):
        jobs = adapter.fetch_jobs(limit=10)
    assert len(jobs) == 2
    employers = {job["employer_name"] for job in jobs}
    assert employers == {"Capgemini", "Allata"}


def test_legacy_bare_list_data_shape_still_supported(monkeypatch):
    """Backward compatibility only — not the primary real shape."""
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    adapter = JSearchAdapter(preference_queries=[SAMPLE_QUERY])
    with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_response(200, {"data": [REAL_STYLE_JOB]})):
        jobs = adapter.fetch_jobs(limit=10)
    assert len(jobs) == 1


def test_malformed_data_shapes_handled_safely_without_crashing(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    for malformed in (
        {"status": "OK", "data": None},
        {"status": "OK", "data": "unexpected string"},
        {"status": "OK", "data": {"jobs": "not-a-list", "cursor": "x"}},
        {"status": "OK", "data": {}},
        {"status": "ERROR"},
        {},
        "not even a dict",
    ):
        adapter = JSearchAdapter(preference_queries=[SAMPLE_QUERY])
        with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_response(200, malformed)):
            jobs = adapter.fetch_jobs(limit=10)
        assert jobs == []


def test_normalize_job_maps_real_jsearch_fields_into_normalized_shape():
    adapter = JSearchAdapter()
    normalized = adapter.normalize_job(REAL_STYLE_JOB)
    for field in NORMALIZED_JOB_FIELDS:
        assert field in normalized
    assert normalized["source"] == "jsearch"
    assert normalized["company"] == "Capital One"
    assert normalized["title"] == "Senior SDET Automation Engineer"
    assert normalized["company_logo_url"] == "https://logos.example.com/capitalone.png"
    assert normalized["location"] == "Austin, TX"
    assert normalized["work_mode"] == "Onsite"
    assert "Selenium" in normalized["description"]
    assert "<p>" not in normalized["description"]
    # source_job_id is deliberately never populated from JSearch's job_id —
    # see the dedup-stability tests below.
    assert normalized["source_job_id"] is None
    assert normalized["posted_at"] is not None


def test_normalize_job_never_uses_raw_job_id_as_source_job_id():
    """Regression for the live-verified bug: JSearch's job_id changes
    between independent requests for the same real posting, so using it as
    a dedup key silently duplicated every job on every poll cycle instead
    of ever recognizing a repeat. Confirmed by fetching the same live query
    twice and observing two different job_id values for the same title +
    employer."""
    adapter = JSearchAdapter()
    first = adapter.normalize_job({**REAL_STYLE_JOB, "job_id": "session-token-aaa"})
    second = adapter.normalize_job({**REAL_STYLE_JOB, "job_id": "session-token-bbb-totally-different"})
    assert first["source_job_id"] is None
    assert second["source_job_id"] is None
    # Same real posting (title+company+location) -> same fallback identity,
    # regardless of job_id ever having differed.
    from app.services.job_normalization_service import compute_fingerprint
    assert compute_fingerprint(first) == compute_fingerprint(second)


def test_normalize_job_marks_remote_work_mode():
    adapter = JSearchAdapter()
    raw = {**REAL_STYLE_JOB, "job_is_remote": True, "job_city": "", "job_state": "", "job_country": "US"}
    normalized = adapter.normalize_job(raw)
    assert normalized["work_mode"] == "Remote"


def test_normalize_job_rejects_record_missing_title_or_employer():
    adapter = JSearchAdapter()
    assert adapter.normalize_job({**REAL_STYLE_JOB, "job_title": ""}) is None
    assert adapter.normalize_job({**REAL_STYLE_JOB, "employer_name": ""}) is None


def test_normalize_job_never_fabricates_a_missing_description():
    adapter = JSearchAdapter()
    raw = {**REAL_STYLE_JOB, "job_description": ""}
    normalized = adapter.normalize_job(raw)
    # build_normalized_job itself rejects too-short descriptions — either way
    # nothing invented is present.
    assert normalized is None or normalized["description"] == ""


# ---------------------------------------------------------------------------
# apply_options domain heuristics — never blindly takes the first URL
# ---------------------------------------------------------------------------

def test_pick_apply_urls_prefers_employer_domain_matched_direct_option():
    application_url, direct_url, source_url = _pick_apply_urls(REAL_STYLE_JOB)
    assert application_url == "https://www.capitalone.com/careers/jobs/12345"
    assert source_url == "https://www.linkedin.com/jobs/view/12345"


def test_pick_apply_urls_falls_back_to_any_direct_option_without_employer_domain_match():
    raw = {
        "employer_website": "https://www.example-employer.com",
        "job_apply_link": "https://ats.example-vendor.com/apply/999",
        "job_apply_is_direct": True,
        "apply_options": [],
    }
    application_url, direct_url, source_url = _pick_apply_urls(raw)
    assert application_url == "https://ats.example-vendor.com/apply/999"


def test_pick_apply_urls_preserves_listing_when_nothing_is_verified_direct():
    """Part 17: is_direct=false must not be treated as invalid — the listing
    URL is still preserved (as the AGGREGATOR_LISTING-tier fallback), never
    discarded."""
    raw = {
        "employer_website": None,
        "job_apply_link": "https://www.indeed.com/viewjob?jk=999",
        "job_apply_is_direct": False,
        "apply_options": [],
    }
    application_url, direct_url, source_url = _pick_apply_urls(raw)
    assert application_url is None
    assert source_url == "https://www.indeed.com/viewjob?jk=999"


def test_normalize_job_never_fabricates_application_url_when_none_verified():
    adapter = JSearchAdapter()
    raw = {**REAL_STYLE_JOB, "job_apply_link": "https://www.indeed.com/viewjob?jk=1", "job_apply_is_direct": False, "apply_options": []}
    normalized = adapter.normalize_job(raw)
    assert normalized["application_url"] is None
    assert normalized["source_url"] == "https://www.indeed.com/viewjob?jk=1"


# ---------------------------------------------------------------------------
# Root-cause regression: live-verified production bug — JSearch's own
# job_apply_is_direct was False for every stored job, even genuine employer
# career pages and ATS-hosted application pages, so every job landed in
# source_url only and the frontend showed "View Listing" for zero real Apply
# buttons out of 45 live jobs. Classification must be semantic (URL domain),
# not dependent on that flag. None of these hardcode a specific employer —
# each uses a generic/fictional company name and a well-known ATS domain.
# ---------------------------------------------------------------------------

def test_pick_apply_urls_promotes_employer_domain_even_when_is_direct_false():
    raw = {
        "employer_website": "https://www.example-corp.com",
        "employer_name": "Example Corp",
        "job_apply_link": "https://careers.example-corp.com/job/123",
        "job_apply_is_direct": False,
        "apply_options": [],
    }
    application_url, direct_url, source_url = _pick_apply_urls(raw)
    assert application_url == "https://careers.example-corp.com/job/123"
    assert direct_url == "https://careers.example-corp.com/job/123"


def test_pick_apply_urls_promotes_alternate_career_domain_even_when_is_direct_false():
    """Case A variant: a distinct-but-branded career domain, not a literal
    subdomain — still recognized generically via employer_website + a
    generic career-suffix word, no specific employer hardcoded."""
    raw = {
        "employer_website": "https://www.acmewidgets.com",
        "employer_name": "Acme Widgets",
        "job_apply_link": "https://www.acmewidgetscareers.com/job/456",
        "job_apply_is_direct": False,
        "apply_options": [],
    }
    application_url, _direct_url, _source_url = _pick_apply_urls(raw)
    assert application_url == "https://www.acmewidgetscareers.com/job/456"


def test_pick_apply_urls_promotes_icims_hosted_url_even_when_is_direct_false():
    raw = {
        "employer_website": None,
        "employer_name": "Some Employer",
        "job_apply_link": "https://careers-someemployer.icims.com/jobs/42/job",
        "job_apply_is_direct": False,
        "apply_options": [],
    }
    application_url, direct_url, source_url = _pick_apply_urls(raw)
    assert application_url == "https://careers-someemployer.icims.com/jobs/42/job"
    assert direct_url == application_url


def test_pick_apply_urls_never_promotes_aggregator_even_when_is_direct_true():
    """A known aggregator domain must never become the Apply destination,
    even if a provider mistakenly flags it as a direct link — the flag is
    corroborating evidence, never proof, for a recognized listing site."""
    raw = {
        "employer_website": "https://www.example-corp.com",
        "employer_name": "Example Corp",
        "job_apply_link": "https://www.linkedin.com/jobs/view/999",
        "job_apply_is_direct": True,
        "apply_options": [],
    }
    application_url, _direct_url, source_url = _pick_apply_urls(raw)
    assert application_url is None
    assert source_url == "https://www.linkedin.com/jobs/view/999"


def test_apply_resolution_matrix_employer_ats_aggregator_unsafe():
    """The exact required test matrix: employer career URL -> DIRECT_EMPLOYER
    / Apply, iCIMS -> DIRECT_ATS / Apply, LinkedIn/Indeed/Dice ->
    AGGREGATOR_LISTING / View Listing, unsafe URL -> rejected/fallback."""
    employer_case = _resolve({
        **REAL_STYLE_JOB, "employer_website": "https://www.example-corp.com", "employer_name": "Example Corp",
        "job_apply_link": "https://careers.example-corp.com/job/1", "job_apply_is_direct": False, "apply_options": [],
    })
    assert employer_case["type"] == "DIRECT_EMPLOYER"
    assert employer_case["is_direct"] is True

    icims_case = _resolve({
        **REAL_STYLE_JOB, "employer_website": None,
        "job_apply_link": "https://careers-someemployer.icims.com/jobs/1/job",
        "job_apply_is_direct": False, "apply_options": [],
    })
    assert icims_case["type"] == "DIRECT_ATS"
    assert icims_case["is_direct"] is True

    for aggregator_url in (
        "https://www.linkedin.com/jobs/view/12345",
        "https://www.indeed.com/viewjob?jk=abc123",
        "https://www.dice.com/job-detail/abc-123",
    ):
        aggregator_case = _resolve({
            **REAL_STYLE_JOB, "employer_website": None,
            "job_apply_link": aggregator_url, "job_apply_is_direct": False, "apply_options": [],
        })
        assert aggregator_case["type"] == "AGGREGATOR_LISTING"
        assert aggregator_case["is_direct"] is False

    unsafe_case = _resolve({
        **REAL_STYLE_JOB, "employer_website": "https://www.example-corp.com",
        "job_apply_link": "javascript:alert(1)", "job_apply_is_direct": True,
        "apply_options": [{"publisher": "Indeed", "apply_link": "https://www.indeed.com/viewjob?jk=safe1", "is_direct": False}],
    })
    assert unsafe_case["url"] == "https://www.indeed.com/viewjob?jk=safe1"
    assert unsafe_case["type"] == "AGGREGATOR_LISTING"


# ---------------------------------------------------------------------------
# End-to-end Apply resolution matrix for real-shaped JSearch apply_options —
# Part 9: employer-domain direct, Greenhouse/Lever/Workday-hosted ATS,
# provider-only listing (Indeed/LinkedIn), missing application_url, unsafe URL.
# ---------------------------------------------------------------------------

def _resolve(raw):
    adapter = JSearchAdapter()
    normalized = adapter.normalize_job(raw)
    return resolve_application_destination(normalized)


def test_apply_resolution_employer_domain_direct_url():
    result = _resolve(REAL_STYLE_JOB)
    assert result["type"] == "DIRECT_EMPLOYER"
    assert result["is_direct"] is True
    assert result["url"] == "https://www.capitalone.com/careers/jobs/12345"


def test_apply_resolution_greenhouse_hosted_url_via_jsearch():
    raw = {
        **REAL_STYLE_JOB, "employer_website": None,
        "job_apply_link": "https://boards.greenhouse.io/example-employer/jobs/999",
        "job_apply_is_direct": True, "apply_options": [],
    }
    result = _resolve(raw)
    assert result["type"] == "DIRECT_ATS"
    assert result["is_direct"] is True


def test_apply_resolution_lever_hosted_url_via_jsearch():
    raw = {
        **REAL_STYLE_JOB, "employer_website": None,
        "job_apply_link": "https://jobs.lever.co/example-employer/999",
        "job_apply_is_direct": True, "apply_options": [],
    }
    result = _resolve(raw)
    assert result["type"] == "DIRECT_ATS"
    assert result["is_direct"] is True


def test_apply_resolution_workday_hosted_url_via_jsearch():
    raw = {
        **REAL_STYLE_JOB, "employer_website": None,
        "job_apply_link": "https://example.wd1.myworkdayjobs.com/en-US/careers/job/999",
        "job_apply_is_direct": True, "apply_options": [],
    }
    result = _resolve(raw)
    assert result["type"] == "DIRECT_ATS"
    assert result["is_direct"] is True


def test_apply_resolution_indeed_linkedin_provider_only_listing():
    raw = {
        **REAL_STYLE_JOB, "employer_website": None,
        "job_apply_link": None, "job_apply_is_direct": False,
        "apply_options": [
            {"publisher": "Indeed", "apply_link": "https://www.indeed.com/viewjob?jk=abc123", "is_direct": False},
            {"publisher": "LinkedIn", "apply_link": "https://www.linkedin.com/jobs/view/abc123", "is_direct": False},
        ],
    }
    result = _resolve(raw)
    assert result["type"] == "AGGREGATOR_LISTING"
    assert result["is_direct"] is False
    assert result["url"] == "https://www.indeed.com/viewjob?jk=abc123"


def test_apply_resolution_missing_application_url_entirely():
    raw = {
        **REAL_STYLE_JOB, "employer_website": None,
        "job_apply_link": None, "job_apply_is_direct": False, "apply_options": [],
    }
    result = _resolve(raw)
    assert result["url"] is None
    assert result["type"] is None
    assert result["is_direct"] is False


def test_apply_resolution_unsafe_url_rejected_falls_through():
    raw = {
        **REAL_STYLE_JOB, "employer_website": "https://www.capitalone.com",
        "job_apply_link": "javascript:alert(1)", "job_apply_is_direct": True,
        "apply_options": [
            {"publisher": "Indeed", "apply_link": "https://www.indeed.com/viewjob?jk=safe1", "is_direct": False},
        ],
    }
    result = _resolve(raw)
    # The unsafe direct option is skipped. The provider-supplied employer
    # website is the official career destination, ahead of the Indeed listing.
    assert result["url"] == "https://www.capitalone.com"
    assert result["type"] == "DIRECT_EMPLOYER"
    assert result["is_direct"] is True


# ---------------------------------------------------------------------------
# Timeout / HTTP failure / rate-limit — bounded retry, safe error handling
# ---------------------------------------------------------------------------

def test_provider_timeout_does_not_crash_and_yields_no_jobs(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    monkeypatch.setenv("JSEARCH_MAX_RETRIES", "0")
    adapter = JSearchAdapter(preference_queries=[SAMPLE_QUERY])
    with patch("app.job_sources.jsearch.url_safety.safe_get", side_effect=requests.Timeout("timed out")):
        jobs = adapter.fetch_jobs(limit=10)
    assert jobs == []
    health = adapter.health_check()
    assert health.healthy is False
    assert health.error == "Timeout"


def test_provider_http_failure_does_not_crash_and_yields_no_jobs(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    monkeypatch.setenv("JSEARCH_MAX_RETRIES", "0")
    adapter = JSearchAdapter(preference_queries=[SAMPLE_QUERY])
    with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_response(503)):
        jobs = adapter.fetch_jobs(limit=10)
    assert jobs == []


def test_provider_retries_bounded_number_of_times_then_gives_up(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    monkeypatch.setenv("JSEARCH_MAX_RETRIES", "2")
    adapter = JSearchAdapter(preference_queries=[SAMPLE_QUERY])
    with patch("app.job_sources.jsearch.url_safety.safe_get", side_effect=requests.ConnectionError("down")) as mocked, \
         patch("app.job_sources.jsearch.time.sleep"):
        jobs = adapter.fetch_jobs(limit=10)
    assert jobs == []
    # 1 initial attempt + 2 retries = 3 calls, never unbounded
    assert mocked.call_count == 3


def test_rate_limit_response_stops_pagination_without_raising(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    monkeypatch.setenv("JSEARCH_MAX_PAGES_PER_QUERY", "3")
    adapter = JSearchAdapter(preference_queries=[SAMPLE_QUERY])
    with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_response(429)) as mocked:
        jobs = adapter.fetch_jobs(limit=10)
    assert jobs == []
    assert mocked.call_count == 1  # one query, page 1 rate-limited -> stop, no further pages


def test_one_query_failing_does_not_prevent_another_query_from_succeeding(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    monkeypatch.setenv("JSEARCH_MAX_RETRIES", "0")
    queries = [SAMPLE_QUERY, {"job_title": "Data Engineer", "location": "Chicago, IL", "work_mode": "Remote", "job_type": "Full-time"}]
    adapter = JSearchAdapter(preference_queries=queries)

    responses = [requests.ConnectionError("down"), _response(200, _real_payload([REAL_STYLE_JOB]))]

    def side_effect(*args, **kwargs):
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    with patch("app.job_sources.jsearch.url_safety.safe_get", side_effect=side_effect):
        jobs = adapter.fetch_jobs(limit=10)
    assert len(jobs) == 1


# ---------------------------------------------------------------------------
# Cursor pagination — bounded, never page-number based
# ---------------------------------------------------------------------------

def test_pagination_stops_when_a_page_returns_no_results(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    monkeypatch.setenv("JSEARCH_MAX_PAGES_PER_QUERY", "3")
    adapter = JSearchAdapter(preference_queries=[SAMPLE_QUERY])
    responses = [_response(200, _real_payload([REAL_STYLE_JOB], cursor="cursor-1")), _response(200, _real_payload([]))]

    def side_effect(*args, **kwargs):
        return responses.pop(0) if responses else _response(200, _real_payload([]))

    with patch("app.job_sources.jsearch.url_safety.safe_get", side_effect=side_effect) as mocked:
        jobs = adapter.fetch_jobs(limit=100)
    assert len(jobs) == 1
    assert mocked.call_count == 2  # page 1 (results + cursor), page 2 (empty) -> stop


def test_pagination_stops_when_cursor_is_missing_even_with_pages_remaining(monkeypatch):
    """Missing cursor must stop pagination even though more pages would
    otherwise be allowed — a page limit doesn't override an absent cursor."""
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    monkeypatch.setenv("JSEARCH_MAX_PAGES_PER_QUERY", "3")
    adapter = JSearchAdapter(preference_queries=[SAMPLE_QUERY])
    with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_response(200, _real_payload([REAL_STYLE_JOB], cursor=None))) as mocked:
        jobs = adapter.fetch_jobs(limit=100)
    assert len(jobs) == 1
    assert mocked.call_count == 1  # no cursor returned -> never asks for a second page


def test_pagination_stops_when_cursor_is_an_empty_string(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    monkeypatch.setenv("JSEARCH_MAX_PAGES_PER_QUERY", "3")
    adapter = JSearchAdapter(preference_queries=[SAMPLE_QUERY])
    with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_response(200, _real_payload([REAL_STYLE_JOB], cursor="   "))) as mocked:
        adapter.fetch_jobs(limit=100)
    assert mocked.call_count == 1


def test_second_request_sends_the_cursor_returned_by_the_first(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    monkeypatch.setenv("JSEARCH_MAX_PAGES_PER_QUERY", "3")
    adapter = JSearchAdapter(preference_queries=[SAMPLE_QUERY])
    responses = [
        _response(200, _real_payload([{**REAL_STYLE_JOB, "job_id": "job-1"}], cursor="next-page-cursor")),
        _response(200, _real_payload([{**REAL_STYLE_JOB, "job_id": "job-2"}], cursor=None)),
    ]

    def side_effect(*args, **kwargs):
        return responses.pop(0)

    with patch("app.job_sources.jsearch.url_safety.safe_get", side_effect=side_effect) as mocked:
        jobs = adapter.fetch_jobs(limit=100)

    assert len(jobs) == 2
    assert mocked.call_count == 2
    first_call_params = mocked.call_args_list[0].kwargs["params"]
    second_call_params = mocked.call_args_list[1].kwargs["params"]
    assert "cursor" not in first_call_params
    assert "page" not in first_call_params  # page-number pagination must not be used
    assert second_call_params["cursor"] == "next-page-cursor"
    assert second_call_params["query"] == first_call_params["query"]  # same query, per spec


def test_max_pages_per_query_is_respected_even_when_cursor_keeps_coming(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    monkeypatch.setenv("JSEARCH_MAX_PAGES_PER_QUERY", "2")
    adapter = JSearchAdapter(preference_queries=[SAMPLE_QUERY])
    # The API keeps offering another cursor forever — the adapter must still
    # stop at JSEARCH_MAX_PAGES_PER_QUERY, never fetch unlimited pages.
    with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_response(200, _real_payload([REAL_STYLE_JOB], cursor="always-more"))) as mocked:
        adapter.fetch_jobs(limit=1000)
    assert mocked.call_count == 2


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------

def test_health_check_reflects_last_fetch_outcome(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    adapter = JSearchAdapter(preference_queries=[SAMPLE_QUERY])
    with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_response(200, _real_payload([REAL_STYLE_JOB]))):
        adapter.fetch_jobs(limit=10)
    health = adapter.health_check()
    assert health.healthy is True
    assert health.mode == "live"


# ---------------------------------------------------------------------------
# Dedup + update detection for JSearch-sourced jobs — end-to-end through the
# same shared job_change_detection_service every other source uses.
# ---------------------------------------------------------------------------

def test_jsearch_sourced_job_deduplicates_across_repeated_polls(db_session):
    adapter = JSearchAdapter()
    normalized = adapter.normalize_job(REAL_STYLE_JOB)

    first = change_detection.process_source_jobs(db_session, "jsearch", [normalized])
    second = change_detection.process_source_jobs(db_session, "jsearch", [normalized])

    assert first["new"] == 1
    assert second["new"] == 0
    assert second["duplicates"] == 1
    assert db_session.query(DiscoveredJob).filter_by(source="jsearch").count() == 1


def test_jsearch_dedup_survives_a_different_job_id_on_each_poll(db_session):
    """Root-cause regression for the live-verified production bug: a live
    poll cycle re-run against the real API produced a BRAND NEW row for the
    same real posting every single time (30 new jobs on run 1, another 30
    new — not 30 duplicates — on run 2), because JSearch's own job_id is not
    stable across independent requests. Simulated here by normalizing the
    "same" real posting from two raw payloads that differ ONLY in job_id."""
    adapter = JSearchAdapter()
    poll_one = adapter.normalize_job({**REAL_STYLE_JOB, "job_id": "OVRXQVV4ZFlfWjNpYVA4aUFBQUFBQT09"})
    poll_two = adapter.normalize_job({**REAL_STYLE_JOB, "job_id": "MmZveEFjRjdYTEUyWUEwUEFBQUFBQT09"})

    first = change_detection.process_source_jobs(db_session, "jsearch", [poll_one])
    second = change_detection.process_source_jobs(db_session, "jsearch", [poll_two])

    assert first["new"] == 1
    assert second["new"] == 0
    assert second["duplicates"] == 1
    assert db_session.query(DiscoveredJob).filter_by(source="jsearch").count() == 1


def test_jsearch_sourced_job_update_detected_without_duplicating(db_session):
    adapter = JSearchAdapter()
    original = adapter.normalize_job(REAL_STYLE_JOB)
    change_detection.process_source_jobs(db_session, "jsearch", [original])

    changed_raw = {**REAL_STYLE_JOB, "job_description": "<p>Updated: now also owns CI/CD pipeline automation.</p>"}
    updated = adapter.normalize_job(changed_raw)
    result = change_detection.process_source_jobs(db_session, "jsearch", [updated])

    assert result["new"] == 0
    assert result["updated"] == 1
    row = db_session.query(DiscoveredJob).filter_by(source="jsearch").one()
    assert "CI/CD pipeline" in row.description
    assert row.first_seen_at is not None  # preserved, not reset on update


def test_health_check_does_not_make_its_own_network_call(monkeypatch):
    """Quota-aware: health_check must not spend a second request beyond the
    one fetch_jobs already makes per poll cycle."""
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    adapter = JSearchAdapter(preference_queries=[SAMPLE_QUERY])
    with patch("app.job_sources.jsearch.url_safety.safe_get") as mocked:
        adapter.health_check()
    mocked.assert_not_called()


# ---------------------------------------------------------------------------
# lookup_direct_apply — targeted single-request lookup for a job another
# provider (Dice, Adzuna, ...) only ever supplied as a listing.
# ---------------------------------------------------------------------------

def test_lookup_direct_apply_hit_with_matching_employer_and_title(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    payload = _real_payload([{
        **REAL_STYLE_JOB, "employer_name": "Acme Widgets", "job_title": "Senior Java Developer",
        "job_apply_link": "https://careers.acmewidgets.com/job/12345/senior-java-developer",
        "job_apply_is_direct": True, "apply_options": [],
    }])
    with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_response(200, payload)) as mocked:
        result = lookup_direct_apply("Senior Java Developer", "Acme Widgets", "Austin, TX")
    assert result == {"url": "https://careers.acmewidgets.com/job/12345/senior-java-developer", "rate_limited": False}
    mocked.assert_called_once()
    params = mocked.call_args.kwargs["params"]
    assert params["query"] == "Senior Java Developer Acme Widgets Austin, TX"
    assert "cursor" not in params


def test_lookup_direct_apply_miss_when_no_result_employer_matches(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    payload = _real_payload([{
        **REAL_STYLE_JOB, "employer_name": "Totally Different Corp", "job_title": "Senior Java Developer",
        "job_apply_link": "https://careers.totallydifferent.com/job/1",
        "job_apply_is_direct": True, "apply_options": [],
    }])
    with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_response(200, payload)):
        result = lookup_direct_apply("Senior Java Developer", "Acme Widgets", "Austin, TX")
    assert result == {"url": None, "rate_limited": False}


def test_lookup_direct_apply_miss_when_title_does_not_match_even_with_employer_match(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    payload = _real_payload([{
        **REAL_STYLE_JOB, "employer_name": "Acme Widgets", "job_title": "Marketing Manager",
        "job_apply_link": "https://careers.acmewidgets.com/job/999/marketing-manager",
        "job_apply_is_direct": True, "apply_options": [],
    }])
    with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_response(200, payload)):
        result = lookup_direct_apply("Senior Java Developer", "Acme Widgets", "Austin, TX")
    assert result == {"url": None, "rate_limited": False}


def test_lookup_direct_apply_picks_greenhouse_over_linkedin_in_apply_options(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    payload = _real_payload([{
        **REAL_STYLE_JOB, "employer_name": "Acme Widgets", "job_title": "Senior Java Developer",
        "employer_website": None,
        "job_apply_link": "https://www.linkedin.com/jobs/view/12345",
        "job_apply_is_direct": False,
        "apply_options": [
            {"publisher": "LinkedIn", "apply_link": "https://www.linkedin.com/jobs/view/12345", "is_direct": False},
            {"publisher": "Acme Widgets", "apply_link": "https://boards.greenhouse.io/acmewidgets/jobs/12345", "is_direct": False},
        ],
    }])
    with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_response(200, payload)):
        result = lookup_direct_apply("Senior Java Developer", "Acme Widgets", "Austin, TX")
    assert result == {"url": "https://boards.greenhouse.io/acmewidgets/jobs/12345", "rate_limited": False}


def test_lookup_direct_apply_rejects_company_mismatch_even_if_url_is_a_real_ats(monkeypatch):
    """An ATS-hosted URL whose tenant token doesn't match the company we
    asked about is never trusted — company mismatch, not just domain
    classification, gates the result."""
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    payload = _real_payload([{
        **REAL_STYLE_JOB, "employer_name": "Acme Widgets", "job_title": "Senior Java Developer",
        "employer_website": None,
        "job_apply_link": "https://boards.greenhouse.io/some-other-company/jobs/12345",
        "job_apply_is_direct": True, "apply_options": [],
    }])
    with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_response(200, payload)):
        result = lookup_direct_apply("Senior Java Developer", "Acme Widgets", "Austin, TX")
    assert result == {"url": None, "rate_limited": False}


def test_lookup_direct_apply_stops_on_429_without_retry(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_response(429)) as mocked:
        result = lookup_direct_apply("Senior Java Developer", "Acme Widgets", "Austin, TX")
    assert result == {"url": None, "rate_limited": True}
    mocked.assert_called_once()


def test_lookup_direct_apply_not_configured_makes_no_http_call(monkeypatch):
    monkeypatch.delenv("JSEARCH_API_KEY", raising=False)
    with patch("app.job_sources.jsearch.url_safety.safe_get") as mocked:
        result = lookup_direct_apply("Senior Java Developer", "Acme Widgets", "Austin, TX")
    assert result == {"url": None, "rate_limited": False}
    mocked.assert_not_called()


def test_lookup_direct_apply_no_match_in_results_is_a_safe_miss(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_response(200, _real_payload([]))):
        result = lookup_direct_apply("Senior Java Developer", "Acme Widgets", "Austin, TX")
    assert result == {"url": None, "rate_limited": False}
