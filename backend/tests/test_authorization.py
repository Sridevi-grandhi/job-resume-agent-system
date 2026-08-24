"""Cross-cutting user-isolation and input-safety tests.

This project has no token/session auth — every route is scoped by a
user_id path parameter, so "authorization" here means every route must
filter by the *authenticated* user_id and never leak or mutate another
user's rows even when a caller supplies someone else's ids.
"""

from __future__ import annotations

import io


def test_user_a_cannot_read_user_b_preferences(client, test_user, second_user, test_preferences):
    response = client.get(f"/api/preferences/{second_user.id}")
    assert response.status_code == 404


def test_user_a_cannot_read_user_b_resume(client, test_user, second_user, test_resume):
    response = client.get(f"/api/resume/latest/{second_user.id}")
    assert response.status_code == 404


def test_user_a_cannot_read_user_b_selected_jobs(client, test_user, second_user, java_job):
    response = client.get(f"/api/jobs/selected/{second_user.id}")
    assert response.status_code == 200
    assert response.json() == []  # second_user's job list, correctly empty — not user_a's job


def test_user_a_cannot_delete_user_b_job(client, test_user, second_user, java_job):
    response = client.delete(f"/api/jobs/selected/{second_user.id}/{java_job.id}")
    assert response.status_code == 404

    still_owned_by_a = client.get(f"/api/jobs/detail/{test_user.id}/{java_job.id}")
    assert still_owned_by_a.status_code == 200


def test_user_a_cannot_optimize_user_b_job(client, test_user, second_user, test_resume, java_job):
    response = client.post(f"/api/resume/optimize/{second_user.id}/{java_job.id}")
    assert response.status_code == 404


def test_user_a_cannot_read_user_b_optimized_resume(client, test_user, second_user, test_resume, java_job):
    client.post(f"/api/resume/optimize/{test_user.id}/{java_job.id}")
    response = client.get(f"/api/resume/optimized/{second_user.id}")
    assert response.status_code == 404


def test_user_a_cannot_read_user_b_match_score(client, test_user, second_user, test_resume, java_job):
    client.post(f"/api/match/calculate/{test_user.id}")
    response = client.get(f"/api/match/{second_user.id}")
    assert response.status_code == 200
    assert response.json() == []


def test_user_a_cannot_edit_user_b_evidence(client, test_user, second_user, test_resume, java_job):
    from app.models.job_model import Job

    # second_user needs their own copy of the job row to own evidence against.
    saved = client.post(f"/api/ats-optimizer/evidence/{test_user.id}/{java_job.id}", json={
        "items": [{
            "skill": "Kubernetes", "answer": "has_it", "evidence_type": "Current employment",
            "experience_level": "Advanced", "experience_range": "2-4 years",
            "usage_description": "Ran clusters.", "confirmed_accurate": True,
        }],
    }).json()
    evidence_id = saved["evidence"][0]["id"]

    response = client.delete(f"/api/ats-optimizer/evidence/{second_user.id}/{java_job.id}/{evidence_id}")
    assert response.status_code == 404

    still_there = client.get(f"/api/ats-optimizer/evidence/{test_user.id}/{java_job.id}")
    assert len(still_there.json()["evidence"]) == 1


def test_user_a_cannot_read_user_b_email_logs(client, test_user, second_user, test_resume, java_job):
    client.post(f"/api/match/calculate/{test_user.id}")
    client.post(f"/api/email/generate/{test_user.id}")

    response = client.get(f"/api/email/logs/{second_user.id}")
    assert response.status_code == 404


def test_user_a_cannot_view_user_b_ats_workflow_by_swapping_user_id(
    client, test_user, second_user, test_resume, java_job
):
    generated = client.post(f"/api/ats-optimizer/generate/{test_user.id}/{java_job.id}").json()
    response = client.get(f"/api/ats-optimizer/result/{second_user.id}/{java_job.id}")
    assert response.status_code == 404
    # The job itself belongs to user_a only, so user_b querying by job_id never matches.
    assert generated["workflow_id"] is not None


# ---------------------------------------------------------------------------
# Input safety
# ---------------------------------------------------------------------------

def test_script_tag_in_job_title_is_stored_verbatim_not_executed(client, test_user):
    payload = {
        "title": "<script>alert(1)</script>", "company": "Acme", "location": "Remote",
        "description": "Java Spring Boot role with enough length to pass validation checks.",
    }
    response = client.post(f"/api/jobs/selected/{test_user.id}", json=payload)
    assert response.status_code == 200
    # Stored as inert text — FastAPI/JSON responses are not HTML-rendered, so no
    # execution risk; this just confirms the value round-trips unmodified.
    assert response.json()["job"]["title"] == payload["title"]


def test_sql_like_string_in_job_title_is_treated_as_plain_text(client, test_user):
    payload = {
        "title": "Robert'); DROP TABLE jobs;--", "company": "Acme", "location": "Remote",
        "description": "Java Spring Boot role with enough length to pass validation checks.",
    }
    response = client.post(f"/api/jobs/selected/{test_user.id}", json=payload)
    assert response.status_code == 200

    # Table must still exist and be queryable — ORM parameter binding, not string concat.
    follow_up = client.get(f"/api/jobs/{test_user.id}")
    assert follow_up.status_code == 200
    assert len(follow_up.json()) == 1


def test_path_traversal_filename_does_not_escape_upload_dir(client, test_user, tmp_path, monkeypatch):
    import app.routes.resume_routes as resume_routes

    upload_dir = tmp_path / "resumes"
    upload_dir.mkdir()
    monkeypatch.setattr(resume_routes, "UPLOAD_DIR", upload_dir)

    response = client.post(
        "/api/resume/upload",
        data={"user_id": str(test_user.id)},
        files={"file": ("../../../etc/passwd.txt", io.BytesIO(b"Java developer"), "text/plain")},
    )
    assert response.status_code == 200
    stored_files = list(upload_dir.iterdir())
    assert len(stored_files) == 1
    # The stored filename is a fresh uuid, never the raw (attacker-controlled) name.
    assert stored_files[0].name != "../../../etc/passwd.txt"
    assert upload_dir in stored_files[0].parents


def test_invalid_url_scheme_for_job_import_is_rejected(client, test_user):
    response = client.post(f"/api/jobs/import-url/{test_user.id}", json={
        "job_url": "javascript:alert(1)",
    })
    assert response.status_code == 400


def test_extremely_long_input_does_not_crash_the_server(client, test_user):
    payload = {
        "title": "Java Developer", "company": "Acme", "location": "Remote",
        "description": "Java Spring Boot role. " + ("x" * 50000),
    }
    response = client.post(f"/api/jobs/selected/{test_user.id}", json=payload)
    assert response.status_code == 200


def test_404_error_never_exposes_internal_stack_trace(client):
    response = client.get("/api/preferences/999999")
    assert response.status_code == 404
    body = response.json()
    assert set(body.keys()) == {"detail"}
    assert "Traceback" not in str(body)
    assert ".py" not in str(body)
