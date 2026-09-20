"""Tests for app/services/job_normalization_service.py's Apply URL resolver
— the single authoritative source of truth for "Apply" vs "View Listing"
across Updated Job Search. Covers the exact regression this fixes: Apply
opening a Remotive listing page instead of the real employer destination.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.job_normalization_service import (
    classify_url_domain,
    classify_url_specificity,
    is_safe_application_url,
    rank_apply_candidates,
    resolve_apply_action,
    resolve_application_destination,
    resolve_application_url,
    select_apply_urls_from_candidates,
)


# ---------------------------------------------------------------------------
# resolve_application_destination — Part 37 test cases A-D
# ---------------------------------------------------------------------------

def test_case_a_employer_direct_application_url_resolves_to_employer():
    job = {
        "source": "generic_feed",
        "application_url": "https://company.com/careers/job/123/apply",
        "direct_url": "https://company.com/careers/job/123",
        "source_url": "https://company.com/careers",
    }
    result = resolve_application_destination(job)
    assert result["url"] == "https://company.com/careers/job/123/apply"
    assert result["type"] == "DIRECT_EMPLOYER"
    assert result["is_direct"] is True


def test_case_b_ats_hosted_url_resolves_to_employer_ats_page():
    job = {
        "source": "greenhouse",
        "application_url": None,
        "direct_url": "https://boards.greenhouse.io/example-employer/jobs/1",
        "source_url": "https://boards.greenhouse.io/example-employer",
    }
    result = resolve_application_destination(job)
    assert result["url"] == "https://boards.greenhouse.io/example-employer/jobs/1"
    assert result["type"] == "DIRECT_ATS"
    assert result["is_direct"] is True


def test_case_c_only_aggregator_listing_exists_never_claims_direct_apply():
    """Regression: the exact reported bug — a Remotive-sourced job with only
    its own listing URL must never be presented as a direct employer apply."""
    job = {
        "source": "remotive",
        "application_url": None,
        "direct_url": None,
        "source_url": "https://remotive.com/remote-jobs/software-dev/example-88888",
    }
    result = resolve_application_destination(job)
    assert result["url"] == "https://remotive.com/remote-jobs/software-dev/example-88888"
    assert result["type"] == "AGGREGATOR_LISTING"
    assert result["is_direct"] is False


def test_case_d_unsafe_url_is_rejected():
    job = {
        "source": "generic_feed",
        "application_url": "javascript:alert(1)",
        "direct_url": "https://company.com/careers/job/123",
        "source_url": "https://company.com/careers",
    }
    result = resolve_application_destination(job)
    # The unsafe application_url is skipped; the resolver falls through to
    # the next genuinely safe tier rather than failing outright.
    assert result["url"] == "https://company.com/careers/job/123"
    assert result["type"] == "DIRECT_EMPLOYER"
    assert result["is_direct"] is True


def test_all_tiers_unsafe_or_missing_returns_no_destination():
    job = {"source": "remotive", "application_url": None, "direct_url": None, "source_url": None}
    result = resolve_application_destination(job)
    assert result == {"url": None, "type": None, "is_direct": False}


def test_regression_apply_never_opens_remotive_when_direct_employer_url_exists():
    """Exact regression scenario from the report: source=REMOTIVE but a real
    employer application_url is also present — Apply must resolve to the
    employer URL, never the Remotive listing."""
    job = {
        "source": "remotive",
        "source_url": "https://remotive.com/remote-jobs/example-123",
        "application_url": "https://company.com/careers/job/123",
    }
    result = resolve_application_destination(job)
    assert result["url"] == "https://company.com/careers/job/123"
    assert result["type"] == "DIRECT_EMPLOYER"
    assert result["is_direct"] is True


def test_works_against_orm_style_object_not_just_dict():
    row = SimpleNamespace(
        source="lever", application_url=None,
        direct_url="https://jobs.lever.co/example/1", source_url="https://jobs.lever.co/example",
    )
    result = resolve_application_destination(row)
    assert result["url"] == "https://jobs.lever.co/example/1"
    assert result["type"] == "DIRECT_ATS"


def test_resolve_application_url_backward_compatible_string_return():
    job = {"source": "remotive", "application_url": None, "direct_url": None, "source_url": "https://remotive.com/x"}
    assert resolve_application_url(job) == "https://remotive.com/x"


# ---------------------------------------------------------------------------
# is_safe_application_url
# ---------------------------------------------------------------------------

def test_safe_url_accepted():
    assert is_safe_application_url("https://company.com/careers/job/123") is True
    assert is_safe_application_url("http://boards.greenhouse.io/example/jobs/1") is True


def test_unsafe_schemes_rejected():
    assert is_safe_application_url("javascript:alert(1)") is False
    assert is_safe_application_url("file:///etc/passwd") is False
    assert is_safe_application_url("data:text/html,<script>1</script>") is False


def test_local_and_private_hosts_rejected():
    assert is_safe_application_url("http://localhost:8000/apply") is False
    assert is_safe_application_url("http://127.0.0.1/apply") is False
    assert is_safe_application_url("http://0.0.0.0/apply") is False
    assert is_safe_application_url("http://192.168.1.10/apply") is False
    assert is_safe_application_url("http://10.0.0.5/apply") is False


def test_credentials_in_url_rejected():
    assert is_safe_application_url("https://user:pass@company.com/apply") is False


def test_empty_and_malformed_rejected():
    assert is_safe_application_url(None) is False
    assert is_safe_application_url("") is False
    assert is_safe_application_url("not-a-url") is False


# ---------------------------------------------------------------------------
# ATS-domain heuristic — classifies DIRECT_ATS by URL domain, not just by a
# single-ATS adapter's source name. Needed for a broad multi-employer
# provider (e.g. JSearch) whose `source` is the provider itself.
# ---------------------------------------------------------------------------

def test_broad_provider_job_with_ats_domain_url_classified_direct_ats():
    job = {
        "source": "jsearch",
        "application_url": "https://boards.greenhouse.io/some-employer/jobs/42",
        "direct_url": None,
        "source_url": None,
    }
    result = resolve_application_destination(job)
    assert result["type"] == "DIRECT_ATS"
    assert result["is_direct"] is True


def test_broad_provider_job_with_true_employer_domain_classified_direct_employer():
    job = {
        "source": "jsearch",
        "application_url": "https://www.capitalone.com/careers/jobs/12345",
        "direct_url": None,
        "source_url": "https://www.linkedin.com/jobs/view/12345",
    }
    result = resolve_application_destination(job)
    assert result["type"] == "DIRECT_EMPLOYER"
    assert result["is_direct"] is True
    assert "capitalone.com" in result["url"]


def test_broad_provider_job_with_only_a_provider_listing_never_claims_direct_apply():
    """Same AGGREGATOR_LISTING classification used for Remotive — a broad
    provider's own listing-only result (no verified employer/ATS URL) must
    never be presented as a direct Apply either."""
    job = {
        "source": "jsearch",
        "application_url": None,
        "direct_url": None,
        "source_url": "https://www.indeed.com/viewjob?jk=999",
    }
    result = resolve_application_destination(job)
    assert result["type"] == "AGGREGATOR_LISTING"
    assert result["is_direct"] is False


# ---------------------------------------------------------------------------
# classify_url_domain — semantic classification used to decide DIRECT_EMPLOYER
# vs DIRECT_ATS vs AGGREGATOR_LISTING independent of any provider-supplied
# "is direct" flag. Never hardcodes a specific employer — every case here
# uses a generic/fictional company to prove the logic is domain-pattern
# based, not a lookup table.
# ---------------------------------------------------------------------------

def test_classify_subdomain_of_employer_website_is_employer():
    result = classify_url_domain(
        "https://careers.example-corp.com/job/123",
        employer_website="https://www.example-corp.com",
    )
    assert result == "employer"


def test_classify_alternate_branded_career_domain_via_employer_website():
    """A distinct-but-related career domain (not a subdomain) — matched via
    the employer's own base name plus a generic career-suffix word, not any
    specific company's name."""
    result = classify_url_domain(
        "https://www.acmewidgetscareers.com/job/123",
        employer_website="https://www.acmewidgets.com",
    )
    assert result == "employer"


def test_classify_employer_matched_from_company_name_alone_no_website():
    """Backfill scenario: no employer_website available, only the company's
    display name — still resolves to 'employer' generically."""
    result = classify_url_domain("https://careers.globex.com/job/9", employer_name="Globex")
    assert result == "employer"


def test_classify_icims_hosted_url_is_ats():
    result = classify_url_domain("https://careers-somecompany.icims.com/jobs/42")
    assert result == "ats"


def test_classify_greenhouse_lever_workday_hosted_urls_are_ats():
    assert classify_url_domain("https://boards.greenhouse.io/some-employer/jobs/1") == "ats"
    assert classify_url_domain("https://jobs.lever.co/some-employer/1") == "ats"
    assert classify_url_domain("https://example.wd1.myworkdayjobs.com/en-US/careers/job/1") == "ats"


def test_classify_linkedin_is_aggregator():
    assert classify_url_domain("https://www.linkedin.com/jobs/view/12345") == "aggregator"


def test_classify_indeed_is_aggregator():
    assert classify_url_domain("https://www.indeed.com/viewjob?jk=abc123") == "aggregator"


def test_classify_dice_is_aggregator():
    assert classify_url_domain("https://www.dice.com/job-detail/abc-123") == "aggregator"


def test_classify_aggregator_never_overridden_by_unrelated_employer_metadata():
    """Even when an (unrelated) employer_website/employer_name is supplied,
    a known aggregator domain must still classify as aggregator — never
    trusted into Apply just because some employer metadata happened to be
    present on the same job record."""
    result = classify_url_domain(
        "https://www.linkedin.com/jobs/view/999",
        employer_website="https://www.example-corp.com",
        employer_name="Example Corp",
    )
    assert result == "aggregator"


def test_classify_unrelated_unknown_domain_returns_none():
    """A domain that's neither a known aggregator, a known ATS, nor
    verifiably the employer's own is left unclassified — never guessed."""
    result = classify_url_domain(
        "https://some-random-vendor.example/apply/1",
        employer_website="https://www.example-corp.com",
        employer_name="Example Corp",
    )
    assert result is None


def test_classify_unsafe_or_empty_url_returns_none():
    assert classify_url_domain("javascript:alert(1)") is None
    assert classify_url_domain(None) is None
    assert classify_url_domain("") is None


# ---------------------------------------------------------------------------
# resolve_apply_action — composes destination TYPE, SPECIFICITY, and
# AVAILABILITY into the one action contract the frontend renders (never
# re-derived on the frontend). All three are deliberately independent
# inputs here (test matrix items 13-16 + the specificity fix's items 1-2,
# 7-9, 12). Specific-job URLs below use a realistic multi-digit requisition
# id — classify_url_specificity requires 4+ digits to count as an
# identifier, matching every real ATS/employer id format observed live.
# ---------------------------------------------------------------------------

def test_action_direct_employer_specific_active_is_apply():
    job = {
        "source": "jsearch", "application_url": "https://careers.example-corp.com/job/48213/software-engineer",
        "direct_url": None, "source_url": None, "availability_status": "ACTIVE",
    }
    action = resolve_apply_action(job)
    assert action["type"] == "DIRECT_EMPLOYER"
    assert action["destination_specificity"] == "SPECIFIC_JOB"
    assert action["action_label"] == "Apply"
    assert action["can_apply"] is True


def test_action_direct_employer_specific_expired_is_job_closed():
    job = {
        "source": "jsearch", "application_url": "https://careers.example-corp.com/job/48213/software-engineer",
        "direct_url": None, "source_url": None, "availability_status": "EXPIRED",
    }
    action = resolve_apply_action(job)
    assert action["type"] == "DIRECT_EMPLOYER"
    assert action["destination_specificity"] == "SPECIFIC_JOB"
    assert action["action_label"] == "Job Closed"
    assert action["can_apply"] is False


def test_action_direct_employer_specific_unknown_availability_is_safe_fallback_label():
    job = {
        "source": "jsearch", "application_url": "https://careers.example-corp.com/job/48213/software-engineer",
        "direct_url": None, "source_url": None, "availability_status": "UNKNOWN",
    }
    action = resolve_apply_action(job)
    assert action["type"] == "DIRECT_EMPLOYER"
    assert action["destination_specificity"] == "SPECIFIC_JOB"
    assert action["action_label"] == "Open Employer Site"
    # UNKNOWN never blocks navigation — but it's also never a VERIFIED
    # Apply (can_apply means "verified specific + active", not
    # "clickable" — the frontend still renders a real, clickable button).
    assert action["can_apply"] is False


def test_action_direct_employer_generic_page_is_never_apply_even_when_active():
    """Root-cause regression: a real employer domain returning HTTP 200 is
    NOT sufficient for Apply — a generic careers/search page must never be
    presented as a verified direct application, regardless of availability."""
    job = {
        "source": "jsearch", "application_url": "https://www.example.jobs/JobSearchResults?keyword=java",
        "direct_url": None, "source_url": None, "availability_status": "ACTIVE",
    }
    action = resolve_apply_action(job)
    assert action["type"] == "DIRECT_EMPLOYER"
    assert action["destination_specificity"] == "GENERIC_EMPLOYER_PAGE"
    assert action["action_label"] == "Open Employer Site"
    assert action["can_apply"] is False


def test_action_direct_ats_specific_active_is_apply():
    job = {
        "source": "jsearch", "application_url": "https://boards.greenhouse.io/example-employer/jobs/48213",
        "direct_url": None, "source_url": None, "availability_status": "ACTIVE",
    }
    action = resolve_apply_action(job)
    assert action["type"] == "DIRECT_ATS"
    assert action["destination_specificity"] == "SPECIFIC_JOB"
    assert action["action_label"] == "Apply"
    assert action["can_apply"] is True


def test_action_aggregator_listing_ignores_availability_and_specificity_entirely():
    """An aggregator listing is always 'View Listing' regardless of
    whatever (irrelevant) availability_status happens to be stored —
    availability/specificity are only ever checked against a genuine
    employer/ATS URL."""
    job = {
        "source": "jsearch", "application_url": None, "direct_url": None,
        "source_url": "https://www.linkedin.com/jobs/view/1", "availability_status": "EXPIRED",
    }
    action = resolve_apply_action(job)
    assert action["type"] == "AGGREGATOR_LISTING"
    assert action["action_label"] == "View Listing"
    assert action["can_apply"] is True


def test_action_no_url_at_all_disables_apply():
    job = {"source": "jsearch", "application_url": None, "direct_url": None, "source_url": None}
    action = resolve_apply_action(job)
    assert action["url"] is None
    assert action["can_apply"] is False


# ---------------------------------------------------------------------------
# classify_url_specificity — deterministic URL-structure classification of
# SPECIFIC_JOB vs GENERIC_EMPLOYER_PAGE vs UNKNOWN, independent of domain
# classification and availability. Never hardcodes a specific employer
# (test matrix items 1-6, 10 of the specificity fix).
# ---------------------------------------------------------------------------

def test_specificity_path_with_numeric_job_id_is_specific():
    assert classify_url_specificity("https://careers.example.com/job/48213/software-engineer") == "SPECIFIC_JOB"


def test_specificity_ats_requisition_style_id_is_specific():
    assert classify_url_specificity("https://careers.example.com/global/en/job/R215394/Software-Engineer") == "SPECIFIC_JOB"


def test_specificity_query_param_job_id_is_specific():
    assert classify_url_specificity("https://www.example.jobs/JobDetails?jobid=R266225&jobtitle=Java-Developer") == "SPECIFIC_JOB"


def test_specificity_uuid_path_segment_is_specific():
    assert classify_url_specificity("https://careers.example.com/job/550e8400-e29b-41d4-a716-446655440000") == "SPECIFIC_JOB"


def test_specificity_trailing_hyphenated_id_is_specific():
    assert classify_url_specificity("https://careers.example.com/job/senior-project-manager-1443152803") == "SPECIFIC_JOB"


def test_specificity_mid_slug_embedded_id_is_specific():
    """Regression: a requisition id embedded in the MIDDLE of a slug
    (region/category code, then id, then title) — verified against a real,
    currently-active live posting whose id was missed by an
    earlier leading/trailing-only version of this check."""
    assert classify_url_specificity("https://www.example.com/job/26-158712-java-developer-city-st/") == "SPECIFIC_JOB"


def test_specificity_generic_careers_jobs_page_is_generic():
    assert classify_url_specificity("https://careers.example.com/jobs") == "GENERIC_EMPLOYER_PAGE"


def test_specificity_generic_search_jobs_page_is_generic():
    assert classify_url_specificity("https://careers.example.com/search-jobs") == "GENERIC_EMPLOYER_PAGE"


def test_specificity_generic_job_search_results_page_is_generic():
    """Root-cause regression: this is exactly the shape of URL that was
    wrongly treated as a valid Apply destination — Uline's
    .../JobSearchResults?... general results page, generalized here to a
    fictional domain (never hardcoding the specific employer)."""
    assert classify_url_specificity("https://www.example.jobs/JobSearchResults?keyword=java&location=") == "GENERIC_EMPLOYER_PAGE"


def test_specificity_careers_homepage_is_generic():
    assert classify_url_specificity("https://careers.example.com/") == "GENERIC_EMPLOYER_PAGE"
    assert classify_url_specificity("https://careers.example.com") == "GENERIC_EMPLOYER_PAGE"


def test_specificity_ambiguous_page_with_no_identifier_is_unknown_never_specific():
    """Do not over-classify: a path that's neither an obvious listing
    keyword nor carries any identifier is UNKNOWN, never guessed SPECIFIC."""
    assert classify_url_specificity("https://careers.example.com/department/engineering") == "UNKNOWN"


def test_specificity_unsafe_or_empty_url_is_unknown():
    assert classify_url_specificity("javascript:alert(1)") == "UNKNOWN"
    assert classify_url_specificity(None) == "UNKNOWN"
    assert classify_url_specificity("") == "UNKNOWN"


# ---------------------------------------------------------------------------
# rank_apply_candidates / select_apply_urls_from_candidates — the shared
# candidate-resolution pipeline (Steps 1-3 of the coverage-enhancement fix):
# every safe-looking apply_options URL is preserved and annotated, never
# silently discarded down to a single guess.
# ---------------------------------------------------------------------------

def test_rank_candidates_preserves_every_option_never_discards_alternates():
    options = [
        {"apply_link": "https://careers.example-corp.com/job/1", "is_direct": True, "publisher": "Example Corp"},
        {"apply_link": "https://boards.greenhouse.io/example-corp/jobs/2", "is_direct": False, "publisher": "Greenhouse"},
        {"apply_link": "https://www.linkedin.com/jobs/view/3", "is_direct": False, "publisher": "LinkedIn"},
    ]
    candidates = rank_apply_candidates(options, employer_website="https://www.example-corp.com")
    assert len(candidates) == 3
    assert {c["url"] for c in candidates} == {opt["apply_link"] for opt in options}


def test_rank_candidates_orders_employer_before_ats_before_aggregator():
    options = [
        {"apply_link": "https://www.linkedin.com/jobs/view/3", "is_direct": False, "publisher": "LinkedIn"},
        {"apply_link": "https://boards.greenhouse.io/example-corp/jobs/2", "is_direct": False, "publisher": "Greenhouse"},
        {"apply_link": "https://careers.example-corp.com/job/1", "is_direct": True, "publisher": "Example Corp"},
    ]
    candidates = rank_apply_candidates(options, employer_website="https://www.example-corp.com")
    assert [c["domain_class"] for c in candidates] == ["employer", "ats", "aggregator"]


def test_rank_candidates_deduplicates_identical_urls():
    options = [
        {"apply_link": "https://careers.example-corp.com/job/1", "is_direct": True, "publisher": None},
        {"apply_link": "https://careers.example-corp.com/job/1", "is_direct": True, "publisher": "Duplicate"},
    ]
    candidates = rank_apply_candidates(options, employer_website="https://www.example-corp.com")
    assert len(candidates) == 1


def test_select_urls_from_candidates_picks_best_direct_and_best_listing():
    options = [
        {"apply_link": "https://www.linkedin.com/jobs/view/3", "is_direct": False, "publisher": "LinkedIn"},
        {"apply_link": "https://careers.example-corp.com/job/1", "is_direct": True, "publisher": "Example Corp"},
    ]
    candidates = rank_apply_candidates(options, employer_website="https://www.example-corp.com")
    application_url, direct_url, source_url = select_apply_urls_from_candidates(candidates)
    assert application_url == "https://careers.example-corp.com/job/1"
    assert source_url == "https://www.linkedin.com/jobs/view/3"


def test_select_urls_from_candidates_falls_back_when_nothing_classifies_as_listing():
    candidates = rank_apply_candidates(
        [{"apply_link": "https://careers.example-corp.com/job/1", "is_direct": True, "publisher": None}],
        employer_website="https://www.example-corp.com",
    )
    application_url, direct_url, source_url = select_apply_urls_from_candidates(
        candidates, fallback_source_url="https://careers.example-corp.com/job/1",
    )
    assert source_url == "https://careers.example-corp.com/job/1"


def test_select_urls_from_candidates_empty_list_returns_all_none():
    application_url, direct_url, source_url = select_apply_urls_from_candidates([])
    assert (application_url, direct_url, source_url) == (None, None, None)
