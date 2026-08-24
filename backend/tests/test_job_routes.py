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
