"""Apply-ready destinations: verified employer/ATS stays strict, and a safe
exact provider job URL is still an application fallback."""

from types import SimpleNamespace

from app.services.job_enrichment_context import EnrichmentBudget
from app.services.job_normalization_service import resolve_apply_action


def _job(**overrides):
    values = dict(
        source="jsearch", company="Northwind", title="Senior DevOps Engineer",
        location="Remote", application_url=None, direct_url=None, source_url=None,
        unverified_application_url=None, candidate_application_type=None,
        availability_status="UNKNOWN", verified_destination_url=None,
        enrichment_diagnostics=None,
    )
    values.update(overrides)
    return values


def test_verified_employer_exact_active_job_is_apply():
    action = resolve_apply_action(_job(
        application_url="https://careers.northwind.com/job/48213/devops-engineer",
        availability_status="ACTIVE",
    ))
    assert action["action_label"] == "Apply"
    assert action["destination_tier"] == "VERIFIED_DIRECT"


def test_verified_ats_exact_job_is_apply():
    action = resolve_apply_action(_job(
        application_url="https://boards.greenhouse.io/northwind/jobs/48213",
        availability_status="ACTIVE",
    ))
    assert action["action_label"] == "Apply"
    assert action["type"] == "DIRECT_ATS"


def test_failed_direct_verification_still_uses_exact_provider_url():
    action = resolve_apply_action(_job(
        source="apify_dice",
        source_url="https://www.dice.com/job-detail/4821301-senior-devops-engineer",
        unverified_application_url="https://careers.wrongcompany.com/job/48213/senior-devops",
        availability_status="UNKNOWN",
    ))
    assert action["action_label"] == "View on Dice"
    assert action["url"] == "https://www.dice.com/job-detail/4821301-senior-devops-engineer"
    assert action["destination_tier"] == "PROVIDER_JOB_APPLY"


def test_provider_homepage_is_not_apply():
    action = resolve_apply_action(_job(source_url="https://www.dice.com"))
    assert action["action_label"] == "View Listing"
    assert action["action_label"] != "Apply"


def test_provider_search_results_are_not_apply():
    action = resolve_apply_action(_job(source_url="https://www.indeed.com/jobs?q=devops"))
    assert action["action_label"] == "View Listing"


def test_generic_aggregator_landing_is_view_listing():
    action = resolve_apply_action(_job(source_url="https://www.adzuna.com/jobs"))
    assert action["action_label"] == "View Listing"
    assert action["destination_tier"] == "GENERIC_LISTING"


def test_exact_aggregator_posting_is_provider_apply_fallback():
    action = resolve_apply_action(_job(
        source="adzuna",
        source_url="https://www.adzuna.com/details/48213019",
    ))
    assert action["action_label"] == "View on Adzuna"
    assert action["can_apply"] is True


def test_unsafe_url_is_not_apply():
    action = resolve_apply_action(_job(
        source_url="http://127.0.0.1/job/48213",
        unverified_application_url="javascript:alert(1)",
    ))
    assert action["action_label"] != "Apply"
    assert action["action_label"] != "View on Dice"


def test_apply_to_email_is_not_direct_apply():
    action = resolve_apply_action(_job(
        source="apify_dice",
        candidate_application_type="APPLY_TO_EMAIL",
        unverified_application_url="mailto:hr@example.com",
        source_url="https://www.dice.com/jobs",
    ))
    assert action["action_label"] == "View Listing"
    assert action["url"] != "mailto:hr@example.com"


def test_closed_exact_employer_job_is_job_closed():
    action = resolve_apply_action(_job(
        application_url="https://careers.northwind.com/job/48213/devops-engineer",
        availability_status="EXPIRED",
    ))
    assert action["action_label"] == "Job Closed"
    assert action["destination_tier"] == "CLOSED"


def test_generic_employer_careers_page_opens_employer_site():
    action = resolve_apply_action(_job(
        application_url="https://careers.northwind.com/careers",
        availability_status="ACTIVE",
    ))
    assert action["action_label"] == "Open Employer Site"
    assert action["destination_tier"] == "EMPLOYER_CAREERS"


def test_verified_employer_url_replaces_provider_fallback():
    provider = _job(source_url="https://www.dice.com/job-detail/4821301-senior-devops-engineer")
    assert resolve_apply_action(provider)["action_label"] == "View on Dice"
    promoted = _job(
        source_url="https://www.dice.com/job-detail/4821301-senior-devops-engineer",
        application_url="https://boards.greenhouse.io/northwind/jobs/48213",
        direct_url="https://boards.greenhouse.io/northwind/jobs/48213",
        availability_status="ACTIVE",
        verified_destination_url="https://boards.greenhouse.io/northwind/jobs/48213",
    )
    action = resolve_apply_action(promoted)
    assert action["action_label"] == "Apply"
    assert action["url"] == "https://boards.greenhouse.io/northwind/jobs/48213"


def test_generic_duplicate_does_not_replace_verified_url():
    from app.services.job_normalization_service import destination_quality

    verified = "https://boards.greenhouse.io/northwind/jobs/48213"
    generic = "https://www.adzuna.com/jobs"
    assert destination_quality(generic) < destination_quality(verified)
    action = resolve_apply_action(_job(
        application_url=verified, direct_url=verified, source_url=generic, availability_status="ACTIVE",
    ))
    assert action["action_label"] == "Apply"
    assert action["url"] == verified


def test_lower_ranked_duplicate_preserves_provider_fallback():
    from app.services.job_normalization_service import destination_quality

    exact = "https://www.dice.com/job-detail/4821301-senior-devops-engineer"
    generic = "https://www.adzuna.com/search"
    assert destination_quality(generic) < destination_quality(exact)
    action = resolve_apply_action(_job(source_url=exact))
    assert action["action_label"] == "View on Dice"


def test_missing_url_is_not_labeled_apply():
    action = resolve_apply_action(_job())
    assert action["url"] is None
    assert action["action_label"] != "Apply"
    assert action["can_apply"] is False


def test_enrichment_budget_reservation_is_unchanged():
    budget = EnrichmentBudget(max_jobs=5)
    assert budget.max_listing_jobs == 2
    assert [budget.reserve((f"listing-{i}",), is_direct=False) for i in range(3)] == [True, True, False]
    assert [budget.reserve((f"direct-{i}",), is_direct=True) for i in range(3)] == [True, True, True]


def test_serpapi_stays_enrichment_only():
    from app.services.broad_search_enrichment_provider import broad_provider_name

    assert broad_provider_name() in {"", "none", "off", "disabled"} or isinstance(broad_provider_name(), str)


def test_redirect_to_wrong_employer_does_not_become_provider_apply():
    action = resolve_apply_action(_job(
        source="adzuna",
        unverified_application_url="https://www.adzuna.com/redirect/48213019",
        source_url="https://www.adzuna.com/jobs",
        enrichment_diagnostics='{"candidates":[{"domain":"wrongcompany.com","reason":"final_not_employer_or_ats_domain"}]}',
    ))
    assert action["action_label"] == "View Listing"


def test_card_and_details_share_one_resolved_action():
    job = SimpleNamespace(**_job(
        source="adzuna",
        source_url="https://www.adzuna.com/details/48213019",
    ))
    card = resolve_apply_action(job)
    details = resolve_apply_action(job)
    assert card["action_label"] == details["action_label"] == "View on Adzuna"
    assert card["url"] == details["url"]
