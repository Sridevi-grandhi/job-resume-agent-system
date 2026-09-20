"""Tests for app/routes/email_routes.py. Daily Summary generation is
intentionally disabled (Email Summary redesign): POST /api/email/generate/*
always returns 410 before any of the old generation logic runs — regardless
of user existence, match state, or anything else — and therefore never
creates an email_logs row. These tests verify that disabled behavior stays
disabled; they do not exercise Daily Summary generation itself."""

from __future__ import annotations


def test_generate_email_summary_returns_410_regardless_of_match_state(client, test_user, test_resume, java_job):
    client.post(f"/api/match/calculate/{test_user.id}")
    response = client.post(f"/api/email/generate/{test_user.id}")
    assert response.status_code == 410
    assert "disabled" in response.json()["detail"].lower()


def test_generate_email_summary_missing_user_still_returns_410_not_404(client):
    """The disabled short-circuit runs before the user lookup — a
    nonexistent user id gets the same 410 as any other, never a 404."""
    response = client.post("/api/email/generate/999999")
    assert response.status_code == 410


def test_generate_email_summary_no_matches_still_returns_410(client, test_user):
    response = client.post(f"/api/email/generate/{test_user.id}")
    assert response.status_code == 410
    assert "disabled" in response.json()["detail"].lower()


def test_generate_email_summary_never_returns_a_recipient_when_disabled(client, test_user, test_resume, java_job):
    client.post(f"/api/match/calculate/{test_user.id}")
    response = client.post(f"/api/email/generate/{test_user.id}")
    assert response.status_code == 410
    assert "recipient_email" not in response.json()


def test_get_email_logs_stays_empty_since_generate_never_creates_a_log(client, test_user, test_resume, java_job):
    client.post(f"/api/match/calculate/{test_user.id}")
    client.post(f"/api/email/generate/{test_user.id}")

    response = client.get(f"/api/email/logs/{test_user.id}")
    assert response.status_code == 404  # no logs were ever created


def test_get_email_logs_none_found_returns_404(client, test_user):
    response = client.get(f"/api/email/logs/{test_user.id}")
    assert response.status_code == 404


def test_get_email_logs_cross_user_isolated(client, test_user, second_user, test_resume, java_job):
    client.post(f"/api/match/calculate/{test_user.id}")
    client.post(f"/api/email/generate/{test_user.id}")

    response = client.get(f"/api/email/logs/{second_user.id}")
    assert response.status_code == 404


def test_generate_email_summary_returns_410_even_with_an_optimized_score(client, test_user, test_resume, java_job):
    client.post(f"/api/resume/optimize-and-match/{test_user.id}/{java_job.id}")
    response = client.post(f"/api/email/generate/{test_user.id}")
    assert response.status_code == 410
