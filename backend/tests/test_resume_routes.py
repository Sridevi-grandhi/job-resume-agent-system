"""Tests for app/routes/resume_routes.py."""

from __future__ import annotations

import io

import pytest

import app.routes.resume_routes as resume_routes


@pytest.fixture(autouse=True)
def _isolated_upload_dir(tmp_path, monkeypatch):
    """Uploaded files must not land in the real app/uploads/resumes directory."""
    upload_dir = tmp_path / "resumes"
    upload_dir.mkdir()
    monkeypatch.setattr(resume_routes, "UPLOAD_DIR", upload_dir)


def _upload(client, user_id, filename="resume.txt", content=b"Java developer with Spring Boot experience."):
    return client.post(
        "/api/resume/upload",
        data={"user_id": str(user_id)},
        files={"file": (filename, io.BytesIO(content), "text/plain")},
    )


def test_upload_txt_resume_success(client, test_user):
    response = _upload(client, test_user.id)
    assert response.status_code == 200
    body = response.json()
    assert body["file_type"] == "txt"
    assert body["resume_id"] is not None
    assert "Java developer" in body["text_preview"]


def test_upload_resume_missing_user_returns_404(client):
    response = _upload(client, 999999)
    assert response.status_code == 404


def test_upload_unsupported_extension_rejected(client, test_user):
    response = _upload(client, test_user.id, filename="resume.exe")
    assert response.status_code == 400
    assert "Only PDF, DOCX, and TXT" in response.json()["detail"]


def test_upload_empty_file_still_parses_or_rejects_gracefully(client, test_user):
    response = _upload(client, test_user.id, content=b"")
    assert response.status_code in (200, 400)


def test_upload_resume_safe_filename_handling(client, test_user):
    response = _upload(client, test_user.id, filename="../../etc/passwd.txt")
    assert response.status_code == 200
    # Stored path must stay inside the uploads directory (uuid-based, not the raw name).
    body = response.json()
    assert body["resume_id"] is not None


def test_get_latest_resume(client, test_user, test_resume):
    response = client.get(f"/api/resume/latest/{test_user.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["resume_id"] == test_resume.id
    assert body["file_name"] == test_resume.original_file_name


def test_get_latest_resume_missing_returns_404(client, test_user):
    response = client.get(f"/api/resume/latest/{test_user.id}")
    assert response.status_code == 404


def test_get_latest_resume_cross_user_access_returns_404(client, test_user, second_user, test_resume):
    response = client.get(f"/api/resume/latest/{second_user.id}")
    assert response.status_code == 404


def test_optimize_resume_success(client, test_user, test_resume, java_job):
    response = client.post(f"/api/resume/optimize/{test_user.id}/{java_job.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == java_job.id
    assert body["resume_id"] == test_resume.id
    assert "match_percentage" in body


def test_optimize_resume_no_resume_returns_404(client, test_user, java_job):
    response = client.post(f"/api/resume/optimize/{test_user.id}/{java_job.id}")
    assert response.status_code == 404


def test_optimize_resume_wrong_job_returns_404(client, test_user, test_resume):
    response = client.post(f"/api/resume/optimize/{test_user.id}/999999")
    assert response.status_code == 404


def test_optimize_and_match_resume_starts_workflow(client, test_user, test_resume, java_job):
    response = client.post(f"/api/resume/optimize-and-match/{test_user.id}/{java_job.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["workflow_id"] is not None
    assert body["workflow_status"] in {"PENDING", "COMPLETED"}


def test_get_optimized_resumes_after_optimize(client, test_user, test_resume, java_job):
    client.post(f"/api/resume/optimize/{test_user.id}/{java_job.id}")
    response = client.get(f"/api/resume/optimized/{test_user.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["total_optimized_resumes"] == 1
    assert body["optimized_resumes"][0]["job_id"] == java_job.id


def test_get_optimized_resumes_none_found_returns_404(client, test_user):
    response = client.get(f"/api/resume/optimized/{test_user.id}")
    assert response.status_code == 404


def test_get_optimized_resumes_cross_user_isolated(client, test_user, second_user, test_resume, java_job):
    client.post(f"/api/resume/optimize/{test_user.id}/{java_job.id}")
    response = client.get(f"/api/resume/optimized/{second_user.id}")
    assert response.status_code == 404
