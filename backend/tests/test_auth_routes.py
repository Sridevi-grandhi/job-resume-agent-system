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
    assert body["name"] == "Jane Developer"
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
    assert body["name"] == "Jane"
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


def test_profile_name_update_is_verified_persisted_and_preserves_email(client, db_session):
    from app.models.user_model import User

    signup = client.post("/api/auth/signup", json={
        "name": "Tanishka", "email": "profile@example.com", "password": "secret12",
    }).json()
    user_id = signup["user_id"]
    response = client.patch(f"/api/auth/profile/{user_id}", json={
        "name": "  Tanishka Test  ", "current_password": "secret12",
    })
    assert response.status_code == 200
    assert response.json() == {"user_id": user_id, "name": "Tanishka Test", "email": "profile@example.com"}
    db_session.expire_all()
    assert db_session.query(User).filter_by(id=user_id).one().name == "Tanishka Test"
    login = client.post("/api/auth/login", json={"email": "profile@example.com", "password": "secret12"})
    assert login.json()["name"] == "Tanishka Test"

    restore = client.patch(f"/api/auth/profile/{user_id}", json={
        "name": "Tanishka", "current_password": "secret12",
    })
    assert restore.status_code == 200
    assert restore.json()["name"] == "Tanishka"


def test_profile_name_update_rejects_wrong_password_and_invalid_names(client, db_session):
    from app.models.user_model import User

    user_id = client.post("/api/auth/signup", json={
        "name": "Tanishka", "email": "profile2@example.com", "password": "secret12",
    }).json()["user_id"]
    path = f"/api/auth/profile/{user_id}"
    assert client.patch(path, json={"name": "Changed", "current_password": "wrong"}).status_code == 401
    assert client.patch(path, json={"name": "   ", "current_password": "secret12"}).status_code == 400
    assert client.patch(path, json={"name": "x" * 101, "current_password": "secret12"}).status_code == 400
    assert client.patch(path, json={
        "name": "Changed", "email": "other@example.com", "current_password": "secret12",
    }).status_code == 422
    db_session.expire_all()
    user = db_session.query(User).filter_by(id=user_id).one()
    assert user.name == "Tanishka"
    assert user.email == "profile2@example.com"
