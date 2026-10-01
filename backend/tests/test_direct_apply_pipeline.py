"""Production discovery/destination regression tests. Never live provider I/O."""
import json
import socket
from datetime import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from app.models.discovered_job_model import DiscoveredJob
from app.models.user_preference_model import UserPreference
from app.services import job_change_detection_service as persistence
from app.services import greenhouse_lever_enrichment_provider as ats
from app.services import broad_search_enrichment_provider as broad
from app.services import job_enrichment_service as enrichment
from app.services.job_normalization_service import build_normalized_job, resolve_apply_action
from app.services.job_enrichment_context import enrichment_scope


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Provider requests and DNS must be mocked")
    monkeypatch.setattr(requests.Session, "request", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(ats, "greenhouse_lever_provider", MagicMock(return_value=[]))
    monkeypatch.setenv("JOB_ENRICHMENT_BROAD_PROVIDER", "none")
    monkeypatch.setenv("JOB_ENRICHMENT_MAX_JOBS_PER_REFRESH", "5")
    monkeypatch.setattr(enrichment.availability, "check_url_availability", MagicMock(return_value={
        "status": "ACTIVE", "reason": "http_200", "final_url": None,
    }))


def job(source="adzuna", job_id="1", **overrides):
    values = dict(source=source, source_mode="live", source_job_id=job_id, company="Acme Widgets",
                  title="Senior Java Developer", location="Austin, TX", description="Java backend developer working on distributed services.",
                  source_url=f"https://www.adzuna.com/details/{job_id}")
    values.update(overrides)
    return build_normalized_job(**values)


def preference(user_id, title):
    return UserPreference(user_id=user_id, job_title=title, location="Austin, TX", experience="5",
                          work_mode="", job_type="", skills="Java", schedule_time=time(8, 0))


def candidate(url="https://careers.acmewidgets.com/job/12345/senior-java-developer", **overrides):
    values = dict(url=url, matched_title="Senior Java Developer", matched_company="Acme Widgets", matched_location="Austin, TX")
    values.update(overrides)
    return enrichment.EnrichmentCandidateResult(**values)


def enable_serpapi(monkeypatch, options):
    from app.services import serpapi_google_jobs_client as serp
    monkeypatch.setenv("JOB_ENRICHMENT_BROAD_PROVIDER", "serpapi_google_jobs")
    monkeypatch.setenv("JOB_ENRICHMENT_BROAD_API_KEY", "mock-key")
    response = MagicMock(status_code=200)
    response.json.return_value = {"jobs_results": [{"title": "Senior Java Developer", "company_name": "Acme Widgets",
        "location": "Austin, TX", "apply_options": [{"link": url} for url in options]}]}
    mocked = MagicMock(return_value=response)
    monkeypatch.setattr(serp.url_safety, "safe_get", mocked)
    return mocked


@pytest.mark.parametrize("source", ["adzuna", "jsearch"])
def test_listing_only_reaches_real_serpapi_client_contract_and_apply(db_session, monkeypatch, source):
    http = enable_serpapi(monkeypatch, [candidate().url])
    persistence.process_source_jobs(db_session, source, [job(source)])
    row = db_session.query(DiscoveredJob).one()
    assert resolve_apply_action(row)["action_label"] == "Apply"
    assert row.direct_url_source == "serpapi_google_jobs_enrichment"
    assert row.verified_destination_url == row.application_url
    http.assert_called_once()
    params = http.call_args.kwargs["params"]
    assert params["engine"] == "google_jobs"
    assert "location" not in params and "start" not in params and "next_page_token" not in params


def test_serpapi_aggregators_only_remain_listing(db_session, monkeypatch):
    enable_serpapi(monkeypatch, ["https://linkedin.com/jobs/view/12345", "https://indeed.com/viewjob?jk=12345"])
    persistence.process_source_jobs(db_session, "adzuna", [job()])
    assert resolve_apply_action(db_session.query(DiscoveredJob).one())["action_label"] == "View Listing"


@pytest.mark.parametrize("source", ["jsearch", "apify_dice"])
def test_provider_candidate_uses_shared_verifier_before_any_lookup(db_session, source):
    persistence.process_source_jobs(db_session, source, [job(source, unverified_application_url=candidate().url, candidate_application_type="APPLY_TO_URL")])
    row = db_session.query(DiscoveredJob).one()
    assert row.unverified_application_url == candidate().url
    assert resolve_apply_action(row)["action_label"] == "Apply"
    ats.greenhouse_lever_provider.assert_not_called()


@pytest.mark.parametrize("url", [
    "https://careers.wrongcompany.com/job/12345/senior-java-developer",
    "https://careers.acmewidgets.com/job/12345/marketing-manager",
    "http://127.0.0.1/job/12345", "http://169.254.169.254/job/12345", "javascript:alert(1)",
    "https://user:secret@careers.acmewidgets.com/job/12345",
])
def test_bad_provider_candidate_never_promoted(db_session, url):
    persistence.process_source_jobs(db_session, "apify_dice", [job("apify_dice", unverified_application_url=url)])
    row = db_session.query(DiscoveredJob).one()
    assert row.application_url is None
    assert resolve_apply_action(row)["action_label"] == "View Listing"
    assert row.enrichment_diagnostics


def test_direct_candidate_diagnostics_record_verification_stages_without_url(db_session):
    persistence.process_source_jobs(
        db_session,
        "apify_dice",
        [job("apify_dice", unverified_application_url="https://careers.wrongcompany.com/job/12345/senior-java-developer")],
    )
    row = db_session.query(DiscoveredJob).one()
    diagnostics = json.loads(row.enrichment_diagnostics)["candidates"]
    assert any(item["reason"] == "outbound_candidate_found" for item in diagnostics)
    diagnostic = next(item for item in diagnostics if item.get("reason") == "not_employer_or_ats_domain")
    assert diagnostic["domain"] == "careers.wrongcompany.com"
    assert diagnostic["url_safety"] == "PASSED"
    assert diagnostic["employer_identity"] == "UNRECOGNIZED_DOMAIN"
    assert diagnostic["title_match"] == "NOT_EVALUATED"
    assert diagnostic["specificity"] == "NOT_EVALUATED"
    assert diagnostic["availability"] is None
    assert diagnostic["reason"] == "not_employer_or_ats_domain"
    assert "https://" not in row.enrichment_diagnostics


def test_email_application_never_promoted_or_looked_up(db_session):
    persistence.process_source_jobs(db_session, "apify_dice", [job("apify_dice", candidate_application_type="APPLY_TO_EMAIL", unverified_application_url="mailto:hr@example.com")])
    row = db_session.query(DiscoveredJob).one()
    assert row.candidate_application_type == "APPLY_TO_EMAIL"
    assert row.unverified_application_url is None
    assert resolve_apply_action(row)["action_label"] == "View Listing"
    ats.greenhouse_lever_provider.assert_not_called()


@pytest.mark.parametrize("provider", ["greenhouse_enrichment", "lever_enrichment"])
def test_ats_success_skips_serpapi(db_session, monkeypatch, provider):
    http = enable_serpapi(monkeypatch, [candidate().url])
    ats.greenhouse_lever_provider.return_value = [candidate(provider_name=provider)]
    persistence.process_source_jobs(db_session, "adzuna", [job()])
    assert resolve_apply_action(db_session.query(DiscoveredJob).one())["action_label"] == "Apply"
    http.assert_not_called()


@pytest.mark.parametrize("url,state,action", [
    ("https://careers.acmewidgets.com/jobs", "ACTIVE", "Open Employer Site"),
    (candidate().url, "EXPIRED", "Job Closed"),
    (candidate().url, "UNKNOWN", "Open Employer Site"),
])
def test_fallback_actions(db_session, url, state, action):
    enrichment.availability.check_url_availability.return_value = {"status": state, "reason": "http_404" if state == "EXPIRED" else "http_200", "final_url": None}
    persistence.process_source_jobs(db_session, "adzuna", [job(unverified_application_url=url)])
    assert resolve_apply_action(db_session.query(DiscoveredJob).one())["action_label"] == action


def test_duplicate_listing_preserves_verified_direct_and_skips_lookup(db_session, monkeypatch):
    persistence.process_source_jobs(db_session, "apify_dice", [job("apify_dice", unverified_application_url=candidate().url)])
    http = enable_serpapi(monkeypatch, [candidate().url])
    persistence.process_source_jobs(db_session, "adzuna", [job("adzuna", "other-id", description="Updated Java backend developer job description.")])
    row = db_session.query(DiscoveredJob).one()
    assert resolve_apply_action(row)["action_label"] == "Apply"
    assert len(json.loads(row.source_provenance)) == 2
    http.assert_not_called()


def test_changed_candidate_without_content_change_reverified(db_session):
    persistence.process_source_jobs(db_session, "apify_dice", [job("apify_dice", unverified_application_url="https://wrongcompany.com/job/12345")])
    row = db_session.query(DiscoveredJob).one()
    old_hash = row.content_hash
    persistence.process_source_jobs(db_session, "apify_dice", [job("apify_dice", unverified_application_url=candidate().url)])
    assert row.content_hash == old_hash
    assert resolve_apply_action(row)["action_label"] == "Apply"


def test_verification_is_bound_to_url(db_session):
    persistence.process_source_jobs(db_session, "adzuna", [job(unverified_application_url=candidate().url)])
    row = db_session.query(DiscoveredJob).one()
    row.application_url = "https://careers.acmewidgets.com/job/67890"
    assert resolve_apply_action(row)["action_label"] != "Apply"
    assert resolve_apply_action(row)["availability_status"] == "UNKNOWN"


def test_adzuna_provider_redirect_is_not_sent_to_strict_verification(db_session):
    persistence.process_source_jobs(db_session, "adzuna", [job(unverified_application_url="https://www.adzuna.com/redirect/12345")])
    row = db_session.query(DiscoveredJob).one()
    assert row.unverified_application_url is None
    assert resolve_apply_action(row)["action_label"] == "View Listing"
    enrichment.availability.check_url_availability.assert_not_called()
    assert "no_outbound_destination" in row.enrichment_diagnostics
    assert "provider_fallback_used" in row.enrichment_diagnostics


def test_adzuna_outbound_workday_candidate_alone_enters_shared_verification(db_session):
    workday = "https://acmewidgets.wd1.myworkdayjobs.com/en-US/Careers/job/Austin/Senior-Java-Developer_R12345"
    persistence.process_source_jobs(db_session, "adzuna", [job(unverified_application_url=workday)])
    row = db_session.query(DiscoveredJob).one()
    assert row.application_url == workday
    assert resolve_apply_action(row)["action_label"] == "Apply"
    assert enrichment.availability.check_url_availability.call_args.args[0] == workday
    assert "outbound_candidate_found" in row.enrichment_diagnostics
    assert "verified_direct" in row.enrichment_diagnostics


def test_new_outbound_candidate_is_not_blocked_by_old_provider_listing_cooldown(db_session):
    provider_url = "https://www.adzuna.com/redirect/12345"
    persistence.process_source_jobs(db_session, "adzuna", [job(unverified_application_url=provider_url)])
    row = db_session.query(DiscoveredJob).one()
    old_hash = row.enrichment_input_hash

    persistence.process_source_jobs(db_session, "adzuna", [job(unverified_application_url=candidate().url)])
    row = db_session.query(DiscoveredJob).one()
    assert row.enrichment_input_hash != old_hash
    assert row.application_url == candidate().url
    assert resolve_apply_action(row)["action_label"] == "Apply"


def test_refresh_budget_and_cooldown(db_session, monkeypatch):
    monkeypatch.setenv("JOB_ENRICHMENT_MAX_JOBS_PER_REFRESH", "1")
    http = enable_serpapi(monkeypatch, [])
    with enrichment_scope():
        persistence.process_source_jobs(db_session, "adzuna", [job(), job(job_id="2", company="Other Company")])
        persistence.process_source_jobs(db_session, "jsearch", [job("jsearch")])
    http.assert_called_once()
    persistence.process_source_jobs(db_session, "adzuna", [job()])
    http.assert_called_once()


def test_enrichment_budget_reserves_capacity_for_direct_candidates():
    """Listing-only jobs (no provider-supplied candidate URL) may only ever
    claim the smaller half of the shared per-refresh budget — the rest stays
    reserved for direct candidates (e.g. Dice's applyUrl) no matter how many
    listing-only jobs ask for a slot first."""
    from app.services.job_enrichment_context import EnrichmentBudget

    budget = EnrichmentBudget(max_jobs=5)
    assert budget.max_listing_jobs == 2
    results = [budget.reserve((f"listing-{i}",), is_direct=False) for i in range(5)]
    assert results == [True, True, False, False, False]
    # The 3 slots reserved for direct candidates remain claimable afterward.
    assert [budget.reserve((f"direct-{i}",), is_direct=True) for i in range(3)] == [True, True, True]
    assert budget.reserve(("direct-overflow",), is_direct=True) is False


def test_direct_candidate_reaches_verification_after_listing_only_exhausts_its_share(db_session, monkeypatch):
    """Reproduces the production starvation scenario: a high-volume
    listing-only source (Adzuna) is processed before a source that supplies
    unverified direct apply URLs (Dice). Listing-only enrichment must stay
    bounded to its own lane so Dice's direct candidate still reaches shared
    verification instead of being silently dropped once the shared budget
    looked exhausted."""
    monkeypatch.setenv("JOB_ENRICHMENT_MAX_JOBS_PER_REFRESH", "2")
    with enrichment_scope() as budget:
        persistence.process_source_jobs(db_session, "adzuna", [
            job(job_id="1", company="Company One"),
            job(job_id="2", company="Company Two"),
        ])
        # Only 1 of the 2 total slots may go to listing-only enrichment.
        assert len(budget.listing_seen) == 1
        assert len(budget.direct_seen) == 0

        persistence.process_source_jobs(db_session, "apify_dice", [
            job("apify_dice", job_id="3", unverified_application_url=candidate().url, candidate_application_type="APPLY_TO_URL"),
        ])
        # The reserved slot was still available for the direct candidate.
        assert len(budget.direct_seen) == 1

    dice_row = db_session.query(DiscoveredJob).filter_by(source="apify_dice").one()
    assert resolve_apply_action(dice_row)["action_label"] == "Apply"
    assert dice_row.application_url == candidate().url


def test_invalid_direct_candidate_still_rejected_despite_reserved_budget(db_session, monkeypatch):
    """Reserving budget for direct candidates must never bypass verification
    — an invalid Dice URL that claims the reserved slot is still rejected
    and the job stays a safe listing."""
    monkeypatch.setenv("JOB_ENRICHMENT_MAX_JOBS_PER_REFRESH", "2")
    with enrichment_scope() as budget:
        persistence.process_source_jobs(db_session, "adzuna", [
            job(job_id="1", company="Company One"),
            job(job_id="2", company="Company Two"),
        ])
        persistence.process_source_jobs(db_session, "apify_dice", [
            job("apify_dice", job_id="3", unverified_application_url="https://careers.wrongcompany.com/job/12345/senior-java-developer",
                candidate_application_type="APPLY_TO_URL"),
        ])
        assert len(budget.direct_seen) == 1  # the reserved slot was spent attempting verification

    dice_row = db_session.query(DiscoveredJob).filter_by(source="apify_dice").one()
    assert dice_row.application_url is None
    assert resolve_apply_action(dice_row)["action_label"] == "View Listing"


def test_no_invented_employment_type():
    assert job()["job_type"] == "Not specified"


def test_current_user_queries_and_registry_are_isolated(db_session, test_user, second_user, monkeypatch):
    from app.services.broad_provider_query_service import get_active_preference_queries
    from app.services.job_source_registry_service import get_adapters
    db_session.add_all([preference(test_user.id, "Java Developer"),
                        preference(second_user.id, "Data Scientist")])
    db_session.commit()
    monkeypatch.setenv("ADZUNA_APP_ID", "test")
    monkeypatch.setenv("ADZUNA_APP_KEY", "test")
    monkeypatch.setenv("APIFY_DICE_ENABLED", "true")
    monkeypatch.setenv("APIFY_API_TOKEN", "test")
    monkeypatch.setenv("APIFY_DICE_ACTOR_ID", "test")
    for user, title in ((test_user, "Java Developer"), (second_user, "Data Scientist")):
        titles = [q["job_title"] for q in get_active_preference_queries(db_session, user_id=user.id)]
        assert titles[0] == title
        assert title in titles
        other = "Data Scientist" if title == "Java Developer" else "Java Developer"
        assert other not in titles
        broad_adapters = [adapter for adapter in get_adapters(db_session, user_id=user.id)
                          if hasattr(adapter, "preference_queries")]
        assert {adapter.source_name for adapter in broad_adapters} == {"jsearch", "adzuna", "apify_dice"}
        for adapter in broad_adapters:
            adapter_titles = [q["job_title"] for q in adapter.preference_queries]
            assert adapter_titles[0] == title
            assert other not in adapter_titles


def test_refresh_passes_user_and_filters_before_enrichment(test_engine, db_session, test_user, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    import app.database as database
    from app.services import updated_job_discovery_service as discovery
    from app.services import job_source_registry_service as registry
    from tests.test_updated_job_discovery_service import FakeAdapter
    db_session.add(preference(test_user.id, "Java Developer"))
    db_session.commit()
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=test_engine))
    adapter = FakeAdapter("adzuna", [job(), job(job_id="2", title="Marketing Manager", description="Marketing campaigns and brand development.")])
    seen_users = []
    def adapters(db, user_id=None):
        if user_id is not None:
            seen_users.append(user_id)
        return [adapter]
    monkeypatch.setattr(registry, "get_adapters", adapters)
    summary = discovery.run_poll_cycle(triggered_by="manual", user_id=test_user.id)
    assert seen_users == [test_user.id]
    assert summary["total_new"] == 1
    assert summary["sources"][0]["diagnostics"]["rejected"] == 1


def test_tracker_remains_off(monkeypatch):
    from app.scheduler.updated_job_scheduler_service import tracker_enabled
    monkeypatch.setenv("UPDATED_JOB_TRACKER_ENABLED", "false")
    assert tracker_enabled() is False


def test_changed_candidate_does_not_destroy_prior_verified_destination(db_session):
    persistence.process_source_jobs(db_session, "adzuna", [job(unverified_application_url=candidate().url)])
    persistence.process_source_jobs(db_session, "adzuna", [job(unverified_application_url="https://wrongcompany.com/job/54321")])
    row = db_session.query(DiscoveredJob).one()
    assert resolve_apply_action(row)["action_label"] == "Apply"
    assert row.application_url == candidate().url
    assert row.enrichment_status == "REJECTED"


def test_aggregator_in_legacy_direct_field_is_still_listing():
    row = job(application_url="https://linkedin.com/jobs/view/12345")
    row["availability_status"] = "ACTIVE"
    action = resolve_apply_action(row)
    assert action["action_label"] == "View on LinkedIn"
    assert action["is_direct"] is False
    assert action["type"] == "PROVIDER_JOB_APPLY"


def test_schema_upgrade_is_additive_idempotent_and_preserves_old_rows(monkeypatch):
    from sqlalchemy import create_engine, inspect, text
    import app.database as database
    from app.services.job_discovery_schema import ensure_job_discovery_schema
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE discovered_jobs (id INTEGER PRIMARY KEY, application_url TEXT)"))
        conn.execute(text("INSERT INTO discovered_jobs VALUES (1, 'https://careers.example.com/job/12345')"))
    monkeypatch.setattr(database, "engine", engine)
    ensure_job_discovery_schema()
    ensure_job_discovery_schema()
    columns = {column["name"]: column for column in inspect(engine).get_columns("discovered_jobs")}
    assert columns["verified_destination_url"]["nullable"]
    assert columns["enrichment_diagnostics"]["nullable"]
    with engine.connect() as conn:
        row = conn.execute(text("SELECT application_url, verified_destination_url FROM discovered_jobs WHERE id=1")).one()
    assert tuple(row) == ("https://careers.example.com/job/12345", None)
    engine.dispose()


def test_postgres_provenance_width_upgrade():
    from sqlalchemy import String
    from app.services.job_discovery_schema import _widen_varchar_columns
    conn, inspector = MagicMock(), MagicMock()
    conn.engine.dialect.name = "postgresql"
    inspector.get_columns.return_value = [{"name": "direct_url_source", "type": String(20)}]
    _widen_varchar_columns(conn, inspector, {"discovered_jobs"})
    assert any("direct_url_source TYPE VARCHAR(80)" in str(args.args[0]) for args in conn.execute.call_args_list)
    assert DiscoveredJob.direct_url_source.type.length == 80
    conn.reset_mock()
    inspector.get_columns.return_value = [{"name": "direct_url_source", "type": String(80)}]
    _widen_varchar_columns(conn, inspector, {"discovered_jobs"})
    conn.execute.assert_not_called()


def test_serpapi_bad_request_diagnostics_are_safe(db_session, monkeypatch):
    http = enable_serpapi(monkeypatch, [])
    http.return_value.status_code = 400
    http.return_value.json.return_value = {"error": "Unsupported location https://example.com/?api_key=secret"}
    with enrichment_scope() as budget:
        persistence.process_source_jobs(db_session, "adzuna", [job()])
    row = db_session.query(DiscoveredJob).one()
    assert budget.counters["serpapi_attempts"] == 1
    assert budget.counters["serpapi_failed"] == 1
    assert "invalid_location" in row.enrichment_diagnostics
    assert "secret" not in row.enrichment_diagnostics
    assert "https://" not in row.enrichment_diagnostics
    http.assert_called_once()


def test_workday_tenant_must_match_even_if_provider_claims_company():
    result = enrichment.enrich_job(SimpleNamespace(**job()), provider=lambda q: [candidate(
        url="https://other-employer.wd5.myworkdayjobs.com/en-US/careers/job/12345")])
    assert result.status == "REJECTED"
    enrichment.availability.check_url_availability.assert_not_called()


def test_apply_click_redirect_cannot_transfer_verification_to_wrong_employer():
    row = SimpleNamespace(**job(application_url=candidate().url))
    enrichment.availability.apply_result(row, {"status": "ACTIVE", "reason": "http_200", "final_url": "https://wrongcompany.com/job/12345"})
    assert row.application_url == candidate().url
    assert row.availability_status == "UNKNOWN"


def test_failed_source_outcome_not_reported_success(test_engine, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    import app.database as database
    from app.services import updated_job_discovery_service as discovery, job_source_registry_service as registry
    from app.job_sources.base import SourceHealth
    from tests.test_updated_job_discovery_service import FakeAdapter
    from datetime import datetime, timezone
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=test_engine))
    rate_limited = FakeAdapter("jsearch")
    rate_limited.health_check = lambda: SourceHealth("jsearch", "broad_provider", "live", False, datetime.now(timezone.utc).isoformat(), "rate_limited")
    monkeypatch.setattr(registry, "get_adapters", lambda db: [rate_limited])
    result = discovery.run_poll_cycle()
    assert result["sources"][0]["outcome"] == "RATE_LIMITED"
    assert result["sources"][0]["error"] is not None


def test_existing_apply_changed_bad_candidate_never_spends_serpapi(db_session, monkeypatch):
    persistence.process_source_jobs(db_session, "adzuna", [job(unverified_application_url=candidate().url)])
    http = enable_serpapi(monkeypatch, [])
    persistence.process_source_jobs(db_session, "adzuna", [job(unverified_application_url="https://wrongcompany.com/job/65432")])
    http.assert_not_called()
    assert resolve_apply_action(db_session.query(DiscoveredJob).one())["action_label"] == "Apply"


def test_expired_other_candidate_cannot_downgrade_verified_generic_destination(db_session):
    persistence.process_source_jobs(db_session, "adzuna", [job(unverified_application_url="https://careers.acmewidgets.com/jobs")])
    enrichment.availability.check_url_availability.return_value = {"status": "EXPIRED", "reason": "http_404", "final_url": None}
    persistence.process_source_jobs(db_session, "adzuna", [job(unverified_application_url=candidate().url)])
    assert resolve_apply_action(db_session.query(DiscoveredJob).one())["action_label"] == "Open Employer Site"


@pytest.mark.parametrize("title,company", [("", "Acme"), ("  ", "Acme"), ("Developer", ""), ("Developer", None)])
def test_serpapi_refuses_empty_identity(monkeypatch, title, company):
    from app.services.serpapi_google_jobs_client import SerpApiGoogleJobsClient
    http = enable_serpapi(monkeypatch, [])
    assert SerpApiGoogleJobsClient().search(enrichment.EnrichmentQuery(title=title, company=company)) == []
    http.assert_not_called()


def test_url_candidate_from_duplicate_can_upgrade_email_only_listing(db_session):
    persistence.process_source_jobs(db_session, "apify_dice", [job("apify_dice", candidate_application_type="APPLY_TO_EMAIL")])
    persistence.process_source_jobs(db_session, "jsearch", [job("jsearch", application_url=candidate().url)])
    row = db_session.query(DiscoveredJob).one()
    assert resolve_apply_action(row)["action_label"] == "Apply"
    assert row.unverified_application_url == candidate().url
    assert row.candidate_application_type != "APPLY_TO_EMAIL"
