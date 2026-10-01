"""JSearch (OpenWeb Ninja) broad job provider adapter.

Unlike Greenhouse/Lever, this adapter is not handed a manually-configured
list of companies — it discovers jobs across whatever real employers JSearch
returns for each user-preference-derived query (see
app.services.broad_provider_query_service). This is the "search thousands of
companies without configuring them one by one" provider.

Docs: https://www.openwebninja.com/api/jsearch (search-v2 endpoint).
Auth: `x-api-key` header. No API key configured -> the adapter contributes
no jobs and never crashes the backend (see `enabled`); every call site logs
JSEARCH_NOT_CONFIGURED instead of raising.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import requests

from app.job_sources.base import BroadJobProviderAdapter, SourceHealth
from app.services import url_safety
from app.services.employer_career_service import matching_career_url
from app.services.job_normalization_service import (
    build_normalized_job,
    rank_apply_candidates,
    select_apply_urls_from_candidates,
    strip_html,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openwebninja.com/jsearch/search-v2"
USER_AGENT = "CareerBoostAI-JobDiscovery/1.0 (+educational project)"
TIMEOUT = (5, 15)


def api_key() -> str:
    return (os.getenv("JSEARCH_API_KEY") or "").strip()


def base_url() -> str:
    return (os.getenv("JSEARCH_API_BASE_URL") or "").strip() or DEFAULT_BASE_URL


def max_pages_per_query() -> int:
    return max(1, min(3, int(os.getenv("JSEARCH_MAX_PAGES_PER_QUERY") or "1")))


def max_retries() -> int:
    return max(0, min(3, int(os.getenv("JSEARCH_MAX_RETRIES") or "1")))


def lookup_max_per_cycle() -> int:
    return max(1, int(os.getenv("JSEARCH_LOOKUP_MAX_PER_CYCLE") or "25"))


def _build_query_string(spec: dict[str, str]) -> str:
    """Builds a JSearch free-text query from one preference-derived spec —
    never a hardcoded role/company. E.g. {"job_title": "SDET",
    "location": "Austin, TX"} -> "SDET jobs in Austin, TX"."""
    title = (spec.get("job_title") or "").strip()
    if not title:
        return ""
    location = (spec.get("location") or "").strip()
    if location and location.lower() not in {"remote", "anywhere"}:
        return f"{title} jobs in {location}"
    return f"{title} jobs"


def _apply_options_from_raw(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    """Builds the full apply_options-shaped candidate list from a raw
    JSearch record — job_apply_link first (as its own option), then every
    entry in apply_options. Preserves ALL of them for
    rank_apply_candidates() rather than inspecting only the first; only
    entries with a non-empty apply_link are kept."""
    options: list[dict[str, Any]] = []
    primary_link = raw.get("job_apply_link")
    if primary_link:
        options.append({
            "apply_link": primary_link,
            "is_direct": bool(raw.get("job_apply_is_direct")),
            "publisher": raw.get("job_publisher"),
        })
    for option in raw.get("apply_options") or []:
        if isinstance(option, dict) and option.get("apply_link"):
            options.append(option)
    return options, primary_link


def _pick_apply_urls(raw: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Chooses (application_url, direct_url, source_url) from JSearch's
    job_apply_link/job_apply_is_direct/apply_options — inspecting the whole
    apply_options array rather than blindly taking the first entry, via the
    shared candidate-resolution pipeline (rank_apply_candidates /
    select_apply_urls_from_candidates in job_normalization_service —
    reused by any future adapter with the same apply_options shape, and by
    job_enrichment_service's own candidate verification).

    Classification is semantic (classify_url_domain), not merely whichever
    field an option came from or what JSearch's own is_direct flag claims —
    verified against live data, job_apply_is_direct was False even for
    options whose URL was a genuine employer career page or ATS-hosted
    application, which is exactly why every JSearch job was landing in
    source_url only. A known aggregator domain (LinkedIn, Indeed, Dice, ...)
    is always kept out of application_url/direct_url even if flagged
    is_direct=True — the flag is corroborating evidence, not proof, and an
    aggregator listing must never be presented as a direct Apply. An option
    with an unclassifiable domain still falls back to trusting is_direct, so
    a provider-flagged direct link to an unrecognized-but-real employer/ATS
    domain is still preserved (never discarded, matching prior behavior).
    Never fabricates a URL — is_direct=False options with no employer/ATS
    match are kept as the last-resort listing rather than discarded, so
    provenance is preserved even when nothing verified-direct is available
    (see resolve_application_destination for how these three fields are
    turned into the final Apply-vs-View-Listing decision)."""
    options, primary_link = _apply_options_from_raw(raw)
    candidates = rank_apply_candidates(
        options, employer_website=raw.get("employer_website"), employer_name=raw.get("employer_name"),
    )
    return select_apply_urls_from_candidates(candidates, fallback_source_url=primary_link)


def _extract_jobs_and_cursor(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    """Pulls (jobs, next_cursor) out of a search-v2 response.

    The real API's `data` is an OBJECT — {"jobs": [...], "cursor": "..."} —
    not a bare list. A bare-list `data` is also accepted for backward
    compatibility with older mocks/tests, but is never the primary shape and
    never carries a cursor (single page only). Anything else malformed
    yields no jobs and no cursor rather than raising.
    """
    if not isinstance(payload, dict):
        return [], None
    data = payload.get("data")
    if isinstance(data, dict):
        jobs = data.get("jobs")
        cursor = data.get("cursor")
        jobs = jobs if isinstance(jobs, list) else []
        cursor = cursor.strip() if isinstance(cursor, str) and cursor.strip() else None
        return jobs, cursor
    if isinstance(data, list):
        return data, None
    return [], None


def _parse_posted_at(raw: dict[str, Any]):
    timestamp = raw.get("job_posted_at_timestamp")
    if timestamp:
        try:
            return datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
        except (ValueError, TypeError, OSError):
            pass
    posted_at = raw.get("job_posted_at")
    if posted_at:
        try:
            return datetime.fromisoformat(str(posted_at).replace("Z", "+00:00"))
        except ValueError:
            pass
    return None


def _lookup_company_matches(raw_employer_name: str, company: str) -> bool:
    from app.services.provider_apply_resolver import _company_slug

    candidate_slug = _company_slug(raw_employer_name)
    company_slug = _company_slug(company)
    if not candidate_slug or not company_slug:
        return False
    return (
        candidate_slug == company_slug
        or candidate_slug.startswith(company_slug)
        or company_slug.startswith(candidate_slug)
    )


def lookup_direct_apply(title: str, company: str, location: str = "") -> dict[str, Any]:
    """Targeted, single-page JSearch lookup for a job another provider (Dice,
    Adzuna, ...) only ever supplied as a listing — tries to find that SAME
    posting's own employer/ATS apply link so a provider-only job can be
    promoted to a real "Apply on Company Site" destination.

    Never invents a URL: a result is only ever returned after (1) its
    employer_name matches `company` and its title matches `title`
    (job_enrichment_service._titles_match — same bidirectional-overlap check
    used everywhere else in this pipeline), (2) its own apply_options/
    job_apply_link are ranked through the exact same shared pipeline
    `_pick_apply_urls` uses, and (3) the winning URL passes
    provider_apply_resolver.is_safe_employer_or_ats_destination against this
    SAME (title, company) — never merely "some real employer/ATS site".

    Returns {"url": str | None, "rate_limited": bool}. Never raises — any
    failure (not configured, network error, bad response, no match) is just
    "no better destination found than the listing already on file", not an
    error the caller needs to handle specially, EXCEPT rate_limited=True,
    which the caller uses to stop attempting further lookups this cycle.
    """
    if not api_key() or not title or not company:
        return {"url": None, "rate_limited": False}

    query = " ".join(part for part in (title, company, location) if part).strip()
    if not query:
        return {"url": None, "rate_limited": False}

    headers = {"x-api-key": api_key(), "Accept": "application/json", "User-Agent": USER_AGENT}
    try:
        with requests.Session() as session:
            response = url_safety.safe_get(session, base_url(), params={"query": query}, headers=headers, timeout=TIMEOUT)
    except Exception:  # noqa: BLE001 — a lookup failure is never fatal to discovery
        return {"url": None, "rate_limited": False}

    if response.status_code == 429:
        logger.warning("JSEARCH_LOOKUP_RATE_LIMITED")
        return {"url": None, "rate_limited": True}
    if not 200 <= response.status_code < 300:
        return {"url": None, "rate_limited": False}
    try:
        payload = response.json()
    except ValueError:
        return {"url": None, "rate_limited": False}

    from app.services.job_enrichment_service import _titles_match
    from app.services.provider_apply_resolver import is_safe_employer_or_ats_destination

    jobs, _cursor = _extract_jobs_and_cursor(payload)
    match = next(
        (
            raw for raw in jobs
            if isinstance(raw, dict)
            and _lookup_company_matches(raw.get("employer_name") or "", company)
            and _titles_match(title, raw.get("job_title"))
        ),
        None,
    )
    if match is None:
        logger.info("JSEARCH_LOOKUP_MISS")
        return {"url": None, "rate_limited": False}

    application_url, direct_url, _source_url = _pick_apply_urls(match)
    candidate_url = application_url or direct_url
    identity = (
        is_safe_employer_or_ats_destination(candidate_url, {"company": company, "title": title})
        if candidate_url else None
    )
    if identity not in ("employer", "ats"):
        logger.info("JSEARCH_LOOKUP_MISS")
        return {"url": None, "rate_limited": False}

    logger.info("JSEARCH_LOOKUP_HIT")
    return {"url": candidate_url, "rate_limited": False}


class JSearchAdapter(BroadJobProviderAdapter):
    source_name = "jsearch"
    source_type = "broad_provider"

    def __init__(self, preference_queries: list[dict[str, str]] | None = None):
        self.preference_queries = preference_queries or []
        self._last_fetch_ok = True
        self._last_fetch_error: str | None = None

    @property
    def is_configured(self) -> bool:
        """Not named `enabled` on purpose — the orchestrator (run_poll_cycle)
        treats an `enabled` attribute as a hard filter that skips the
        adapter's fetch_jobs/health_check entirely. This adapter instead
        always runs and reports its own unconfigured state through
        fetch_jobs()/health_check(), the same way Greenhouse/Lever report an
        unconfigured company list — so JSEARCH_NOT_CONFIGURED is actually
        logged every cycle and the JobSource registry row reflects real
        status instead of staying stale."""
        return bool(api_key())

    def _request(self, session: requests.Session, query: str, remote_only: bool, cursor: str | None) -> dict | None:
        params: dict[str, Any] = {"query": query}
        if remote_only:
            params["remote_jobs_only"] = "true"
        if cursor:
            params["cursor"] = cursor
        headers = {"x-api-key": api_key(), "Accept": "application/json", "User-Agent": USER_AGENT}

        attempts = max_retries() + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = url_safety.safe_get(session, base_url(), params=params, headers=headers, timeout=TIMEOUT)
            except Exception as error:  # noqa: BLE001 — any transport failure is retried the same way
                last_error = error
            else:
                if response.status_code == 429:
                    self._rate_limited = True
                    logger.warning("JSEARCH_RATE_LIMITED query=%r cursor=%s", query, bool(cursor))
                    self._last_fetch_error = "rate_limited"
                    return None
                if response.status_code >= 500:
                    last_error = requests.HTTPError(f"HTTP {response.status_code}")
                else:
                    response.raise_for_status()
                    return response.json()
            if attempt < attempts - 1:
                time.sleep(0.5 * (attempt + 1))
        if last_error is not None:
            raise last_error
        return None

    def fetch_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        self._rate_limited = False
        if not self.is_configured:
            logger.warning("JSEARCH_NOT_CONFIGURED")
            return []
        if not self.preference_queries:
            return []

        session = requests.Session()
        jobs: list[dict[str, Any]] = []
        any_success = False
        last_error: str | None = None
        try:
            for spec in self.preference_queries:
                query = _build_query_string(spec)
                if not query:
                    continue
                remote_only = (spec.get("work_mode") or "").strip().lower() == "remote"
                cursor: str | None = None
                for page_index in range(max_pages_per_query()):
                    try:
                        payload = self._request(session, query, remote_only, cursor)
                    except Exception as error:
                        last_error = type(error).__name__
                        logger.warning(
                            "jsearch_fetch_failed query=%r page=%s error=%s", query, page_index + 1, last_error,
                        )
                        break
                    if payload is None:
                        break  # rate-limited (see _request) — stop this query's pagination
                    any_success = True
                    page_jobs, next_cursor = _extract_jobs_and_cursor(payload)
                    if not page_jobs:
                        break
                    for raw in page_jobs:
                        if isinstance(raw, dict):
                            raw = dict(raw)
                            raw["_query_spec"] = spec
                            jobs.append(raw)
                    if len(jobs) >= limit:
                        break
                    if not next_cursor:
                        break  # no further page offered — cursor absent/empty
                    cursor = next_cursor
                if len(jobs) >= limit:
                    break
        finally:
            session.close()

        # Quota-aware liveness for health_check(): a query that legitimately
        # returned zero results is still success; only a transport/HTTP
        # failure on every attempted query marks the provider unhealthy.
        self._last_fetch_ok = any_success or not any(_build_query_string(spec) for spec in self.preference_queries)
        self._last_fetch_error = "rate_limited" if self._rate_limited else last_error if not self._last_fetch_ok else None
        if self._rate_limited:
            self._last_fetch_ok = False
        return jobs[:limit]

    def normalize_job(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        title = raw.get("job_title") or ""
        company = raw.get("employer_name") or ""
        if not title or not company:
            return None

        city = (raw.get("job_city") or "").strip()
        state = (raw.get("job_state") or "").strip()
        country = (raw.get("job_country") or "").strip()
        location = ", ".join(part for part in (city, state) if part) or country

        is_remote = bool(raw.get("job_is_remote"))
        if is_remote:
            work_mode = "Remote"
        elif location:
            work_mode = "Onsite"
        else:
            work_mode = ""

        description = strip_html(raw.get("job_description") or "")
        application_url, direct_url, source_url = _pick_apply_urls(raw)

        return build_normalized_job(
            source=self.source_name,
            source_mode="live",
            # Deliberately never populated from raw["job_id"] — verified
            # against the live API that JSearch's job_id is NOT stable
            # across independent requests for the same real posting (two
            # fetches of the same job returned two different ids), so using
            # it as a dedup key would create a new duplicate row on every
            # poll cycle instead of recognizing an already-seen job.
            # job_change_detection_service falls back to fingerprint-based
            # identity (canonical direct_url, or title+company+location)
            # when source_job_id is None — which IS stable here.
            source_job_id=None,
            company=company,
            company_logo_url=raw.get("employer_logo"),
            title=title,
            location=location,
            work_mode=work_mode,
            job_type=raw.get("job_employment_type") or "",
            description=description,
            skills="",
            posted_at=_parse_posted_at(raw),
            direct_url=direct_url,
            source_url=source_url,
            application_url=application_url,
            career_page_url=matching_career_url(raw.get("employer_website"), company),
        )

    def health_check(self) -> SourceHealth:
        checked_at = datetime.now(timezone.utc).isoformat()
        if not self.is_configured:
            return SourceHealth(
                self.source_name, self.source_type, "unavailable", False, checked_at,
                error="JSEARCH_NOT_CONFIGURED",
            )
        # Deliberately no extra network call here (unlike Greenhouse/Lever's
        # health_check) — JSearch is a metered API, and fetch_jobs() already
        # runs every poll cycle, so its outcome is reused as the liveness
        # signal instead of spending a second request purely on a health
        # check.
        return SourceHealth(
            self.source_name, self.source_type, "live", self._last_fetch_ok, checked_at,
            error=self._last_fetch_error,
        )
