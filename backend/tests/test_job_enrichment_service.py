"""Tests for app/services/job_enrichment_service.py — the optional,
pluggable direct-employer-posting enrichment for AGGREGATOR_LISTING jobs.

The production provider chain is Greenhouse/Lever (Phase 1, key-free,
always available) then the broad structured-search provider (Phase 2,
disabled/unconfigured by default — see broad_search_enrichment_provider.py)
— see enrich_job_with_fallback(). Most tests here inject a fake provider
function directly via enrich_job(..., provider=...) to prove the
verification/matching pipeline itself works correctly, independent of any
real external service. No real network call is ever made: availability
checks are mocked at job_availability_service.check_url_availability, same
as the rest of this test suite.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.services.job_enrichment_service import (
    EnrichmentCandidateResult,
    EnrichmentQuery,
    enrich_job,
    enrich_job_with_fallback,
    enrichment_provider,
)
from app.services.job_normalization_service import resolve_apply_action
from app.services import job_normalization_service as normalization


def _aggregator_job(**overrides):
    defaults = dict(
        company="Example Corp", title="Senior Java Developer", location="Austin, TX",
        source="jsearch", direct_url=None, application_url=None,
        source_url="https://www.linkedin.com/jobs/view/12345",
        availability_status="UNKNOWN",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_provider(candidates):
    def provider(query: EnrichmentQuery):
        assert isinstance(query, EnrichmentQuery)
        return candidates
    return provider


# ---------------------------------------------------------------------------
# 1. Exact employer candidate -> enrichment succeeds -> Apply
# ---------------------------------------------------------------------------

def test_exact_employer_candidate_enriches_successfully():
    job = _aggregator_job()
    provider = _mock_provider([
        EnrichmentCandidateResult(
            url="https://careers.example-corp.com/job/48213/senior-java-developer",
            matched_title="Senior Java Developer", matched_company="Example Corp", matched_location="Austin, TX",
        ),
    ])
    with patch("app.services.job_enrichment_service.availability.check_url_availability",
               return_value={"status": "ACTIVE", "reason": "http_200", "final_url": None}):
        result = enrich_job(job, provider=provider, employer_website="https://www.example-corp.com")
    assert result.status == "ENRICHED"
    assert result.url == "https://careers.example-corp.com/job/48213/senior-java-developer"

    # Applying the result then makes resolve_apply_action say Apply.
    job.direct_url = result.url
    job.application_url = result.url
    job.availability_status = "ACTIVE"
    action = resolve_apply_action(job)
    assert action["action_label"] == "Apply"
    assert action["can_apply"] is True


def test_explicit_verified_employer_domain_alias_can_pass_shared_verification(monkeypatch):
    """Brand/parent relationships require a reviewed exact-domain entry;
    they are never inferred from a provider-supplied URL."""
    monkeypatch.setitem(
        normalization._VERIFIED_EMPLOYER_DOMAIN_ALIASES,
        "exampletalentbrand",
        frozenset({"example-parent-careers.com"}),
    )
    job = _aggregator_job(company="Example Talent Brand LLC")
    provider = _mock_provider([
        EnrichmentCandidateResult(
            url="https://jobs.example-parent-careers.com/job/48213/senior-java-developer",
            matched_title="Senior Java Developer", matched_company="Example Talent Brand",
            matched_location="Austin, TX",
        ),
    ])
    with patch("app.services.job_enrichment_service.availability.check_url_availability",
               return_value={"status": "ACTIVE", "reason": "http_200", "final_url": None}):
        result = enrich_job(job, provider=provider)
    assert result.status == "ENRICHED"
    evidence = next(item for item in result.diagnostics if item.get("employer_identity") == "VERIFIED_ALIAS")
    assert evidence["employer_alias"] == "example-parent-careers.com"


def test_unconfirmed_app_domain_remains_rejected_before_availability_lookup():
    job = _aggregator_job(company="Example Talent Brand LLC")
    provider = _mock_provider([
        EnrichmentCandidateResult(
            url="https://hrmsportal.app/job/48213/senior-java-developer",
            matched_title="Senior Java Developer", matched_company="Example Talent Brand",
            matched_location="Austin, TX",
        ),
    ])
    with patch("app.services.job_enrichment_service.availability.check_url_availability") as mocked_availability:
        result = enrich_job(job, provider=provider)
    assert result.status == "REJECTED"
    evidence = next(item for item in result.diagnostics if item.get("employer_identity") == "UNRECOGNIZED_DOMAIN")
    assert evidence["url_safety"] == "PASSED"
    mocked_availability.assert_not_called()


# ---------------------------------------------------------------------------
# 2. Same company, wrong role -> reject -> View Listing
# ---------------------------------------------------------------------------

def test_same_company_wrong_role_is_rejected():
    job = _aggregator_job(title="Senior Java Developer")
    provider = _mock_provider([
        EnrichmentCandidateResult(
            url="https://careers.example-corp.com/job/99110/marketing-manager",
            matched_title="Marketing Manager", matched_company="Example Corp", matched_location="Austin, TX",
        ),
    ])
    result = enrich_job(job, provider=provider, employer_website="https://www.example-corp.com")
    assert result.status == "REJECTED"
    assert result.url is None

    action = resolve_apply_action(job)  # employer candidate rejected; exact listing remains
    assert action["action_label"] == "View on LinkedIn"
    assert action["is_direct"] is False


# ---------------------------------------------------------------------------
# 3. Same title, wrong employer -> reject
# ---------------------------------------------------------------------------

def test_same_title_wrong_employer_is_rejected():
    job = _aggregator_job(company="Example Corp", title="Senior Java Developer")
    provider = _mock_provider([
        EnrichmentCandidateResult(
            url="https://careers.a-totally-different-company.com/job/55210/senior-java-developer",
            matched_title="Senior Java Developer", matched_company="A Totally Different Company",
            matched_location="Austin, TX",
        ),
    ])
    result = enrich_job(job, provider=provider, employer_website="https://www.example-corp.com")
    assert result.status == "REJECTED"


# ---------------------------------------------------------------------------
# 4. Generic employer careers page -> reject as direct Apply
# ---------------------------------------------------------------------------

def test_generic_employer_page_candidate_is_rejected():
    job = _aggregator_job()
    provider = _mock_provider([
        EnrichmentCandidateResult(
            url="https://careers.example-corp.com/jobs",
            matched_title="Senior Java Developer", matched_company="Example Corp", matched_location="Austin, TX",
        ),
    ])
    result = enrich_job(job, provider=provider, employer_website="https://www.example-corp.com")
    assert result.status == "REJECTED"
    assert result.reason == "no_candidate_passed_verification"


# ---------------------------------------------------------------------------
# 5. Exact ATS requisition -> Apply (ATS domain requires provider-confirmed
# employer identity, since one ATS board hosts many employers)
# ---------------------------------------------------------------------------

def test_exact_ats_requisition_candidate_enriches_successfully():
    job = _aggregator_job(company="Example Corp", title="Senior Java Developer")
    provider = _mock_provider([
        EnrichmentCandidateResult(
            url="https://boards.greenhouse.io/example-corp/jobs/48213",
            matched_title="Senior Java Developer", matched_company="Example Corp", matched_location="Austin, TX",
        ),
    ])
    with patch("app.services.job_enrichment_service.availability.check_url_availability",
               return_value={"status": "ACTIVE", "reason": "http_200", "final_url": None}):
        result = enrich_job(job, provider=provider)
    assert result.status == "ENRICHED"


def test_ats_candidate_without_employer_confirmation_is_rejected():
    """An ATS board hosts many employers — a candidate URL alone doesn't
    prove which one; the provider must confirm matched_company."""
    job = _aggregator_job(company="Example Corp", title="Senior Java Developer")
    provider = _mock_provider([
        EnrichmentCandidateResult(
            url="https://boards.greenhouse.io/some-other-employer/jobs/48213",
            matched_title="Senior Java Developer", matched_company=None, matched_location="Austin, TX",
        ),
    ])
    result = enrich_job(job, provider=provider)
    assert result.status == "REJECTED"


# ---------------------------------------------------------------------------
# 6. Multiple candidates -> best verified exact destination selected
# ---------------------------------------------------------------------------

def test_multiple_candidates_first_verified_one_wins():
    job = _aggregator_job(company="Example Corp", title="Senior Java Developer")
    provider = _mock_provider([
        EnrichmentCandidateResult(
            url="https://careers.example-corp.com/jobs",  # generic — rejected
            matched_title="Senior Java Developer", matched_company="Example Corp", matched_location="Austin, TX",
        ),
        EnrichmentCandidateResult(
            url="https://careers.a-totally-different-company.com/job/1/senior-java-developer",  # wrong employer
            matched_title="Senior Java Developer", matched_company="A Totally Different Company",
            matched_location="Austin, TX",
        ),
        EnrichmentCandidateResult(
            url="https://careers.example-corp.com/job/48213/senior-java-developer",  # verified match
            matched_title="Senior Java Developer", matched_company="Example Corp", matched_location="Austin, TX",
        ),
    ])
    with patch("app.services.job_enrichment_service.availability.check_url_availability",
               return_value={"status": "ACTIVE", "reason": "http_200", "final_url": None}):
        result = enrich_job(job, provider=provider, employer_website="https://www.example-corp.com")
    assert result.status == "ENRICHED"
    assert result.url == "https://careers.example-corp.com/job/48213/senior-java-developer"


# ---------------------------------------------------------------------------
# 8. Candidate's own availability check comes back non-ACTIVE (e.g. 403) ->
# never silently enrich into an unverified destination
# ---------------------------------------------------------------------------

def test_candidate_blocked_at_availability_check_is_not_enriched():
    job = _aggregator_job(company="Example Corp", title="Senior Java Developer")
    provider = _mock_provider([
        EnrichmentCandidateResult(
            url="https://careers.example-corp.com/job/48213/senior-java-developer",
            matched_title="Senior Java Developer", matched_company="Example Corp", matched_location="Austin, TX",
        ),
    ])
    with patch("app.services.job_enrichment_service.availability.check_url_availability",
               return_value={"status": "UNKNOWN", "reason": "http_403", "final_url": None}):
        result = enrich_job(job, provider=provider, employer_website="https://www.example-corp.com")
    assert result.status == "REJECTED"

    # Availability failure never promotes a verified direct apply. The exact
    # provider listing remains an application fallback.
    action = resolve_apply_action(job)
    assert action["action_label"] == "View on LinkedIn"
    assert action["is_direct"] is False


# ---------------------------------------------------------------------------
# 9. Direct destination + aggregator source -> primary action + source
# fallback retained (resolve_apply_action's secondary_action)
# ---------------------------------------------------------------------------

def test_direct_job_with_aggregator_source_keeps_secondary_action():
    job = _aggregator_job(
        direct_url="https://careers.example-corp.com/job/48213/senior-java-developer",
        application_url="https://careers.example-corp.com/job/48213/senior-java-developer",
        source_url="https://www.linkedin.com/jobs/view/12345",
        availability_status="ACTIVE",
    )
    action = resolve_apply_action(job)
    assert action["action_label"] == "Apply"
    assert action["secondary_action"] == {"label": "View Source", "url": "https://www.linkedin.com/jobs/view/12345"}


def test_direct_job_with_no_alternate_source_has_no_secondary_action():
    job = _aggregator_job(
        direct_url="https://careers.example-corp.com/job/48213/senior-java-developer",
        application_url="https://careers.example-corp.com/job/48213/senior-java-developer",
        source_url="https://careers.example-corp.com/job/48213/senior-java-developer",  # same as primary
        availability_status="ACTIVE",
    )
    action = resolve_apply_action(job)
    assert action["secondary_action"] is None


def test_aggregator_only_job_has_no_secondary_action():
    """The primary action already IS the listing — nothing to fall back to."""
    job = _aggregator_job()
    action = resolve_apply_action(job)
    assert action["action_label"] == "View on LinkedIn"
    assert action["is_direct"] is False
    assert action["secondary_action"] is None


# ---------------------------------------------------------------------------
# 10. Enrichment service unavailable -> existing View Listing behavior
# remains functional
# ---------------------------------------------------------------------------

def test_no_provider_configured_leaves_job_untouched():
    """When enrichment_provider() itself is unavailable (e.g. a future
    config toggle turns it off), enrich_job() must be a pure no-op."""
    job = _aggregator_job()
    with patch("app.services.job_enrichment_service.enrichment_provider", return_value=None):
        result = enrich_job(job)  # no provider= passed -> falls back to enrichment_provider()
    assert result.status == "PROVIDER_UNAVAILABLE"
    assert job.direct_url is None
    assert job.application_url is None

    action = resolve_apply_action(job)
    assert action["action_label"] == "View on LinkedIn"
    assert action["is_direct"] is False
    assert action["can_apply"] is True


def test_production_enrichment_provider_is_greenhouse_lever_phase_1():
    """Phase 1: the production provider is now Greenhouse + Lever's
    key-free public APIs — no paid/new external search provider."""
    from app.services.greenhouse_lever_enrichment_provider import greenhouse_lever_provider

    assert enrichment_provider() is greenhouse_lever_provider


def test_provider_error_never_crashes_the_pipeline():
    def broken_provider(_query):
        raise RuntimeError("provider is down")

    job = _aggregator_job()
    result = enrich_job(job, provider=broken_provider)
    assert result.status == "PROVIDER_ERROR"
    assert job.direct_url is None


# ---------------------------------------------------------------------------
# 11. Unsafe URL -> reject
# ---------------------------------------------------------------------------

def test_unsafe_candidate_url_is_rejected():
    job = _aggregator_job()
    provider = _mock_provider([
        EnrichmentCandidateResult(
            url="javascript:alert(1)",
            matched_title="Senior Java Developer", matched_company="Example Corp", matched_location="Austin, TX",
        ),
    ])
    result = enrich_job(job, provider=provider, employer_website="https://www.example-corp.com")
    assert result.status == "REJECTED"


# ---------------------------------------------------------------------------
# 12. No direct candidate exists -> never invent one
# ---------------------------------------------------------------------------

def test_no_candidates_from_provider_never_invents_a_url():
    job = _aggregator_job()
    provider = _mock_provider([])
    result = enrich_job(job, provider=provider)
    assert result.status == "REJECTED"
    assert result.url is None
    assert job.direct_url is None
    assert job.application_url is None


def test_already_direct_job_is_not_applicable_for_enrichment():
    job = _aggregator_job(
        direct_url="https://careers.example-corp.com/job/48213/senior-java-developer",
        application_url="https://careers.example-corp.com/job/48213/senior-java-developer",
    )
    result = enrich_job(job, provider=_mock_provider([]))
    assert result.status == "NOT_APPLICABLE"


# ---------------------------------------------------------------------------
# enrich_job_with_fallback — Phase 2's provider-agnostic orchestration:
# Greenhouse/Lever first, broad structured-search provider only if that
# finds nothing, View Listing preserved if neither does (Task 8 items 1, 9,
# 11, 12; provider-specific matching/rejection is covered in
# test_broad_search_enrichment_provider.py).
# ---------------------------------------------------------------------------

def test_broad_provider_disabled_by_default_no_behavior_change(monkeypatch):
    """Item 1/14: with no JOB_ENRICHMENT_BROAD_* env vars set (the default
    in this project), the orchestrated result must be IDENTICAL to a plain
    Greenhouse/Lever attempt — and the broad provider's own search() must
    never even be reached."""
    monkeypatch.delenv("JOB_ENRICHMENT_BROAD_PROVIDER", raising=False)
    monkeypatch.delenv("JOB_ENRICHMENT_BROAD_API_KEY", raising=False)
    job = _aggregator_job()

    with patch("app.services.greenhouse_lever_enrichment_provider.greenhouse_lever_provider", return_value=[]) as mocked_gh, \
         patch("app.services.broad_search_enrichment_provider.broad_search_provider") as mocked_broad:
        result = enrich_job_with_fallback(job)

    mocked_gh.assert_called_once()
    mocked_broad.assert_not_called()
    assert result.status == "REJECTED"
    assert job.direct_url is None


def test_greenhouse_lever_match_found_first_broad_provider_not_called():
    """Item 11: once Greenhouse/Lever produces a verified match, the broad
    provider must never be invoked at all — not even its config/enabled
    check needs to matter."""
    job = _aggregator_job()
    gh_candidate = EnrichmentCandidateResult(
        url="https://careers.example-corp.com/job/48213/senior-java-developer",
        matched_title="Senior Java Developer", matched_company="Example Corp", matched_location="Austin, TX",
        provider_name="greenhouse_enrichment",
    )

    with patch("app.services.greenhouse_lever_enrichment_provider.greenhouse_lever_provider", return_value=[gh_candidate]), \
         patch("app.services.job_enrichment_service.availability.check_url_availability",
               return_value={"status": "ACTIVE", "reason": "http_200", "final_url": None}), \
         patch("app.services.broad_search_enrichment_provider.broad_search_provider") as mocked_broad:
        result = enrich_job_with_fallback(job, employer_website="https://www.example-corp.com")

    assert result.status == "ENRICHED"
    assert result.provider_name == "greenhouse_enrichment"
    mocked_broad.assert_not_called()


def test_broad_provider_used_when_greenhouse_lever_finds_nothing_and_broad_is_configured(monkeypatch):
    """When Greenhouse/Lever come up empty AND a broad provider is
    configured, the orchestration falls through to it — proving the chain
    itself works, independent of what a real provider would return."""
    monkeypatch.setenv("JOB_ENRICHMENT_BROAD_PROVIDER", "example_search")
    monkeypatch.setenv("JOB_ENRICHMENT_BROAD_API_KEY", "test-key")
    job = _aggregator_job()
    broad_candidate = EnrichmentCandidateResult(
        url="https://careers.example-corp.com/job/48213/senior-java-developer",
        matched_title="Senior Java Developer", matched_company="Example Corp", matched_location="Austin, TX",
        provider_name="broad_search_enrichment",
    )

    with patch("app.services.greenhouse_lever_enrichment_provider.greenhouse_lever_provider", return_value=[]), \
         patch("app.services.broad_search_enrichment_provider.broad_search_provider", return_value=[broad_candidate]), \
         patch("app.services.job_enrichment_service.availability.check_url_availability",
               return_value={"status": "ACTIVE", "reason": "http_200", "final_url": None}):
        result = enrich_job_with_fallback(job, employer_website="https://www.example-corp.com")

    assert result.status == "ENRICHED"
    assert result.provider_name == "broad_search_enrichment"


def test_provider_error_from_greenhouse_lever_falls_through_to_broad_or_view_listing(monkeypatch):
    """Item 9: a provider-level error never propagates as a crash — the
    job simply stays View Listing (broad disabled by default in this test
    too, so the orchestration returns the Greenhouse/Lever error result
    directly)."""
    # This test exercises the no-broad-provider branch. Developer .env files
    # may legitimately configure SerpAPI, so make that branch explicit and
    # guarantee no external broad-provider client can be reached.
    monkeypatch.setenv("JOB_ENRICHMENT_BROAD_PROVIDER", "none")
    monkeypatch.delenv("JOB_ENRICHMENT_BROAD_API_KEY", raising=False)
    job = _aggregator_job()

    def broken_provider(_query):
        raise RuntimeError("boom")

    with patch("app.services.greenhouse_lever_enrichment_provider.greenhouse_lever_provider", side_effect=broken_provider):
        result = enrich_job_with_fallback(job)

    assert result.status == "PROVIDER_ERROR"
    assert job.direct_url is None


def test_existing_direct_job_skips_orchestration_entirely():
    """Item 12."""
    job = _aggregator_job(
        direct_url="https://careers.example-corp.com/job/48213/senior-java-developer",
        application_url="https://careers.example-corp.com/job/48213/senior-java-developer",
    )
    with patch("app.services.greenhouse_lever_enrichment_provider.greenhouse_lever_provider") as mocked_gh, \
         patch("app.services.broad_search_enrichment_provider.broad_search_provider") as mocked_broad:
        result = enrich_job_with_fallback(job)

    assert result.status == "NOT_APPLICABLE"
    mocked_gh.assert_not_called()
    mocked_broad.assert_not_called()
