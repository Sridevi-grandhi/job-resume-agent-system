"""SerpAPI Google Jobs client — Phase 3's first real BroadSearchClient
implementation (see broad_search_enrichment_provider.py for the generic
interface this satisfies). Activated only when an operator sets
JOB_ENRICHMENT_BROAD_PROVIDER=serpapi_google_jobs and
JOB_ENRICHMENT_BROAD_API_KEY — a deployment decision, never made or
persisted by this code, and backend/.env is never touched here.

Uses SerpAPI's Google Jobs ENGINE specifically (engine=google_jobs) — a
structured job-search result, never generic Google web search.

Quota discipline (this is expected to run against a small/free SerpAPI
plan): exactly ONE HTTP request per search() call — no automatic
pagination, no multi-page fetch. At most one retry, and only for a
transient CONNECTION-level failure (DNS/connect timeout) — never for a
429 (rate limit), 4xx, or 5xx, which are terminal for this one attempt;
retrying those would multiply load on an already-struggling or
already-exhausted quota, which is the opposite of "small free plan"
discipline.

Never lets a Google Jobs result "become Apply" on its own: this client only
inspects a result's own apply_options/related_links, classifies each link's
domain via the EXISTING classify_url_domain (never a second aggregator
list, never duplicated logic), and forwards only the employer/ATS-looking
ones — ordered employer-first — as raw candidate dicts.
job_enrichment_service.enrich_job() independently re-verifies every one of
them (safety, specificity, identity, availability) before any is ever
persisted.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import requests

from app.services import url_safety
from app.services.broad_search_enrichment_provider import broad_provider_api_key
from app.services.job_enrichment_service import EnrichmentQuery
from app.services.job_normalization_service import classify_url_domain
from app.services.job_enrichment_context import record_event

logger = logging.getLogger(__name__)

API_URL = "https://serpapi.com/search.json"
ENGINE = "google_jobs"
USER_AGENT = "CareerBoostAI-Enrichment/1.0 (+educational project)"
TIMEOUT = (5, 12)


def _build_query_text(query: EnrichmentQuery) -> str:
    """A narrow query built specifically for enrichment (confirming ONE
    already-known posting), never a broad discovery search — quoted title
    and company so Google Jobs favors an exact-phrase match over a loose
    keyword one (e.g. '"Senior Java Developer" "Example Corp" Dallas TX')."""
    parts: list[str] = []
    if query.title:
        parts.append('"' + re.sub(r'\s+', ' ', query.title.replace('"', ' ')).strip() + '"')
    if query.company:
        parts.append('"' + re.sub(r'\s+', ' ', query.company.replace('"', ' ')).strip() + '"')
    if query.location and query.location.strip().lower() not in {"remote", "anywhere", "not specified", "usa", "united states"}:
        parts.append(query.location)
    return " ".join(parts)


def _apply_option_links(job: dict[str, Any]) -> list[dict[str, Any]]:
    """SerpAPI Google Jobs normally supplies `apply_options` (a list of
    {"title": <publisher>, "link": <url>}); falls back to `related_links`
    if that's absent. A job with neither (Task 8's "missing apply
    options") contributes nothing — never guessed at."""
    options = job.get("apply_options")
    if isinstance(options, list) and options:
        return [option for option in options if isinstance(option, dict)]
    related = job.get("related_links")
    if isinstance(related, list):
        return [option for option in related if isinstance(option, dict)]
    return []


def _job_to_candidates(job: dict[str, Any]) -> list[dict[str, Any]]:
    """Task 4: inspects a Google Jobs result's own apply options and keeps
    only the ones an employer/ATS domain check (classify_url_domain —
    reused, never reimplemented) actually classifies as "employer" or
    "ats" — LinkedIn/Indeed/Dice/ZipRecruiter/other aggregator links, and
    anything unclassifiable, are dropped here rather than forwarded as
    false candidates. Employer-classified links are ordered before
    ATS-classified ones (Task 4's stated priority); the shared
    verification pipeline still independently re-classifies and matches
    every one that IS forwarded."""
    title = job.get("title")
    company = job.get("company_name")
    location = job.get("location")

    scored: list[tuple[int, dict[str, Any]]] = []
    for option in _apply_option_links(job):
        link = option.get("link")
        if not link or not isinstance(link, str):
            continue
        domain_class = classify_url_domain(link, employer_name=company)
        if domain_class not in ("employer", "ats"):
            continue
        rank = 0 if domain_class == "employer" else 1
        scored.append((rank, {
            "url": link, "title": title, "company": company, "location": location,
            "source_metadata": {
                "via": job.get("via"), "job_id": job.get("job_id"), "apply_option_title": option.get("title"),
            },
        }))
    scored.sort(key=lambda item: item[0])
    return [candidate for _rank, candidate in scored]


class SerpApiGoogleJobsClient:
    """BroadSearchClient implementation — see module docstring."""

    def __init__(self, *, api_key: str | None = None, session: requests.Session | None = None):
        self._api_key = api_key if api_key is not None else broad_provider_api_key()
        self._session = session

    def search(self, query: EnrichmentQuery) -> list[dict[str, Any]]:
        # Redundant with broad_search_provider's own gate — defense in
        # depth so this client alone never makes a keyless request even if
        # called directly (Task 6/7).
        if not self._api_key:
            return []
        if not all((value or "").replace('"', ' ').strip() for value in (query.title, query.company)):
            return []

        params: dict[str, Any] = {"engine": ENGINE, "q": _build_query_text(query), "api_key": self._api_key}
        # Provider job locations are free text, not validated Google geotargets.
        # Keep useful location text in q; do not submit it as API `location`.

        session = self._session or requests.Session()
        owns_session = self._session is None
        try:
            record_event("serpapi_attempts")
            response = self._request_once_with_bounded_retry(session, params)
            if response is None:
                return []
            if response.status_code != 200:
                # 401/403/429/5xx are all terminal for this one attempt —
                # never retried, never treated as EXPIRED, always just "no
                # candidates this time" (Task 8).
                logger.warning("serpapi_google_jobs_http_error status=%s", response.status_code)
                record_event("serpapi_failed", http_status=response.status_code)
                # Safe categorical reason only; never log the response/error text.
                try:
                    raw_error = str(response.json().get("error", "")).lower()
                except (ValueError, AttributeError):
                    raw_error = ""
                code = "invalid_location" if "location" in raw_error else "invalid_request" if response.status_code == 400 else "rate_limited" if response.status_code == 429 else "provider_http_error"
                record_event("serpapi_error_reason", code=code)
                return []
            try:
                payload = response.json()
            except ValueError:
                logger.warning("serpapi_google_jobs_malformed_json")
                return []
            jobs = payload.get("jobs_results") if isinstance(payload, dict) else None
            if not isinstance(jobs, list):
                return []
            candidates: list[dict[str, Any]] = []
            for job in jobs:
                if isinstance(job, dict):
                    candidates.extend(_job_to_candidates(job))
            record_event("serpapi_response", candidates=len(candidates))
            return candidates
        finally:
            if owns_session:
                session.close()

    def _request_once_with_bounded_retry(self, session: requests.Session, params: dict[str, Any]):
        """Exactly one HTTP request, plus at most one retry — and ONLY for
        a transient connection-level failure (Task 7/20: no automatic
        pagination, one bounded retry at most). A timeout, or any other
        request exception, ends the attempt immediately without retrying."""
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        for attempt in range(2):
            try:
                return url_safety.safe_get(session, API_URL, params=params, headers=headers, timeout=TIMEOUT)
            except requests.Timeout:
                logger.warning("serpapi_google_jobs_timeout")
                return None
            except requests.ConnectionError:
                if attempt == 0:
                    continue
                logger.warning("serpapi_google_jobs_connection_failed_after_retry")
                return None
            except Exception as error:  # noqa: BLE001 — any other failure ends this attempt, never crashes the caller
                logger.warning("serpapi_google_jobs_request_failed error=%s", type(error).__name__)
                return None
        return None
