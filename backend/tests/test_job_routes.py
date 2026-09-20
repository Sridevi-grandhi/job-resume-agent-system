"""Tests for app/routes/job_routes.py."""

from __future__ import annotations

from unittest.mock import patch

from app.agents.job_url_import_agent import JobUrlImportError


def test_search_jobs_with_existing_agent(client, test_user, test_preferences):
    response = client.post(f"/api/jobs/search/{test_user.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["agent_used"].startswith("Existing Job Search Agent")
    assert body["jobs_created"] > 0
    assert all(job.get("job_url") for job in body["jobs"])


def test_search_jobs_existing_agent_requires_preferences(client, test_user):
    response = client.post(f"/api/jobs/search/{test_user.id}")
    assert response.status_code == 404
    assert "preferences" in response.json()["detail"].lower()


def test_search_jobs_existing_agent_missing_user(client):
    response = client.post("/api/jobs/search/999999")
    assert response.status_code == 404


def test_search_jobs_updated_agent_success(client, test_user, test_preferences, mock_remotive_response):
    with patch(
        "app.routes.job_routes.get_updated_jobs_from_remotive",
        return_value={"jobs": [
            {
                "title": "Java Full Stack Developer", "company": "Remote Co", "location": "Anywhere",
                "work_mode": "Remote", "job_type": "Full-time",
                "description": "Java Spring Boot React PostgreSQL AWS Docker role.",
                "skills_required": "Java, Spring Boot, React",
                "source": "Remotive API", "job_url": "https://remotive.com/remote-jobs/1",
            }
        ], "api_status": "ok"},
    ):
        response = client.post(f"/api/jobs/search-updated/{test_user.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["jobs_created"] == 1
    assert body["jobs"][0]["source"] == "Remotive API"


def test_search_jobs_updated_agent_no_results(client, test_user, test_preferences):
    with patch(
        "app.routes.job_routes.get_updated_jobs_from_remotive",
        return_value={"jobs": [], "api_status": "ok", "diagnostics": {"reason": "no matches"}},
    ):
        response = client.post(f"/api/jobs/search-updated/{test_user.id}")
    assert response.status_code == 200
    assert response.json()["jobs_created"] == 0


def test_search_jobs_updated_agent_unavailable_never_crashes(client, test_user, test_preferences):
    with patch(
        "app.routes.job_routes.get_updated_jobs_from_remotive",
        side_effect=RuntimeError("Remotive down"),
    ):
        response = client.post(f"/api/jobs/search-updated/{test_user.id}")
    assert response.status_code == 200
    assert "unavailable" in response.json()["message"].lower()


def test_add_selected_job(client, test_user):
    response = client.post(f"/api/jobs/selected/{test_user.id}", json={
        "title": "Java Developer", "company": "Acme", "location": "Remote",
        "description": "Java Spring Boot role building REST APIs for enterprise systems.",
        "skills_required": "Java, Spring Boot",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["job_saved"] is True
    assert body["job"]["source"] == "User Selected Job"


def test_add_selected_job_missing_user_returns_404(client):
    response = client.post("/api/jobs/selected/999999", json={
        "title": "Java Developer", "company": "Acme", "location": "Remote", "description": "desc",
    })
    assert response.status_code == 404


def test_add_selected_job_imported_source_requires_fields(client, test_user):
    response = client.post(f"/api/jobs/selected/{test_user.id}", json={
        "title": "", "company": "", "location": "Remote", "description": "",
        "source": "LinkedIn Imported", "job_url": "",
    })
    assert response.status_code == 422


def test_add_selected_job_imported_duplicate_url_rejected(client, test_user):
    with patch(
        "app.routes.job_routes.normalize_job_url",
        return_value=("LinkedIn", "https://linkedin.com/jobs/view/123", None),
    ):
        first = client.post(f"/api/jobs/selected/{test_user.id}", json={
            "title": "Java Developer", "company": "Acme", "location": "Remote",
            "description": "Java Spring Boot role.", "source": "LinkedIn Imported",
            "job_url": "https://linkedin.com/jobs/view/123",
        })
        assert first.status_code == 200

        second = client.post(f"/api/jobs/selected/{test_user.id}", json={
            "title": "Java Developer", "company": "Acme", "location": "Remote",
            "description": "Java Spring Boot role.", "source": "LinkedIn Imported",
            "job_url": "https://linkedin.com/jobs/view/123",
        })
    assert second.status_code == 409
    assert second.json()["detail"]["duplicate"] is True


def test_add_selected_job_import_url_error_returns_400(client, test_user):
    with patch(
        "app.routes.job_routes.normalize_job_url",
        side_effect=JobUrlImportError("Unsupported job URL"),
    ):
        response = client.post(f"/api/jobs/selected/{test_user.id}", json={
            "title": "Java Developer", "company": "Acme", "location": "Remote",
            "description": "Java Spring Boot role.", "source": "LinkedIn Imported",
            "job_url": "https://not-a-real-source.example.com/job/1",
        })
    assert response.status_code == 400


def test_import_job_by_url_success(client, test_user):
    preview = {
        "normalized_job_url": "https://linkedin.com/jobs/view/456",
        "title": "Java Developer", "company": "Acme",
    }
    with patch("app.routes.job_routes.build_job_preview", return_value=preview):
        response = client.post(f"/api/jobs/import-url/{test_user.id}", json={
            "job_url": "https://linkedin.com/jobs/view/456",
        })
    assert response.status_code == 200
    assert response.json() == preview


def test_import_job_by_url_duplicate_rejected(client, test_user, db_session):
    from app.models.job_model import Job

    existing = Job(
        user_id=test_user.id, title="Java Developer", company="Acme", location="Remote",
        work_mode="Remote", job_type="Full-time", description="desc", skills_required="",
        source="LinkedIn Imported", job_url="https://linkedin.com/jobs/view/456",
    )
    db_session.add(existing)
    db_session.commit()

    preview = {"normalized_job_url": "https://linkedin.com/jobs/view/456"}
    with patch("app.routes.job_routes.build_job_preview", return_value=preview):
        response = client.post(f"/api/jobs/import-url/{test_user.id}", json={
            "job_url": "https://linkedin.com/jobs/view/456",
        })
    assert response.status_code == 409


def test_import_job_by_url_invalid_url_returns_400(client, test_user):
    with patch("app.routes.job_routes.build_job_preview", side_effect=JobUrlImportError("Invalid URL scheme")):
        response = client.post(f"/api/jobs/import-url/{test_user.id}", json={"job_url": "ftp://example.com"})
    assert response.status_code == 400


def test_get_selected_jobs_returns_user_selected_only(client, test_user, java_job):
    response = client.get(f"/api/jobs/selected/{test_user.id}")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == java_job.id


def test_get_selected_jobs_missing_user_returns_404(client):
    response = client.get("/api/jobs/selected/999999")
    assert response.status_code == 404


def test_get_selected_jobs_excludes_generic_search_jobs(client, test_user, generic_search_job):
    response = client.get(f"/api/jobs/selected/{test_user.id}")
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# match_status — every Selected Job card must be in one explicit state
# (COMPLETE/CALCULATING/FAILED/UNAVAILABLE), never an unexplained blank.
# ---------------------------------------------------------------------------

def test_selected_job_match_status_complete_from_match_score_row(client, test_user, test_resume, java_job, db_session):
    from app.models.match_model import MatchScore

    db_session.add(MatchScore(user_id=test_user.id, resume_id=test_resume.id, job_id=java_job.id, match_percentage=78, resume_version="original"))
    db_session.commit()

    body = client.get(f"/api/jobs/selected/{test_user.id}").json()
    assert body[0]["match_status"] == "COMPLETE"
    assert body[0]["original_match_percentage"] == 78


def test_selected_job_match_status_complete_falls_back_to_workflow_score_when_match_score_row_missing(client, test_user, test_resume, java_job, db_session):
    """The exact bug fix: a real score already computed on the workflow
    (e.g. an INELIGIBLE_LOW_SCORE or FAILED-after-scoring run, from before
    execute_workflow was fixed to persist immediately) must still surface
    as a real, honest score — never blank, and never recomputed here."""
    from app.models.ats_optimization_workflow_model import AtsOptimizationWorkflow

    db_session.add(AtsOptimizationWorkflow(
        user_id=test_user.id, resume_id=test_resume.id, job_id=java_job.id,
        workflow_status="INELIGIBLE_LOW_SCORE", original_match_score=35, optimization_allowed=False,
    ))
    db_session.commit()

    body = client.get(f"/api/jobs/selected/{test_user.id}").json()
    assert body[0]["match_status"] == "COMPLETE"
    assert body[0]["original_match_percentage"] == 35


def test_selected_job_match_status_calculating_while_workflow_running(client, test_user, test_resume, java_job, db_session):
    from app.models.ats_optimization_workflow_model import AtsOptimizationWorkflow

    db_session.add(AtsOptimizationWorkflow(
        user_id=test_user.id, resume_id=test_resume.id, job_id=java_job.id, workflow_status="GENERATING_RESUME",
    ))
    db_session.commit()

    body = client.get(f"/api/jobs/selected/{test_user.id}").json()
    assert body[0]["match_status"] == "CALCULATING"
    assert body[0]["original_match_percentage"] is None


def test_selected_job_match_status_failed_when_workflow_failed_with_no_score(client, test_user, test_resume, java_job, db_session):
    from app.models.ats_optimization_workflow_model import AtsOptimizationWorkflow

    db_session.add(AtsOptimizationWorkflow(
        user_id=test_user.id, resume_id=test_resume.id, job_id=java_job.id,
        workflow_status="FAILED", original_match_score=None, error_message="Job or resume missing",
    ))
    db_session.commit()

    body = client.get(f"/api/jobs/selected/{test_user.id}").json()
    assert body[0]["match_status"] == "FAILED"
    assert body[0]["original_match_percentage"] is None
    # never a raw backend error surfaced through this field
    assert "error_message" not in body[0]


def test_selected_job_match_status_unavailable_when_no_workflow_ever_ran(client, test_user, java_job):
    body = client.get(f"/api/jobs/selected/{test_user.id}").json()
    assert body[0]["match_status"] == "UNAVAILABLE"
    assert body[0]["original_match_percentage"] is None


def test_selected_job_match_status_never_recomputes_an_existing_valid_score(client, test_user, test_resume, java_job, db_session):
    """Performance guard (item 7/23): a job with an existing persisted score
    must be served from that row — no scoring call happens on a plain GET."""
    from app.models.match_model import MatchScore

    db_session.add(MatchScore(user_id=test_user.id, resume_id=test_resume.id, job_id=java_job.id, match_percentage=61, resume_version="original"))
    db_session.commit()

    with patch("app.services.ats_scoring.calculate_weighted_ats_score") as mocked_score:
        body = client.get(f"/api/jobs/selected/{test_user.id}").json()
    mocked_score.assert_not_called()
    assert body[0]["original_match_percentage"] == 61


def test_delete_selected_job(client, test_user, java_job):
    response = client.delete(f"/api/jobs/selected/{test_user.id}/{java_job.id}")
    assert response.status_code == 200
    assert response.json()["job_id"] == java_job.id

    follow_up = client.get(f"/api/jobs/selected/{test_user.id}")
    assert follow_up.json() == []


def test_delete_selected_job_cross_user_denied(client, test_user, second_user, java_job):
    response = client.delete(f"/api/jobs/selected/{second_user.id}/{java_job.id}")
    assert response.status_code == 404

    still_there = client.get(f"/api/jobs/selected/{test_user.id}")
    assert len(still_there.json()) == 1


def test_delete_selected_job_not_found(client, test_user):
    response = client.delete(f"/api/jobs/selected/{test_user.id}/999999")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# CURRENT VERIFIED BUG fix: deleting a Selected Job that has related records
# in tables added by later features (ATS requirement analysis, skill
# confirmation evidence, Email Summary's application tracking / Gmail
# events) used to raise an uncaught sqlalchemy.exc.IntegrityError
# (ForeignKeyViolation) because _delete_jobs_and_related only cleared
# ats_optimization_workflows/match_scores/optimized_resumes. That uncaught
# 500 also came back without CORS headers (Starlette's ServerErrorMiddleware
# sits outside CORSMiddleware), which is what a browser reports to
# JavaScript as "Failed to fetch" rather than a normal HTTP error.
# ---------------------------------------------------------------------------

def test_delete_selected_job_with_requirement_analysis_succeeds(client, test_user, java_job, db_session):
    from app.models.skill_confirmation_model import JobRequirementAnalysis

    analysis = JobRequirementAnalysis(user_id=test_user.id, job_id=java_job.id, analysis_json="{}")
    db_session.add(analysis)
    db_session.commit()

    response = client.delete(f"/api/jobs/selected/{test_user.id}/{java_job.id}")
    assert response.status_code == 200
    assert db_session.query(JobRequirementAnalysis).filter_by(job_id=java_job.id).count() == 0


def test_delete_selected_job_with_confirmed_skill_evidence_succeeds(client, test_user, java_job, db_session):
    from app.models.skill_confirmation_model import UserConfirmedSkill

    skill = UserConfirmedSkill(
        user_id=test_user.id, job_id=java_job.id, skill_name="Java", normalized_skill_name="java",
        confirmed=True, evidence_status="COMPLETE", evidence_source="user_selection",
    )
    db_session.add(skill)
    db_session.commit()

    response = client.delete(f"/api/jobs/selected/{test_user.id}/{java_job.id}")
    assert response.status_code == 200
    assert db_session.query(UserConfirmedSkill).filter_by(job_id=java_job.id).count() == 0


def test_delete_selected_job_preserves_application_tracking_history(client, test_user, java_job, db_session):
    """Application tracking is real user history (status timeline), never
    destroyed just because the convenience "Selected Jobs" entry is
    removed — the row survives with job_id detached (set NULL), matching
    the model's own documented "tracked purely from email evidence" state."""
    from app.models.application_tracking_model import ApplicationTracking

    tracking = ApplicationTracking(
        user_id=test_user.id, job_id=java_job.id, company=java_job.company, title=java_job.title,
        status="OFFER", status_source="manual",
    )
    db_session.add(tracking)
    db_session.commit()
    tracking_id = tracking.id

    response = client.delete(f"/api/jobs/selected/{test_user.id}/{java_job.id}")
    assert response.status_code == 200

    db_session.expire_all()
    surviving = db_session.query(ApplicationTracking).filter_by(id=tracking_id).first()
    assert surviving is not None
    assert surviving.status == "OFFER"  # untouched
    assert surviving.job_id is None  # detached, not deleted


def test_delete_selected_job_preserves_email_event_history(client, test_user, java_job, db_session):
    """Email Summary's Gmail-derived event history must never be destroyed
    by removing a Selected Job — the event survives with matched_job_id
    detached (set NULL)."""
    from app.models.email_connection_model import EmailConnection
    from app.models.job_email_event_model import JobEmailEvent

    connection = EmailConnection(user_id=test_user.id, provider="gmail", status="CONNECTED", email_address="candidate@gmail.com")
    db_session.add(connection)
    db_session.commit()
    db_session.refresh(connection)

    event = JobEmailEvent(
        user_id=test_user.id, connection_id=connection.id, message_id="m1", sender="hr@example.com",
        subject="Re: Java Full Stack Developer", event_type="APPLICATION_CONFIRMATION", confidence=0.9,
        resolution="CONFIRMED", matched_job_id=java_job.id,
    )
    db_session.add(event)
    db_session.commit()
    event_id = event.id

    response = client.delete(f"/api/jobs/selected/{test_user.id}/{java_job.id}")
    assert response.status_code == 200

    db_session.expire_all()
    surviving = db_session.query(JobEmailEvent).filter_by(id=event_id).first()
    assert surviving is not None
    assert surviving.resolution == "CONFIRMED"  # untouched
    assert surviving.matched_job_id is None  # detached, not deleted


def test_delete_selected_job_with_every_related_record_type_at_once(client, test_user, java_job, test_resume, db_session):
    """The exact real-world scenario: a job that has been through ATS
    analysis, skill confirmation, and is also tracked/matched via Email
    Summary — every related row type at once, all in a single request."""
    from app.models.skill_confirmation_model import JobRequirementAnalysis, UserConfirmedSkill
    from app.models.application_tracking_model import ApplicationTracking
    from app.models.email_connection_model import EmailConnection
    from app.models.job_email_event_model import JobEmailEvent
    from app.models.match_model import MatchScore
    from app.models.optimized_resume_model import OptimizedResume

    connection = EmailConnection(user_id=test_user.id, provider="gmail", status="CONNECTED", email_address="candidate@gmail.com")
    db_session.add(connection)
    db_session.add(JobRequirementAnalysis(user_id=test_user.id, job_id=java_job.id, analysis_json="{}"))
    db_session.add(UserConfirmedSkill(
        user_id=test_user.id, job_id=java_job.id, skill_name="Java", normalized_skill_name="java",
        confirmed=True, evidence_status="COMPLETE", evidence_source="user_selection",
    ))
    db_session.add(ApplicationTracking(
        user_id=test_user.id, job_id=java_job.id, company=java_job.company, title=java_job.title,
        status="APPLIED", status_source="manual",
    ))
    db_session.add(MatchScore(user_id=test_user.id, job_id=java_job.id, resume_id=test_resume.id, match_percentage=80, resume_version="original"))
    db_session.commit()
    db_session.refresh(connection)
    db_session.add(JobEmailEvent(
        user_id=test_user.id, connection_id=connection.id, message_id="m1", sender="hr@example.com",
        subject="Re: Java Full Stack Developer", event_type="APPLICATION_CONFIRMATION", confidence=0.9,
        resolution="NEEDS_CONFIRMATION", matched_job_id=java_job.id,
    ))
    db_session.commit()

    job_id, company, title = java_job.id, java_job.company, java_job.title

    response = client.delete(f"/api/jobs/selected/{test_user.id}/{job_id}")
    assert response.status_code == 200

    follow_up = client.get(f"/api/jobs/selected/{test_user.id}")
    assert follow_up.json() == []

    db_session.expire_all()
    assert db_session.query(JobRequirementAnalysis).filter_by(job_id=job_id).count() == 0
    assert db_session.query(UserConfirmedSkill).filter_by(job_id=job_id).count() == 0
    assert db_session.query(MatchScore).filter_by(job_id=job_id).count() == 0
    assert db_session.query(ApplicationTracking).filter_by(company=company, title=title).first().job_id is None
    assert db_session.query(JobEmailEvent).filter_by(message_id="m1").first().matched_job_id is None


def test_delete_selected_job_does_not_touch_another_users_related_records(client, test_user, second_user, java_job, db_session):
    """No unrelated records deleted — a second user's data referencing an
    unrelated job of their own must never be touched by this request."""
    from app.models.job_model import Job
    from app.models.application_tracking_model import ApplicationTracking

    other_job = Job(
        user_id=second_user.id, title="Other Job", company="Other Co", location="Remote",
        work_mode="Remote", job_type="Full-time", description="desc", skills_required="",
        source="User Selected Job", job_url="https://example.com/jobs/other",
    )
    db_session.add(other_job)
    db_session.commit()
    db_session.refresh(other_job)
    other_tracking = ApplicationTracking(
        user_id=second_user.id, job_id=other_job.id, company="Other Co", title="Other Job",
        status="APPLIED", status_source="manual",
    )
    db_session.add(other_tracking)
    db_session.commit()
    other_tracking_id = other_tracking.id

    response = client.delete(f"/api/jobs/selected/{test_user.id}/{java_job.id}")
    assert response.status_code == 200

    db_session.expire_all()
    untouched = db_session.query(ApplicationTracking).filter_by(id=other_tracking_id).first()
    assert untouched is not None
    assert untouched.job_id == other_job.id  # still attached — never detached by another user's delete


def test_get_job_detail(client, test_user, java_job):
    response = client.get(f"/api/jobs/detail/{test_user.id}/{java_job.id}")
    assert response.status_code == 200
    assert response.json()["id"] == java_job.id


def test_get_job_detail_cross_user_denied(client, test_user, second_user, java_job):
    response = client.get(f"/api/jobs/detail/{second_user.id}/{java_job.id}")
    assert response.status_code == 404


def test_get_jobs_for_user(client, test_user, java_job, sdet_job):
    response = client.get(f"/api/jobs/{test_user.id}")
    assert response.status_code == 200
    ids = {job["id"] for job in response.json()}
    assert ids == {java_job.id, sdet_job.id}


def test_get_jobs_for_user_isolated_from_other_users(client, test_user, second_user, java_job):
    response = client.get(f"/api/jobs/{second_user.id}")
    assert response.status_code == 200
    assert response.json() == []
