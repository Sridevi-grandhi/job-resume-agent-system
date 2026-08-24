"""Tests for app/routes/ats_optimizer_routes.py."""

from __future__ import annotations


def test_get_job_requirements(client, test_user, test_resume, java_job):
    response = client.get(f"/api/ats-optimizer/requirements/{test_user.id}/{java_job.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == java_job.id
    assert "mandatory_skills" in body


def test_get_job_requirements_missing_job_returns_404(client, test_user, test_resume):
    response = client.get(f"/api/ats-optimizer/requirements/{test_user.id}/999999")
    assert response.status_code == 404


def test_get_job_requirements_generic_search_job_rejected(client, test_user, generic_search_job):
    response = client.get(f"/api/ats-optimizer/requirements/{test_user.id}/{generic_search_job.id}")
    assert response.status_code == 400


def test_get_missing_skills(client, test_user, test_resume, java_job):
    response = client.get(f"/api/ats-optimizer/missing-skills/{test_user.id}/{java_job.id}")
    assert response.status_code == 200
    body = response.json()
    assert "skills" in body
    assert body["resume_employments"]


def test_analyze_requirements_force_refreshes(client, test_user, test_resume, java_job):
    response = client.post(f"/api/ats-optimizer/analyze/{test_user.id}/{java_job.id}")
    assert response.status_code == 200
    assert response.json()["force_refresh"] is True


def test_get_evidence_empty_initially(client, test_user, test_resume, java_job):
    response = client.get(f"/api/ats-optimizer/evidence/{test_user.id}/{java_job.id}")
    assert response.status_code == 200
    assert response.json()["evidence"] == []


def test_post_evidence_saves_items(client, test_user, test_resume, java_job):
    response = client.post(f"/api/ats-optimizer/evidence/{test_user.id}/{java_job.id}", json={
        "items": [{
            "skill": "Kubernetes", "answer": "has_it", "evidence_type": "Current employment",
            "experience_level": "Advanced", "experience_range": "2-4 years",
            "usage_description": "Ran Kubernetes clusters.", "confirmed_accurate": True,
        }],
    })
    assert response.status_code == 200
    assert len(response.json()["evidence"]) == 1

    loaded = client.get(f"/api/ats-optimizer/evidence/{test_user.id}/{java_job.id}")
    assert len(loaded.json()["evidence"]) == 1


def test_post_evidence_no_items_rejected(client, test_user, test_resume, java_job):
    response = client.post(f"/api/ats-optimizer/evidence/{test_user.id}/{java_job.id}", json={"items": []})
    assert response.status_code == 400


def test_post_evidence_invalid_item_returns_400(client, test_user, test_resume, java_job):
    response = client.post(f"/api/ats-optimizer/evidence/{test_user.id}/{java_job.id}", json={
        "items": [{"skill": "Kubernetes", "answer": "has_it", "evidence_type": "Current employment"}],
    })
    assert response.status_code == 400


def test_delete_evidence(client, test_user, test_resume, java_job):
    saved = client.post(f"/api/ats-optimizer/evidence/{test_user.id}/{java_job.id}", json={
        "items": [{
            "skill": "Kubernetes", "answer": "has_it", "evidence_type": "Current employment",
            "experience_level": "Advanced", "experience_range": "2-4 years",
            "usage_description": "Ran Kubernetes clusters.", "confirmed_accurate": True,
        }],
    }).json()
    evidence_id = saved["evidence"][0]["id"]

    response = client.delete(f"/api/ats-optimizer/evidence/{test_user.id}/{java_job.id}/{evidence_id}")
    assert response.status_code == 200

    loaded = client.get(f"/api/ats-optimizer/evidence/{test_user.id}/{java_job.id}")
    assert loaded.json()["evidence"] == []


def test_delete_evidence_not_found_returns_404(client, test_user, test_resume, java_job):
    response = client.delete(f"/api/ats-optimizer/evidence/{test_user.id}/{java_job.id}/999999")
    assert response.status_code == 404


def test_generate_ats_optimization(client, test_user, test_resume, java_job):
    response = client.post(f"/api/ats-optimizer/generate/{test_user.id}/{java_job.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["workflow_id"] is not None
    assert body["result"]["workflow_status"] in {"PENDING", "COMPLETED"}


def test_get_workflow_status(client, test_user, test_resume, java_job):
    generated = client.post(f"/api/ats-optimizer/generate/{test_user.id}/{java_job.id}").json()
    response = client.get(f"/api/ats-optimizer/status/{generated['workflow_id']}")
    assert response.status_code == 200
    assert response.json()["workflow_id"] == generated["workflow_id"]


def test_get_workflow_status_not_found(client):
    response = client.get("/api/ats-optimizer/status/999999")
    assert response.status_code == 404


def test_get_result_by_job(client, test_user, test_resume, java_job):
    client.post(f"/api/ats-optimizer/generate/{test_user.id}/{java_job.id}")
    response = client.get(f"/api/ats-optimizer/result/{test_user.id}/{java_job.id}")
    assert response.status_code == 200
    assert response.json()["job_id"] == java_job.id


def test_get_result_by_job_not_found(client, test_user, test_resume, java_job):
    response = client.get(f"/api/ats-optimizer/result/{test_user.id}/{java_job.id}")
    assert response.status_code == 404


def test_list_results(client, test_user, test_resume, java_job):
    client.post(f"/api/ats-optimizer/generate/{test_user.id}/{java_job.id}")
    response = client.get(f"/api/ats-optimizer/results/{test_user.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1


def test_retry_workflow(client, test_user, test_resume, java_job):
    generated = client.post(f"/api/ats-optimizer/generate/{test_user.id}/{java_job.id}").json()
    response = client.post(f"/api/ats-optimizer/retry/{generated['workflow_id']}")
    assert response.status_code == 200
    assert response.json()["workflow_id"] != generated["workflow_id"]


def test_retry_workflow_not_found(client):
    response = client.post("/api/ats-optimizer/retry/999999")
    assert response.status_code == 404


def test_download_generated_resume(client, test_user, test_resume, java_job):
    generated = client.post(f"/api/ats-optimizer/generate/{test_user.id}/{java_job.id}").json()
    response = client.get(f"/api/ats-optimizer/download/{generated['workflow_id']}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "attachment" in response.headers["content-disposition"]


def test_download_generated_resume_not_ready_returns_400(client, test_user, test_resume, java_job):
    from app.services.ats_optimization_workflow_service import start_or_get_workflow
    import app.database as database_module

    workflow = client.post(f"/api/ats-optimizer/requirements/{test_user.id}/{java_job.id}")  # no-op, ensures user/job exist
    # Create a workflow directly without executing it (still PENDING).
    session = database_module.SessionLocal()
    try:
        wf = start_or_get_workflow(session, user_id=test_user.id, job_id=java_job.id)
        session.commit()
        workflow_id = wf.id
    finally:
        session.close()

    response = client.get(f"/api/ats-optimizer/download/{workflow_id}")
    assert response.status_code == 400


def test_download_generated_resume_not_found_returns_404(client):
    response = client.get("/api/ats-optimizer/download/999999")
    assert response.status_code == 404


def test_analyze_optimize_compat_endpoint(client, test_user, test_resume, java_job):
    response = client.post(f"/api/ats/analyze-optimize/{test_user.id}/{java_job.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["workflow_status"] == "COMPLETED"


def test_debug_skill_evidence(client, test_user, test_resume, java_job):
    response = client.get(f"/api/ats-optimizer/debug/skill-evidence/{test_user.id}/{java_job.id}", params={"skill": "Java"})
    assert response.status_code == 200
    assert response.json()["normalized_skill"] == "java"
