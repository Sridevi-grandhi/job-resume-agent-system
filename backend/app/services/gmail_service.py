"""Gmail integration: OAuth 2.0 authorization-code flow + the minimum Gmail
REST calls Email Summary needs (list/get messages, read the profile).
Implemented with plain `requests` — consistent with every other external
integration in this codebase (Greenhouse/Lever/JSearch/LLM providers) — so
no new SDK/dependency or Docker rebuild is required beyond the two things
this feature genuinely does add: `cryptography` (see email_token_crypto.py)
and the GOOGLE_* / EMAIL_TOKEN_ENCRYPTION_KEY env vars.

Scope requested — READ-ONLY, current version:
  - https://www.googleapis.com/auth/gmail.readonly
`gmail.compose` and `gmail.send` are never requested, and this module never
calls a draft-creation or send endpoint (users.messages.send /
users.drafts.create / users.drafts.send). gmail.readonly technically
permits reading the full mailbox — the job-related/unrelated distinction
(application confirmations, recruiter outreach, interview invites,
assessments, offers, rejections vs. banking/shopping/personal/newsletter
mail) is enforced entirely in this application's own classification logic
(see application_email_classifier.py), never by the OAuth grant itself.
Gmail drafting (EmailProvider.create_draft / GmailEmailProvider.create_draft
below) is existing code from an earlier version, kept but unused while this
build only requests gmail.readonly — see docs/email-summary-gmail-setup.md.

Access/refresh tokens are stored encrypted (email_token_crypto) and are
never logged, never returned in an API response, and never printed.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any
from urllib.parse import urlencode

import requests
from sqlalchemy.orm import Session

from app.models.email_connection_model import EmailConnection
from app.services import email_token_crypto as crypto
from app.services.email_provider import ConnectionStatus, DraftResult, EmailProvider, ProviderMessage

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

SCOPES = "https://www.googleapis.com/auth/gmail.readonly"

STATE_TTL_MINUTES = 10
TIMEOUT = (5, 15)
USER_AGENT = "CareerBoostAI-EmailSummary/1.0 (+educational project)"


class GmailNotConfigured(RuntimeError):
    pass


class GmailAuthError(RuntimeError):
    """Invalid/expired auth — never worth retrying automatically (Part 20)."""


class GmailApiError(RuntimeError):
    """Transient/unexpected Gmail API failure — safe to retry with backoff."""


class GmailMessageNotFoundError(GmailApiError):
    """A single Gmail message id returned HTTP 404 — it no longer exists or
    is no longer accessible (deleted, permanently trashed, etc.). Never
    transient/worth an immediate retry the way a 429/5xx is, so
    _gmail_request raises it right away without the backoff loop — but IS a
    GmailApiError, so any existing broad `except GmailApiError` handler
    (e.g. scan_for_job_emails' stale-historyId fallback) still catches it
    without needing its own change. get_message_raw() catches this
    specifically and returns None, which job_email_scan_service._process_message
    already treats as a normal, batch-continuing per-message skip
    ("fetch_failed") — see CURRENT VERIFIED BUG this fixes: one 404'd
    message id used to raise an uncaught requests.exceptions.HTTPError here,
    crashing the entire scan (and, critically, never reaching the cursor-
    advancement code at the end of scan_for_job_emails — so the very next
    scan attempt would hit the exact same 404 and crash again)."""


def client_id() -> str:
    return (os.getenv("GOOGLE_CLIENT_ID") or "").strip()


def client_secret() -> str:
    return (os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()


def redirect_uri() -> str:
    return (os.getenv("GOOGLE_REDIRECT_URI") or "").strip()


def is_oauth_configured() -> bool:
    return bool(client_id() and client_secret() and redirect_uri())


def is_fully_configured() -> bool:
    """Gmail connection requires both OAuth credentials AND a token
    encryption key — never store a real refresh token unencrypted."""
    return is_oauth_configured() and crypto.is_configured()


# ---------------------------------------------------------------------------
# OAuth authorization-code flow
# ---------------------------------------------------------------------------

def build_authorization_url(state: str) -> str:
    params = {
        "client_id": client_id(),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",   # required to receive a refresh_token
        "prompt": "consent",        # ensures a refresh_token even on reconnect
        "state": state,
        "include_granted_scopes": "true",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _token_request(payload: dict[str, str]) -> dict[str, Any]:
    try:
        response = requests.post(
            TOKEN_URL, data=payload,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
    except requests.Timeout as error:
        raise GmailApiError("timeout") from error
    except requests.RequestException as error:
        raise GmailApiError("network_error") from error
    if response.status_code in (400, 401):
        raise GmailAuthError(f"HTTP {response.status_code}")
    if response.status_code >= 500:
        raise GmailApiError(f"HTTP {response.status_code}")
    response.raise_for_status()
    return response.json()


def exchange_code_for_tokens(code: str) -> dict[str, Any]:
    if not is_oauth_configured():
        raise GmailNotConfigured("Google OAuth is not configured on this server.")
    return _token_request({
        "code": code,
        "client_id": client_id(),
        "client_secret": client_secret(),
        "redirect_uri": redirect_uri(),
        "grant_type": "authorization_code",
    })


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    if not is_oauth_configured():
        raise GmailNotConfigured("Google OAuth is not configured on this server.")
    return _token_request({
        "refresh_token": refresh_token,
        "client_id": client_id(),
        "client_secret": client_secret(),
        "grant_type": "refresh_token",
    })


# ---------------------------------------------------------------------------
# Connection lifecycle — persisted, encrypted, per-user (unique on user_id)
# ---------------------------------------------------------------------------

def _get_or_create_connection(db: Session, user_id: int) -> EmailConnection:
    connection = db.query(EmailConnection).filter(EmailConnection.user_id == user_id).first()
    if connection is None:
        connection = EmailConnection(user_id=user_id, provider="gmail", status="DISCONNECTED")
        db.add(connection)
        db.flush()
    return connection


def start_oauth(db: Session, user_id: int) -> str:
    if not is_fully_configured():
        raise GmailNotConfigured(
            "Gmail integration is not configured on this server "
            "(missing GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET/GOOGLE_REDIRECT_URI "
            "or EMAIL_TOKEN_ENCRYPTION_KEY)."
        )
    connection = _get_or_create_connection(db, user_id)
    state = secrets.token_urlsafe(32)
    connection.pending_oauth_state = state
    connection.pending_oauth_state_expires_at = datetime.now(timezone.utc) + timedelta(minutes=STATE_TTL_MINUTES)
    db.commit()
    return build_authorization_url(state)


def handle_oauth_callback(db: Session, code: str, state: str) -> EmailConnection:
    connection = db.query(EmailConnection).filter(EmailConnection.pending_oauth_state == state).first()
    if connection is None:
        raise GmailAuthError("invalid_state")

    expires_at = connection.pending_oauth_state_expires_at
    connection.pending_oauth_state = None
    connection.pending_oauth_state_expires_at = None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is None or datetime.now(timezone.utc) > expires_at:
        db.commit()
        raise GmailAuthError("expired_state")

    tokens = exchange_code_for_tokens(code)
    access_token = tokens.get("access_token") or ""
    refresh_token = tokens.get("refresh_token") or ""
    expires_in = int(tokens.get("expires_in") or 3600)
    granted_scope = tokens.get("scope") or SCOPES

    if not access_token:
        connection.status = "ERROR"
        connection.last_error = "no_access_token_returned"
        db.commit()
        raise GmailAuthError("no_access_token_returned")

    profile = get_profile(access_token)

    connection.encrypted_access_token = crypto.encrypt_token(access_token)
    if refresh_token:
        # Google only returns a refresh_token on first consent (or with
        # prompt=consent, which we always pass) — never overwrite a
        # previously-stored one with an empty value.
        connection.encrypted_refresh_token = crypto.encrypt_token(refresh_token)
    connection.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    connection.scopes = granted_scope
    connection.email_address = profile.get("emailAddress")
    # Deliberately NOT profile.get("historyId") here — seeding the cursor at
    # connect time made job_email_scan_service.scan_for_job_emails treat the
    # very first "Scan Job Emails" click as an incremental (history-API)
    # scan with nothing "new" yet, silently skipping the intended bounded
    # recent-messages scan (messages_checked stayed 0). Leaving/resetting
    # this to None — including on a reconnect, where a previous session may
    # have left a stale value — means the scan service's own existing
    # "no last_history_id -> bounded scan" branch runs first; it fetches
    # and stores the real historyId itself once that bounded scan succeeds.
    connection.last_history_id = None
    connection.last_scanned_at = None
    connection.status = "CONNECTED"
    connection.last_error = None
    db.commit()
    db.refresh(connection)
    return connection


def _ensure_valid_access_token(db: Session, connection: EmailConnection) -> str:
    access_token = crypto.decrypt_token(connection.encrypted_access_token or "")
    expires_at = connection.token_expires_at
    still_valid = access_token and expires_at and datetime.now(timezone.utc) < (
        expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
    ) - timedelta(seconds=60)
    if still_valid:
        return access_token

    refresh_token = crypto.decrypt_token(connection.encrypted_refresh_token or "")
    if not refresh_token:
        connection.status = "ERROR"
        connection.last_error = "refresh_token_missing"
        db.commit()
        raise GmailAuthError("refresh_token_missing")

    try:
        tokens = refresh_access_token(refresh_token)
    except GmailAuthError:
        connection.status = "ERROR"
        connection.last_error = "refresh_failed"
        db.commit()
        raise

    new_access_token = tokens.get("access_token") or ""
    if not new_access_token:
        connection.status = "ERROR"
        connection.last_error = "refresh_failed"
        db.commit()
        raise GmailAuthError("refresh_failed")

    expires_in = int(tokens.get("expires_in") or 3600)
    connection.encrypted_access_token = crypto.encrypt_token(new_access_token)
    connection.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    connection.status = "CONNECTED"
    connection.last_error = None
    db.commit()
    return new_access_token


def get_connection(db: Session, user_id: int) -> EmailConnection | None:
    return db.query(EmailConnection).filter(EmailConnection.user_id == user_id).first()


def get_provider_for_user(db: Session, user_id: int) -> "GmailEmailProvider | None":
    connection = get_connection(db, user_id)
    if connection is None or connection.status != "CONNECTED":
        return None
    access_token = _ensure_valid_access_token(db, connection)
    return GmailEmailProvider(db=db, connection=connection, access_token=access_token)


def disconnect(db: Session, user_id: int) -> None:
    connection = _get_or_create_connection(db, user_id)
    connection.encrypted_access_token = None
    connection.encrypted_refresh_token = None
    connection.token_expires_at = None
    connection.status = "DISCONNECTED"
    connection.last_error = None
    connection.pending_oauth_state = None
    connection.pending_oauth_state_expires_at = None
    db.commit()


# ---------------------------------------------------------------------------
# Gmail REST calls
# ---------------------------------------------------------------------------

def _gmail_request(method: str, access_token: str, path: str, *, params=None, json_body=None) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "User-Agent": USER_AGENT}
    url = f"{GMAIL_API_BASE}{path}"
    attempts = 3
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.request(method, url, headers=headers, params=params, json=json_body, timeout=TIMEOUT)
        except requests.Timeout as error:
            last_error = GmailApiError("timeout")
            last_error.__cause__ = error
        except requests.RequestException as error:
            last_error = GmailApiError("network_error")
            last_error.__cause__ = error
        else:
            if response.status_code in (401, 403):
                raise GmailAuthError(f"HTTP {response.status_code}")
            # Not found is never worth retrying (the resource is gone, not
            # temporarily unavailable) — raised immediately, same as 401/403,
            # never folded into the 429/5xx backoff loop below.
            if response.status_code == 404:
                raise GmailMessageNotFoundError(f"HTTP 404: {url}")
            if response.status_code == 429 or response.status_code >= 500:
                last_error = GmailApiError(f"HTTP {response.status_code}")
            else:
                response.raise_for_status()
                return response.json() if response.content else {}
        if attempt < attempts - 1:
            time.sleep(min(8, 0.5 * (2 ** attempt)))  # exponential backoff for 429/5xx (Part 20)
    raise last_error or GmailApiError("unknown_error")


def get_profile(access_token: str) -> dict[str, Any]:
    return _gmail_request("GET", access_token, "/profile")


def list_messages(access_token: str, *, query: str, max_results: int = 25) -> list[dict[str, Any]]:
    payload = _gmail_request("GET", access_token, "/messages", params={"q": query, "maxResults": max_results})
    return payload.get("messages") or []


def get_history(access_token: str, *, start_history_id: str, max_results: int = 100) -> dict[str, Any]:
    return _gmail_request(
        "GET", access_token, "/history",
        params={"startHistoryId": start_history_id, "maxResults": max_results, "historyTypes": "messageAdded"},
    )


def _decode_body_part(part: dict[str, Any]) -> str:
    """Base64url-decodes a Gmail message part's raw body data only — never
    normalizes or strips anything here. Callers below know the part's
    mimeType and apply the matching text/HTML normalization; keeping decode
    and normalization separate avoids double-processing a part reached via
    more than one code path."""
    data = (part.get("body") or {}).get("data")
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
    except Exception:
        return ""


_HTML_SCRIPT_STYLE_PATTERN = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
_HTML_BR_PATTERN = re.compile(r"(?i)<br\s*/?>")
_HTML_BLOCK_BOUNDARY_PATTERN = re.compile(r"(?i)</(p|div|li|h[1-6]|tr|blockquote|section|article)>")
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_WHITESPACE_RUN_PATTERN = re.compile(r"\s+")

# Invisible Unicode separators some HTML-email template generators insert
# between adjacent inline elements in place of a literal space (a rendering
# hack so no *visible* extra gap appears) — real content whitespace, not
# arbitrary characters, so treating them as a space is safe and never
# affects ordinary words/acronyms/URLs. Expressed as explicit codepoints
# (ZWSP, ZWNJ, ZWJ, word joiner), never literal invisible characters in
# source, so the file stays legible/diffable. A byte-order mark is pure
# encoding noise (never intentional content) and is dropped instead.
_ZERO_WIDTH_SEPARATOR_CODEPOINTS = (0x200B, 0x200C, 0x200D, 0x2060)
_BOM_CODEPOINT = 0xFEFF


def _normalize_extracted_text(text: str) -> str:
    """Shared final pass for both plain-text and HTML-derived body text:
    decodes any leftover HTML entities (plain-text parts occasionally still
    carry a stray one), normalizes invisible zero-width/BOM characters,
    then collapses every run of whitespace — spaces, tabs, and hard-wrapped
    newlines alike — into a single separating space, and trims the ends.
    Never drops whitespace outright, so two words are never glued together
    (see CURRENT VERIFIED BUG: "Thank youfor applying", "SystemOne")."""
    if not text:
        return ""
    text = unescape(text).replace("\xa0", " ")
    text = text.replace(chr(_BOM_CODEPOINT), "")
    for codepoint in _ZERO_WIDTH_SEPARATOR_CODEPOINTS:
        text = text.replace(chr(codepoint), " ")
    return _WHITESPACE_RUN_PATTERN.sub(" ", text).strip()


def _html_to_text(raw_html: str) -> str:
    """Converts an HTML message body into flowing plain text. Every tag —
    not only block-level ones — is replaced with a SPACE, never an empty
    string, so two elements with no literal whitespace between them in the
    source markup (e.g. "<p>Thank you</p><p>for applying</p>") still end up
    word-separated once tags are gone. <br> and closing block-level tags are
    handled first so the same rule also covers those cases explicitly."""
    if not raw_html:
        return ""
    text = _HTML_SCRIPT_STYLE_PATTERN.sub(" ", raw_html)
    text = _HTML_BR_PATTERN.sub(" ", text)
    text = _HTML_BLOCK_BOUNDARY_PATTERN.sub(" ", text)
    text = _HTML_TAG_PATTERN.sub(" ", text)
    return _normalize_extracted_text(text)


def _strip_whitespace(text: str) -> str:
    return _WHITESPACE_RUN_PATTERN.sub("", text)


def _prefer_better_spaced_alternative(plain_text: str, html_text: str) -> str:
    """LIVE VERIFIED BUG (this task): a sender's multipart/alternative
    text/plain part is not always generated as carefully as its paired
    text/html part — some senders build the plain-text alternative by
    concatenating template fields with no separating space at a specific
    punctuation boundary (e.g. "...at SES." + "Your background..." ->
    "SES.Your"), even though the SAME content in the HTML alternative has
    an explicit tag boundary at that exact point (already correctly
    space-separated by _html_to_text). Detected, never guessed, and never
    a blind punctuation rewrite: only when stripping ALL whitespace from
    both leaves the EXACT SAME non-whitespace characters (proof they are
    the same underlying content, not two genuinely different renderings —
    e.g. one carrying extra footer/disclaimer text the other lacks) do we
    ever prefer the HTML extraction over the normal text/plain preference —
    and only when it is STRICTLY longer, i.e. provably preserves at least
    one whitespace separator the plain-text version lost. Any mismatch at
    all falls back to the existing, unchanged text/plain preference."""
    if not html_text or not plain_text:
        return plain_text
    if _strip_whitespace(html_text) != _strip_whitespace(plain_text):
        return plain_text
    if len(html_text) > len(plain_text):
        return html_text
    return plain_text


_BLANK_LINE_RUN_PATTERN = re.compile(r"\n{3,}")
_HORIZONTAL_WHITESPACE_RUN_PATTERN = re.compile(r"[ \t]+")


def _normalize_display_text(text: str) -> str:
    """Display-only sibling of _normalize_extracted_text (Email Summary's
    View Details full email body) — collapses horizontal whitespace (spaces/
    tabs) exactly the same way, but PRESERVES line breaks instead of
    collapsing them, so the frontend can render the original paragraph
    structure with white-space: pre-wrap. Runs of 3+ blank lines (which a
    chain of adjacent block-level tags can produce) collapse to a single
    blank line — one clean paragraph gap, never a wall of empty lines.
    Never used for classification/matching — _normalize_extracted_text
    itself is untouched."""
    if not text:
        return ""
    text = unescape(text).replace("\xa0", " ")
    text = text.replace(chr(_BOM_CODEPOINT), "")
    for codepoint in _ZERO_WIDTH_SEPARATOR_CODEPOINTS:
        text = text.replace(chr(codepoint), " ")
    text = _HORIZONTAL_WHITESPACE_RUN_PATTERN.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANK_LINE_RUN_PATTERN.sub("\n\n", text)
    return text.strip()


def _html_to_display_text(raw_html: str) -> str:
    """Paragraph-preserving sibling of _html_to_text, for display only. A
    <br> becomes a single line break; the end of a block-level element
    (paragraph/div/list item/heading/table row/blockquote/section/article —
    the same set _HTML_BLOCK_BOUNDARY_PATTERN already matches) becomes a
    paragraph break. Tag/script/style stripping is otherwise identical to
    _html_to_text, which this never calls or modifies."""
    if not raw_html:
        return ""
    text = _HTML_SCRIPT_STYLE_PATTERN.sub(" ", raw_html)
    text = _HTML_BR_PATTERN.sub("\n", text)
    text = _HTML_BLOCK_BOUNDARY_PATTERN.sub("\n\n", text)
    text = _HTML_TAG_PATTERN.sub(" ", text)
    return _normalize_display_text(text)


def _extract_display_body(payload: dict[str, Any]) -> str:
    """Paragraph-preserving sibling of _extract_body, for Email Summary's
    View Details full email body only. Prefers the sender's text/plain part
    (which already carries their real line breaks) exactly like
    _extract_body does, falling back to the HTML part via
    _html_to_display_text. Never used for classification or matching, and
    never modifies anything _extract_body itself produces/consumes."""
    mime_type = payload.get("mimeType", "")
    if mime_type == "text/plain":
        return _normalize_display_text(_decode_body_part(payload))

    parts = payload.get("parts") or []
    plain_part = next((part for part in parts if part.get("mimeType") == "text/plain"), None)
    if plain_part is not None:
        plain_text = _normalize_display_text(_decode_body_part(plain_part))
        if plain_text:
            return plain_text
        html_part = next((part for part in parts if part.get("mimeType") == "text/html"), None)
        if html_part is not None:
            return _html_to_display_text(_decode_body_part(html_part))
        return plain_text

    for part in parts:
        text = _extract_display_body(part)
        if text:
            return text
    if mime_type == "text/html":
        return _html_to_display_text(_decode_body_part(payload))
    return ""


def _extract_body(payload: dict[str, Any]) -> str:
    mime_type = payload.get("mimeType", "")
    if mime_type == "text/plain":
        return _normalize_extracted_text(_decode_body_part(payload))

    parts = payload.get("parts") or []
    plain_part = next((part for part in parts if part.get("mimeType") == "text/plain"), None)
    if plain_part is not None:
        plain_text = _normalize_extracted_text(_decode_body_part(plain_part))
        html_part = next((part for part in parts if part.get("mimeType") == "text/html"), None)
        if html_part is not None:
            html_text = _html_to_text(_decode_body_part(html_part))
            return _prefer_better_spaced_alternative(plain_text, html_text)
        return plain_text

    for part in parts:
        text = _extract_body(part)
        if text:
            return text
    if mime_type == "text/html":
        return _html_to_text(_decode_body_part(payload))
    return ""


def get_message_raw(access_token: str, message_id: str) -> dict[str, Any] | None:
    try:
        return _gmail_request("GET", access_token, f"/messages/{message_id}", params={"format": "full"})
    except GmailMessageNotFoundError:
        # Safe to log — a message id, never message content/subject/body/
        # tokens. The caller (get_message) already treats None the same as
        # any other unreadable message, so this is purely diagnostic.
        logger.info("gmail_message_not_found message_id=%s", message_id)
        return None
    except GmailApiError:
        return None


def _parse_internal_date(raw: dict[str, Any]) -> datetime | None:
    """Gmail's own record of when the message was received — milliseconds
    since the Unix epoch, as a string, at the top level of the message
    resource (not inside payload.headers). Preferred over the Date header:
    it's Gmail's authoritative timestamp regardless of a sender's
    misconfigured/spoofed/missing Date header. Never raises — malformed or
    missing input just means "try the Date header fallback instead"."""
    internal_date = raw.get("internalDate")
    if not internal_date:
        return None
    try:
        millis = int(internal_date)
    except (TypeError, ValueError):
        return None
    if millis <= 0:
        return None
    try:
        return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_date_header(date_header: str | None) -> datetime | None:
    """Fallback only — the RFC 2822 Date header a sender's mail client set,
    used only when Gmail's own internalDate is unavailable/malformed.
    Never raises on a missing/malformed header."""
    if not date_header:
        return None
    try:
        parsed = parsedate_to_datetime(date_header)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_message(raw: dict[str, Any]) -> ProviderMessage:
    payload = raw.get("payload") or {}
    headers = {h.get("name", "").lower(): h.get("value", "") for h in payload.get("headers") or []}
    # Never the scan's current time — see CURRENT VERIFIED BUG: received_at
    # must reflect when Gmail actually received the message.
    received_at = _parse_internal_date(raw) or _parse_date_header(headers.get("date"))
    return ProviderMessage(
        message_id=raw.get("id", ""),
        thread_id=raw.get("threadId"),
        sender=headers.get("from", ""),
        subject=headers.get("subject", ""),
        received_at=received_at,
        snippet=raw.get("snippet", ""),
        body=_extract_body(payload),
        body_display=_extract_display_body(payload),
    )


def _build_raw_mime(*, to: str, subject: str, body: str, thread_headers: dict[str, str] | None = None) -> str:
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    if thread_headers:
        for key, value in thread_headers.items():
            message[key] = value
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")


def create_draft_raw(access_token: str, *, to: str, subject: str, body: str, thread_id: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"raw": _build_raw_mime(to=to, subject=subject, body=body)}
    if thread_id:
        message["threadId"] = thread_id
    # NEVER users.drafts.send / users.messages.send — drafts.create only.
    return _gmail_request("POST", access_token, "/drafts", json_body={"message": message})


# ---------------------------------------------------------------------------
# EmailProvider implementation
# ---------------------------------------------------------------------------

class GmailEmailProvider(EmailProvider):
    def __init__(self, db: Session, connection: EmailConnection, access_token: str):
        self.db = db
        self.connection = connection
        self.access_token = access_token

    def get_connection_status(self) -> ConnectionStatus:
        return ConnectionStatus(
            connected=self.connection.status == "CONNECTED",
            email_address=self.connection.email_address,
            status=self.connection.status,
            last_scanned_at=self.connection.last_scanned_at.isoformat() if self.connection.last_scanned_at else None,
            error=self.connection.last_error,
        )

    def list_recent_messages(self, *, query: str, max_results: int = 25) -> list[dict[str, Any]]:
        return list_messages(self.access_token, query=query, max_results=max_results)

    def get_message(self, message_id: str) -> ProviderMessage | None:
        raw = get_message_raw(self.access_token, message_id)
        return parse_message(raw) if raw else None

    def create_draft(self, *, to: str, subject: str, body: str, thread_id: str | None = None) -> DraftResult:
        result = create_draft_raw(self.access_token, to=to, subject=subject, body=body, thread_id=thread_id)
        return DraftResult(draft_id=result.get("id", ""), created=bool(result.get("id")))

    def disconnect(self) -> None:
        disconnect(self.db, self.connection.user_id)
