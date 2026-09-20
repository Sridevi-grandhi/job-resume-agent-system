"""Tests for app/services/updated_job_discovery_service.py.

Covers: one failing source never stops the others (#40/#50), and a poll
already in progress returns its status instead of starting a duplicate
(#10) — the same guard the manual Refresh button and the scheduler share.
"""

from __future__ import annotations

from datetime import datetime, timezone

import app.database as database_module
from sqlalchemy.orm import sessionmaker

from app.job_sources.base import JobSourceAdapter, SourceHealth
from app.services import job_source_registry_service as registry_module
from app.services import updated_job_discovery_service as discovery
from app.services.job_normalization_service import build_normalized_job


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
