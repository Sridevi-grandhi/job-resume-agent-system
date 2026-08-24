"""Reusable authentication email helpers (password reset, etc.).

Separate from the Email Summary / job-digest feature. Uses SMTP_* env vars when
configured; otherwise logs safely for local development.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Any


logger = logging.getLogger(__name__)


def is_development_environment() -> bool:
    env = (os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "development").strip().lower()
    return env in {"", "development", "dev", "local", "test"}


def smtp_configured() -> bool:
    return bool((os.getenv("SMTP_HOST") or "").strip() and (os.getenv("SMTP_FROM_EMAIL") or "").strip())


def frontend_base_url() -> str:
    configured = (os.getenv("FRONTEND_BASE_URL") or "").strip().rstrip("/")
    if configured:
        return configured
    origins = (os.getenv("CORS_ORIGINS") or "http://localhost:5173").split(",")
    return (origins[0] or "http://localhost:5173").strip().rstrip("/")


def build_password_reset_email(*, recipient_email: str, reset_url: str, expires_minutes: int) -> dict[str, str]:
    subject = "CareerBoost AI — Password Reset Request"
    text_body = (
        "CareerBoost AI\n"
        "Password Reset Request\n\n"
        "We received a request to reset the password for your CareerBoost account.\n\n"
        f"Reset your password using this link (expires in {expires_minutes} minutes):\n"
        f"{reset_url}\n\n"
        "If you did not request this, you can ignore this email. "
        "Your password will remain unchanged.\n"
    )
    html_body = f"""<!DOCTYPE html>
<html><body style="font-family:Segoe UI,Arial,sans-serif;color:#17233a;background:#f7f9fd;padding:24px;">
  <table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;margin:0 auto;background:#fff;border:1px solid #e3e9f3;border-radius:12px;padding:28px;">
    <tr><td>
      <div style="font-weight:700;color:#1154d4;margin-bottom:18px;">CareerBoost AI</div>
      <h1 style="font-size:20px;margin:0 0 10px;">Password Reset Request</h1>
      <p style="font-size:14px;line-height:1.6;color:#4a5a73;">
        We received a request to reset the password for your CareerBoost account.
        This link expires in <strong>{expires_minutes} minutes</strong>.
      </p>
      <p style="margin:24px 0;">
        <a href="{reset_url}" style="display:inline-block;background:linear-gradient(100deg,#1557cd,#1b66ef);color:#fff;text-decoration:none;padding:12px 18px;border-radius:8px;font-weight:700;font-size:13px;">
          Reset Password
        </a>
      </p>
      <p style="font-size:12px;color:#7187a8;line-height:1.5;">
        If the button does not work, copy and paste this URL into your browser:<br/>
        <span style="word-break:break-all;">{reset_url}</span>
      </p>
      <p style="font-size:12px;color:#7187a8;">If you did not request this, you can ignore this email.</p>
    </td></tr>
  </table>
</body></html>
"""
    return {
        "to": recipient_email,
        "subject": subject,
        "text": text_body,
        "html": html_body,
    }


def send_email_message(*, to_email: str, subject: str, text_body: str, html_body: str | None = None) -> dict[str, Any]:
    """Send via SMTP when configured. Returns delivery metadata (no secrets)."""
    if not smtp_configured():
        logger.info(
            "AUTH_EMAIL_SMTP_NOT_CONFIGURED to=%s subject=%s",
            to_email,
            subject,
        )
        return {"sent": False, "reason": "smtp_not_configured"}

    host = (os.getenv("SMTP_HOST") or "").strip()
    port = int(os.getenv("SMTP_PORT") or "587")
    username = (os.getenv("SMTP_USERNAME") or "").strip()
    password = (os.getenv("SMTP_PASSWORD") or "").strip()
    from_email = (os.getenv("SMTP_FROM_EMAIL") or "").strip()

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_email
    message["To"] = to_email
    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.ehlo()
            try:
                smtp.starttls()
                smtp.ehlo()
            except smtplib.SMTPException:
                pass
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
        logger.info("AUTH_EMAIL_SENT to=%s subject=%s", to_email, subject)
        return {"sent": True, "reason": None}
    except Exception as error:
        logger.exception("AUTH_EMAIL_SEND_FAILED to=%s error=%s", to_email, type(error).__name__)
        return {"sent": False, "reason": "provider_error"}


def deliver_password_reset_email(*, recipient_email: str, reset_url: str, expires_minutes: int) -> dict[str, Any]:
    payload = build_password_reset_email(
        recipient_email=recipient_email,
        reset_url=reset_url,
        expires_minutes=expires_minutes,
    )
    if is_development_environment() or not smtp_configured():
        # Safe local/dev observability — never logs the user's password.
        logger.info(
            "AUTH_PASSWORD_RESET_URL_DEV to=%s expires_minutes=%s url=%s",
            recipient_email,
            expires_minutes,
            reset_url,
        )
    result = send_email_message(
        to_email=payload["to"],
        subject=payload["subject"],
        text_body=payload["text"],
        html_body=payload["html"],
    )
    return {
        **result,
        "dev_logged": is_development_environment() or not smtp_configured(),
    }
