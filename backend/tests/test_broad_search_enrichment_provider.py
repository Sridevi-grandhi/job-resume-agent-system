"""Tests for app/services/broad_search_enrichment_provider.py — the Phase-2
broad structured-search provider INTERFACE. No real provider is chosen or
integrated in this phase (_CLIENT_FACTORIES is empty in production), so
every test here registers a temporary FAKE client via _CLIENT_FACTORIES to
prove the config contract, normalization, and error handling work
correctly — independent of any real external search API. No real network
call is ever made anywhere in this file.
"""

from __future__ import annotations

from unittest.mock import patch

from app.services import broad_search_enrichment_provider as broad_module
from app.services.broad_search_enrichment_provider import (
    broad_provider_configured,
    broad_search_provider,
)
from app.services.job_enrichment_service import EnrichmentQuery, enrich_job
from app.services.job_normalization_service import resolve_apply_action
from types import SimpleNamespace


class _FakeClient:
    def __init__(self, results=None, error=None):
        self._results = results if results is not None else []
        self._error = error

    def search(self, query):
        assert isinstance(query, EnrichmentQuery)
        if self._error:
            raise self._error
        return self._results


def _aggregator_job(**overrides):
    defaults = dict(
        company="Acme Widgets", title="Senior Java Developer", location="Austin, TX",
        source="jsearch", direct_url=None, application_url=None,
        source_url="https://www.linkedin.com/jobs/view/999", availability_status="UNKNOWN",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _enable_fake_provider(monkeypatch, client):
    monkeypatch.setenv("JOB_ENRICHMENT_BROAD_PROVIDER", "test_provider")
    monkeypatch.setenv("JOB_ENRICHMENT_BROAD_API_KEY", "test-key-not-a-secret")
    monkeypatch.setitem(broad_module._CLIENT_FACTORIES, "test_provider", lambda: client)


# ---------------------------------------------------------------------------
# Config contract
# ---------------------------------------------------------------------------

def test_disabled_by_default_no_env_vars_set(monkeypatch):
    """Item 14: unconfigured is the out-of-the-box state in this project."""
    monkeypatch.delenv("JOB_ENRICHMENT_BROAD_PROVIDER", raising=False)
    monkeypatch.delenv("JOB_ENRICHMENT_BROAD_API_KEY", raising=False)
    assert broad_provider_configured() is False


def test_provider_name_none_is_disabled_even_with_a_key(monkeypatch):
    monkeypatch.setenv("JOB_ENRICHMENT_BROAD_PROVIDER", "none")
    monkeypatch.setenv("JOB_ENRICHMENT_BROAD_API_KEY", "some-key")
    assert broad_provider_configured() is False


def test_provider_name_set_without_api_key_is_disabled(monkeypatch):
    monkeypatch.setenv("JOB_ENRICHMENT_BROAD_PROVIDER", "test_provider")
    monkeypatch.delenv("JOB_ENRICHMENT_BROAD_API_KEY", raising=False)
    assert broad_provider_configured() is False


# ---------------------------------------------------------------------------
# 14. Disabled by default -> zero network / client activity
# ---------------------------------------------------------------------------

def test_disabled_provider_never_touches_the_client_registry(monkeypatch):
    monkeypatch.delenv("JOB_ENRICHMENT_BROAD_PROVIDER", raising=False)
    monkeypatch.delenv("JOB_ENRICHMENT_BROAD_API_KEY", raising=False)
    with patch.object(broad_module, "_get_client") as mocked_get_client:
        result = broad_search_provider(EnrichmentQuery(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX"))
    assert result == []
    mocked_get_client.assert_not_called()


def test_unregistered_provider_name_returns_empty_never_crashes(monkeypatch):
    """A provider name with no matching factory (nothing implemented for
    it yet, e.g. today's production state for ANY name) is treated exactly
    like disabled — never a crash, never invented results."""
    monkeypatch.setenv("JOB_ENRICHMENT_BROAD_PROVIDER", "google_custom_search")
    monkeypatch.setenv("JOB_ENRICHMENT_BROAD_API_KEY", "test-key")
    result = broad_search_provider(EnrichmentQuery(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX"))
    assert result == []


# ---------------------------------------------------------------------------
# 2. Exact verified candidate -> enrichment succeeds
# ---------------------------------------------------------------------------

def test_exact_candidate_from_broad_provider_enriches_successfully(monkeypatch):
    job = _aggregator_job()
    _enable_fake_provider(monkeypatch, _FakeClient(results=[{
        "url": "https://careers.acmewidgets.com/job/48213/senior-java-developer",
        "title": "Senior Java Developer", "company": "Acme Widgets", "location": "Austin, TX",
    }]))
    with patch("app.services.job_enrichment_service.availability.check_url_availability",
               return_value={"status": "ACTIVE", "reason": "http_200", "final_url": None}):
        result = enrich_job(job, provider=broad_search_provider, employer_website="https://www.acmewidgets.com")
    assert result.status == "ENRICHED"
    assert result.url == "https://careers.acmewidgets.com/job/48213/senior-java-developer"
    # Provenance names the actual configured provider ("test_provider"),
    # never a generic label — see broad_search_provider's dynamic tag.
    assert result.provider_name == "test_provider_enrichment"


# ---------------------------------------------------------------------------
# 3/4. Correct company wrong title / correct title wrong company -> reject
# ---------------------------------------------------------------------------

def test_correct_company_wrong_title_is_rejected(monkeypatch):
    job = _aggregator_job(title="Senior Java Developer")
    _enable_fake_provider(monkeypatch, _FakeClient(results=[{
        "url": "https://careers.acmewidgets.com/job/48213/marketing-manager",
        "title": "Marketing Manager", "company": "Acme Widgets", "location": "Austin, TX",
    }]))
    result = enrich_job(job, provider=broad_search_provider, employer_website="https://www.acmewidgets.com")
    assert result.status == "REJECTED"


def test_correct_title_wrong_company_is_rejected(monkeypatch):
    job = _aggregator_job(company="Acme Widgets", title="Senior Java Developer")
    _enable_fake_provider(monkeypatch, _FakeClient(results=[{
        "url": "https://careers.a-totally-different-company.com/job/48213/senior-java-developer",
        "title": "Senior Java Developer", "company": "A Totally Different Company", "location": "Austin, TX",
    }]))
    result = enrich_job(job, provider=broad_search_provider, employer_website="https://www.acmewidgets.com")
    assert result.status == "REJECTED"


# ---------------------------------------------------------------------------
# 5. Incompatible location -> reject
# ---------------------------------------------------------------------------

def test_incompatible_location_is_rejected(monkeypatch):
    job = _aggregator_job(location="Austin, TX")
    _enable_fake_provider(monkeypatch, _FakeClient(results=[{
        "url": "https://careers.acmewidgets.com/job/48213/senior-java-developer",
        "title": "Senior Java Developer", "company": "Acme Widgets", "location": "Berlin, Germany",
    }]))
    result = enrich_job(job, provider=broad_search_provider, employer_website="https://www.acmewidgets.com")
    assert result.status == "REJECTED"


# ---------------------------------------------------------------------------
# 6. Generic careers page -> reject
# ---------------------------------------------------------------------------

def test_generic_careers_page_is_rejected(monkeypatch):
    job = _aggregator_job()
    _enable_fake_provider(monkeypatch, _FakeClient(results=[{
        "url": "https://careers.acmewidgets.com/jobs",
        "title": "Senior Java Developer", "company": "Acme Widgets", "location": "Austin, TX",
    }]))
    result = enrich_job(job, provider=broad_search_provider, employer_website="https://www.acmewidgets.com")
    assert result.status == "REJECTED"


# ---------------------------------------------------------------------------
# 7. Unsafe URL -> reject
# ---------------------------------------------------------------------------

def test_unsafe_url_from_broad_provider_is_rejected(monkeypatch):
    job = _aggregator_job()
    _enable_fake_provider(monkeypatch, _FakeClient(results=[{
        "url": "javascript:alert(1)",
        "title": "Senior Java Developer", "company": "Acme Widgets", "location": "Austin, TX",
    }]))
    result = enrich_job(job, provider=broad_search_provider, employer_website="https://www.acmewidgets.com")
    assert result.status == "REJECTED"


def test_result_with_no_url_is_dropped_never_invents_one(monkeypatch):
    job = _aggregator_job()
    _enable_fake_provider(monkeypatch, _FakeClient(results=[{"title": "Senior Java Developer", "company": "Acme Widgets"}]))
    result = enrich_job(job, provider=broad_search_provider, employer_website="https://www.acmewidgets.com")
    assert result.status == "REJECTED"
    assert result.url is None


# ---------------------------------------------------------------------------
# 8. Expired candidate -> reject as Apply
# ---------------------------------------------------------------------------

def test_expired_candidate_is_not_promoted_to_apply(monkeypatch):
    job = _aggregator_job()
    _enable_fake_provider(monkeypatch, _FakeClient(results=[{
        "url": "https://careers.acmewidgets.com/job/48213/senior-java-developer",
        "title": "Senior Java Developer", "company": "Acme Widgets", "location": "Austin, TX",
    }]))
    with patch("app.services.job_enrichment_service.availability.check_url_availability",
               return_value={"status": "EXPIRED", "reason": "http_404", "final_url": None}):
        result = enrich_job(job, provider=broad_search_provider, employer_website="https://www.acmewidgets.com")
    assert result.status == "EXPIRED"
    assert job.direct_url is None
    action = resolve_apply_action(job)
    assert action["action_label"] == "View Listing"


# ---------------------------------------------------------------------------
# 9/10. Provider error / timeout -> View Listing preserved
# ---------------------------------------------------------------------------

def test_provider_error_preserves_view_listing(monkeypatch):
    job = _aggregator_job()
    _enable_fake_provider(monkeypatch, _FakeClient(error=RuntimeError("provider is down")))
    result = enrich_job(job, provider=broad_search_provider)
    assert result.status == "REJECTED"
    assert job.direct_url is None


def test_provider_timeout_preserves_view_listing(monkeypatch):
    job = _aggregator_job()
    _enable_fake_provider(monkeypatch, _FakeClient(error=TimeoutError("timed out")))
    result = enrich_job(job, provider=broad_search_provider)
    assert result.status == "REJECTED"
    assert job.direct_url is None
    action = resolve_apply_action(job)
    assert action["action_label"] == "View Listing"


def test_client_init_failure_returns_empty_never_crashes(monkeypatch):
    monkeypatch.setenv("JOB_ENRICHMENT_BROAD_PROVIDER", "test_provider")
    monkeypatch.setenv("JOB_ENRICHMENT_BROAD_API_KEY", "test-key")

    def broken_factory():
        raise RuntimeError("client init failed")

    monkeypatch.setitem(broad_module._CLIENT_FACTORIES, "test_provider", broken_factory)
    result = broad_search_provider(EnrichmentQuery(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX"))
    assert result == []


# ---------------------------------------------------------------------------
# 13. Provenance saved as broad_search_enrichment
# ---------------------------------------------------------------------------

def test_provenance_names_the_specific_configured_provider(monkeypatch):
    from app.services.job_enrichment_service import apply_enrichment_result

    job = _aggregator_job()
    _enable_fake_provider(monkeypatch, _FakeClient(results=[{
        "url": "https://careers.acmewidgets.com/job/48213/senior-java-developer",
        "title": "Senior Java Developer", "company": "Acme Widgets", "location": "Austin, TX",
    }]))
    with patch("app.services.job_enrichment_service.availability.check_url_availability",
               return_value={"status": "ACTIVE", "reason": "http_200", "final_url": None}):
        result = enrich_job(job, provider=broad_search_provider, employer_website="https://www.acmewidgets.com")

    apply_enrichment_result(job, result)
    assert job.direct_url_source == "test_provider_enrichment"
    assert job.direct_url == "https://careers.acmewidgets.com/job/48213/senior-java-developer"
