"""Tests for app/services/job_availability_service.py — the safe, bounded
HTTP-based check that answers "is this specific job destination still open
right now?", kept deliberately separate from URL/destination-type
classification (see job_normalization_service).

No real network call is ever made: a mock `requests.Session` is injected
directly (the function accepts an optional `session=`), and
url_safety.assert_public_http_url is patched so even DNS resolution never
touches the real network — matching this repo's existing pattern of
mocking at the url_safety boundary (see test_jsearch_adapter.py).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

from app.services import job_availability_service as availability
from app.services import url_safety


def _response(status_code, body=b"", location=None):
    response = MagicMock()
    response.status_code = status_code
    response.headers = {"Location": location} if location else {}
    response.is_redirect = status_code in (301, 302, 303, 307, 308) and bool(location)
    response.iter_content = MagicMock(return_value=iter([body]) if body else iter([]))
    response.close = MagicMock()
    return response


def _session(*responses_or_exceptions):
    session = MagicMock()
    session.get = MagicMock(side_effect=list(responses_or_exceptions))
    return session


def _check(url, *responses_or_exceptions):
    """Runs check_url_availability with url_safety mocked to always accept
    (never touches real DNS) and a mock session returning the given
    sequence of responses/exceptions, one per HTTP call."""
    with patch("app.services.job_availability_service.url_safety.assert_public_http_url", return_value=None):
        return availability.check_url_availability(url, session=_session(*responses_or_exceptions))


# ---------------------------------------------------------------------------
# 1-2: direct employer / ATS HTTP 200 + normal content -> ACTIVE
# ---------------------------------------------------------------------------

def test_http_200_normal_employer_content_is_active():
    result = _check("https://careers.example-corp.com/job/1", _response(200, b"<html>Senior Engineer role. Apply now!</html>"))
    assert result["status"] == "ACTIVE"
    assert result["reason"] == "http_200"


def test_http_200_normal_ats_content_is_active():
    result = _check("https://careers-someemployer.icims.com/jobs/1/job", _response(200, b"<html>Job description details.</html>"))
    assert result["status"] == "ACTIVE"


# ---------------------------------------------------------------------------
# 3-4: confirmed-gone HTTP statuses -> EXPIRED
# ---------------------------------------------------------------------------

def test_http_404_is_expired():
    result = _check("https://careers.example-corp.com/job/1", _response(404))
    assert result["status"] == "EXPIRED"
    assert result["reason"] == "http_404"


def test_http_410_is_expired():
    result = _check("https://careers.example-corp.com/job/1", _response(410))
    assert result["status"] == "EXPIRED"
    assert result["reason"] == "http_410"


# ---------------------------------------------------------------------------
# 5-6: HTTP 200 with a generic soft-expiry phrase -> EXPIRED (never a
# hardcoded employer's specific wording)
# ---------------------------------------------------------------------------

def test_http_200_with_job_currently_unavailable_phrase_is_expired():
    body = b"<html><body>Sorry, this job is currently unavailable.</body></html>"
    result = _check("https://careers.example-corp.com/job/1", _response(200, body))
    assert result["status"] == "EXPIRED"
    assert "job is currently unavailable" in result["reason"]


def test_http_200_with_position_has_been_filled_phrase_is_expired():
    body = b"<html><body>This position has been filled.</body></html>"
    result = _check("https://careers.example-corp.com/job/1", _response(200, body))
    assert result["status"] == "EXPIRED"
    assert "position has been filled" in result["reason"]


def test_expiry_phrase_detection_is_case_insensitive():
    body = b"<html>JOB IS NO LONGER AVAILABLE</html>"
    result = _check("https://careers.example-corp.com/job/1", _response(200, body))
    assert result["status"] == "EXPIRED"


# ---------------------------------------------------------------------------
# 7-9: ambiguous HTTP statuses -> UNKNOWN, never EXPIRED — an employer must
# never be penalized for bot protection / rate limiting / a transient error.
# ---------------------------------------------------------------------------

def test_http_403_is_unknown_not_expired():
    result = _check("https://careers.example-corp.com/job/1", _response(403))
    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "http_403"


def test_http_429_is_unknown_not_expired():
    result = _check("https://careers.example-corp.com/job/1", _response(429))
    assert result["status"] == "UNKNOWN"


def test_http_500_is_unknown_not_expired():
    result = _check("https://careers.example-corp.com/job/1", _response(500))
    assert result["status"] == "UNKNOWN"


def test_http_401_is_unknown_not_expired():
    result = _check("https://careers.example-corp.com/job/1", _response(401))
    assert result["status"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# 10: timeout / network failures -> UNKNOWN, never a crash
# ---------------------------------------------------------------------------

def test_timeout_is_unknown():
    result = _check("https://careers.example-corp.com/job/1", requests.Timeout("timed out"))
    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "timeout"


def test_connection_error_is_unknown():
    result = _check("https://careers.example-corp.com/job/1", requests.ConnectionError("dns failure"))
    assert result["status"] == "UNKNOWN"
    assert "network_error" in result["reason"]


# ---------------------------------------------------------------------------
# 11: unsafe redirect target -> rejected / UNKNOWN, never followed
# ---------------------------------------------------------------------------

def test_unsafe_redirect_target_is_rejected():
    session = _session(_response(302, location="http://169.254.169.254/latest/meta-data/"))
    call_count = {"n": 0}

    def fake_assert(url):
        call_count["n"] += 1
        if "169.254.169.254" in url:
            raise url_safety.UnsafeUrlError("Private or internal network addresses are not allowed.")

    with patch("app.services.job_availability_service.url_safety.assert_public_http_url", side_effect=fake_assert):
        result = availability.check_url_availability("https://careers.example-corp.com/job/1", session=session)
    assert result["status"] == "UNKNOWN"
    assert "unsafe_url" in result["reason"]
    # The unsafe redirect target must never actually be fetched.
    assert session.get.call_count == 1


def test_initial_unsafe_url_is_rejected_without_any_request():
    session = _session()
    with patch("app.services.job_availability_service.url_safety.assert_public_http_url", side_effect=url_safety.UnsafeUrlError("nope")):
        result = availability.check_url_availability("http://localhost/job/1", session=session)
    assert result["status"] == "UNKNOWN"
    session.get.assert_not_called()


# ---------------------------------------------------------------------------
# 12: safe redirect to another specific job page -> follow + classify the
# final destination (ACTIVE when that page is genuinely open)
# ---------------------------------------------------------------------------

def test_safe_redirect_to_another_specific_job_page_is_canonicalized_and_checked():
    result = _check(
        "https://careers.example-corp.com/job/old-id",
        _response(302, location="https://careers.example-corp.com/job/new-id"),
        _response(200, b"<html>Full job description here.</html>"),
    )
    assert result["status"] == "ACTIVE"
    assert result["final_url"] == "https://careers.example-corp.com/job/new-id"


def test_too_many_redirects_is_unknown():
    responses = [_response(302, location=f"https://careers.example-corp.com/job/{i}") for i in range(availability.MAX_REDIRECTS + 2)]
    result = _check("https://careers.example-corp.com/job/0", *responses)
    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "too_many_redirects"


def test_redirect_without_location_header_is_unknown():
    result = _check("https://careers.example-corp.com/job/1", _response(302, location=None))
    assert result["status"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# Specificity-aware response evidence — root-cause regression for a URL
# that LOOKS like one specific posting (per classify_url_specificity) but
# actually serves a generic careers/search page. A real employer domain
# returning HTTP 200 is not sufficient evidence of ACTIVE on its own (never
# hardcodes the specific employer this was diagnosed against — a fictional
# domain reproduces the same URL shape: a job-detail URL with a job-id
# query param that redirects to, or renders, a generic results page).
# ---------------------------------------------------------------------------

def test_specific_url_redirecting_to_generic_page_is_unknown_not_active():
    result = _check(
        "https://www.example.jobs/JobDetails?jobid=R266225&jobtitle=Java-Developer",
        _response(302, location="https://www.example.jobs/JobSearchResults?keyword=java"),
        _response(200, b"<html>Search results for your query.</html>"),
    )
    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "redirected_to_generic_page"


def test_specific_url_rendering_generic_listing_text_without_redirect_is_unknown():
    """Same failure mode, no redirect involved: the SAME URL returns 200 but
    the page itself is a generic listing (e.g. a client-rendered fallback
    when the specific posting can't be resolved)."""
    body = b"<html>Search Jobs<br/>Search by title or keyword to find your next role.</html>"
    result = _check("https://www.example.jobs/JobDetails?jobid=R266225", _response(200, body))
    assert result["status"] == "UNKNOWN"
    assert "generic_page_text" in result["reason"]


def test_specific_url_redirecting_to_another_specific_url_stays_active():
    """A redirect between two equally-specific URLs (e.g. a canonical
    redirect) is NOT evidence of anything wrong — stays ACTIVE."""
    result = _check(
        "https://careers.example.com/job/48213",
        _response(302, location="https://careers.example.com/job/48213/software-engineer"),
        _response(200, b"<html>Full job description with responsibilities and requirements.</html>"),
    )
    assert result["status"] == "ACTIVE"


def test_generic_url_returning_200_is_still_active_here():
    """The specific-vs-generic response-evidence check only applies when
    the ORIGINAL url already looked specific — an originally-generic URL
    (e.g. a careers homepage) legitimately returning 200 just means the
    site is up; classify_url_specificity's own GENERIC_EMPLOYER_PAGE
    result (checked separately, in resolve_apply_action) is what keeps
    this from ever being labeled Apply, not this availability check."""
    result = _check("https://careers.example.com/jobs", _response(200, b"<html>Browse all open roles.</html>"))
    assert result["status"] == "ACTIVE"


# ---------------------------------------------------------------------------
# Miscellaneous safety/behavior
# ---------------------------------------------------------------------------

def test_no_url_is_unknown_without_any_request():
    result = availability.check_url_availability(None)
    assert result == {"status": "UNKNOWN", "reason": "no_url", "final_url": None}


def test_unexpected_exception_never_crashes_caller():
    session = MagicMock()
    session.get = MagicMock(side_effect=RuntimeError("boom"))
    with patch("app.services.job_availability_service.url_safety.assert_public_http_url", return_value=None):
        result = availability.check_url_availability("https://careers.example-corp.com/job/1", session=session)
    assert result["status"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# TTL / staleness
# ---------------------------------------------------------------------------

def test_is_stale_true_when_never_checked():
    assert availability.is_stale(None) is True


def test_is_stale_false_within_ttl(monkeypatch):
    monkeypatch.setenv("JOB_AVAILABILITY_TTL_MINUTES", "360")
    recent = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert availability.is_stale(recent) is False


def test_is_stale_true_outside_ttl(monkeypatch):
    monkeypatch.setenv("JOB_AVAILABILITY_TTL_MINUTES", "360")
    old = datetime.now(timezone.utc) - timedelta(minutes=500)
    assert availability.is_stale(old) is True


def test_apply_result_mutates_row_and_sets_checked_at():
    row = SimpleNamespace(availability_status="UNKNOWN", availability_checked_at=None, availability_reason=None)
    availability.apply_result(row, {"status": "EXPIRED", "reason": "http_404"})
    assert row.availability_status == "EXPIRED"
    assert row.availability_reason == "http_404"
    assert row.availability_checked_at is not None
