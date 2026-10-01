"""Official careers through the existing registry, verifier and persistence."""
import json
import socket
from unittest.mock import MagicMock

import pytest
import requests
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

import app.database as database
from app.models.discovered_job_model import DiscoveredJob
from app.models.company_source_model import CompanySource
from app.job_sources.career_sources.adapter import CompanyCareerAdapter
from app.job_sources.career_sources.base import CareerSource
from app.services import job_change_detection_service as persistence
from app.services import job_enrichment_service as enrichment
from app.services import greenhouse_lever_enrichment_provider as ats
from app.services import job_source_registry_service as registry
from app.services import updated_job_discovery_service as discovery
from app.services.job_normalization_service import build_normalized_job, resolve_apply_action
from app.routes.job_discovery_routes import _job_payload, _visible_jobs_query

SOURCE = CareerSource("Acme Widgets", "https://careers.acmewidgets.com/jobs")
URL = SOURCE.career_url + "/12345/senior-java-developer"


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Unmocked provider HTTP/DNS is forbidden")
    monkeypatch.setattr(requests.Session, "request", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setenv("JOB_ENRICHMENT_BROAD_PROVIDER", "none")
    monkeypatch.setenv("COMPANY_CAREER_SOURCES", "[]")
    monkeypatch.setenv("UPDATED_JOB_DEMO_MODE", "false")
    monkeypatch.setenv("APIFY_DICE_ENABLED", "false")
    monkeypatch.setenv("INACTIVE_AFTER_MISSED_POLLS", "3")
    monkeypatch.setenv("JOB_ENRICHMENT_MAX_JOBS_PER_REFRESH", "5")
    monkeypatch.setattr(ats, "greenhouse_lever_provider", MagicMock(return_value=[]))
    monkeypatch.setattr(enrichment.availability, "check_url_availability", MagicMock(return_value={
        "status": "ACTIVE", "reason": "http_200", "final_url": None,
    }))


def job(source="adzuna", identifier="1", **overrides):
    return build_normalized_job(**{
        "source": source, "source_mode": "live", "source_job_id": identifier,
        "company": "Acme Widgets", "title": "Senior Java Developer", "location": "Austin, TX",
        "description": "Develop Java Spring Boot APIs and distributed services.", "skills": "Java, Spring Boot",
        "source_url": "https://adzuna.com/details/12345", **overrides,
    })


def official(identifier="12345", url=URL, candidate=None, source=SOURCE, **overrides):
    raw = job("company_direct", identifier, source_url=url, application_url=candidate or url, **overrides)
    raw.update(salary_min=100000, salary_max=140000, salary_currency="USD", preferred_skills="Redis")
    return CompanyCareerAdapter(source).normalize_job(raw)


def persist(db, value):
    return persistence.process_source_jobs(db, value["source"], [value])


def test_official_candidate_uses_shared_verifier_before_apply(db_session, monkeypatch):
    verify = MagicMock(wraps=enrichment._candidate_verification)
    monkeypatch.setattr(enrichment, "_candidate_verification", verify)
    normalized = official()
    assert normalized["application_url"] is None and normalized["direct_url"] is None
    persist(db_session, normalized)
    row = db_session.query(DiscoveredJob).one()
    assert verify.called and enrichment.availability.check_url_availability.called
    assert row.verified_destination_url == URL and row.application_url == URL
    assert resolve_apply_action(row)["action_label"] == "Apply"
    assert row.direct_url_source == "company_direct"
    payload = _job_payload(row, None, False)
    assert payload["source_kind"] == "company_direct" and payload["source_platform"] == "custom"
    assert payload["source_label"] == "Official Company Career Page"
    assert payload["salary_min"] == 100000 and payload["preferred_skills"] == "Redis"
    ats.greenhouse_lever_provider.assert_not_called()


@pytest.mark.parametrize("candidate", [
    "https://careers.wrongcompany.com/job/12345/senior-java-developer",
    "https://other.wd1.myworkdayjobs.com/External/job/Austin/Developer_R12345",
    "https://jobs.ashbyhq.com/other/abcd1234-abcd-1234-abcd-123456789012",
    "https://careers.acmewidgets.com/job/12345/marketing-manager",
    "https://127.0.0.1/jobs/12345", "javascript:alert(1)",
])
def test_bad_official_apply_candidate_not_promoted_and_job_retained(db_session, candidate):
    persist(db_session, official(candidate=candidate))
    row = db_session.query(DiscoveredJob).one()
    assert row.application_url is None and row.source_url == URL
    assert resolve_apply_action(row)["action_label"] != "Apply"
    assert _job_payload(row, None, False)["official_job_url"] == URL


@pytest.mark.parametrize("state,label", [("UNKNOWN", "Open Employer Site"), ("EXPIRED", "Job Closed")])
def test_availability_not_bypassed(db_session, state, label):
    enrichment.availability.check_url_availability.return_value = {"status": state, "reason": "http_404" if state == "EXPIRED" else "timeout", "final_url": None}
    persist(db_session, official())
    row = db_session.query(DiscoveredJob).one()
    assert resolve_apply_action(row)["action_label"] == label
    if state == "EXPIRED":
        assert _visible_jobs_query(db_session).count() == 0


@pytest.mark.parametrize("source", ["adzuna", "jsearch", "apify_dice"])
@pytest.mark.parametrize("official_first", [True, False])
def test_official_preference_bidirectional_preserves_identity_history_and_provenance(db_session, source, official_first):
    values = [official(), job(source)] if official_first else [job(source), official()]
    persist(db_session, values[0])
    first = db_session.query(DiscoveredJob).one()
    first_id, first_seen = first.id, first.first_seen_at
    persist(db_session, values[1])
    row = db_session.query(DiscoveredJob).one()
    assert row.id == first_id and row.first_seen_at == first_seen
    assert row.source.startswith("company_direct:") and row.source_url == URL
    assert row.application_url == URL and resolve_apply_action(row)["action_label"] == "Apply"
    assert {entry["source"] for entry in json.loads(row.source_provenance)} == {official()["source"], source}


def test_distinct_official_requisitions_not_merged_by_title_or_generic_url(db_session):
    persist(db_session, official("A", url=SOURCE.career_url))
    persist(db_session, official("B", url=SOURCE.career_url))
    assert db_session.query(DiscoveredJob).count() == 2


def test_official_destination_change_reverified_without_changing_description(db_session):
    persist(db_session, official())
    calls = enrichment.availability.check_url_availability.call_count
    updated = URL.replace("12345", "12346")
    persist(db_session, official(url=updated))
    row = db_session.query(DiscoveredJob).one()
    assert enrichment.availability.check_url_availability.call_count > calls
    assert row.verified_destination_url == updated


def test_aggregator_cannot_reactivate_inactive_official_job(db_session):
    value = official()
    persist(db_session, value)
    for _ in range(3):
        persistence.close_stale_jobs(db_session, value["source"], set())
    row = db_session.query(DiscoveredJob).one()
    persist(db_session, job())
    assert row.status == "INACTIVE" and row.missed_poll_count == 3
    assert _visible_jobs_query(db_session).count() == 0
    persist(db_session, value)
    assert row.status == "ACTIVE" and row.missed_poll_count == 0
    assert _visible_jobs_query(db_session).count() == 1


def test_registry_extension_preserves_existing_providers_and_disables_only_managed_company(db_session, monkeypatch):
    monkeypatch.setenv("UPDATED_JOB_PRIMARY_PROVIDER", "jsearch")
    monkeypatch.setenv("ADZUNA_APP_ID", "mock-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "mock-key")
    monkeypatch.setenv("COMPANY_CAREER_SOURCES", json.dumps([{"company_name": SOURCE.company_name, "career_url": SOURCE.career_url}]))
    adapters = registry.get_adapters(db_session)
    names = {item.source_name for item in adapters}
    assert {"jsearch", "adzuna", "greenhouse", "lever", "workday"} <= names
    assert len([name for name in names if name.startswith("company_direct:")]) == 1
    row = db_session.query(CompanySource).filter_by(source_type="COMPANY_CAREER_PAGE").one()
    assert row.enabled and row.career_page_url == SOURCE.career_url
    monkeypatch.setenv("COMPANY_CAREER_SOURCES", "[]")
    assert not any(item.source_name.startswith("company_direct:") for item in registry.get_adapters(db_session))
    assert row.enabled is False


@pytest.mark.parametrize("complete,healthy,expected", [(True, True, "INACTIVE"), (False, True, "ACTIVE"), (True, False, "ACTIVE")])
def test_only_complete_healthy_inventory_can_age_out_jobs(test_engine, db_session, monkeypatch, complete, healthy, expected):
    value = official()
    persist(db_session, value)
    collector = CompanyCareerAdapter(SOURCE)
    collector.complete_inventory = complete
    collector._error = None if healthy else "access_restricted"
    collector.fetch_normalized_jobs = MagicMock(return_value=[])
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=test_engine))
    monkeypatch.setattr(registry, "get_adapters", lambda db: [collector])
    for _ in range(3):
        discovery.run_poll_cycle("scheduler")
    db_session.expire_all()
    assert db_session.query(DiscoveredJob).one().status == expected


def test_failure_isolation_keeps_other_company_jobs(test_engine, db_session, monkeypatch):
    bad = CompanyCareerAdapter(CareerSource("Unavailable", "https://unavailable.example/careers"))
    bad.fetch_normalized_jobs = MagicMock(side_effect=ValueError("simulated error"))
    good = CompanyCareerAdapter(SOURCE)
    good.fetch_normalized_jobs = MagicMock(return_value=[official()])
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=test_engine))
    monkeypatch.setattr(registry, "get_adapters", lambda db: [bad, good])
    summary = discovery.run_poll_cycle("scheduler")
    assert summary["total_new"] == 1 and summary["sources"][0]["healthy"] is False
    assert summary["sources"][1]["healthy"] is True
    assert db_session.query(DiscoveredJob).one().company == SOURCE.company_name


def test_current_user_relevance_path_filters_official_inventory(test_engine, db_session, test_preferences, monkeypatch):
    collector = CompanyCareerAdapter(SOURCE)
    collector.fetch_normalized_jobs = MagicMock(return_value=[official(), official("2", url=URL.replace("12345", "23456"), title="Registered Nurse", description="Provide bedside nursing care to patients in a hospital.", skills="Nursing")])
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=test_engine))
    monkeypatch.setattr(registry, "get_adapters", lambda db, user_id=None: [collector])
    summary = discovery.run_poll_cycle("manual", user_id=test_preferences.user_id)
    assert summary["total_new"] == 1
    assert summary["sources"][0]["diagnostics"]["rejected"] == 1
    assert db_session.query(DiscoveredJob).one().title == "Senior Java Developer"


def test_official_jobs_use_same_existing_deterministic_matcher(java_resume_text):
    from app.services.job_match_service import deterministic_match
    def match(value):
        return deterministic_match(job_title=value["title"], job_description=value["description"], job_skills_csv=value["skills"], resume_text=java_resume_text)
    assert match(official()) == match(job())


def test_saving_unverified_official_job_preserves_listing_without_submission(db_session, test_user, monkeypatch):
    from fastapi import BackgroundTasks
    from app.models.job_model import Job
    from app.routes import job_discovery_routes as routes
    enrichment.availability.check_url_availability.return_value = {"status": "UNKNOWN", "reason": "timeout", "final_url": None}
    persist(db_session, official())
    row = db_session.query(DiscoveredJob).one()
    workflow = MagicMock(return_value={"workflow_status": "PENDING"})
    monkeypatch.setattr(routes, "start_workflow_for_saved_job", workflow)
    result = routes.save_discovered_job(test_user.id, routes.SaveDiscoveredJobRequest(discovered_job_id=row.id), BackgroundTasks(), db_session)
    saved = db_session.query(Job).filter_by(id=result["job_id"]).one()
    assert saved.job_url == URL and saved.source == "User Selected Job"
    workflow.assert_called_once()


def test_additive_nullable_metadata_upgrade_preserves_legacy_rows(monkeypatch):
    from app.services.job_discovery_schema import ensure_job_discovery_schema
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE discovered_jobs (id INTEGER PRIMARY KEY, application_url TEXT)"))
        conn.execute(text("INSERT INTO discovered_jobs VALUES (1, 'https://careers.example.com/job/12345')"))
    monkeypatch.setattr(database, "engine", engine)
    ensure_job_discovery_schema()
    ensure_job_discovery_schema()
    columns = {column["name"]: column for column in inspect(engine).get_columns("discovered_jobs")}
    assert columns["career_metadata"]["nullable"]
    with engine.connect() as conn:
        assert tuple(conn.execute(text("SELECT application_url, career_metadata FROM discovered_jobs WHERE id=1")).one()) == ("https://careers.example.com/job/12345", None)
    engine.dispose()
