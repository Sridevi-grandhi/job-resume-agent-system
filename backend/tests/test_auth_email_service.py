"""Tests for app/services/auth_email_service.py — the password-reset email
builder/transport, separate from the Email Summary / job-digest feature.
No real SMTP connection is ever made here (smtplib.SMTP is mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services import auth_email_service as svc


# ---------------------------------------------------------------------------
# show_dev_reset_link — BOTH flags required
# ---------------------------------------------------------------------------

def test_dev_reset_link_hidden_by_default(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("SHOW_DEV_RESET_LINK", raising=False)
    assert svc.show_dev_reset_link() is False  # dev-by-default APP_ENV, but flag unset


def test_dev_reset_link_hidden_when_only_flag_set_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SHOW_DEV_RESET_LINK", "true")
    assert svc.show_dev_reset_link() is False


def test_dev_reset_link_hidden_when_only_dev_mode_set(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SHOW_DEV_RESET_LINK", "false")
    assert svc.show_dev_reset_link() is False


def test_dev_reset_link_shown_only_when_both_set(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SHOW_DEV_RESET_LINK", "true")
    assert svc.show_dev_reset_link() is True


# ---------------------------------------------------------------------------
# build_password_reset_email — separate template, never sends the password
# ---------------------------------------------------------------------------

def test_password_reset_email_subject_and_recipient():
    payload = svc.build_password_reset_email(recipient_email="jane@x.com", reset_url="https://x/reset?token=abc", expires_minutes=45)
    assert payload["to"] == "jane@x.com"
    assert "password" in payload["subject"].lower()
    assert "reset" in payload["subject"].lower()


def test_password_reset_email_includes_link_and_expiry_in_both_bodies():
    payload = svc.build_password_reset_email(recipient_email="jane@x.com", reset_url="https://x/reset?token=abc123", expires_minutes=30)
    assert "https://x/reset?token=abc123" in payload["text"]
    assert "https://x/reset?token=abc123" in payload["html"]
    assert "30" in payload["text"]
    assert "30" in payload["html"]


def test_password_reset_email_never_includes_a_password_field():
    payload = svc.build_password_reset_email(recipient_email="jane@x.com", reset_url="https://x/reset?token=abc", expires_minutes=45)
    for value in payload.values():
        assert "your password is" not in value.lower()
        assert "current password" not in value.lower()


# ---------------------------------------------------------------------------
# send_email_message — SMTP transport (mocked)
# ---------------------------------------------------------------------------

def test_send_email_message_not_configured_returns_safe_result(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_FROM_EMAIL", raising=False)
    result = svc.send_email_message(to_email="jane@x.com", subject="s", text_body="b")
    assert result == {"sent": False, "reason": "smtp_not_configured"}


def test_send_email_message_success_uses_starttls(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password-value")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "no-reply@careerboost.example")

    mock_smtp = MagicMock()
    with patch("smtplib.SMTP", return_value=mock_smtp) as smtp_cls:
        mock_smtp.__enter__.return_value = mock_smtp
        result = svc.send_email_message(to_email="jane@x.com", subject="Reset", text_body="body", html_body="<p>body</p>")
    smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=20)
    mock_smtp.starttls.assert_called_once()
    mock_smtp.login.assert_called_once_with("bot@example.com", "app-password-value")
    mock_smtp.send_message.assert_called_once()
    assert result == {"sent": True, "reason": None}


def test_send_email_message_failure_returns_safe_reason_no_exception_leaks(monkeypatch, caplog):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "no-reply@careerboost.example")
    monkeypatch.setenv("SMTP_USERNAME", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "super-secret-app-password")

    with patch("smtplib.SMTP", side_effect=ConnectionRefusedError("connection refused")):
        result = svc.send_email_message(to_email="jane@x.com", subject="Reset", text_body="body")
    assert result == {"sent": False, "reason": "provider_error"}
    assert "super-secret-app-password" not in caplog.text


def test_deliver_password_reset_email_does_not_log_url_by_default(monkeypatch, caplog):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("SHOW_DEV_RESET_LINK", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    with caplog.at_level("INFO"):
        result = svc.deliver_password_reset_email(recipient_email="jane@x.com", reset_url="https://x/reset?token=super-secret-raw-token", expires_minutes=45)
    assert result["dev_logged"] is False
    assert "super-secret-raw-token" not in caplog.text


def test_deliver_password_reset_email_logs_url_only_with_explicit_opt_in(monkeypatch, caplog):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SHOW_DEV_RESET_LINK", "true")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    with caplog.at_level("INFO"):
        result = svc.deliver_password_reset_email(recipient_email="jane@x.com", reset_url="https://x/reset?token=super-secret-raw-token", expires_minutes=45)
    assert result["dev_logged"] is True
    assert "super-secret-raw-token" in caplog.text
