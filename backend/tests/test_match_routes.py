"""Tests for app/routes/match_routes.py."""

from __future__ import annotations


def test_calculate_matches_for_user(client, test_user, test_resume, java_job, sdet_job):
    response = client.post(f"/api/match/calculate/{test_user.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["matches_created"] == 2
    assert {m["job_id"] for m in body["matches"]} == {java_job.id, sdet_job.id}


def test_calculate_matches_missing_user_returns_404(client):
    response = client.post("/api/match/calculate/999999")
    assert response.status_code == 404


def test_calculate_matches_no_resume_returns_404(client, test_user, java_job):
    response = client.post(f"/api/match/calculate/{test_user.id}")
    assert response.status_code == 404
    assert "resume" in response.json()["detail"].lower()


def test_calculate_matches_no_eligible_jobs_returns_404(client, test_user, test_resume, generic_search_job):
    response = client.post(f"/api/match/calculate/{test_user.id}")
    assert response.status_code == 404


def test_calculate_matches_recalculates_and_replaces_prior_scores(client, test_user, test_resume, java_job):
    first = client.post(f"/api/match/calculate/{test_user.id}")
    second = client.post(f"/api/match/calculate/{test_user.id}")
    assert first.status_code == 200 and second.status_code == 200

    loaded = client.get(f"/api/match/{test_user.id}")
    assert len(loaded.json()) == 1  # not duplicated across the two calculate calls


def test_get_matches_for_user(client, test_user, test_resume, java_job):
    client.post(f"/api/match/calculate/{test_user.id}")
    response = client.get(f"/api/match/{test_user.id}")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["job_id"] == java_job.id
    assert 0 <= body[0]["match_percentage"] <= 100


def test_get_matches_for_user_with_no_matches_returns_empty_list(client, test_user):
    response = client.get(f"/api/match/{test_user.id}")
    assert response.status_code == 200
    assert response.json() == []


def test_get_matches_cross_user_isolated(client, test_user, second_user, test_resume, java_job):
    client.post(f"/api/match/calculate/{test_user.id}")
    response = client.get(f"/api/match/{second_user.id}")
    assert response.status_code == 200
    assert response.json() == []


def test_generic_search_jobs_never_produce_a_match_score(client, test_user, test_resume, generic_search_job, java_job):
    response = client.post(f"/api/match/calculate/{test_user.id}")
    assert response.status_code == 200
    job_ids = {m["job_id"] for m in response.json()["matches"]}
    assert generic_search_job.id not in job_ids
    assert java_job.id in job_ids
