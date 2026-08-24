"""Password reset / forgot-password flow tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.models.password_reset_model import PasswordResetToken
from app.models.user_model import User
from app.routes.auth_routes import (
    GENERIC_FORGOT_MESSAGE,
    RESET_TOKEN_EXPIRY_MINUTES,
    _forgot_password_last_request,
    _hash_reset_token,
    create_password_reset_token,
    hash_password,
    verify_password,
)


GENERIC = GENERIC_FORGOT_MESSAGE


def _signup(client, email="reset-user@example.com", password="old-pass-1"):
    return client.post("/api/auth/signup", json={
        "name": "Reset User",
        "email": email,
        "password": password,
    })


def test_forgot_password_existing_email_generic_response(client, db_session, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    _forgot_password_last_request.clear()
    _signup(client)
    with patch("app.routes.auth_routes.deliver_password_reset_email") as deliver:
        deliver.return_value = {"sent": False, "reason": "smtp_not_configured", "dev_logged": True}
        response = client.post("/api/auth/forgot-password", json={"email": "reset-user@example.com"})
    assert response.status_code == 200
    body = response.json()
    assert body["message"] == GENERIC
    assert deliver.called
    assert db_session.query(PasswordResetToken).count() == 1


def test_forgot_password_unknown_email_same_generic_response(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    _forgot_password_last_request.clear()
    with patch("app.routes.auth_routes.deliver_password_reset_email") as deliver:
        response = client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert response.status_code == 200
    assert response.json()["message"] == GENERIC
    assert not deliver.called
    assert response.json().get("dev_reset_url") in (None, "")


def test_forgot_password_does_not_expose_token_in_production(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    _forgot_password_last_request.clear()
    _signup(client, email="prod-reset@example.com")
    with patch("app.routes.auth_routes.deliver_password_reset_email") as deliver:
        deliver.return_value = {"sent": True, "reason": None, "dev_logged": False}
        response = client.post("/api/auth/forgot-password", json={"email": "prod-reset@example.com"})
    assert response.status_code == 200
    assert response.json().get("dev_reset_url") is None


def test_valid_token_reset_updates_password_hash(client, db_session, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    _forgot_password_last_request.clear()
    _signup(client, email="change@example.com", password="old-pass-1")
    user = db_session.query(User).filter(User.email == "change@example.com").first()
    old_hash = user.password
    raw = create_password_reset_token(db_session, user=user)

    response = client.post("/api/auth/reset-password", json={
        "token": raw,
        "new_password": "new-pass-99",
    })
    assert response.status_code == 200
    assert "successfully" in response.json()["message"].lower()

    db_session.expire_all()
    user = db_session.query(User).filter(User.email == "change@example.com").first()
    assert user.password != old_hash
    assert user.password != "new-pass-99"
    assert verify_password("new-pass-99", user.password)
    assert not verify_password("old-pass-1", user.password)

    login_old = client.post("/api/auth/login", json={"email": "change@example.com", "password": "old-pass-1"})
    assert login_old.status_code == 401
    login_new = client.post("/api/auth/login", json={"email": "change@example.com", "password": "new-pass-99"})
    assert login_new.status_code == 200


def test_invalid_token_rejected(client):
    response = client.post("/api/auth/reset-password", json={
        "token": "this-token-does-not-exist-anywhere",
        "new_password": "new-pass-99",
    })
    assert response.status_code == 400
    assert "invalid or has expired" in response.json()["detail"].lower()


def test_expired_token_rejected(client, db_session):
    _signup(client, email="expired@example.com", password="old-pass-1")
    user = db_session.query(User).filter(User.email == "expired@example.com").first()
    raw = "expired-token-value-1234567890"
    row = PasswordResetToken(
        user_id=user.id,
        token_hash=_hash_reset_token(raw),
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        used_at=None,
    )
    db_session.add(row)
    db_session.commit()

    response = client.post("/api/auth/reset-password", json={
        "token": raw,
        "new_password": "new-pass-99",
    })
    assert response.status_code == 400
    assert "invalid or has expired" in response.json()["detail"].lower()


def test_reused_token_rejected(client, db_session, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    _forgot_password_last_request.clear()
    _signup(client, email="reuse@example.com", password="old-pass-1")
    user = db_session.query(User).filter(User.email == "reuse@example.com").first()
    raw = create_password_reset_token(db_session, user=user)

    first = client.post("/api/auth/reset-password", json={"token": raw, "new_password": "new-pass-99"})
    assert first.status_code == 200
    second = client.post("/api/auth/reset-password", json={"token": raw, "new_password": "another-pass"})
    assert second.status_code == 400
    assert "invalid or has expired" in second.json()["detail"].lower()


def test_reset_token_expiry_constant():
    assert 30 <= RESET_TOKEN_EXPIRY_MINUTES <= 60


def test_create_password_reset_token_stores_hash_not_plaintext(db_session):
    user = User(name="Hash Check", email="hashcheck@example.com", password=hash_password("old-pass-1"))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    raw = create_password_reset_token(db_session, user=user)
    row = db_session.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).first()
    assert row is not None
    assert row.token_hash == _hash_reset_token(raw)
    assert row.token_hash != raw
    assert raw not in (row.token_hash or "")
