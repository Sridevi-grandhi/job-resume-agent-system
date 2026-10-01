"""Tests for app/services/updated_job_discovery_service.py.

Covers: one failing source never stops the others (#40/#50), and a poll
already in progress returns its status instead of starting a duplicate
(#10) — the same guard the manual Refresh button and the scheduler share.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import app.database as database_module
from sqlalchemy.orm import sessionmaker

from app.job_sources.base import JobSourceAdapter, SourceHealth
from app.job_sources.apify_dice import ApifyDiceAdapter
from app.services import job_source_registry_service as registry_module
from app.services import updated_job_discovery_service as discovery
from app.services.job_normalization_service import build_normalized_job, present_updated_job_action, resolve_apply_action


class FakeAdapter(JobSourceAdapter):
    def __init__(self, name, jobs=None, fail=False):
        self.source_name = name
        self.source_type = "ats_board"
        self.enabled = True
        self._jobs = jobs or []
        self._fail = fail

    def fetch_jobs(self, limit=100):
        if self._fail:
            raise RuntimeError("simulated source outage")
        return self._jobs

    def normalize_job(self, raw):
        return raw

    def health_check(self):
        return SourceHealth(self.source_name, self.source_type, "mock", not self._fail, datetime.now(timezone.utc).isoformat())


class NeverCalledAdapter(JobSourceAdapter):
    source_name = "should-not-run"
    source_type = "ats_board"
    enabled = True

    def fetch_jobs(self, limit=100):
        raise AssertionError("fetch_jobs must not run while a poll is already in progress")

    def normalize_job(self, raw):
        return raw

    def health_check(self):
        return SourceHealth(self.source_name, self.source_type, "mock", True, datetime.now(timezone.utc).isoformat())


def _job(source, source_job_id):
    # source_mode="live" — these tests use adapter names (greenhouse/lever/
    # remotive) that ARE live-capable in production; process_source_jobs
    # rejects source_mode="mock" outside UPDATED_JOB_DEMO_MODE (see
    # test_job_change_detection.py for that guard's own dedicated coverage).
    return build_normalized_job(
        source=source, source_mode="live", source_job_id=source_job_id,
        company="Test Co", title="Engineer", location="Remote", work_mode="Remote",
        job_type="Full-time", description="A sufficiently detailed job description for testing.",
        skills="Python", direct_url=None,
    )


def _use_test_database(test_engine, monkeypatch):
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    monkeypatch.setattr(database_module, "SessionLocal", TestingSessionLocal)


def test_one_failing_source_does_not_stop_the_others(test_engine, monkeypatch):
    _use_test_database(test_engine, monkeypatch)
    good1 = FakeAdapter("greenhouse", jobs=[_job("greenhouse", "1")])
    bad = FakeAdapter("lever", fail=True)
    good2 = FakeAdapter("remotive", jobs=[_job("remotive", "2")])
    monkeypatch.setattr(registry_module, "get_adapters", lambda db: [good1, bad, good2])

    summary = discovery.run_poll_cycle(triggered_by="manual")

    assert summary["already_running"] is False
    by_source = {row["source"]: row for row in summary["sources"]}
    assert by_source["greenhouse"]["new"] == 1
    assert by_source["greenhouse"]["error"] is None
    assert by_source["remotive"]["new"] == 1
    assert by_source["remotive"]["error"] is None
    assert by_source["lever"]["error"] is not None
    assert by_source["lever"]["healthy"] is False
    # Whole workflow completed despite the failure — no exception propagated.
    assert summary["total_new"] == 2


def test_poll_already_running_returns_status_instead_of_duplicate_run(monkeypatch):
    monkeypatch.setattr(registry_module, "get_adapters", lambda db: [NeverCalledAdapter()])
    discovery._poll_lock.acquire()
    try:
        result = discovery.run_poll_cycle(triggered_by="manual")
    finally:
        discovery._poll_lock.release()
    assert result["already_running"] is True


# ---------------------------------------------------------------------------
# Production-readiness hardening: a source that is simply unconfigured must
# never be logged/recorded as a failure, and a genuine failure must never
# leave the DB session poisoned for sources processed afterward.
# ---------------------------------------------------------------------------

def test_unconfigured_source_completes_cleanly_not_marked_failed(test_engine, monkeypatch):
    """An adapter reporting mode='unavailable' with no exception (e.g.
    Greenhouse with no configured boards) must flow through the success
    path — no JOB_SOURCE_FAILED, no 'temporarily unavailable' error, and its
    JobSource row must persist without a DataError (job_sources.mode is
    VARCHAR(20), wide enough for 'unavailable')."""
    _use_test_database(test_engine, monkeypatch)
    unconfigured = FakeAdapter("greenhouse", jobs=[])
    unconfigured.health_check = lambda: SourceHealth("greenhouse", "ats_board", "unavailable", False, datetime.now(timezone.utc).isoformat())
    monkeypatch.setattr(registry_module, "get_adapters", lambda db: [unconfigured])

    summary = discovery.run_poll_cycle(triggered_by="manual")

    row = summary["sources"][0]
    assert row["error"] is None
    assert row["mode"] == "unavailable"

    from app.database import SessionLocal
    from app.models.job_source_model import JobSource
    db = SessionLocal()
    try:
        job_source = db.query(JobSource).filter_by(source_name="greenhouse").one()
        assert job_source.mode == "unavailable"
        assert registry_module.classify_source_status(job_source.source_name, job_source.enabled, job_source.mode) == "OPTIONAL_NOT_CONFIGURED"
    finally:
        db.close()


def test_failing_source_persists_mode_error_not_unavailable(test_engine, monkeypatch):
    """A genuine exception must be distinguishable from an intentional
    unconfigured state — see job_source_registry_service.classify_source_status."""
    _use_test_database(test_engine, monkeypatch)
    bad = FakeAdapter("lever", fail=True)
    monkeypatch.setattr(registry_module, "get_adapters", lambda db: [bad])

    discovery.run_poll_cycle(triggered_by="manual")

    from app.database import SessionLocal
    from app.models.job_source_model import JobSource
    db = SessionLocal()
    try:
        job_source = db.query(JobSource).filter_by(source_name="lever").one()
        assert job_source.mode == "error"
        assert registry_module.classify_source_status(job_source.source_name, job_source.enabled, job_source.mode) == "LIVE_ERROR"
    finally:
        db.close()


def test_persistence_stage_failure_does_not_poison_session_for_later_sources(test_engine, monkeypatch):
    """A DataError-style exception raised specifically during persistence
    (not fetch) for one source must not prevent a source processed
    afterward from succeeding and committing its own jobs."""
    _use_test_database(test_engine, monkeypatch)
    good_before = FakeAdapter("greenhouse", jobs=[_job("greenhouse", "1")])
    broken = FakeAdapter("lever", jobs=[_job("lever", "2")])
    good_after = FakeAdapter("remotive", jobs=[_job("remotive", "3")])
    monkeypatch.setattr(registry_module, "get_adapters", lambda db: [good_before, broken, good_after])

    from app.services import job_change_detection_service as change_detection
    real_process_source_jobs = change_detection.process_source_jobs

    def flaky_process_source_jobs(db, source, normalized_jobs):
        if source == "lever":
            raise RuntimeError("simulated DataError during persistence")
        return real_process_source_jobs(db, source, normalized_jobs)

    monkeypatch.setattr(discovery.change_detection, "process_source_jobs", flaky_process_source_jobs)

    summary = discovery.run_poll_cycle(triggered_by="manual")

    by_source = {row["source"]: row for row in summary["sources"]}
    assert by_source["greenhouse"]["new"] == 1
    assert by_source["greenhouse"]["error"] is None
    assert by_source["lever"]["error"] is not None
    assert by_source["remotive"]["new"] == 1
    assert by_source["remotive"]["error"] is None

    from app.database import SessionLocal
    from app.models.discovered_job_model import DiscoveredJob
    db = SessionLocal()
    try:
        assert db.query(DiscoveredJob).filter_by(source="greenhouse").count() == 1
        assert db.query(DiscoveredJob).filter_by(source="remotive").count() == 1
        assert db.query(DiscoveredJob).filter_by(source="lever").count() == 0
    finally:
        db.close()


def test_failure_stage_is_recorded_on_the_job_source_run(test_engine, monkeypatch):
    _use_test_database(test_engine, monkeypatch)
    bad = FakeAdapter("lever", fail=True)
    monkeypatch.setattr(registry_module, "get_adapters", lambda db: [bad])

    discovery.run_poll_cycle(triggered_by="manual")

    from app.database import SessionLocal
    from app.models.job_source_model import JobSourceRun
    db = SessionLocal()
    try:
        run = db.query(JobSourceRun).filter_by(source_name="lever").order_by(JobSourceRun.id.desc()).first()
        assert run is not None
        assert run.error_message.startswith("fetch:")  # FakeAdapter(fail=True) raises inside fetch_jobs
        assert "RuntimeError" in run.error_message
    finally:
        db.close()


def test_real_registry_unconfigured_sources_produce_no_job_source_failed(test_engine, monkeypatch, caplog):
    """End-to-end with the REAL adapter registry (Greenhouse/Lever/Workday/
    generic-feed unconfigured, jsearch with no API key) — a full poll cycle
    against a totally unconfigured environment must complete with zero
    JOB_SOURCE_FAILED log lines and zero exceptions."""
    _use_test_database(test_engine, monkeypatch)
    for var in ("GREENHOUSE_COMPANY_SOURCES", "LEVER_COMPANY_SOURCES", "JSEARCH_API_KEY", "UPDATED_JOB_DEMO_MODE"):
        monkeypatch.delenv(var, raising=False)

    import logging
    caplog.set_level(logging.WARNING)
    summary = discovery.run_poll_cycle(triggered_by="manual")

    for row in summary["sources"]:
        assert row["error"] is None, f"{row['source']} unexpectedly failed: {row}"
    assert "JOB_SOURCE_FAILED" not in caplog.text

    from app.database import SessionLocal
    from app.models.job_source_model import JobSource
    db = SessionLocal()
    try:
        for row in db.query(JobSource).all():
            status = registry_module.classify_source_status(row.source_name, row.enabled, row.mode)
            assert status != "LIVE_ERROR", f"{row.source_name} incorrectly classified as LIVE_ERROR: mode={row.mode}"
    finally:
        db.close()


def test_poll_status_reports_running_flag(test_engine, monkeypatch):
    _use_test_database(test_engine, monkeypatch)
    monkeypatch.setattr(registry_module, "get_adapters", lambda db: [FakeAdapter("greenhouse", jobs=[_job("greenhouse", "1")])])
    assert discovery.poll_status()["running"] is False
    discovery.run_poll_cycle(triggered_by="scheduler")
    status = discovery.poll_status()
    assert status["running"] is False
    assert status["last_summary"]["total_new"] == 1


# ---------------------------------------------------------------------------
# Untrusted provider apply URLs must be handled by the shared enrichment
# pipeline after normal discovery persistence, never promoted by an adapter.
# ---------------------------------------------------------------------------

def _dice_job(**overrides):
    raw = {
        "id": "dice-1", "title": "Lead DevOps Engineer", "company": "EPAM Systems",
        "city": "Austin", "state": "TX", "description": "Build Kubernetes and Terraform platform automation.",
        "url": "https://www.dice.com/job-detail/dice-1", "applyType": "APPLY_TO_URL",
        "applyUrl": "https://careers.epam.com/job/12345/lead-devops-engineer",
    }
    raw.update(overrides)
    return ApifyDiceAdapter().normalize_job(raw)


def _disable_external_enrichment(monkeypatch):
    """All candidates in these integration tests are local fixtures."""
    monkeypatch.setenv("JOB_ENRICHMENT_BROAD_PROVIDER", "none")
    monkeypatch.delenv("JOB_ENRICHMENT_BROAD_API_KEY", raising=False)
    import app.services.greenhouse_lever_enrichment_provider as greenhouse_lever
    monkeypatch.setattr(greenhouse_lever, "greenhouse_lever_provider", lambda query: [])


def _run_single_adapter_poll(test_engine, monkeypatch, adapter):
    _use_test_database(test_engine, monkeypatch)
    monkeypatch.setattr(registry_module, "get_adapters", lambda db: [adapter])
    return discovery.run_poll_cycle(triggered_by="manual")


def test_normal_poll_promotes_valid_dice_candidate_through_shared_enrichment(test_engine, monkeypatch):
    _disable_external_enrichment(monkeypatch)
    dice = FakeAdapter("apify_dice", jobs=[_dice_job()])
    with patch(
        "app.services.job_enrichment_service.availability.check_url_availability",
        return_value={"status": "ACTIVE", "reason": "http_200", "final_url": None},
    ):
        summary = _run_single_adapter_poll(test_engine, monkeypatch, dice)

    assert summary["sources"][0]["error"] is None
    from app.database import SessionLocal
    from app.models.discovered_job_model import DiscoveredJob
    db = SessionLocal()
    try:
        row = db.query(DiscoveredJob).filter_by(source="apify_dice").one()
        assert row.application_url == "https://careers.epam.com/job/12345/lead-devops-engineer"
        assert row.direct_url == row.application_url
        assert resolve_apply_action(row)["action_label"] == "Apply"
    finally:
        db.close()


def test_normal_poll_rejects_mismatched_dice_candidate_and_keeps_listing(test_engine, monkeypatch):
    _disable_external_enrichment(monkeypatch)
    dice = FakeAdapter("apify_dice", jobs=[_dice_job(
        company="Company A", applyUrl="https://company-b.wd1.myworkdayjobs.com/en-US/careers/job/12345",
    )])
    _run_single_adapter_poll(test_engine, monkeypatch, dice)

    from app.database import SessionLocal
    from app.models.discovered_job_model import DiscoveredJob
    db = SessionLocal()
    try:
        row = db.query(DiscoveredJob).filter_by(source="apify_dice").one()
        assert row.application_url is None and row.direct_url is None
        assert row.unverified_application_url == "https://company-b.wd1.myworkdayjobs.com/en-US/careers/job/12345"
        assert resolve_apply_action(row)["action_label"] == "View Listing"
    finally:
        db.close()


def test_dice_apply_to_email_remains_listing_and_skips_enrichment(test_engine, monkeypatch):
    dice = FakeAdapter("apify_dice", jobs=[_dice_job(applyType="APPLY_TO_EMAIL", applyUrl="mailto:jobs@example.com")])
    with patch("app.services.job_change_detection_service.enrich_job_with_fallback") as enrich:
        _run_single_adapter_poll(test_engine, monkeypatch, dice)
    enrich.assert_not_called()


def test_jsearch_candidate_and_adzuna_listing_both_reach_shared_enrichment(test_engine, monkeypatch):
    jsearch = FakeAdapter("jsearch", jobs=[build_normalized_job(
        source="jsearch", source_mode="live", source_job_id="j-1", company="Test Co", title="Engineer",
        location="Remote", work_mode="Remote", job_type="Full-time", description="Detailed engineering role.",
        direct_url="https://careers.testco.example/jobs/12345",
    )])
    adzuna = FakeAdapter("adzuna", jobs=[build_normalized_job(
        source="adzuna", source_mode="live", source_job_id="a-1", company="Other Co", title="Engineer",
        location="Remote", work_mode="Remote", job_type="Full-time", description="Detailed engineering role.",
        source_url="https://www.adzuna.com/details/1",
    )])
    _use_test_database(test_engine, monkeypatch)
    monkeypatch.setattr(registry_module, "get_adapters", lambda db: [jsearch, adzuna])
    from app.services.job_enrichment_service import EnrichmentResult
    with patch("app.services.job_change_detection_service.enrich_job_with_fallback", return_value=EnrichmentResult("REJECTED", "no_candidates")) as enrich:
        summary = discovery.run_poll_cycle(triggered_by="manual")
    assert summary["total_new"] == 2
    assert enrich.call_count == 2


def test_enrichment_error_is_isolated_and_keeps_discovered_job(test_engine, monkeypatch):
    dice = FakeAdapter("apify_dice", jobs=[_dice_job()])
    with patch("app.services.job_change_detection_service.enrich_job_with_fallback", side_effect=RuntimeError("enrichment unavailable")):
        summary = _run_single_adapter_poll(test_engine, monkeypatch, dice)
    assert summary["sources"][0]["error"] is None

    from app.database import SessionLocal
    from app.models.discovered_job_model import DiscoveredJob
    db = SessionLocal()
    try:
        row = db.query(DiscoveredJob).filter_by(source="apify_dice").one()
        assert row.application_url is None
        assert resolve_apply_action(row)["action_label"] == "Open Employer Site"
        assert resolve_apply_action(row)["url"] == "https://careers.epam.com/job/12345/lead-devops-engineer"
    finally:
        db.close()


def test_aggregator_refresh_never_erases_a_previously_promoted_direct_url(test_engine, monkeypatch):
    _disable_external_enrichment(monkeypatch)
    first = FakeAdapter("apify_dice", jobs=[_dice_job()])
    with patch(
        "app.services.job_enrichment_service.availability.check_url_availability",
        return_value={"status": "ACTIVE", "reason": "http_200", "final_url": None},
    ):
        _run_single_adapter_poll(test_engine, monkeypatch, first)

    # A later listing refresh has no direct URL of its own and changes content.
    # Even if optional enrichment is unavailable then, it cannot demote Apply.
    refreshed = FakeAdapter("apify_dice", jobs=[_dice_job(description="Updated Kubernetes and Terraform role details.")])
    with patch("app.services.job_change_detection_service.enrich_job_with_fallback", side_effect=RuntimeError("temporary outage")):
        _run_single_adapter_poll(test_engine, monkeypatch, refreshed)

    from app.database import SessionLocal
    from app.models.discovered_job_model import DiscoveredJob
    db = SessionLocal()
    try:
        row = db.query(DiscoveredJob).filter_by(source="apify_dice").one()
        assert row.application_url == "https://careers.epam.com/job/12345/lead-devops-engineer"
        assert row.direct_url == row.application_url
        assert resolve_apply_action(row)["action_label"] == "Apply"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# JSearch targeted lookup for provider-only listings — turns Dice/Adzuna/
# LinkedIn-only jobs into a real "Apply on Company Site" where one exists,
# without ever making an unbounded number of calls per cycle.
# ---------------------------------------------------------------------------

def _jsearch_lookup_response(status_code=200, jobs=None):
    response = MagicMock(status_code=status_code)
    response.json.return_value = {
        "status": "OK", "request_id": "test", "parameters": {},
        "data": {"jobs": jobs or [], "cursor": None},
    }
    return response


def _jsearch_lookup_hit(employer_name, job_title, apply_link):
    return {
        "employer_name": employer_name, "job_title": job_title, "employer_website": None,
        "job_apply_link": apply_link, "job_apply_is_direct": True, "apply_options": [],
    }


def _listing_only_job(source, source_job_id, company, title, source_url, **overrides):
    return build_normalized_job(
        source=source, source_mode="live", source_job_id=source_job_id,
        company=company, title=title, location="Austin, TX", work_mode="Onsite", job_type="Full-time",
        description="A sufficiently detailed job description for testing purposes here.",
        source_url=source_url, **overrides,
    )


def test_jsearch_lookup_promotes_dice_email_only_listing_to_a_real_employer_url(test_engine, monkeypatch):
    """A lookup hit writes a real employer URL into unverified_application_url,
    immediately promoting the destination away from the provider listing —
    "Open Career Page" until a later availability check confirms it ACTIVE
    (same path every other freshly-found direct candidate takes), never
    "Apply on Dice" anymore."""
    _disable_external_enrichment(monkeypatch)
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    dice = FakeAdapter("apify_dice", jobs=[_listing_only_job(
        "apify_dice", "d-1", "Acme Widgets", "Senior Java Developer",
        "https://www.dice.com/job-detail/48213019", candidate_application_type="APPLY_TO_EMAIL",
    )])
    _use_test_database(test_engine, monkeypatch)
    monkeypatch.setattr(registry_module, "get_adapters", lambda db: [dice])
    hit = _jsearch_lookup_hit("Acme Widgets", "Senior Java Developer", "https://careers.acmewidgets.com/job/12345/senior-java-developer")
    with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_jsearch_lookup_response(jobs=[hit])) as mocked:
        discovery.run_poll_cycle(triggered_by="manual")
    mocked.assert_called_once()

    from app.database import SessionLocal
    from app.models.discovered_job_model import DiscoveredJob
    db = SessionLocal()
    try:
        row = db.query(DiscoveredJob).filter_by(source="apify_dice").one()
        assert row.unverified_application_url == "https://careers.acmewidgets.com/job/12345/senior-java-developer"
        action = present_updated_job_action(resolve_apply_action(row))
        assert action["action_label"] == "Open Career Page"
        assert action["url"] == "https://careers.acmewidgets.com/job/12345/senior-java-developer"
    finally:
        db.close()


def test_jsearch_lookup_miss_keeps_the_provider_listing_label(test_engine, monkeypatch):
    _disable_external_enrichment(monkeypatch)
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    adzuna = FakeAdapter("adzuna", jobs=[_listing_only_job(
        "adzuna", "a-1", "Beta Corp", "Backend Engineer", "https://www.adzuna.com/details/1",
    )])
    _use_test_database(test_engine, monkeypatch)
    monkeypatch.setattr(registry_module, "get_adapters", lambda db: [adzuna])
    with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_jsearch_lookup_response(jobs=[])) as mocked:
        discovery.run_poll_cycle(triggered_by="manual")
    mocked.assert_called_once()

    from app.database import SessionLocal
    from app.models.discovered_job_model import DiscoveredJob
    db = SessionLocal()
    try:
        row = db.query(DiscoveredJob).filter_by(source="adzuna").one()
        assert row.unverified_application_url is None
        action = present_updated_job_action(resolve_apply_action(row))
        assert action["action_label"] == "Apply on Adzuna"
    finally:
        db.close()


def test_jsearch_lookup_both_dice_and_adzuna_listings_are_attempted(test_engine, monkeypatch):
    """Deliberately a SEPARATE call site from the greenhouse/lever/SerpAPI
    enrichment pass — must still run for an APPLY_TO_EMAIL Dice row, which
    that pass intentionally skips before ever calling enrichment."""
    _disable_external_enrichment(monkeypatch)
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    dice_email_only = _listing_only_job(
        "apify_dice", "d-2", "Acme Widgets", "Senior Java Developer",
        "https://www.dice.com/job-detail/48213020", candidate_application_type="APPLY_TO_EMAIL",
    )
    adzuna_listing = _listing_only_job(
        "adzuna", "a-2", "Beta Corp", "Backend Engineer", "https://www.adzuna.com/details/2",
    )
    _use_test_database(test_engine, monkeypatch)
    monkeypatch.setattr(
        registry_module, "get_adapters",
        lambda db: [FakeAdapter("apify_dice", jobs=[dice_email_only]), FakeAdapter("adzuna", jobs=[adzuna_listing])],
    )
    with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_jsearch_lookup_response(jobs=[])) as mocked:
        discovery.run_poll_cycle(triggered_by="manual")
    assert mocked.call_count == 2


def test_jsearch_lookup_per_cycle_cap_is_respected(test_engine, monkeypatch):
    _disable_external_enrichment(monkeypatch)
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    monkeypatch.setenv("JSEARCH_LOOKUP_MAX_PER_CYCLE", "1")
    jobs = [
        _listing_only_job("adzuna", f"a-{i}", f"Company {i}", "Backend Engineer", f"https://www.adzuna.com/details/{i}")
        for i in range(3)
    ]
    _use_test_database(test_engine, monkeypatch)
    monkeypatch.setattr(registry_module, "get_adapters", lambda db: [FakeAdapter("adzuna", jobs=jobs)])
    with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_jsearch_lookup_response(jobs=[])) as mocked:
        discovery.run_poll_cycle(triggered_by="manual")
    assert mocked.call_count == 1


def test_jsearch_lookup_stops_entirely_on_rate_limit_within_the_same_cycle(test_engine, monkeypatch):
    _disable_external_enrichment(monkeypatch)
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    monkeypatch.setenv("JSEARCH_LOOKUP_MAX_PER_CYCLE", "25")
    jobs = [
        _listing_only_job("adzuna", f"a-{i}", f"Company {i}", "Backend Engineer", f"https://www.adzuna.com/details/{i}")
        for i in range(3)
    ]
    _use_test_database(test_engine, monkeypatch)
    monkeypatch.setattr(registry_module, "get_adapters", lambda db: [FakeAdapter("adzuna", jobs=jobs)])
    with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_jsearch_lookup_response(status_code=429)) as mocked:
        discovery.run_poll_cycle(triggered_by="manual")
    assert mocked.call_count == 1  # the 429 on job 1 stops the other 2 this cycle


def test_jsearch_lookup_skips_a_job_already_attempted_within_7_days(test_engine, monkeypatch):
    _disable_external_enrichment(monkeypatch)
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    job = _listing_only_job("adzuna", "a-cooldown", "Beta Corp", "Backend Engineer", "https://www.adzuna.com/details/9")
    _use_test_database(test_engine, monkeypatch)
    monkeypatch.setattr(registry_module, "get_adapters", lambda db: [FakeAdapter("adzuna", jobs=[job])])

    with patch("app.job_sources.jsearch.url_safety.safe_get", return_value=_jsearch_lookup_response(jobs=[])) as mocked:
        discovery.run_poll_cycle(triggered_by="manual")
        discovery.run_poll_cycle(triggered_by="manual")
    # Second cycle's own enrichment cooldown (6h) also skips re-touching
    # enrichment_diagnostics, which is exactly what preserves the lookup
    # marker between cycles — asserting the net effect: only 1 HTTP call.
    assert mocked.call_count == 1
