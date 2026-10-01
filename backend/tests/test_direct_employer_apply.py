"""Direct-to-employer apply resolution. All HTTP/DNS is mocked."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.job_enrichment_context import EnrichmentBudget
from app.services.job_enrichment_service import enrich_job, apply_enrichment_result
from app.services.job_normalization_service import incoming_destination_wins, resolve_apply_action
from app.services.provider_apply_resolver import (
    ats_platform_from_url,
    collect_outbound_apply_urls,
    detect_ats_from_job,
    provider_fallback_label,
)


def _job(**overrides):
    values = dict(
        source="jsearch", company="Acme Widgets", title="Senior Java Developer",
        location="Austin, TX", application_url=None, direct_url=None, source_url=None,
        unverified_application_url=None, candidate_application_type=None,
        availability_status="UNKNOWN", verified_destination_url=None,
        enrichment_diagnostics=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_dice_listing_with_employer_ats_url_becomes_apply():
    job = _job(
        source="apify_dice",
        source_url="https://www.dice.com/job-detail/4821301-senior-java-developer",
        unverified_application_url="https://boards.greenhouse.io/acmewidgets/jobs/48213",
        availability_status="ACTIVE",
        application_url="https://boards.greenhouse.io/acmewidgets/jobs/48213",
        direct_url="https://boards.greenhouse.io/acmewidgets/jobs/48213",
        verified_destination_url="https://boards.greenhouse.io/acmewidgets/jobs/48213",
    )
    action = resolve_apply_action(job)
    assert action["action_label"] == "Apply"
    assert action["url"] == "https://boards.greenhouse.io/acmewidgets/jobs/48213"
    assert "dice.com" not in action["url"]


def test_jsearch_employer_apply_url_wins_over_listing():
    job = _job(
        source="jsearch",
        source_url="https://www.linkedin.com/jobs/view/48213019",
        application_url="https://careers.acmewidgets.com/job/48213/senior-java-developer",
        direct_url="https://careers.acmewidgets.com/job/48213/senior-java-developer",
        availability_status="ACTIVE",
    )
    action = resolve_apply_action(job)
    assert action["action_label"] == "Apply"
    assert action["url"] == "https://careers.acmewidgets.com/job/48213/senior-java-developer"


def test_adzuna_redirect_resolves_to_employer_apply():
    job = _job(
        source="adzuna",
        source_url="https://www.adzuna.com/redirect/48213019",
        unverified_application_url="https://www.adzuna.com/redirect/48213019",
        application_url="https://careers.acmewidgets.com/job/48213/senior-java-developer",
        direct_url="https://careers.acmewidgets.com/job/48213/senior-java-developer",
        availability_status="ACTIVE",
        verified_destination_url="https://careers.acmewidgets.com/job/48213/senior-java-developer",
    )
    action = resolve_apply_action(job)
    assert action["action_label"] == "Apply"
    assert action["url"].startswith("https://careers.acmewidgets.com")


def test_provider_listing_without_employer_url_is_view_on_provider():
    action = resolve_apply_action(_job(
        source="apify_dice",
        source_url="https://www.dice.com/job-detail/4821301-senior-java-developer",
    ))
    assert action["action_label"] == "View on Dice"
    assert action["action_label"] != "Apply"
    assert action["url"] == "https://www.dice.com/job-detail/4821301-senior-java-developer"


def test_provider_homepage_is_never_apply():
    action = resolve_apply_action(_job(source_url="https://www.dice.com"))
    assert action["action_label"] != "Apply"
    assert action["action_label"] == "View Listing"


def test_company_careers_homepage_opens_employer_site():
    action = resolve_apply_action(_job(
        application_url="https://careers.acmewidgets.com/careers",
        availability_status="ACTIVE",
    ))
    assert action["action_label"] == "Open Employer Site"


def test_exact_greenhouse_posting_is_apply():
    action = resolve_apply_action(_job(
        application_url="https://boards.greenhouse.io/acmewidgets/jobs/48213",
        availability_status="ACTIVE",
    ))
    assert action["action_label"] == "Apply"
    assert action["type"] == "DIRECT_ATS"
    assert ats_platform_from_url(action["url"]) == "greenhouse"


def test_exact_lever_posting_is_apply():
    action = resolve_apply_action(_job(
        application_url="https://jobs.lever.co/acmewidgets/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        availability_status="ACTIVE",
    ))
    assert action["action_label"] == "Apply"
    assert ats_platform_from_url(action["url"]) == "lever"


def test_exact_workday_requisition_is_apply():
    action = resolve_apply_action(_job(
        application_url="https://acmewidgets.wd1.myworkdayjobs.com/en-US/Careers/job/Austin/Senior-Java-Developer_R48213",
        availability_status="ACTIVE",
    ))
    assert action["action_label"] == "Apply"
    assert ats_platform_from_url(action["url"]) == "workday"


def test_wrong_employer_ats_url_is_rejected():
    action = resolve_apply_action(_job(
        source="apify_dice",
        company="Company A",
        source_url="https://www.dice.com/job-detail/4821301-senior-java-developer",
        unverified_application_url="https://company-b.wd1.myworkdayjobs.com/en-US/careers/job/12345",
    ))
    assert action["action_label"] == "View on Dice"
    assert "company-b" not in (action["url"] or "")


def test_wrong_job_title_is_rejected_during_enrichment():
    job = _job(
        title="Senior Java Developer",
        source_url="https://www.linkedin.com/jobs/view/48213019",
        unverified_application_url="https://careers.acmewidgets.com/job/99110/marketing-manager",
    )
    from app.services.job_enrichment_service import EnrichmentCandidateResult
    with patch("app.services.job_enrichment_service.availability.check_url_availability") as check:
        result = enrich_job(job, provider=lambda query: [
            EnrichmentCandidateResult(
                url="https://careers.acmewidgets.com/job/99110/marketing-manager",
                matched_title="Marketing Manager", matched_company="Acme Widgets",
                matched_location="Austin, TX",
            ),
        ], employer_website="https://www.acmewidgets.com")
    check.assert_not_called()
    assert result.status == "REJECTED"
    assert resolve_apply_action(job)["action_label"] != "Apply"


def test_closed_posting_is_job_closed():
    action = resolve_apply_action(_job(
        application_url="https://careers.acmewidgets.com/job/48213/senior-java-developer",
        availability_status="EXPIRED",
    ))
    assert action["action_label"] == "Job Closed"


def test_unsafe_redirect_does_not_become_apply():
    action = resolve_apply_action(_job(
        source="adzuna",
        unverified_application_url="https://www.adzuna.com/redirect/48213019",
        source_url="https://www.adzuna.com/jobs",
        enrichment_diagnostics='{"candidates":[{"domain":"127.0.0.1","reason":"final_unsafe_url"}]}',
    ))
    assert action["action_label"] != "Apply"


def test_localhost_is_rejected():
    action = resolve_apply_action(_job(
        unverified_application_url="http://127.0.0.1/job/48213",
        source_url="https://www.dice.com/jobs",
    ))
    assert action["action_label"] != "Apply"
    assert action["url"] != "http://127.0.0.1/job/48213"


def test_verified_employer_destination_is_not_overwritten_by_provider_duplicate():
    verified = "https://boards.greenhouse.io/acmewidgets/jobs/48213"
    generic = "https://www.adzuna.com/details/48213019"
    assert incoming_destination_wins(generic, verified) is False
    action = resolve_apply_action(_job(
        application_url=verified, direct_url=verified, source_url=generic, availability_status="ACTIVE",
    ))
    assert action["url"] == verified
    assert action["action_label"] == "Apply"


def test_unverified_matching_ats_url_is_preferred_over_dice_listing():
    job = _job(
        source="apify_dice",
        source_url="https://www.dice.com/job-detail/4821301-senior-java-developer",
        unverified_application_url="https://boards.greenhouse.io/acmewidgets/jobs/48213",
    )
    action = resolve_apply_action(job)
    assert action["url"] == "https://boards.greenhouse.io/acmewidgets/jobs/48213"
    assert action["action_label"] == "Open Employer Site"
    assert action["type"] == "DIRECT_ATS"


def test_card_and_details_share_backend_action():
    job = _job(source="adzuna", source_url="https://www.adzuna.com/details/48213019")
    assert resolve_apply_action(job)["action_label"] == resolve_apply_action(job)["action_label"] == "View on Adzuna"


def test_adzuna_redirect_is_not_collected_as_an_outbound_input():
    job = _job(source="adzuna", source_url="https://www.adzuna.com/redirect/48213019")
    assert collect_outbound_apply_urls(job) == []


def test_provider_listing_in_candidate_field_is_never_collected():
    job = _job(source="adzuna", unverified_application_url="https://www.adzuna.com/redirect/48213019")
    assert collect_outbound_apply_urls(job) == []


def test_dice_html_listing_is_not_treated_as_followable_redirect():
    job = _job(source="apify_dice", source_url="https://www.dice.com/job-detail/4821301-senior-java-developer")
    assert collect_outbound_apply_urls(job) == []


def test_workday_hint_skips_greenhouse_lever_lookup():
    from app.services.greenhouse_lever_enrichment_provider import greenhouse_lever_provider
    from app.services.job_enrichment_service import EnrichmentQuery

    job = _job(unverified_application_url="https://acmewidgets.wd1.myworkdayjobs.com/en-US/Careers/job/R48213")
    hint, token = detect_ats_from_job(job)
    assert hint == "workday"
    assert greenhouse_lever_provider(EnrichmentQuery(
        company="Acme Widgets", title="Senior Java Developer", ats_hint=hint, ats_board_token=token,
    )) == []


def test_fallback_label_is_truthful():
    assert provider_fallback_label(_job(source="apify_dice"), "https://www.dice.com/job-detail/1") == "View on Dice"
    assert provider_fallback_label(_job(source="adzuna"), "https://www.adzuna.com/details/48213019") == "View on Adzuna"


def test_enrichment_budget_unchanged():
    budget = EnrichmentBudget(max_jobs=5)
    assert budget.max_listing_jobs == 2
    assert [budget.reserve((f"listing-{i}",), is_direct=False) for i in range(3)] == [True, True, False]
    assert [budget.reserve((f"direct-{i}",), is_direct=True) for i in range(3)] == [True, True, True]


def test_dice_apply_url_is_verified_through_enrichment_without_live_network():
    job = _job(
        source="apify_dice",
        source_url="https://www.dice.com/job-detail/4821301-senior-java-developer",
        unverified_application_url="https://boards.greenhouse.io/acmewidgets/jobs/48213",
    )
    with patch("app.services.job_enrichment_service.availability.check_url_availability", return_value={
        "status": "ACTIVE", "reason": "http_200", "final_url": None,
    }):
        result = enrich_job(job, provider=lambda query: [])
    assert result.status == "ENRICHED"
    assert result.url == "https://boards.greenhouse.io/acmewidgets/jobs/48213"
    apply_enrichment_result(job, result)
    action = resolve_apply_action(job)
    assert action["action_label"] == "Apply"
    assert action["url"] == result.url
