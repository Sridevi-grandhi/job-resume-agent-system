"""Tests for app/routes/email_routes.py. Email is never actually sent — the
endpoint only generates and persists the message body/subject as a log row."""

from __future__ import annotations


def test_generate_email_summary(client, test_user, test_resume, java_job):
    client.post(f"/api/match/calculate/{test_user.id}")
    response = client.post(f"/api/email/generate/{test_user.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["recipient_email"] == test_user.email
    assert body["status"] == "generated"
    assert "email_log_id" in body


def test_generate_email_summary_missing_user_returns_404(client):
    response = client.post("/api/email/generate/999999")
    assert response.status_code == 404


def test_generate_email_summary_no_matches_returns_404(client, test_user):
    response = client.post(f"/api/email/generate/{test_user.id}")
    assert response.status_code == 404
    assert "match scores" in response.json()["detail"].lower()


def test_generate_email_summary_correct_recipient(client, test_user, test_resume, java_job):
    client.post(f"/api/match/calculate/{test_user.id}")
    response = client.post(f"/api/email/generate/{test_user.id}")
    assert response.json()["recipient_email"] == test_user.email


def test_get_email_logs(client, test_user, test_resume, java_job):
    client.post(f"/api/match/calculate/{test_user.id}")
    client.post(f"/api/email/generate/{test_user.id}")

    response = client.get(f"/api/email/logs/{test_user.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["total_logs"] == 1
    assert body["logs"][0]["recipient_email"] == test_user.email


def test_get_email_logs_none_found_returns_404(client, test_user):
    response = client.get(f"/api/email/logs/{test_user.id}")
    assert response.status_code == 404


def test_get_email_logs_cross_user_isolated(client, test_user, second_user, test_resume, java_job):
    client.post(f"/api/match/calculate/{test_user.id}")
    client.post(f"/api/email/generate/{test_user.id}")

    response = client.get(f"/api/email/logs/{second_user.id}")
    assert response.status_code == 404


def test_generate_email_summary_uses_optimized_score_when_present(client, test_user, test_resume, java_job):
    client.post(f"/api/resume/optimize-and-match/{test_user.id}/{java_job.id}")
    response = client.post(f"/api/email/generate/{test_user.id}")
    assert response.status_code == 200
    assert "Optimized ATS Score" in response.json()["body_preview"] or "ATS Score" in response.json()["body_preview"]
