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

import json
import logging
import os
import re
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


def _title_tokens(title: str | None) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9+#.]+", (title or "").lower()) if len(word) > 1}


def _titles_strongly_match(expected_title: str | None, structured_title: str | None) -> bool:
    """Conservative title match for page-owned structured metadata.

    This is intentionally evidence only, not a replacement for the shared
    verifier's employer/title checks.  It is used only after that verifier
    has already accepted the URL as a safe, employer/ATS specific-job page.
    """
    expected = _title_tokens(expected_title)
    actual = _title_tokens(structured_title)
    if not expected or not actual:
        return False
    overlap = len(expected & actual)
    return overlap / len(expected) >= 0.75 and overlap / len(actual) >= 0.6


def _canonical_page_url(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlsplit(str(value))
    if not parsed.scheme or not parsed.hostname:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.hostname.lower()}{parsed.path.rstrip('/') or '/'}"


def _iter_json_ld_nodes(value):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _iter_json_ld_nodes(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_json_ld_nodes(nested)


def _is_job_posting(node: dict[str, Any]) -> bool:
    kind = node.get("@type")
    return kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind)


def _jobposting_confirms_specific_page(body: str, expected_title: str | None, current_url: str) -> bool:
    """Find a page-owned JobPosting that confirms this exact job detail URL.

    A generic careers/search page can contain a list of jobs and even mention
    the expected title.  Therefore a title match alone is insufficient: the
    structured JobPosting must also name this canonical page URL or carry an
    identifier visible in it.  Malformed/absent JSON-LD remains no evidence.
    """
    if not expected_title:
        return False
    current_canonical = _canonical_page_url(current_url)
    if not current_canonical:
        return False
    scripts = re.findall(
        r"<script\b[^>]*\btype\s*=\s*['\"]application/ld\+json['\"][^>]*>(.*?)</script\s*>",
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for script in scripts:
        try:
            payload = json.loads(script.strip())
        except (TypeError, ValueError):
            continue
        for node in _iter_json_ld_nodes(payload):
            if not isinstance(node, dict) or not _is_job_posting(node):
                continue
            if not _titles_strongly_match(expected_title, node.get("title")):
                continue
            structured_url = _canonical_page_url(node.get("url"))
            if structured_url and structured_url == current_canonical:
                return True
            identifier = node.get("identifier")
            if isinstance(identifier, dict):
                identifier = identifier.get("value") or identifier.get("name")
            identifier = str(identifier or "").strip().lower()
            if len(identifier) >= 4 and identifier in current_url.lower():
                return True
    return False


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


def check_url_availability(
    url: str | None, session: requests.Session | None = None, *, expected_title: str | None = None,
) -> dict[str, Any]:
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
                original_specificity = classify_url_specificity(url)
                if original_specificity == "SPECIFIC_JOB":
                    if current_url != url and classify_url_specificity(current_url) != "SPECIFIC_JOB":
                        return {"status": "UNKNOWN", "reason": "redirected_to_generic_page", "final_url": current_url}
                    generic_phrase = _detect_generic_listing_phrase(body)
                    if generic_phrase:
                        # A generic-looking page is still not assumed active.
                        # The one safe exception is a page-owned JobPosting
                        # that confirms BOTH this exact detail URL and the
                        # expected job title supplied by the shared verifier.
                        if _jobposting_confirms_specific_page(body, expected_title, current_url):
                            return {"status": "ACTIVE", "reason": "jobposting_title_and_url_match", "final_url": current_url}
                        return {"status": "UNKNOWN", "reason": f"generic_page_text:{generic_phrase}", "final_url": current_url}
                elif original_specificity == "GENERIC_EMPLOYER_PAGE":
                    # A careers homepage or listing/search URL may prove the
                    # employer site is reachable, but cannot prove this
                    # particular opportunity is open.  The action resolver
                    # can still offer a safe employer-site fallback when one
                    # has already been independently verified as direct.
                    return {"status": "UNKNOWN", "reason": "generic_or_unspecific_url", "final_url": current_url}
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
    current = getattr(discovered_job, "application_url", None) or getattr(discovered_job, "direct_url", None)
    final = result.get("final_url")
    if final and final != current:
        from app.services.job_enrichment_service import EnrichmentCandidateResult, _candidate_passes_verification
        candidate = EnrichmentCandidateResult(final, matched_title=getattr(discovered_job, "title", None),
                                              matched_company=getattr(discovered_job, "company", None))
        passes, reason = _candidate_passes_verification(candidate, discovered_job, allow_generic=True)
        if passes:
            discovered_job.application_url = final
            discovered_job.direct_url = final
            current = final
        else:
            result = {**result, "status": "UNKNOWN", "reason": "final_" + reason}
    discovered_job.availability_status = result.get("status") or "UNKNOWN"
    discovered_job.verified_destination_url = current
    discovered_job.availability_checked_at = datetime.now(timezone.utc)
    discovered_job.availability_reason = (result.get("reason") or "")[:300]
