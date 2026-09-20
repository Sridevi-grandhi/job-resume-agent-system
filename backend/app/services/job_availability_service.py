"""Safe, bounded availability check for a DIRECT_EMPLOYER/DIRECT_ATS job URL.

Answers exactly one narrow question: is this specific posting still open
right now? Deliberately kept separate from URL classification
(job_normalization_service.classify_url_domain / resolve_application_destination
decide DESTINATION TYPE — is this a genuine employer/ATS URL at all — which
never changes based on this module's result; see the DiscoveredJob model's
comment for why TYPE and AVAILABILITY must never be conflated).

Not a scraper: the minimum safe HTTP validation necessary; never runs
JavaScript, submits a form, authenticates, or attempts to bypass bot
protection. Reuses url_safety's SSRF protections (public-IP-only, HTTP(S)
only, no embedded credentials) for the initial URL and every redirect hop.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests

from app.services import url_safety
from app.services.job_normalization_service import classify_url_specificity

logger = logging.getLogger(__name__)

USER_AGENT = "CareerBoostAI-AvailabilityCheck/1.0 (+educational project)"
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 10
MAX_REDIRECTS = 3
MAX_BODY_BYTES = 300_000  # enough for phrase detection — never a full asset download

# Confirmed-gone HTTP responses.
_EXPIRED_STATUS_CODES = {404, 410}

# Ambiguous responses are NEVER treated as expired — an employer's careers
# site blocking automated requests (bot protection, rate limiting, a
# transient server error) is not evidence the job itself is gone.
_AMBIGUOUS_STATUS_CODES = {401, 403, 429}

# Generic soft-expiry phrases — a real employer page can return HTTP 200 for
# a "this posting is gone" message instead of a 404/410. Deliberately
# generic (never a specific employer's wording).
_EXPIRY_PHRASES = (
    "job is currently unavailable",
    "job is no longer available",
    "position is no longer available",
    "this job has expired",
    "job posting has expired",
    "position has been filled",
    "job has been filled",
    "job is closed",
    "position is closed",
    "job no longer exists",
    "posting no longer exists",
)

# Response-evidence signal for a URL that LOOKED like a specific job page
# (per classify_url_specificity) but actually renders a generic
# search/listing page — e.g. a broken deep link whose server quietly falls
# back to "browse all jobs" instead of erroring. Deliberately generic
# wording, never a specific employer's copy.
_GENERIC_PAGE_PHRASES = (
    "search jobs", "search by title or keyword", "search by keyword",
    "jobs available", "browse all jobs", "job search results",
    "showing all jobs", "search results for",
)


def ttl_minutes() -> int:
    return max(15, int(os.getenv("JOB_AVAILABILITY_TTL_MINUTES") or "360"))


def is_stale(checked_at: datetime | None) -> bool:
    """True when a cached availability result is missing or old enough to
    need revalidation — used both for the payload's fast-path (trust a
    recent result) and the Apply-click recheck (Step 6)."""
    if checked_at is None:
        return True
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - checked_at > timedelta(minutes=ttl_minutes())


def _detect_expiry_phrase(text: str) -> str | None:
    lowered = text.lower()
    for phrase in _EXPIRY_PHRASES:
        if phrase in lowered:
            return phrase
    return None


def _detect_generic_listing_phrase(text: str) -> str | None:
    lowered = text.lower()
    for phrase in _GENERIC_PAGE_PHRASES:
        if phrase in lowered:
            return phrase
    return None


def _bounded_body_text(response: requests.Response) -> str:
    """Reads at most MAX_BODY_BYTES of the response body for phrase
    detection — never a full large-asset download."""
    chunks: list[bytes] = []
    total = 0
    try:
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if total >= MAX_BODY_BYTES:
                break
    except Exception:  # noqa: BLE001 — a body-read failure just means less signal, not fatal
        pass
    return b"".join(chunks).decode("utf-8", errors="ignore")


def check_url_availability(url: str | None, session: requests.Session | None = None) -> dict[str, Any]:
    """Returns {"status": "ACTIVE" | "EXPIRED" | "UNKNOWN", "reason": str,
    "final_url": str | None}. Never raises — every failure mode (unsafe URL,
    timeout, network error, unexpected exception) becomes UNKNOWN with a
    diagnostic reason, never a crash and never a false EXPIRED."""
    if not url:
        return {"status": "UNKNOWN", "reason": "no_url", "final_url": None}

    owns_session = session is None
    session = session or requests.Session()
    current_url = url
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    try:
        for hop in range(MAX_REDIRECTS + 1):
            try:
                url_safety.assert_public_http_url(current_url)
            except url_safety.UnsafeUrlError as error:
                return {"status": "UNKNOWN", "reason": f"unsafe_url:{error}", "final_url": None}

            try:
                response = session.get(
                    current_url, headers=headers, allow_redirects=False,
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), stream=True,
                )
            except requests.Timeout:
                return {"status": "UNKNOWN", "reason": "timeout", "final_url": current_url}
            except requests.RequestException as error:
                return {"status": "UNKNOWN", "reason": f"network_error:{type(error).__name__}", "final_url": current_url}

            if response.is_redirect or response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                response.close()
                if not location:
                    return {"status": "UNKNOWN", "reason": "redirect_without_location", "final_url": current_url}
                if hop == MAX_REDIRECTS:
                    return {"status": "UNKNOWN", "reason": "too_many_redirects", "final_url": current_url}
                current_url = urljoin(current_url, location)
                continue

            status_code = response.status_code
            if status_code in _EXPIRED_STATUS_CODES:
                response.close()
                return {"status": "EXPIRED", "reason": f"http_{status_code}", "final_url": current_url}
            if status_code in _AMBIGUOUS_STATUS_CODES or status_code >= 500:
                response.close()
                return {"status": "UNKNOWN", "reason": f"http_{status_code}", "final_url": current_url}
            if status_code == 200:
                body = _bounded_body_text(response)
                response.close()
                phrase = _detect_expiry_phrase(body)
                if phrase:
                    return {"status": "EXPIRED", "reason": f"page_text:{phrase}", "final_url": current_url}
                # A URL that LOOKED like one specific posting but actually
                # serves a generic careers/search page (response evidence,
                # not just URL text) is not proof the job is active — it's
                # proof we can't confirm it. Two ways this shows up: the
                # server redirected us to a structurally generic URL, or the
                # SAME URL rendered a generic listing page (e.g. a
                # client-rendered "no results, browse all jobs" fallback).
                # Never trusts a real employer's 200 blindly (see
                # classify_url_specificity's docstring).
                if classify_url_specificity(url) == "SPECIFIC_JOB":
                    if current_url != url and classify_url_specificity(current_url) != "SPECIFIC_JOB":
                        return {"status": "UNKNOWN", "reason": "redirected_to_generic_page", "final_url": current_url}
                    generic_phrase = _detect_generic_listing_phrase(body)
                    if generic_phrase:
                        return {"status": "UNKNOWN", "reason": f"generic_page_text:{generic_phrase}", "final_url": current_url}
                return {"status": "ACTIVE", "reason": "http_200", "final_url": current_url}
            # Any other status we didn't special-case — conservative UNKNOWN
            # rather than a guess.
            response.close()
            return {"status": "UNKNOWN", "reason": f"http_{status_code}", "final_url": current_url}

        return {"status": "UNKNOWN", "reason": "too_many_redirects", "final_url": current_url}
    except url_safety.UnsafeUrlError as error:
        return {"status": "UNKNOWN", "reason": f"unsafe_url:{error}", "final_url": None}
    except Exception as error:  # noqa: BLE001 — availability checks must never crash the caller
        logger.warning(
            "AVAILABILITY_CHECK_FAILED url_host=%s error=%s",
            (urlsplit(current_url).hostname or ""), type(error).__name__,
        )
        return {"status": "UNKNOWN", "reason": "check_failed", "final_url": None}
    finally:
        if owns_session:
            session.close()


def apply_result(discovered_job: Any, result: dict[str, Any]) -> None:
    """Mutates a DiscoveredJob row in place with a fresh check result — the
    caller is responsible for db.commit()."""
    discovered_job.availability_status = result.get("status") or "UNKNOWN"
    discovered_job.availability_checked_at = datetime.now(timezone.utc)
    discovered_job.availability_reason = (result.get("reason") or "")[:300]
