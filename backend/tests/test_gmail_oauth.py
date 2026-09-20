"""Tests for the Gmail OAuth 2.0 flow (app/services/gmail_service.py).
No real network calls — `requests.post`/`requests.request` are mocked.
Never depends on a real Google account or a real GOOGLE_CLIENT_SECRET.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from app.models.email_connection_model import EmailConnection
from app.services import gmail_service


@pytest.fixture(autouse=True)
def _oauth_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/email/gmail/callback")
    monkeypatch.setenv("EMAIL_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))


def _response(status_code=200, payload=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload or {}
    response.content = b"1" if payload is not None else b""
    if status_code >= 400:
        response.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        response.raise_for_status.side_effect = None
    return response


# ---------------------------------------------------------------------------
# Connect URL + state
# ---------------------------------------------------------------------------

def test_connect_url_generated(db_session, test_user):
    url = gmail_service.start_oauth(db_session, test_user.id)
    assert url.startswith(gmail_service.AUTH_URL)
    assert "client_id=test-client-id" in url
    assert "scope=" in url

    connection = db_session.query(EmailConnection).filter_by(user_id=test_user.id).first()
    assert connection is not None
    assert connection.pending_oauth_state is not None


def test_oauth_url_requests_gmail_readonly(db_session, test_user):
    """Requirement: the OAuth URL must contain gmail.readonly."""
    from urllib.parse import parse_qs, urlsplit

    url = gmail_service.start_oauth(db_session, test_user.id)
    assert "gmail.readonly" in url  # unencoded substring check (dots survive urlencode)
    query = parse_qs(urlsplit(url).query)
    assert query["scope"] == ["https://www.googleapis.com/auth/gmail.readonly"]


def test_oauth_url_does_not_request_gmail_compose(db_session, test_user):
    """Requirement: gmail.compose must never be requested — read-only version."""
    url = gmail_service.start_oauth(db_session, test_user.id)
    assert "gmail.compose" not in url


def test_oauth_url_does_not_request_gmail_send(db_session, test_user):
    """Requirement: gmail.send must never be requested."""
    url = gmail_service.start_oauth(db_session, test_user.id)
    assert "gmail.send" not in url


def test_scopes_constant_is_readonly_only():
    assert gmail_service.SCOPES == "https://www.googleapis.com/auth/gmail.readonly"
    assert "gmail.compose" not in gmail_service.SCOPES
    assert "gmail.send" not in gmail_service.SCOPES


def test_connect_requires_configuration(db_session, test_user, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    with pytest.raises(gmail_service.GmailNotConfigured):
        gmail_service.start_oauth(db_session, test_user.id)


def test_connect_requires_token_encryption_configured(db_session, test_user, monkeypatch):
    monkeypatch.delenv("EMAIL_TOKEN_ENCRYPTION_KEY", raising=False)
    with pytest.raises(gmail_service.GmailNotConfigured):
        gmail_service.start_oauth(db_session, test_user.id)


# ---------------------------------------------------------------------------
# Callback: state validation + success + denial
# ---------------------------------------------------------------------------

def test_callback_invalid_state_rejected(db_session, test_user):
    with pytest.raises(gmail_service.GmailAuthError):
        gmail_service.handle_oauth_callback(db_session, "some-code", "totally-unknown-state")


def test_callback_expired_state_rejected(db_session, test_user):
    gmail_service.start_oauth(db_session, test_user.id)
    connection = db_session.query(EmailConnection).filter_by(user_id=test_user.id).first()
    state = connection.pending_oauth_state
    connection.pending_oauth_state_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()

    with pytest.raises(gmail_service.GmailAuthError):
        gmail_service.handle_oauth_callback(db_session, "some-code", state)


def test_callback_success_stores_encrypted_tokens(db_session, test_user):
    url = gmail_service.start_oauth(db_session, test_user.id)
    state = url.split("state=")[1].split("&")[0]

    token_response = _response(200, {"access_token": "real-access-token", "refresh_token": "real-refresh-token", "expires_in": 3600, "scope": gmail_service.SCOPES})
    profile_response = _response(200, {"emailAddress": "candidate@gmail.com", "historyId": "12345"})

    with patch("app.services.gmail_service.requests.post", return_value=token_response), \
         patch("app.services.gmail_service.requests.request", return_value=profile_response):
        connection = gmail_service.handle_oauth_callback(db_session, "auth-code", state)

    assert connection.status == "CONNECTED"
    assert connection.email_address == "candidate@gmail.com"
    assert connection.pending_oauth_state is None
    # Tokens are stored encrypted, never in plaintext.
    assert connection.encrypted_access_token != "real-access-token"
    assert "real-access-token" not in (connection.encrypted_access_token or "")
    assert "real-refresh-token" not in (connection.encrypted_refresh_token or "")
    # Requirement A: the profile's historyId (12345 here) is NOT seeded as
    # the scan cursor — that would make the first "Scan Job Emails" call
    # look like an incremental scan with nothing new, silently skipping the
    # intended bounded initial scan (see job_email_scan_service.py).
    assert connection.last_history_id is None


def test_successful_oauth_callback_leaves_history_id_unset_for_initial_scan(db_session, test_user):
    """Requirement A, dedicated: get_profile() is still called (needed for
    the connected email address), but its historyId is never used to seed
    the scan cursor."""
    url = gmail_service.start_oauth(db_session, test_user.id)
    state = url.split("state=")[1].split("&")[0]
    token_response = _response(200, {"access_token": "tok", "refresh_token": "ref", "expires_in": 3600, "scope": gmail_service.SCOPES})
    profile_response = _response(200, {"emailAddress": "candidate@gmail.com", "historyId": "999999"})

    with patch("app.services.gmail_service.requests.post", return_value=token_response), \
         patch("app.services.gmail_service.requests.request", return_value=profile_response) as mocked_request:
        connection = gmail_service.handle_oauth_callback(db_session, "auth-code", state)

    mocked_request.assert_called_once()  # get_profile() still called — needed for email_address
    assert connection.email_address == "candidate@gmail.com"
    assert connection.last_history_id is None
    assert connection.last_scanned_at is None


def test_callback_state_is_single_use(db_session, test_user):
    url = gmail_service.start_oauth(db_session, test_user.id)
    state = url.split("state=")[1].split("&")[0]
    token_response = _response(200, {"access_token": "tok", "refresh_token": "ref", "expires_in": 3600, "scope": gmail_service.SCOPES})
    profile_response = _response(200, {"emailAddress": "candidate@gmail.com", "historyId": "1"})

    with patch("app.services.gmail_service.requests.post", return_value=token_response), \
         patch("app.services.gmail_service.requests.request", return_value=profile_response):
        gmail_service.handle_oauth_callback(db_session, "auth-code", state)

    with pytest.raises(gmail_service.GmailAuthError):
        gmail_service.handle_oauth_callback(db_session, "another-code", state)


def test_route_callback_denied_redirects_without_exchanging_code(client, test_user, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/email/gmail/callback")
    with patch("app.services.gmail_service.requests.post") as mocked_post:
        response = client.get("/api/email/gmail/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "gmail=denied" in response.headers["location"]
    mocked_post.assert_not_called()


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------

def _connected_connection(db_session, user_id, *, access_token="old-access", refresh_token="the-refresh-token", expired=False):
    connection = EmailConnection(
        user_id=user_id, provider="gmail", status="CONNECTED", email_address="candidate@gmail.com",
        encrypted_access_token=gmail_service.crypto.encrypt_token(access_token),
        encrypted_refresh_token=gmail_service.crypto.encrypt_token(refresh_token),
        token_expires_at=datetime.now(timezone.utc) + (timedelta(minutes=-5) if expired else timedelta(hours=1)),
    )
    db_session.add(connection)
    db_session.commit()
    db_session.refresh(connection)
    return connection


def test_expired_access_token_is_refreshed(db_session, test_user):
    connection = _connected_connection(db_session, test_user.id, expired=True)
    refresh_response = _response(200, {"access_token": "new-access-token", "expires_in": 3600})

    with patch("app.services.gmail_service.requests.post", return_value=refresh_response) as mocked_post:
        token = gmail_service._ensure_valid_access_token(db_session, connection)

    assert token == "new-access-token"
    mocked_post.assert_called_once()
    payload = mocked_post.call_args.kwargs["data"]
    assert payload["grant_type"] == "refresh_token"
    assert payload["refresh_token"] == "the-refresh-token"


def test_valid_access_token_is_not_refreshed(db_session, test_user):
    connection = _connected_connection(db_session, test_user.id, expired=False)
    with patch("app.services.gmail_service.requests.post") as mocked_post:
        token = gmail_service._ensure_valid_access_token(db_session, connection)
    assert token == "old-access"
    mocked_post.assert_not_called()


def test_refresh_failure_marks_connection_error(db_session, test_user):
    connection = _connected_connection(db_session, test_user.id, expired=True)
    with patch("app.services.gmail_service.requests.post", return_value=_response(401, {})):
        with pytest.raises(gmail_service.GmailAuthError):
            gmail_service._ensure_valid_access_token(db_session, connection)
    db_session.refresh(connection)
    assert connection.status == "ERROR"


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------

def test_disconnect_clears_tokens(db_session, test_user):
    _connected_connection(db_session, test_user.id)
    gmail_service.disconnect(db_session, test_user.id)
    connection = db_session.query(EmailConnection).filter_by(user_id=test_user.id).first()
    assert connection.status == "DISCONNECTED"
    assert connection.encrypted_access_token is None
    assert connection.encrypted_refresh_token is None


# ---------------------------------------------------------------------------
# Reconnection (Requirement H)
# ---------------------------------------------------------------------------

def test_reconnection_resets_stale_history_id(db_session, test_user):
    """A previously-connected account (real usage, real scans) that
    disconnects and reconnects must not carry a stale scan cursor forward —
    the new connection must also start as an initial scan."""
    connection = _connected_connection(db_session, test_user.id)
    connection.last_history_id = "stale-old-history-id"
    connection.last_scanned_at = datetime.now(timezone.utc)
    db_session.commit()

    gmail_service.disconnect(db_session, test_user.id)

    url = gmail_service.start_oauth(db_session, test_user.id)
    state = url.split("state=")[1].split("&")[0]
    token_response = _response(200, {"access_token": "tok2", "refresh_token": "ref2", "expires_in": 3600, "scope": gmail_service.SCOPES})
    profile_response = _response(200, {"emailAddress": "candidate@gmail.com", "historyId": "brand-new-history-id"})

    with patch("app.services.gmail_service.requests.post", return_value=token_response), \
         patch("app.services.gmail_service.requests.request", return_value=profile_response):
        reconnected = gmail_service.handle_oauth_callback(db_session, "auth-code-2", state)

    assert reconnected.status == "CONNECTED"
    assert reconnected.last_history_id is None
    assert reconnected.last_scanned_at is None
