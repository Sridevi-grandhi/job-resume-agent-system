"""Tests for app/routes/job_discovery_routes.py (the new /api/job-discovery
API). Verifies the feature works without a resume (#17/#46), without
preferences, filters/search, the manual refresh guard, that mock jobs never
reach normal users (#6/#7/#36), that Apply resolves to a real, tracked
destination (#15/#16/#29), and — most importantly — that Save Job /
Optimize Resume reuse the existing Selected Jobs + ATS Optimizer surfaces
unmodified (#37/#38/#56/#57).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.models.discovered_job_model import DiscoveredJob
from app.models.job_apply_event_model import JobApplyEvent
from app.services.job_normalization_service import resolve_apply_action


def _seed_job(db_session, **overrides):
    defaults = dict(
        source="greenhouse", source_mode="live", source_job_id="1", fingerprint="fp-1",
        company="Example Employer", company_logo_url="https://logos.example.test/e.png",
        title="Java Full Stack Developer", location="Remote",
        work_mode="Remote", job_type="Full-time",
        description="We need a Java Full Stack Developer with Spring Boot, React, and AWS experience.",
        skills="Java, Spring Boot, React, AWS",
        # Realistic multi-digit requisition id — classify_url_specificity
        # requires 4+ digits to count as a specific-job identifier, so a
        # short placeholder like "/jobs/1" would (correctly) classify as
        # UNKNOWN specificity instead of the SPECIFIC_JOB this fixture means
        # to represent for every test that doesn't care about specificity.
        direct_url="https://boards.greenhouse.io/example-employer/jobs/48213",
        application_url="https://boards.greenhouse.io/example-employer/jobs/48213",
        status="ACTIVE", content_hash="hash-1",
        first_seen_at=datetime.now(timezone.utc), last_seen_at=datetime.now(timezone.utc),
        last_checked_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    job = DiscoveredJob(**defaults)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def test_get_jobs_without_resume_still_shows_jobs(client, test_user, db_session):
    _seed_job(db_session)
    response = client.get(f"/api/job-discovery/jobs/{test_user.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["has_resume"] is False
    assert len(body["jobs"]) == 1
    assert body["jobs"][0]["match"] is None
    assert body["jobs"][0]["needs_resume"] is True


def test_get_jobs_with_resume_and_preferences_computes_match(client, test_user, test_preferences, test_resume, db_session):
    _seed_job(db_session)
    response = client.get(f"/api/job-discovery/jobs/{test_user.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["has_resume"] is True
    assert len(body["jobs"]) == 1
    match = body["jobs"][0]["match"]
    assert match is not None
    assert 0 <= match["match_score"] <= 100
    assert isinstance(match["fitting_skills"], list)
    assert isinstance(match["missing_skills"], list)
    assert match["verdict"]


def test_get_jobs_missing_user_returns_404(client):
    response = client.get("/api/job-discovery/jobs/999999")
    assert response.status_code == 404


def test_search_filter_narrows_results(client, test_user, db_session):
    _seed_job(db_session, source_job_id="1", fingerprint="fp-1", title="Java Full Stack Developer")
    _seed_job(db_session, source_job_id="2", fingerprint="fp-2", title="Site Reliability Engineer",
              company="Northwind Cloud", skills="Kubernetes, Terraform, AWS",
              description="We need an SRE with Kubernetes and Terraform experience on AWS.")
    response = client.get(f"/api/job-discovery/jobs/{test_user.id}", params={"search": "Reliability"})
    assert response.status_code == 200
    titles = [job["title"] for job in response.json()["jobs"]]
    assert titles == ["Site Reliability Engineer"]


def test_min_score_filter_excludes_low_matches(client, test_user, test_preferences, test_resume, db_session):
    # Deliberately disjoint from the Java resume fixture (no title/skill overlap at all).
    _seed_job(
        db_session, source_job_id="9", fingerprint="fp-9",
        title="Site Reliability Engineer", company="Northwind Cloud",
        skills="Kubernetes, Terraform, Prometheus, Grafana",
        description="We need an SRE with Kubernetes, Terraform, Prometheus, and Grafana experience.",
    )
    response = client.get(f"/api/job-discovery/jobs/{test_user.id}", params={"min_score": 50})
    assert response.status_code == 200
    assert response.json()["jobs"] == []


# ---------------------------------------------------------------------------
# Company-centric identity + no mock jobs in normal mode
# ---------------------------------------------------------------------------

def test_job_payload_is_company_centric(client, test_user, db_session):
    _seed_job(db_session)
    response = client.get(f"/api/job-discovery/jobs/{test_user.id}")
    job = response.json()["jobs"][0]
    assert job["company"] == "Example Employer"
    assert job["company_logo_url"] == "https://logos.example.test/e.png"
    assert job["application_url"] == "https://boards.greenhouse.io/example-employer/jobs/48213"
    # Internal/diagnostic fields may still be present in the payload (for
    # future admin tooling) but must never be the rendered identity — that's
    # enforced at the frontend layer (DiscoveredJobCard never renders them).
    assert job["source"] == "greenhouse"


def test_mock_jobs_are_excluded_from_normal_results(client, test_user, db_session, monkeypatch):
    monkeypatch.delenv("UPDATED_JOB_DEMO_MODE", raising=False)
    _seed_job(db_session, source_job_id="1", fingerprint="fp-1", company="Real Employer", source_mode="live")
    _seed_job(db_session, source_job_id="2", fingerprint="fp-2", company="Meridian Systems", source_mode="mock")
    response = client.get(f"/api/job-discovery/jobs/{test_user.id}")
    companies = {job["company"] for job in response.json()["jobs"]}
    assert companies == {"Real Employer"}


def test_mock_jobs_appear_only_in_demo_mode(client, test_user, db_session, monkeypatch):
    monkeypatch.setenv("UPDATED_JOB_DEMO_MODE", "true")
    _seed_job(db_session, source_job_id="2", fingerprint="fp-2", company="Meridian Systems", source_mode="mock")
    response = client.get(f"/api/job-discovery/jobs/{test_user.id}")
    companies = {job["company"] for job in response.json()["jobs"]}
    assert companies == {"Meridian Systems"}


def test_companies_monitored_reflects_only_visible_real_companies(client, test_user, db_session, monkeypatch):
    monkeypatch.delenv("UPDATED_JOB_DEMO_MODE", raising=False)
    _seed_job(db_session, source_job_id="1", fingerprint="fp-1", company="Real Employer A", source_mode="live")
    _seed_job(db_session, source_job_id="2", fingerprint="fp-2", company="Real Employer B", source_mode="live",
              direct_url="https://boards.greenhouse.io/b/jobs/2", application_url="https://boards.greenhouse.io/b/jobs/2")
    _seed_job(db_session, source_job_id="3", fingerprint="fp-3", company="Meridian Systems", source_mode="mock")
    response = client.get(f"/api/job-discovery/jobs/{test_user.id}")
    assert response.json()["summary"]["companies_monitored"] == 2


def test_sources_endpoint_returns_registry_and_company_count(client):
    response = client.get("/api/job-discovery/sources")
    assert response.status_code == 200
    body = response.json()
    names = {row["source_name"] for row in body["sources"]}
    # Remotive is now an optional supplemental source, off by default under
    # the broad-provider (JSearch) architecture — see
    # UPDATED_JOB_REMOTIVE_ENABLED. jsearch always appears in the registry
    # (like Greenhouse/Lever) even when JSEARCH_API_KEY isn't configured.
    assert {"greenhouse", "lever", "workday", "jsearch"}.issubset(names)
    assert "remotive" not in names
    assert "configured_companies" in body
    assert body["demo_mode"] is False


# ---------------------------------------------------------------------------
# Multi-user isolation (Part 11) — the DiscoveredJob pool is shared, but
# match scores, saved state, and apply-tracking must never leak across users.
# ---------------------------------------------------------------------------

def test_two_users_get_independent_match_scores_for_the_shared_job(client, db_session, test_user, second_user, java_resume_text, python_resume_text):
    from app.models.resume_model import Resume

    _seed_job(
        db_session, source_job_id="iso-1", fingerprint="fp-iso-1",
        title="Java Full Stack Developer", company="Example Employer",
        skills="Java, Spring Boot, React, AWS",
        description="We need a Java Full Stack Developer with Spring Boot, React, and AWS experience.",
    )
    db_session.add(Resume(user_id=test_user.id, original_file_name="a.txt", stored_file_path="/tmp/a.txt", file_type="txt", parsed_text=java_resume_text))
    db_session.add(Resume(user_id=second_user.id, original_file_name="b.txt", stored_file_path="/tmp/b.txt", file_type="txt", parsed_text=python_resume_text))
    db_session.commit()

    java_body = client.get(f"/api/job-discovery/jobs/{test_user.id}").json()
    python_body = client.get(f"/api/job-discovery/jobs/{second_user.id}").json()

    java_match = java_body["jobs"][0]["match"]
    python_match = python_body["jobs"][0]["match"]
    assert java_match is not None and python_match is not None
    # The Java-skilled resume must score at least as well as the unrelated
    # Python resume against a Java-only job description — never identical
    # score-computation state leaking between users.
    assert java_match["match_score"] >= python_match["match_score"]


def test_save_by_one_user_does_not_mark_the_job_saved_for_another_user(client, db_session, test_user, second_user):
    job = _seed_job(db_session)
    save_response = client.post(f"/api/job-discovery/save/{test_user.id}", json={"discovered_job_id": job.id})
    assert save_response.status_code == 200
    assert save_response.json()["already_saved"] is False

    from app.models.job_model import Job
    assert db_session.query(Job).filter(Job.user_id == test_user.id).count() == 1
    assert db_session.query(Job).filter(Job.user_id == second_user.id).count() == 0

    # The second user saving the SAME discovered job independently succeeds
    # (not blocked/deduped by the first user's own save).
    second_save = client.post(f"/api/job-discovery/save/{second_user.id}", json={"discovered_job_id": job.id})
    assert second_save.status_code == 200
    assert second_save.json()["already_saved"] is False
    assert db_session.query(Job).filter(Job.user_id == second_user.id).count() == 1


def test_apply_tracking_is_scoped_per_user(client, db_session, test_user, second_user):
    job = _seed_job(db_session)
    with patch("app.services.job_availability_service.check_url_availability", return_value={"status": "ACTIVE", "reason": "mocked", "final_url": None}):
        response = client.post(f"/api/job-discovery/apply/{test_user.id}", json={"discovered_job_id": job.id})
    assert response.status_code == 200

    from app.models.job_apply_event_model import JobApplyEvent
    assert db_session.query(JobApplyEvent).filter(JobApplyEvent.user_id == test_user.id).count() == 1
    assert db_session.query(JobApplyEvent).filter(JobApplyEvent.user_id == second_user.id).count() == 0


def test_config_status_endpoint_never_leaks_the_api_key(client, monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "super-secret-key")
    monkeypatch.setenv("UPDATED_JOB_PRIMARY_PROVIDER", "jsearch")
    response = client.get("/api/job-discovery/config-status")
    assert response.status_code == 200
    body = response.json()
    assert body["primary_provider"] == "jsearch"
    assert body["configured"] is True
    assert "super-secret-key" not in response.text


def test_config_status_endpoint_reports_not_configured_without_key(client, monkeypatch):
    monkeypatch.delenv("JSEARCH_API_KEY", raising=False)
    response = client.get("/api/job-discovery/config-status")
    assert response.json()["configured"] is False


def test_refresh_endpoint_runs_poll_and_reports_summary(client, test_user):
    with patch("app.routes.job_discovery_routes.run_poll_cycle") as mock_run:
        mock_run.return_value = {"already_running": False, "total_new": 3, "total_updated": 1, "sources": []}
        response = client.post(f"/api/job-discovery/refresh/{test_user.id}")
    assert response.status_code == 200
    assert response.json()["total_new"] == 3
    mock_run.assert_called_once_with(triggered_by="manual")


def test_save_discovered_job_creates_selected_job_and_is_idempotent(client, test_user, db_session):
    job = _seed_job(db_session)

    first = client.post(f"/api/job-discovery/save/{test_user.id}", json={"discovered_job_id": job.id})
    assert first.status_code == 200
    body = first.json()
    assert body["already_saved"] is False
    saved_job_id = body["job_id"]

    # Reuses the existing Selected Jobs surface unmodified.
    selected = client.get(f"/api/jobs/selected/{test_user.id}")
    assert selected.status_code == 200
    assert any(row["id"] == saved_job_id for row in selected.json())

    # Clicking Save again must not create a duplicate selected job.
    second = client.post(f"/api/job-discovery/save/{test_user.id}", json={"discovered_job_id": job.id})
    assert second.status_code == 200
    assert second.json()["already_saved"] is True
    assert second.json()["job_id"] == saved_job_id

    selected_again = client.get(f"/api/jobs/selected/{test_user.id}")
    assert len(selected_again.json()) == 1


def test_save_discovered_job_missing_returns_404(client, test_user):
    response = client.post(f"/api/job-discovery/save/{test_user.id}", json={"discovered_job_id": 999999})
    assert response.status_code == 404


def test_optimize_handoff_job_is_reachable_by_existing_job_detail_endpoint(client, test_user, db_session):
    job = _seed_job(db_session)
    saved = client.post(f"/api/job-discovery/save/{test_user.id}", json={"discovered_job_id": job.id})
    job_id = saved.json()["job_id"]

    # Existing ATS Optimizer job-detail endpoint (unmodified) must resolve it.
    detail = client.get(f"/api/jobs/detail/{test_user.id}/{job_id}")
    assert detail.status_code == 200
    assert detail.json()["title"] == "Java Full Stack Developer"
    assert detail.json()["source"] == "User Selected Job"


# ---------------------------------------------------------------------------
# Apply — direct to the real employer destination, tracked server-side
# ---------------------------------------------------------------------------

def test_apply_resolves_real_url_and_records_tracking_event(client, test_user, db_session):
    job = _seed_job(
        db_session, direct_url="https://boards.greenhouse.io/example-employer/jobs/48213",
        application_url="https://boards.greenhouse.io/example-employer/jobs/48213/apply",
    )
    with patch("app.services.job_availability_service.check_url_availability", return_value={"status": "ACTIVE", "reason": "mocked", "final_url": None}):
        response = client.post(f"/api/job-discovery/apply/{test_user.id}", json={"discovered_job_id": job.id})
    assert response.status_code == 200
    body = response.json()
    assert body["application_url"] == "https://boards.greenhouse.io/example-employer/jobs/48213/apply"
    assert body["status"] == "APPLY_STARTED"

    event = db_session.query(JobApplyEvent).filter_by(user_id=test_user.id, discovered_job_id=job.id).first()
    assert event is not None
    assert event.application_url == "https://boards.greenhouse.io/example-employer/jobs/48213/apply"
    assert event.status == "APPLY_STARTED"
    assert event.apply_started_at is not None


def test_apply_url_priority_falls_back_to_direct_url_then_source_url(client, test_user, db_session):
    job = _seed_job(
        db_session, application_url=None,
        direct_url="https://boards.greenhouse.io/example-employer/jobs/1",
        source_url="https://boards.greenhouse.io/example-employer",
    )
    with patch("app.services.job_availability_service.check_url_availability", return_value={"status": "ACTIVE", "reason": "mocked", "final_url": None}):
        response = client.post(f"/api/job-discovery/apply/{test_user.id}", json={"discovered_job_id": job.id})
    assert response.json()["application_url"] == "https://boards.greenhouse.io/example-employer/jobs/1"


def test_apply_missing_job_returns_404(client, test_user):
    response = client.post(f"/api/job-discovery/apply/{test_user.id}", json={"discovered_job_id": 999999})
    assert response.status_code == 404


def test_apply_without_any_url_returns_422(client, test_user, db_session):
    job = _seed_job(db_session, application_url=None, direct_url=None, source_url=None)
    response = client.post(f"/api/job-discovery/apply/{test_user.id}", json={"discovered_job_id": job.id})
    assert response.status_code == 422


def test_apply_does_not_open_remotive_listing_when_direct_employer_url_exists(client, test_user, db_session):
    """Regression for the exact reported bug: source=remotive but a real
    employer application_url is ALSO present — Apply must open the employer
    URL, never the Remotive listing page."""
    job = _seed_job(
        db_session, source="remotive", source_job_id="88888", fingerprint="fp-remotive-1",
        source_url="https://remotive.com/remote-jobs/software-dev/example-88888",
        direct_url=None,
        application_url="https://company.com/careers/job/123",
    )
    with patch("app.services.job_availability_service.check_url_availability", return_value={"status": "ACTIVE", "reason": "mocked", "final_url": None}):
        response = client.post(f"/api/job-discovery/apply/{test_user.id}", json={"discovered_job_id": job.id})
    assert response.status_code == 200
    body = response.json()
    assert body["application_url"] == "https://company.com/careers/job/123"
    assert "remotive.com" not in body["application_url"]
    assert body["is_direct_apply"] is True
    assert body["apply_destination_type"] == "DIRECT_EMPLOYER"


def test_apply_remotive_only_listing_is_truthfully_classified_as_aggregator(client, test_user, db_session):
    """When Remotive is genuinely the only source (no employer URL was ever
    obtainable), the destination must be classified AGGREGATOR_LISTING —
    the frontend uses this to show "View Listing" instead of "Apply"."""
    job = _seed_job(
        db_session, source="remotive", source_job_id="12345", fingerprint="fp-remotive-2",
        direct_url=None, application_url=None,
        source_url="https://remotive.com/remote-jobs/software-dev/example-12345",
    )
    response = client.post(f"/api/job-discovery/apply/{test_user.id}", json={"discovered_job_id": job.id})
    assert response.status_code == 200
    body = response.json()
    assert body["application_url"] == "https://remotive.com/remote-jobs/software-dev/example-12345"
    assert body["is_direct_apply"] is False
    assert body["apply_destination_type"] == "AGGREGATOR_LISTING"


def test_job_payload_includes_apply_destination_classification(client, test_user, db_session):
    job = _seed_job(db_session)  # greenhouse-sourced fixture with a genuine ATS application_url
    response = client.get(f"/api/job-discovery/jobs/{test_user.id}")
    payload = response.json()["jobs"][0]
    assert payload["is_direct_apply"] is True
    assert payload["apply_destination_type"] == "DIRECT_ATS"
    assert job.id == payload["discovered_job_id"]


def test_job_payload_remotive_only_listing_is_not_direct_apply(client, test_user, db_session):
    _seed_job(
        db_session, source="remotive", source_job_id="55555", fingerprint="fp-remotive-3",
        direct_url=None, application_url=None,
        source_url="https://remotive.com/remote-jobs/software-dev/example-55555",
    )
    response = client.get(f"/api/job-discovery/jobs/{test_user.id}")
    payload = response.json()["jobs"][0]
    assert payload["is_direct_apply"] is False
    assert payload["apply_destination_type"] == "AGGREGATOR_LISTING"
    assert payload["application_url"] == "https://remotive.com/remote-jobs/software-dev/example-55555"


# ---------------------------------------------------------------------------
# Availability workflow — Step 6 (Apply-click revalidation), Step 7 (expired
# jobs excluded from active listings). Network is always mocked at
# job_availability_service.check_url_availability — never a real employer
# request in a test (test matrix items 17-20).
# ---------------------------------------------------------------------------

def test_apply_click_revalidates_stale_job_and_returns_job_expired_without_dead_url(client, test_user, db_session):
    """Item 17: a job that WAS active is now confirmed 404 at click-time —
    the frontend must get no application_url to navigate to, and the row
    must be updated to EXPIRED for future requests."""
    stale_checked_at = datetime.now(timezone.utc) - timedelta(hours=48)
    job = _seed_job(
        db_session, availability_status="ACTIVE", availability_checked_at=stale_checked_at,
    )
    with patch(
        "app.services.job_availability_service.check_url_availability",
        return_value={"status": "EXPIRED", "reason": "http_404", "final_url": job.application_url},
    ) as mocked_check:
        response = client.post(f"/api/job-discovery/apply/{test_user.id}", json={"discovered_job_id": job.id})
    mocked_check.assert_called_once()
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "JOB_EXPIRED"
    assert body["application_url"] is None
    assert body["availability_status"] == "EXPIRED"

    db_session.refresh(job)
    assert job.availability_status == "EXPIRED"
    assert job.availability_reason == "http_404"

    # No apply event was recorded for a destination that never opened.
    assert db_session.query(JobApplyEvent).filter_by(discovered_job_id=job.id).count() == 0


def test_apply_click_on_fresh_active_job_skips_revalidation_and_returns_destination(client, test_user, db_session):
    """Item 18: a RECENTLY-verified ACTIVE job must not trigger a fresh
    network check on every click (TTL cache) — it just returns the
    destination and records APPLY_STARTED."""
    job = _seed_job(
        db_session, availability_status="ACTIVE", availability_checked_at=datetime.now(timezone.utc),
    )
    with patch("app.services.job_availability_service.check_url_availability") as mocked_check:
        response = client.post(f"/api/job-discovery/apply/{test_user.id}", json={"discovered_job_id": job.id})
    mocked_check.assert_not_called()
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "APPLY_STARTED"
    assert body["application_url"] == job.application_url

    event = db_session.query(JobApplyEvent).filter_by(discovered_job_id=job.id).first()
    assert event is not None
    assert event.status == "APPLY_STARTED"


def test_apply_click_unknown_availability_uses_safe_fallback_and_still_navigates(client, test_user, db_session):
    """UNKNOWN (e.g. bot-blocked) must never prevent navigation — only a
    CONFIRMED expiry does. A SPECIFIC_JOB + UNKNOWN destination resolves to
    "Open Employer Site" (see resolve_apply_action), so the tracked event
    must be EMPLOYER_SITE_OPENED, never the misleading APPLY_STARTED —
    this is exactly the live Leidos/Uline scenario."""
    job = _seed_job(db_session, availability_status="UNKNOWN", availability_checked_at=None)
    with patch(
        "app.services.job_availability_service.check_url_availability",
        return_value={"status": "UNKNOWN", "reason": "http_403", "final_url": None},
    ):
        response = client.post(f"/api/job-discovery/apply/{test_user.id}", json={"discovered_job_id": job.id})
    assert response.status_code == 200
    body = response.json()
    assert body["action_label"] == "Open Employer Site"
    assert body["can_apply"] is False
    assert body["status"] == "EMPLOYER_SITE_OPENED"
    # Still a real, safe destination — navigation must still occur.
    assert body["application_url"] == job.application_url
    assert body["availability_status"] == "UNKNOWN"

    event = db_session.query(JobApplyEvent).filter_by(discovered_job_id=job.id).first()
    assert event is not None
    assert event.status == "EMPLOYER_SITE_OPENED"


def test_apply_click_on_aggregator_listing_never_triggers_availability_check(client, test_user, db_session):
    """Item 13/9: LinkedIn (and any aggregator) must never run the
    employer-availability workflow at all."""
    job = _seed_job(
        db_session, source="remotive", source_job_id="lnkd-1", fingerprint="fp-agg-1",
        direct_url=None, application_url=None,
        source_url="https://www.linkedin.com/jobs/view/999",
    )
    with patch("app.services.job_availability_service.check_url_availability") as mocked_check:
        response = client.post(f"/api/job-discovery/apply/{test_user.id}", json={"discovered_job_id": job.id})
    mocked_check.assert_not_called()
    assert response.status_code == 200
    body = response.json()
    # A listing click is real navigation but never a verified apply — must
    # not be recorded as APPLY_STARTED.
    assert body["action_label"] == "View Listing"
    assert body["status"] == "LISTING_OPENED"


# ---------------------------------------------------------------------------
# Destination specificity — root-cause regression for a real employer/ATS
# domain whose URL is only a generic careers/search page (e.g. Uline's
# JobSearchResults page), never a specific posting. Never hardcodes the
# employer this was diagnosed against (test matrix items 3-6, 10, 11).
# ---------------------------------------------------------------------------

def test_apply_endpoint_never_returns_generic_page_as_verified_apply(client, test_user, db_session):
    """Item 11: the Apply endpoint must refuse to present a generic
    careers/search page as a verified Apply — it still returns the
    destination (a real page worth opening) but labeled and flagged
    accordingly, never silently upgraded to "Apply"."""
    job = _seed_job(
        db_session, source_job_id="generic-1", fingerprint="fp-generic-1",
        direct_url="https://careers.example-corp.com/jobs",
        application_url="https://careers.example-corp.com/jobs",
        availability_status="ACTIVE",
    )
    with patch("app.services.job_availability_service.check_url_availability") as mocked_check:
        response = client.post(f"/api/job-discovery/apply/{test_user.id}", json={"discovered_job_id": job.id})
    body = response.json()
    assert response.status_code == 200
    assert body["action_label"] == "Open Employer Site"
    assert body["destination_specificity"] == "GENERIC_EMPLOYER_PAGE"
    assert body["can_apply"] is False
    # Still a real, navigable destination — never null.
    assert body["application_url"] == job.application_url
    # Item: re-verifying a generic page's availability never changes its
    # label, so the endpoint must not waste a request on it.
    mocked_check.assert_not_called()
    # Item 8: never falsely recorded as an application.
    assert body["status"] == "EMPLOYER_SITE_OPENED"
    event = db_session.query(JobApplyEvent).filter_by(discovered_job_id=job.id).first()
    assert event is not None
    assert event.status == "EMPLOYER_SITE_OPENED"
    assert db_session.query(JobApplyEvent).filter_by(discovered_job_id=job.id, status="APPLY_STARTED").count() == 0


def test_apply_endpoint_generic_page_never_shown_as_apply_in_listing(client, test_user, db_session):
    """Item 10: a generic employer page must NEVER return can_apply=true,
    including in the plain job-listing payload (not just the click
    endpoint)."""
    _seed_job(
        db_session, source_job_id="generic-2", fingerprint="fp-generic-2",
        direct_url="https://careers.example-corp.com/JobSearchResults?keyword=java",
        application_url="https://careers.example-corp.com/JobSearchResults?keyword=java",
        availability_status="ACTIVE",
    )
    response = client.get(f"/api/job-discovery/jobs/{test_user.id}")
    payload = response.json()["jobs"][0]
    assert payload["destination_specificity"] == "GENERIC_EMPLOYER_PAGE"
    assert payload["action_label"] == "Open Employer Site"
    assert payload["can_apply"] is False


def test_apply_endpoint_specific_job_still_gets_apply_after_specificity_fix(client, test_user, db_session):
    """Item 12: the specificity fix must not regress a genuinely specific,
    active employer job — it still resolves to Apply."""
    job = _seed_job(
        db_session, source_job_id="specific-1", fingerprint="fp-specific-1",
        direct_url="https://careers.example-corp.com/job/48213/software-engineer",
        application_url="https://careers.example-corp.com/job/48213/software-engineer",
        availability_status="ACTIVE", availability_checked_at=datetime.now(timezone.utc),
    )
    with patch("app.services.job_availability_service.check_url_availability") as mocked_check:
        response = client.post(f"/api/job-discovery/apply/{test_user.id}", json={"discovered_job_id": job.id})
    body = response.json()
    assert response.status_code == 200
    assert body["action_label"] == "Apply"
    assert body["destination_specificity"] == "SPECIFIC_JOB"
    assert body["can_apply"] is True
    assert body["application_url"] == job.application_url
    # Fresh (not stale) — no need to hit the network again.
    mocked_check.assert_not_called()


def test_expired_job_excluded_from_listing_but_active_job_still_shown(client, test_user, db_session):
    """Item 19: a confirmed-EXPIRED job must not appear in the jobs list
    that feeds both "Top Job Matches" and "All Jobs" — an ACTIVE one still
    does."""
    active_job = _seed_job(
        db_session, source_job_id="active-1", fingerprint="fp-active-1",
        title="Active Role", availability_status="ACTIVE",
    )
    _seed_job(
        db_session, source_job_id="expired-1", fingerprint="fp-expired-1",
        title="Expired Role", availability_status="EXPIRED",
    )
    response = client.get(f"/api/job-discovery/jobs/{test_user.id}")
    payload = response.json()["jobs"]
    titles = {job["title"] for job in payload}
    assert "Active Role" in titles
    assert "Expired Role" not in titles
    assert active_job.id in {job["discovered_job_id"] for job in payload}


def test_expired_job_row_and_apply_history_preserved_not_deleted(client, test_user, db_session):
    """Item 20: an expired job is excluded from active listings (item 19)
    but its row — and any apply history tied to it — is never deleted, and
    its own resolved action is truthfully "Job Closed"."""
    job = _seed_job(
        db_session, source_job_id="was-saved-1", fingerprint="fp-was-saved-1",
        availability_status="ACTIVE",
    )
    event = JobApplyEvent(
        user_id=test_user.id, discovered_job_id=job.id,
        application_url=job.application_url, apply_started_at=datetime.now(timezone.utc),
        status="APPLY_STARTED",
    )
    db_session.add(event)
    db_session.commit()

    # The posting closes sometime later.
    job.availability_status = "EXPIRED"
    job.availability_reason = "http_404"
    db_session.commit()

    # Excluded from the active listing (item 19)...
    response = client.get(f"/api/job-discovery/jobs/{test_user.id}")
    assert job.id not in {j["discovered_job_id"] for j in response.json()["jobs"]}

    # ...but the row and its apply history both still exist, never deleted.
    from app.models.discovered_job_model import DiscoveredJob as DiscoveredJobModel

    preserved = db_session.query(DiscoveredJobModel).filter_by(id=job.id).first()
    assert preserved is not None
    assert db_session.query(JobApplyEvent).filter_by(discovered_job_id=job.id).count() == 1

    action = resolve_apply_action(preserved)
    assert action["action_label"] == "Job Closed"
    assert action["can_apply"] is False
