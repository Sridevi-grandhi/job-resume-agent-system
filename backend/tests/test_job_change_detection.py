"""Tests for app/services/job_change_detection_service.py.

Covers spec requirements: duplicate prevention across repeated polls (#13,
#49), update detection without duplication (#14, #51), and the safe
inactive-not-deleted closing strategy (#15).
"""

from __future__ import annotations

from app.models.discovered_job_model import DiscoveredJob
from app.services import job_change_detection_service as change_detection
from app.services.job_normalization_service import build_normalized_job


def _job(source_job_id="1", title="Senior Java Full Stack Developer", description="Java Spring Boot React role."):
    return build_normalized_job(
        source="greenhouse",
        source_mode="live",
        source_job_id=source_job_id,
        company="Meridian Systems",
        title=title,
        location="Remote",
        work_mode="Remote",
        job_type="Full-time",
        description=description,
        skills="Java, Spring Boot, React",
        direct_url="https://boards.greenhouse.io/meridian/jobs/1",
    )


def test_first_poll_inserts_all_jobs(db_session):
    jobs = [_job(source_job_id=str(i)) for i in range(1, 101)]
    result = change_detection.process_source_jobs(db_session, "greenhouse", jobs)
    assert result["new"] == 100
    assert result["duplicates"] == 0
    assert result["updated"] == 0
    assert db_session.query(DiscoveredJob).count() == 100


def test_second_identical_poll_inserts_nothing(db_session):
    jobs = [_job(source_job_id=str(i)) for i in range(1, 101)]
    change_detection.process_source_jobs(db_session, "greenhouse", jobs)

    result = change_detection.process_source_jobs(db_session, "greenhouse", jobs)
    assert result["new"] == 0
    assert result["duplicates"] == 100
    assert db_session.query(DiscoveredJob).count() == 100


def test_third_poll_with_two_extra_jobs_inserts_only_two(db_session):
    jobs = [_job(source_job_id=str(i)) for i in range(1, 101)]
    change_detection.process_source_jobs(db_session, "greenhouse", jobs)
    change_detection.process_source_jobs(db_session, "greenhouse", jobs)

    extended = jobs + [_job(source_job_id="101", title="Staff Java Engineer"), _job(source_job_id="102", title="Java Backend Lead")]
    result = change_detection.process_source_jobs(db_session, "greenhouse", extended)
    assert result["new"] == 2
    assert result["duplicates"] == 100
    assert db_session.query(DiscoveredJob).count() == 102


def test_job_content_change_updates_existing_row_without_duplicating(db_session):
    original = [_job(source_job_id="1", description="Java Spring Boot React role.")]
    change_detection.process_source_jobs(db_session, "greenhouse", original)
    first_seen = db_session.query(DiscoveredJob).filter_by(source_job_id="1").first().first_seen_at

    changed = [_job(source_job_id="1", description="Java Spring Boot React AND GraphQL role, now hybrid in Austin.")]
    result = change_detection.process_source_jobs(db_session, "greenhouse", changed)

    assert result["new"] == 0
    assert result["updated"] == 1
    assert db_session.query(DiscoveredJob).count() == 1
    row = db_session.query(DiscoveredJob).filter_by(source_job_id="1").first()
    assert "GraphQL" in row.description
    assert row.first_seen_at == first_seen  # never lost
    assert row.updated_detected_at is not None


def test_fingerprint_fallback_dedupes_jobs_without_source_job_id(db_session, monkeypatch):
    # Workday-shaped data only ever exists in demo mode in this codebase
    # (see job_sources/workday.py) — enable it here so this mock-shaped
    # fixture isn't rejected by process_source_jobs' production mock guard;
    # the fingerprint-fallback behavior under test is unrelated to that gate.
    monkeypatch.setenv("UPDATED_JOB_DEMO_MODE", "true")
    job = build_normalized_job(
        source="workday", source_mode="mock", source_job_id=None,
        company="Northwind Cloud", title="DevOps Engineer", location="Remote",
        work_mode="Remote", job_type="Full-time",
        description="Terraform and Kubernetes on AWS, CI/CD pipelines.",
        skills="Terraform, Kubernetes, AWS", direct_url=None,
    )
    change_detection.process_source_jobs(db_session, "workday", [job])
    result = change_detection.process_source_jobs(db_session, "workday", [job])
    assert result["new"] == 0
    assert result["duplicates"] == 1
    assert db_session.query(DiscoveredJob).count() == 1


def test_mock_job_never_persisted_outside_demo_mode(db_session, monkeypatch):
    """Production real-job-validation guard: even if an adapter bug ever
    produced source_mode='mock' data, process_source_jobs must refuse to
    persist it unless UPDATED_JOB_DEMO_MODE is explicitly on."""
    monkeypatch.delenv("UPDATED_JOB_DEMO_MODE", raising=False)
    job = build_normalized_job(
        source="workday", source_mode="mock", source_job_id="fake-1",
        company="Fake Co", title="Fake Engineer", location="Remote",
        work_mode="Remote", job_type="Full-time",
        description="This should never be persisted in production mode.",
        skills="", direct_url=None,
    )
    result = change_detection.process_source_jobs(db_session, "workday", [job])
    assert result["new"] == 0
    assert db_session.query(DiscoveredJob).count() == 0


def test_mock_job_persisted_when_demo_mode_explicitly_enabled(db_session, monkeypatch):
    monkeypatch.setenv("UPDATED_JOB_DEMO_MODE", "true")
    job = build_normalized_job(
        source="workday", source_mode="mock", source_job_id="fake-1",
        company="Fake Co", title="Fake Engineer", location="Remote",
        work_mode="Remote", job_type="Full-time",
        description="Demo-mode mock job, allowed only in local development.",
        skills="", direct_url=None,
    )
    result = change_detection.process_source_jobs(db_session, "workday", [job])
    assert result["new"] == 1


def test_close_stale_jobs_marks_inactive_after_threshold_and_never_deletes(db_session, monkeypatch):
    monkeypatch.setenv("INACTIVE_AFTER_MISSED_POLLS", "2")
    jobs = [_job(source_job_id="1"), _job(source_job_id="2", title="Java Backend Lead")]
    change_detection.process_source_jobs(db_session, "greenhouse", jobs)

    # Job "2" disappears from subsequent polls.
    remaining = [_job(source_job_id="1")]
    for _ in range(2):
        result = change_detection.process_source_jobs(db_session, "greenhouse", remaining)
        change_detection.close_stale_jobs(db_session, "greenhouse", result["seen_ids"])

    row = db_session.query(DiscoveredJob).filter_by(source_job_id="2").first()
    assert row is not None  # never deleted
    assert row.status == "INACTIVE"
    assert db_session.query(DiscoveredJob).count() == 2


def test_reappearing_job_reactivates_to_active(db_session, monkeypatch):
    monkeypatch.setenv("INACTIVE_AFTER_MISSED_POLLS", "1")
    jobs = [_job(source_job_id="1")]
    result = change_detection.process_source_jobs(db_session, "greenhouse", jobs)
    change_detection.close_stale_jobs(db_session, "greenhouse", set())  # nothing seen this round

    row = db_session.query(DiscoveredJob).filter_by(source_job_id="1").first()
    assert row.status == "INACTIVE"

    change_detection.process_source_jobs(db_session, "greenhouse", jobs)
    row = db_session.query(DiscoveredJob).filter_by(source_job_id="1").first()
    assert row.status == "ACTIVE"
