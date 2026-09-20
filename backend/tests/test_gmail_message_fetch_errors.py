"""Tests for the fix to CURRENT VERIFIED BUG: a single Gmail message id that
returns HTTP 404 (deleted/no-longer-accessible) used to raise an uncaught
requests.exceptions.HTTPError from _gmail_request, crashing the entire
"Scan Job Emails" request with "Failed to fetch" — and, critically, never
reaching scan_for_job_emails' own cursor-advancement code, so the very next
scan would hit the exact same message id and crash again.

No real Gmail call anywhere in this file — `requests.request` is mocked at
the app.services.gmail_service.requests boundary, same convention as
test_gmail_oauth.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services import gmail_service


def _response(status_code=200, payload=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload or {}
    response.content = b"1" if payload is not None else b""
    response.raise_for_status.side_effect = None if status_code < 400 else Exception(f"HTTP {status_code}")
    return response


# ---------------------------------------------------------------------------
# _gmail_request — the shared low-level call every Gmail endpoint goes
# through. 404 must become a distinct, immediately-raised, non-retried
# GmailApiError subclass — never an uncaught requests.exceptions.HTTPError.
# ---------------------------------------------------------------------------

def test_404_raises_gmail_message_not_found_error_not_a_raw_http_error():
    with patch("app.services.gmail_service.requests.request", return_value=_response(404)):
        with pytest.raises(gmail_service.GmailMessageNotFoundError):
            gmail_service._gmail_request("GET", "token", "/messages/gone")


def test_gmail_message_not_found_error_is_a_gmail_api_error_subclass():
    """So any EXISTING broad `except GmailApiError` handler elsewhere (e.g.
    the stale-historyId fallback in scan_for_job_emails) already catches it
    without needing its own change."""
    assert issubclass(gmail_service.GmailMessageNotFoundError, gmail_service.GmailApiError)


def test_404_is_not_retried_unlike_429_and_5xx():
    """Not found is never worth retrying — same immediate-raise treatment as
    401/403, never folded into the backoff loop."""
    with patch("app.services.gmail_service.requests.request", return_value=_response(404)) as mocked:
        with pytest.raises(gmail_service.GmailMessageNotFoundError):
            gmail_service._gmail_request("GET", "token", "/messages/gone")
    assert mocked.call_count == 1


# Item 6 — 401/403 must NOT be swallowed by this fix.
def test_401_and_403_still_raise_gmail_auth_error_unchanged():
    for status in (401, 403):
        with patch("app.services.gmail_service.requests.request", return_value=_response(status)):
            with pytest.raises(gmail_service.GmailAuthError):
                gmail_service._gmail_request("GET", "token", "/messages/x")


# Item 7 — 429/5xx must NOT be accidentally converted into a successful skip
# at the _gmail_request level: still retries with backoff, still eventually
# raises a plain GmailApiError (not GmailMessageNotFoundError).
def test_429_still_retries_and_raises_plain_gmail_api_error_not_not_found(monkeypatch):
    monkeypatch.setattr(gmail_service.time, "sleep", lambda _seconds: None)
    with patch("app.services.gmail_service.requests.request", return_value=_response(429)) as mocked:
        with pytest.raises(gmail_service.GmailApiError) as excinfo:
            gmail_service._gmail_request("GET", "token", "/messages/x")
    assert not isinstance(excinfo.value, gmail_service.GmailMessageNotFoundError)
    assert mocked.call_count == 3  # unchanged retry count


def test_5xx_still_retries_and_raises_plain_gmail_api_error_not_not_found(monkeypatch):
    monkeypatch.setattr(gmail_service.time, "sleep", lambda _seconds: None)
    with patch("app.services.gmail_service.requests.request", return_value=_response(503)) as mocked:
        with pytest.raises(gmail_service.GmailApiError) as excinfo:
            gmail_service._gmail_request("GET", "token", "/messages/x")
    assert not isinstance(excinfo.value, gmail_service.GmailMessageNotFoundError)
    assert mocked.call_count == 3


# ---------------------------------------------------------------------------
# get_message_raw — the fix's actual point of consumption: a 404 for ONE
# message id must resolve to None (the existing "unreadable message" signal
# job_email_scan_service._process_message already treats as a safe,
# batch-continuing per-message skip), never raise.
# ---------------------------------------------------------------------------

def test_get_message_raw_returns_none_on_404_without_raising(caplog):
    with patch("app.services.gmail_service.requests.request", return_value=_response(404)):
        result = gmail_service.get_message_raw("token", "missing-message-id")
    assert result is None


def test_get_message_raw_logs_the_missing_message_id_safely(caplog):
    import logging
    caplog.set_level(logging.INFO, logger="app.services.gmail_service")
    with patch("app.services.gmail_service.requests.request", return_value=_response(404)):
        gmail_service.get_message_raw("token", "missing-message-id")
    assert "missing-message-id" in caplog.text
    assert "gmail_message_not_found" in caplog.text


def test_get_message_raw_still_works_normally_for_a_found_message():
    with patch("app.services.gmail_service.requests.request", return_value=_response(200, {"id": "m1"})):
        result = gmail_service.get_message_raw("token", "m1")
    assert result == {"id": "m1"}


# Item 6 — 401/403 during a single message fetch must NOT be swallowed into
# a per-message skip; it must propagate (existing reconnect-required flow).
def test_get_message_raw_does_not_swallow_401_403():
    for status in (401, 403):
        with patch("app.services.gmail_service.requests.request", return_value=_response(status)):
            with pytest.raises(gmail_service.GmailAuthError):
                gmail_service.get_message_raw("token", "m1")
