"""Tests for app/services/serpapi_google_jobs_client.py — Phase 3's first
real BroadSearchClient implementation (SerpAPI's Google Jobs engine).

No real network call is ever made: url_safety.safe_get is mocked directly
(matching this repo's established pattern — see test_jsearch_adapter.py).
Company names used throughout are generic/fictional.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

from app.services import broad_search_enrichment_provider as broad_module
from app.services.broad_search_enrichment_provider import broad_search_provider
from app.services.job_enrichment_service import EnrichmentQuery, enrich_job
from app.services.job_normalization_service import resolve_apply_action
from app.services.serpapi_google_jobs_client import SerpApiGoogleJobsClient, _build_query_text


def _response(status_code=200, payload=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload if payload is not None else {}
    return response


def _gh_job_result(title, company, location, apply_options):
    return {
        "title": title, "company_name": company, "location": location,
        "via": "via Company Careers", "job_id": "abc123",
        "apply_options": apply_options,
    }


def _aggregator_job(**overrides):
    defaults = dict(
        company="Acme Widgets", title="Senior Java Developer", location="Austin, TX",
        source="jsearch", direct_url=None, application_url=None,
        source_url="https://www.linkedin.com/jobs/view/999", availability_status="UNKNOWN",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _enable_serpapi(monkeypatch):
    monkeypatch.setenv("JOB_ENRICHMENT_BROAD_PROVIDER", "serpapi_google_jobs")
    monkeypatch.setenv("JOB_ENRICHMENT_BROAD_API_KEY", "test-key-not-a-secret")


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------

def test_query_text_is_narrow_quoted_title_and_company():
    query = EnrichmentQuery(company="Acme Widgets", title="Senior Java Developer", location="Dallas, TX")
    assert _build_query_text(query) == '"Senior Java Developer" "Acme Widgets" Dallas, TX'


# ---------------------------------------------------------------------------
# 1/2. Disabled / missing API key -> zero request
# ---------------------------------------------------------------------------

def test_provider_disabled_makes_zero_request(monkeypatch):
    monkeypatch.delenv("JOB_ENRICHMENT_BROAD_PROVIDER", raising=False)
    monkeypatch.delenv("JOB_ENRICHMENT_BROAD_API_KEY", raising=False)
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get") as mocked_get:
        result = broad_search_provider(EnrichmentQuery(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX"))
    assert result == []
    mocked_get.assert_not_called()


def test_missing_api_key_makes_zero_request(monkeypatch):
    monkeypatch.setenv("JOB_ENRICHMENT_BROAD_PROVIDER", "serpapi_google_jobs")
    monkeypatch.delenv("JOB_ENRICHMENT_BROAD_API_KEY", raising=False)
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get") as mocked_get:
        result = broad_search_provider(EnrichmentQuery(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX"))
    assert result == []
    mocked_get.assert_not_called()


def test_client_itself_refuses_a_keyless_request():
    """Defense in depth — even calling the client directly, bypassing the
    outer broad_search_provider gate, must never fire a keyless request."""
    client = SerpApiGoogleJobsClient(api_key="")
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get") as mocked_get:
        result = client.search(EnrichmentQuery(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX"))
    assert result == []
    mocked_get.assert_not_called()


# ---------------------------------------------------------------------------
# 3/17. Greenhouse/Lever succeeds, or job already direct -> SerpAPI not called
# ---------------------------------------------------------------------------

def test_greenhouse_lever_success_means_serpapi_never_called(monkeypatch):
    from app.services.job_enrichment_service import EnrichmentCandidateResult, enrich_job_with_fallback

    _enable_serpapi(monkeypatch)
    job = _aggregator_job()
    gh_candidate = EnrichmentCandidateResult(
        url="https://careers.acmewidgets.com/job/48213/senior-java-developer",
        matched_title="Senior Java Developer", matched_company="Acme Widgets", matched_location="Austin, TX",
        provider_name="greenhouse_enrichment",
    )
    with patch("app.services.greenhouse_lever_enrichment_provider.greenhouse_lever_provider", return_value=[gh_candidate]), \
         patch("app.services.job_enrichment_service.availability.check_url_availability",
               return_value={"status": "ACTIVE", "reason": "http_200", "final_url": None}), \
         patch("app.services.serpapi_google_jobs_client.url_safety.safe_get") as mocked_get:
        result = enrich_job_with_fallback(job, employer_website="https://www.acmewidgets.com")

    assert result.status == "ENRICHED"
    assert result.provider_name == "greenhouse_enrichment"
    mocked_get.assert_not_called()


def test_existing_direct_job_never_calls_serpapi(monkeypatch):
    _enable_serpapi(monkeypatch)
    job = _aggregator_job(
        direct_url="https://careers.acmewidgets.com/job/48213/senior-java-developer",
        application_url="https://careers.acmewidgets.com/job/48213/senior-java-developer",
    )
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get") as mocked_get:
        result = enrich_job(job, provider=broad_search_provider)
    assert result.status == "NOT_APPLICABLE"
    mocked_get.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Exact structured result + safe employer URL -> forwarded and verified
# ---------------------------------------------------------------------------

def test_exact_employer_apply_option_is_forwarded_and_enriches(monkeypatch):
    _enable_serpapi(monkeypatch)
    job = _aggregator_job()
    payload = {"jobs_results": [_gh_job_result(
        "Senior Java Developer", "Acme Widgets", "Austin, TX",
        [{"title": "Acme Widgets", "link": "https://careers.acmewidgets.com/job/48213/senior-java-developer"}],
    )]}
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", return_value=_response(200, payload)), \
         patch("app.services.job_enrichment_service.availability.check_url_availability",
               return_value={"status": "ACTIVE", "reason": "http_200", "final_url": None}):
        result = enrich_job(job, provider=broad_search_provider, employer_website="https://www.acmewidgets.com")
    assert result.status == "ENRICHED"
    assert result.url == "https://careers.acmewidgets.com/job/48213/senior-java-developer"


# ---------------------------------------------------------------------------
# 5. ATS-specific result -> forwarded
# ---------------------------------------------------------------------------

def test_ats_apply_option_is_forwarded_and_enriches(monkeypatch):
    _enable_serpapi(monkeypatch)
    job = _aggregator_job()
    payload = {"jobs_results": [_gh_job_result(
        "Senior Java Developer", "Acme Widgets", "Austin, TX",
        [{"title": "Acme Widgets", "link": "https://boards.greenhouse.io/acmewidgets/jobs/48213"}],
    )]}
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", return_value=_response(200, payload)), \
         patch("app.services.job_enrichment_service.availability.check_url_availability",
               return_value={"status": "ACTIVE", "reason": "http_200", "final_url": None}):
        result = enrich_job(job, provider=broad_search_provider)
    assert result.status == "ENRICHED"
    assert result.url == "https://boards.greenhouse.io/acmewidgets/jobs/48213"


# ---------------------------------------------------------------------------
# 6/7. LinkedIn-only / Indeed-only apply options -> no direct Apply enrichment
# ---------------------------------------------------------------------------

def test_linkedin_only_apply_option_never_enriches(monkeypatch):
    _enable_serpapi(monkeypatch)
    job = _aggregator_job()
    payload = {"jobs_results": [_gh_job_result(
        "Senior Java Developer", "Acme Widgets", "Austin, TX",
        [{"title": "LinkedIn", "link": "https://www.linkedin.com/jobs/view/12345"}],
    )]}
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", return_value=_response(200, payload)):
        result = enrich_job(job, provider=broad_search_provider)
    assert result.status == "REJECTED"
    assert job.direct_url is None


def test_indeed_only_apply_option_never_enriches(monkeypatch):
    _enable_serpapi(monkeypatch)
    job = _aggregator_job()
    payload = {"jobs_results": [_gh_job_result(
        "Senior Java Developer", "Acme Widgets", "Austin, TX",
        [{"title": "Indeed", "link": "https://www.indeed.com/viewjob?jk=abc123"}],
    )]}
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", return_value=_response(200, payload)):
        result = enrich_job(job, provider=broad_search_provider)
    assert result.status == "REJECTED"


# ---------------------------------------------------------------------------
# 8. Generic employer careers homepage -> rejected by specificity
# ---------------------------------------------------------------------------

def test_generic_careers_homepage_rejected_by_specificity(monkeypatch):
    _enable_serpapi(monkeypatch)
    job = _aggregator_job()
    payload = {"jobs_results": [_gh_job_result(
        "Senior Java Developer", "Acme Widgets", "Austin, TX",
        [{"title": "Acme Widgets", "link": "https://careers.acmewidgets.com/jobs"}],
    )]}
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", return_value=_response(200, payload)):
        result = enrich_job(job, provider=broad_search_provider, employer_website="https://www.acmewidgets.com")
    assert result.status == "REJECTED"


# ---------------------------------------------------------------------------
# 9/10/11. Wrong company / wrong title / incompatible location -> rejected
# ---------------------------------------------------------------------------

def test_wrong_company_rejected(monkeypatch):
    _enable_serpapi(monkeypatch)
    job = _aggregator_job(company="Acme Widgets", title="Senior Java Developer")
    payload = {"jobs_results": [_gh_job_result(
        "Senior Java Developer", "A Totally Different Company", "Austin, TX",
        [{"title": "A Totally Different Company", "link": "https://careers.a-totally-different-company.com/job/48213/senior-java-developer"}],
    )]}
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", return_value=_response(200, payload)):
        result = enrich_job(job, provider=broad_search_provider, employer_website="https://www.acmewidgets.com")
    assert result.status == "REJECTED"


def test_wrong_title_rejected(monkeypatch):
    _enable_serpapi(monkeypatch)
    job = _aggregator_job(title="Senior Java Developer")
    payload = {"jobs_results": [_gh_job_result(
        "Marketing Manager", "Acme Widgets", "Austin, TX",
        [{"title": "Acme Widgets", "link": "https://careers.acmewidgets.com/job/48213/marketing-manager"}],
    )]}
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", return_value=_response(200, payload)):
        result = enrich_job(job, provider=broad_search_provider, employer_website="https://www.acmewidgets.com")
    assert result.status == "REJECTED"


def test_incompatible_location_rejected(monkeypatch):
    _enable_serpapi(monkeypatch)
    job = _aggregator_job(location="Austin, TX")
    payload = {"jobs_results": [_gh_job_result(
        "Senior Java Developer", "Acme Widgets", "Berlin, Germany",
        [{"title": "Acme Widgets", "link": "https://careers.acmewidgets.com/job/48213/senior-java-developer"}],
    )]}
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", return_value=_response(200, payload)):
        result = enrich_job(job, provider=broad_search_provider, employer_website="https://www.acmewidgets.com")
    assert result.status == "REJECTED"


# ---------------------------------------------------------------------------
# 12. Unsafe URL -> rejected
# ---------------------------------------------------------------------------

def test_javascript_url_never_even_forwarded_as_a_candidate(monkeypatch):
    """The client's own domain-class filter already drops this — it never
    reaches the shared verification pipeline at all."""
    _enable_serpapi(monkeypatch)
    job = _aggregator_job()
    payload = {"jobs_results": [_gh_job_result(
        "Senior Java Developer", "Acme Widgets", "Austin, TX",
        [{"title": "Acme Widgets", "link": "javascript:alert(1)"}],
    )]}
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", return_value=_response(200, payload)):
        result = enrich_job(job, provider=broad_search_provider, employer_website="https://www.acmewidgets.com")
    assert result.status == "REJECTED"


def test_credentialed_url_passes_domain_filter_but_rejected_by_shared_safety_check(monkeypatch):
    """A URL with embedded credentials looks like a real employer domain
    (so THIS client's own filter forwards it) but the SHARED
    is_safe_application_url check still catches and rejects it — proving
    the existing safety gate is never bypassed by this provider."""
    _enable_serpapi(monkeypatch)
    job = _aggregator_job()
    payload = {"jobs_results": [_gh_job_result(
        "Senior Java Developer", "Acme Widgets", "Austin, TX",
        [{"title": "Acme Widgets", "link": "https://user:pass@careers.acmewidgets.com/job/48213/senior-java-developer"}],
    )]}
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", return_value=_response(200, payload)):
        result = enrich_job(job, provider=broad_search_provider, employer_website="https://www.acmewidgets.com")
    assert result.status == "REJECTED"


# ---------------------------------------------------------------------------
# 13. Expired direct candidate -> rejected as Apply
# ---------------------------------------------------------------------------

def test_expired_candidate_never_promoted_to_apply(monkeypatch):
    _enable_serpapi(monkeypatch)
    job = _aggregator_job()
    payload = {"jobs_results": [_gh_job_result(
        "Senior Java Developer", "Acme Widgets", "Austin, TX",
        [{"title": "Acme Widgets", "link": "https://careers.acmewidgets.com/job/48213/senior-java-developer"}],
    )]}
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", return_value=_response(200, payload)), \
         patch("app.services.job_enrichment_service.availability.check_url_availability",
               return_value={"status": "EXPIRED", "reason": "http_404", "final_url": None}):
        result = enrich_job(job, provider=broad_search_provider, employer_website="https://www.acmewidgets.com")
    assert result.status == "EXPIRED"
    from app.services.job_enrichment_service import apply_enrichment_result
    apply_enrichment_result(job, result)
    assert resolve_apply_action(job)["action_label"] == "Job Closed"


# ---------------------------------------------------------------------------
# 14/15/16. 429 / timeout / malformed JSON -> View Listing preserved
# ---------------------------------------------------------------------------

def test_429_rate_limit_is_safe_failure(monkeypatch):
    _enable_serpapi(monkeypatch)
    job = _aggregator_job()
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", return_value=_response(429)):
        result = enrich_job(job, provider=broad_search_provider)
    assert result.status == "REJECTED"
    assert job.direct_url is None
    assert job.source_url == "https://www.linkedin.com/jobs/view/999"  # never erased


def test_timeout_preserves_view_listing(monkeypatch):
    _enable_serpapi(monkeypatch)
    job = _aggregator_job()
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", side_effect=requests.Timeout("timed out")):
        result = enrich_job(job, provider=broad_search_provider)
    assert result.status == "REJECTED"
    action = resolve_apply_action(job)
    assert action["action_label"] == "View Listing"


def test_malformed_json_preserves_view_listing(monkeypatch):
    _enable_serpapi(monkeypatch)
    job = _aggregator_job()
    bad_response = MagicMock()
    bad_response.status_code = 200
    bad_response.json.side_effect = ValueError("not json")
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", return_value=bad_response):
        result = enrich_job(job, provider=broad_search_provider)
    assert result.status == "REJECTED"


def test_empty_jobs_results_is_safe(monkeypatch):
    _enable_serpapi(monkeypatch)
    job = _aggregator_job()
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", return_value=_response(200, {"jobs_results": []})):
        result = enrich_job(job, provider=broad_search_provider)
    assert result.status == "REJECTED"


def test_missing_apply_options_is_safe(monkeypatch):
    _enable_serpapi(monkeypatch)
    job = _aggregator_job()
    payload = {"jobs_results": [{"title": "Senior Java Developer", "company_name": "Acme Widgets", "location": "Austin, TX"}]}
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", return_value=_response(200, payload)):
        result = enrich_job(job, provider=broad_search_provider)
    assert result.status == "REJECTED"


def test_5xx_is_safe_failure(monkeypatch):
    _enable_serpapi(monkeypatch)
    job = _aggregator_job()
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", return_value=_response(503)):
        result = enrich_job(job, provider=broad_search_provider)
    assert result.status == "REJECTED"


def test_401_403_are_safe_failures(monkeypatch):
    _enable_serpapi(monkeypatch)
    for status in (401, 403):
        job = _aggregator_job()
        with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", return_value=_response(status)):
            result = enrich_job(job, provider=broad_search_provider)
        assert result.status == "REJECTED"


# ---------------------------------------------------------------------------
# 18. Provenance
# ---------------------------------------------------------------------------

def test_provenance_is_serpapi_google_jobs_enrichment(monkeypatch):
    from app.services.job_enrichment_service import apply_enrichment_result

    _enable_serpapi(monkeypatch)
    job = _aggregator_job()
    payload = {"jobs_results": [_gh_job_result(
        "Senior Java Developer", "Acme Widgets", "Austin, TX",
        [{"title": "Acme Widgets", "link": "https://careers.acmewidgets.com/job/48213/senior-java-developer"}],
    )]}
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", return_value=_response(200, payload)), \
         patch("app.services.job_enrichment_service.availability.check_url_availability",
               return_value={"status": "ACTIVE", "reason": "http_200", "final_url": None}):
        result = enrich_job(job, provider=broad_search_provider, employer_website="https://www.acmewidgets.com")

    assert result.provider_name == "serpapi_google_jobs_enrichment"
    apply_enrichment_result(job, result)
    assert job.direct_url_source == "serpapi_google_jobs_enrichment"


# ---------------------------------------------------------------------------
# 19/20. Exactly one HTTP request per attempt, no pagination
# ---------------------------------------------------------------------------

def test_exactly_one_request_per_search_call(monkeypatch):
    _enable_serpapi(monkeypatch)
    payload = {"jobs_results": [_gh_job_result(
        "Senior Java Developer", "Acme Widgets", "Austin, TX",
        [{"title": "Acme Widgets", "link": "https://careers.acmewidgets.com/job/48213/senior-java-developer"}],
    )] * 10}  # many results in ONE response — still one request
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", return_value=_response(200, payload)) as mocked_get:
        SerpApiGoogleJobsClient(api_key="test-key").search(
            EnrichmentQuery(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX"),
        )
    assert mocked_get.call_count == 1


def test_no_pagination_params_sent():
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", return_value=_response(200, {})) as mocked_get:
        SerpApiGoogleJobsClient(api_key="test-key").search(
            EnrichmentQuery(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX"),
        )
    sent_params = mocked_get.call_args.kwargs.get("params", {})
    assert "next_page_token" not in sent_params
    assert "page" not in sent_params
    assert "start" not in sent_params


def test_connection_error_retried_exactly_once_then_gives_up():
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get",
               side_effect=requests.ConnectionError("dns failure")) as mocked_get:
        result = SerpApiGoogleJobsClient(api_key="test-key").search(
            EnrichmentQuery(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX"),
        )
    assert result == []
    assert mocked_get.call_count == 2  # 1 initial + 1 bounded retry, never more


def test_connection_error_then_success_on_retry():
    responses = [requests.ConnectionError("dns failure"), _response(200, {"jobs_results": []})]

    def side_effect(*args, **kwargs):
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", side_effect=side_effect) as mocked_get:
        result = SerpApiGoogleJobsClient(api_key="test-key").search(
            EnrichmentQuery(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX"),
        )
    assert result == []
    assert mocked_get.call_count == 2


def test_timeout_is_never_retried():
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", side_effect=requests.Timeout("timed out")) as mocked_get:
        SerpApiGoogleJobsClient(api_key="test-key").search(
            EnrichmentQuery(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX"),
        )
    assert mocked_get.call_count == 1


def test_429_is_never_retried():
    with patch("app.services.serpapi_google_jobs_client.url_safety.safe_get", return_value=_response(429)) as mocked_get:
        SerpApiGoogleJobsClient(api_key="test-key").search(
            EnrichmentQuery(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX"),
        )
    assert mocked_get.call_count == 1
