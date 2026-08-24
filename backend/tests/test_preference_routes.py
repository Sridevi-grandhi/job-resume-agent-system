"""Tests for app/routes/preference_routes.py."""

from __future__ import annotations


def _payload(user_id, **overrides):
    payload = {
        "user_id": user_id,
        "job_title": "Java Developer",
        "location": "Remote",
        "experience": "5",
        "work_mode": "Remote",
        "job_type": "Full-time",
        "skills": "Java, Spring Boot",
        "schedule_time": "08:00:00",
        "timezone": "America/Chicago",
    }
    payload.update(overrides)
    return payload


def test_save_preferences_for_existing_user(client, test_user):
    response = client.post("/api/preferences", json=_payload(test_user.id))
    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "Preferences saved successfully"
    assert body["user_id"] == test_user.id


def test_save_preferences_for_missing_user_returns_404(client):
    response = client.post("/api/preferences", json=_payload(999999))
    assert response.status_code == 404


def test_update_preferences_overwrites_existing_row(client, test_user, test_preferences):
    response = client.post("/api/preferences", json=_payload(test_user.id, job_title="Python Developer"))
    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "Preferences updated successfully"
    assert body["job_title"] == "Python Developer"
    assert body["preference_id"] == test_preferences.id


def test_load_preferences(client, test_user, test_preferences):
    response = client.get(f"/api/preferences/{test_user.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == test_user.id
    assert body["job_title"] == test_preferences.job_title


def test_load_preferences_missing_for_new_user(client, second_user):
    response = client.get(f"/api/preferences/{second_user.id}")
    assert response.status_code == 404


def test_save_preferences_invalid_schedule_time_rejected(client, test_user):
    response = client.post("/api/preferences", json=_payload(test_user.id, schedule_time="not-a-time"))
    assert response.status_code == 422


def test_cross_user_preferences_are_isolated(client, test_user, second_user):
    client.post("/api/preferences", json=_payload(test_user.id, job_title="Java Developer"))
    client.post("/api/preferences", json=_payload(second_user.id, job_title="Python Developer"))

    mine = client.get(f"/api/preferences/{test_user.id}").json()
    theirs = client.get(f"/api/preferences/{second_user.id}").json()
    assert mine["job_title"] == "Java Developer"
    assert theirs["job_title"] == "Python Developer"
    assert mine["id"] != theirs["id"]
