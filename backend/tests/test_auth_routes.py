"""Tests for app/routes/auth_routes.py."""

from __future__ import annotations

from app.routes.auth_routes import hash_password, verify_password


def test_hash_password_produces_verifiable_hash():
    hashed = hash_password("secret12")
    assert hashed != "secret12"
    assert verify_password("secret12", hashed) is True


def test_verify_password_rejects_wrong_password():
    hashed = hash_password("secret12")
    assert verify_password("wrong", hashed) is False


def test_signup_success(client):
    response = client.post("/api/auth/signup", json={
        "name": "Jane Developer", "email": "jane@example.com", "password": "secret12",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "jane@example.com"
    assert isinstance(body["user_id"], int)
    assert "password" not in body


def test_signup_duplicate_email_rejected(client):
    payload = {"name": "Jane", "email": "dup@example.com", "password": "secret12"}
    first = client.post("/api/auth/signup", json=payload)
    assert first.status_code == 200
    second = client.post("/api/auth/signup", json=payload)
    assert second.status_code == 400
    assert "already exists" in second.json()["detail"]


def test_signup_invalid_email_rejected(client):
    response = client.post("/api/auth/signup", json={
        "name": "Jane", "email": "not-an-email", "password": "secret12",
    })
    assert response.status_code == 422


def test_signup_missing_password_rejected(client):
    response = client.post("/api/auth/signup", json={"name": "Jane", "email": "jane2@example.com"})
    assert response.status_code == 422


def test_login_success(client):
    client.post("/api/auth/signup", json={"name": "Jane", "email": "login@example.com", "password": "secret12"})
    response = client.post("/api/auth/login", json={"email": "login@example.com", "password": "secret12"})
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "login@example.com"
    assert "password" not in body


def test_login_wrong_password_rejected(client):
    client.post("/api/auth/signup", json={"name": "Jane", "email": "login2@example.com", "password": "secret12"})
    response = client.post("/api/auth/login", json={"email": "login2@example.com", "password": "wrong"})
    assert response.status_code == 401


def test_login_unknown_user_rejected(client):
    response = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "x"})
    assert response.status_code == 401


def test_signup_user_id_is_correct_and_persisted(client, db_session):
    from app.models.user_model import User

    response = client.post("/api/auth/signup", json={
        "name": "Jane", "email": "persisted@example.com", "password": "secret12",
    })
    user_id = response.json()["user_id"]
    row = db_session.query(User).filter(User.id == user_id).first()
    assert row is not None
    assert row.email == "persisted@example.com"
    assert row.password != "secret12"
