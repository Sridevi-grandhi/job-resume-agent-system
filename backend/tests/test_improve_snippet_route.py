"""Tests for POST /api/optimizer/resume/{user_id}/{job_id}/improve-snippet
("Try Again" / AI Improve). Covers: Ollama success, safe fallback to the
deterministic rewrite on any LLM failure, the empty-result protection
(never a blank suggestion), and that only the requested bullet is affected
(scoped to the endpoint's own request/response, never the whole resume).
"""

from __future__ import annotations

from unittest.mock import patch

from app.models.optimized_resume_model import OptimizedResume


def _seed_optimized(db_session, test_user, test_resume, java_job):
    optimized = OptimizedResume(
        user_id=test_user.id,
        resume_id=test_resume.id,
        job_id=java_job.id,
        optimized_text=test_resume.parsed_text,
        generated_resume_text=test_resume.parsed_text,
        final_resume_text=test_resume.parsed_text,
        original_score=70,
        generated_score=80,
        edited_score=80,
        template_preserved=True,
        source_file_type="docx",
        validation_status="passed",
    )
    db_session.add(optimized)
    db_session.commit()
    db_session.refresh(optimized)
    return optimized


def _url(test_user, java_job):
    return f"/api/optimizer/resume/{test_user.id}/{java_job.id}/improve-snippet"


def test_improve_snippet_uses_ollama_when_available(client, db_session, test_user, test_resume, java_job):
    optimized = _seed_optimized(db_session, test_user, test_resume, java_job)
    with patch(
        "app.routes.optimizer_edit_routes.rewrite_bullet_with_ollama",
        return_value=("Delivered production-grade REST APIs using Java and Spring Boot.", "ollama", False),
    ):
        response = client.post(_url(test_user, java_job), json={
            "optimized_resume_id": optimized.id,
            "text": "Built REST APIs using Java.",
            "context": "bullet",
        })
    assert response.status_code == 200
    body = response.json()
    assert body["used_llm"] is True
    assert body["llm_provider"] == "ollama"
    assert body["suggested"]
    assert body["retry_failed"] is False
    assert body["no_change_recommended"] is False


def test_improve_snippet_falls_back_to_deterministic_when_ollama_unavailable(client, db_session, test_user, test_resume, java_job):
    optimized = _seed_optimized(db_session, test_user, test_resume, java_job)
    with patch("app.routes.optimizer_edit_routes.rewrite_bullet_with_ollama", return_value=(None, None, False)):
        response = client.post(_url(test_user, java_job), json={
            "optimized_resume_id": optimized.id,
            "text": "Responsible for deployment pipelines.",
            "context": "bullet",
        })
    assert response.status_code == 200
    body = response.json()
    assert body["used_llm"] is False
    assert body["llm_provider"] is None
    assert body["suggested"]  # never blank


def test_improve_snippet_never_returns_blank_suggestion_on_repeated_failure(client, db_session, test_user, test_resume, java_job):
    optimized = _seed_optimized(db_session, test_user, test_resume, java_job)
    with patch("app.routes.optimizer_edit_routes.rewrite_bullet_with_ollama", return_value=(None, None, False)):
        response = client.post(_url(test_user, java_job), json={
            "optimized_resume_id": optimized.id,
            "text": "Worked on internal tools.",
            "context": "bullet",
            "previous_suggestion": "Worked on internal tools.",
        })
    assert response.status_code == 200
    body = response.json()
    assert body["suggested"].strip() != ""


def test_improve_snippet_deterministic_fallback_identical_to_original_reports_no_change_recommended(client, db_session, test_user, test_resume, java_job):
    """Regression: when Ollama is unreachable and the deterministic fallback
    also lands back on the original wording (common for an already-clean
    bullet), the response must say no_change_recommended=True — not silently
    return an "AI-improved" suggestion identical to the original, which the
    frontend would otherwise present as a meaningful change to Accept/Reject."""
    optimized = _seed_optimized(db_session, test_user, test_resume, java_job)
    already_clean = "Developed REST APIs using Java and Spring Boot for account workflows."
    with patch("app.routes.optimizer_edit_routes.rewrite_bullet_with_ollama", return_value=(None, None, False)):
        response = client.post(_url(test_user, java_job), json={
            "optimized_resume_id": optimized.id,
            "text": already_clean,
            "context": "bullet",
        })
    assert response.status_code == 200
    body = response.json()
    assert body["no_change_recommended"] is True
    assert body["used_llm"] is False
    assert body["suggested"] == already_clean


def test_improve_snippet_reports_no_change_recommended_instead_of_fake_improvement(client, db_session, test_user, test_resume, java_job):
    optimized = _seed_optimized(db_session, test_user, test_resume, java_job)
    with patch("app.routes.optimizer_edit_routes.rewrite_bullet_with_ollama", return_value=(None, None, True)):
        response = client.post(_url(test_user, java_job), json={
            "optimized_resume_id": optimized.id,
            "text": "Built REST APIs using Java.",
            "context": "bullet",
        })
    assert response.status_code == 200
    body = response.json()
    assert body["no_change_recommended"] is True
    assert body["used_llm"] is False
    assert body["suggested"] == "Built REST APIs using Java."


def test_improve_snippet_summary_context_uses_openai_summary_rewriter(client, db_session, test_user, test_resume, java_job):
    optimized = _seed_optimized(db_session, test_user, test_resume, java_job)
    with patch(
        "app.routes.optimizer_edit_routes.rewrite_summary_with_llm",
        return_value=("Senior Java Full Stack Developer with 8 years building enterprise systems in Java and Spring Boot.", "openai", False),
    ) as mock_rewrite:
        response = client.post(_url(test_user, java_job), json={
            "optimized_resume_id": optimized.id,
            "text": "Java Full Stack Developer with experience building enterprise applications.",
            "context": "summary",
        })
    assert response.status_code == 200
    body = response.json()
    assert body["used_llm"] is True
    assert body["llm_provider"] == "openai"
    mock_rewrite.assert_called_once()
    assert "current_summary" in mock_rewrite.call_args.kwargs


def test_improve_snippet_summary_no_change_recommended_not_presented_as_improvement(client, db_session, test_user, test_resume, java_job):
    optimized = _seed_optimized(db_session, test_user, test_resume, java_job)
    with patch("app.routes.optimizer_edit_routes.rewrite_summary_with_llm", return_value=(None, None, True)):
        response = client.post(_url(test_user, java_job), json={
            "optimized_resume_id": optimized.id,
            "text": "Senior Java developer with 8 years of experience.",
            "context": "summary",
        })
    assert response.status_code == 200
    body = response.json()
    assert body["no_change_recommended"] is True
    assert body["used_llm"] is False
    assert body["suggested"] == "Senior Java developer with 8 years of experience."


def test_improve_snippet_bullet_context_never_calls_summary_rewriter(client, db_session, test_user, test_resume, java_job):
    optimized = _seed_optimized(db_session, test_user, test_resume, java_job)
    with patch("app.routes.optimizer_edit_routes.rewrite_bullet_with_ollama", return_value=(None, None, False)):
        with patch("app.routes.optimizer_edit_routes.rewrite_summary_with_llm") as mock_summary:
            response = client.post(_url(test_user, java_job), json={
                "optimized_resume_id": optimized.id,
                "text": "Built REST APIs using Java.",
                "context": "bullet",
            })
    assert response.status_code == 200
    mock_summary.assert_not_called()


def test_improve_snippet_cross_user_access_denied(client, db_session, test_user, second_user, test_resume, java_job):
    """User B must never be able to improve a snippet against User A's
    optimized resume — verifies user isolation on the new endpoint."""
    optimized = _seed_optimized(db_session, test_user, test_resume, java_job)
    response = client.post(
        f"/api/optimizer/resume/{second_user.id}/{java_job.id}/improve-snippet",
        json={"optimized_resume_id": optimized.id, "text": "Built REST APIs using Java.", "context": "bullet"},
    )
    assert response.status_code == 404


def test_improve_snippet_requires_text(client, db_session, test_user, test_resume, java_job):
    optimized = _seed_optimized(db_session, test_user, test_resume, java_job)
    response = client.post(_url(test_user, java_job), json={
        "optimized_resume_id": optimized.id,
        "text": "",
    })
    assert response.status_code == 400


def test_improve_snippet_guards_against_invented_technology(client, db_session, test_user, test_resume, java_job):
    optimized = _seed_optimized(db_session, test_user, test_resume, java_job)
    with patch(
        "app.routes.optimizer_edit_routes.rewrite_bullet_with_ollama",
        return_value=("Built REST APIs using Java and Kubernetes with 99.9% uptime.", "ollama", False),
    ):
        response = client.post(_url(test_user, java_job), json={
            "optimized_resume_id": optimized.id,
            "text": "Built REST APIs using Java.",
            "context": "bullet",
        })
    body = response.json()
    # "Kubernetes" and the invented uptime metric are not in the original bullet,
    # resume, or JD — the guard must reject the whole suggestion.
    assert "Kubernetes" not in body["suggested"]


def test_improve_snippet_retry_with_previous_suggestion_produces_different_text(client, db_session, test_user, test_resume, java_job):
    optimized = _seed_optimized(db_session, test_user, test_resume, java_job)
    with patch(
        "app.routes.optimizer_edit_routes.rewrite_bullet_with_ollama",
        return_value=("Delivered scalable Java-based REST services.", "ollama", False),
    ) as mock_rewrite:
        response = client.post(_url(test_user, java_job), json={
            "optimized_resume_id": optimized.id,
            "text": "Built REST APIs using Java.",
            "context": "bullet",
            "previous_suggestion": "Engineered production REST APIs in Java.",
        })
    assert response.status_code == 200
    assert mock_rewrite.call_args.kwargs["previous_suggestion"] == "Engineered production REST APIs in Java."
    assert response.json()["suggested"] == "Delivered scalable Java-based REST services."
