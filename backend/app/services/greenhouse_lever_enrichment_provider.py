"""Phase-1 direct-Apply enrichment provider: attempts to resolve an
AGGREGATOR_LISTING job through Greenhouse's and Lever's already-integrated,
PUBLIC, key-free job-board APIs — the exact same endpoints
app/job_sources/greenhouse.py and app/job_sources/lever.py already use for
their manually-configured company lists, reused here to find a company's
board WITHOUT any pre-configuration or new API key/provider approval.

Never fabricates a job URL or a job id. Only a small, bounded, deterministic
set of BOARD TOKEN / SLUG candidates is derived from the job's own employer
name, and each candidate is discarded unless the real public API confirms
it (a live board that actually returns real job listings). This module's
only job is producing CANDIDATES with honest metadata — job_enrichment_
service.enrich_job()'s existing pipeline (safety, employer/ATS
classification, specificity, title/company/location matching, availability
confirmation) still has to independently accept every candidate before it
is ever persisted as a direct Apply destination. See that module's
docstring for the full verification chain.

Identity note: neither Greenhouse's nor Lever's public job-board response
includes an explicit "employer name" field (verified against the existing
adapters, which inject company name externally from their own config —
there is nothing to read it from). This provider's identity evidence is
therefore twofold: (1) the candidate slug was deterministically DERIVED
from the target job's own employer name (never an arbitrary guess), and
(2) the confirmed board must contain a job whose title AND location
strongly match the target job — job_enrichment_service's existing
_titles_match/_locations_compatible gates, unchanged and un-weakened. A
same-named-but-different company whose board happens to share a guessed
slug would need to ALSO have a posting with the same title in the same
location to slip through — realistically as unlikely as it sounds.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import requests

from app.services import url_safety
from app.services.job_enrichment_service import EnrichmentCandidateResult, EnrichmentQuery
from app.services.job_normalization_service import strip_html

logger = logging.getLogger(__name__)

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
LEVER_API = "https://api.lever.co/v0/postings/{slug}"
USER_AGENT = "CareerBoostAI-Enrichment/1.0 (+educational project)"
TIMEOUT = (5, 10)

# Small, bounded, deterministic — never "dozens of guesses".
MAX_CANDIDATES_PER_PROVIDER = 3
CACHE_TTL_SECONDS = 6 * 3600

_LEGAL_SUFFIX_WORDS = {
    "inc", "incorporated", "llc", "corp", "corporation", "co", "company",
    "ltd", "limited", "llp", "plc", "group", "holdings",
}

# In-process, bounded TTL cache: {(provider, slug): (expires_at, verified)}.
# Only ever caches a CONCLUSIVE outcome (confirmed real board, or confirmed
# 404/nonexistent) — an ambiguous response (timeout, 5xx, malformed body)
# is never cached, so a transient failure just gets retried next time
# rather than permanently sticking as a false negative.
_slug_cache: dict[tuple[str, str], tuple[float, bool]] = {}


def clear_cache() -> None:
    """Test-only reset of the in-process slug cache."""
    _slug_cache.clear()


def _cache_get(provider: str, slug: str) -> bool | None:
    entry = _slug_cache.get((provider, slug))
    if not entry:
        return None
    expires_at, verified = entry
    if time.time() > expires_at:
        _slug_cache.pop((provider, slug), None)
        return None
    return verified


def _cache_set(provider: str, slug: str, verified: bool) -> None:
    _slug_cache[(provider, slug)] = (time.time() + CACHE_TTL_SECONDS, verified)


def _normalize_words(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (name or "").lower())


def candidate_slugs(company_name: str) -> list[str]:
    """A small, bounded, deterministic set of plausible Greenhouse
    board-token / Lever company-slug candidates derived from a company's
    display name. Never a scraped/searched guess — a pure, reproducible
    transform of the name actually supplied (lowercase, strip punctuation,
    join vs. hyphenate, with/without common legal suffixes)."""
    words = _normalize_words(company_name)
    if not words:
        return []
    stripped = [word for word in words if word not in _LEGAL_SUFFIX_WORDS] or words

    candidates: list[str] = []
    joined = "".join(stripped)
    hyphenated = "-".join(stripped)
    full_joined = "".join(words)  # legal suffix included — some boards use the formal name
    for candidate in (joined, hyphenated, full_joined):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates[:MAX_CANDIDATES_PER_PROVIDER]


def _fetch_greenhouse_jobs(token: str, session: requests.Session) -> list[dict[str, Any]] | None:
    """Returns the board's job list if `token` is a real, non-empty
    Greenhouse board, else None (never raises)."""
    cached = _cache_get("greenhouse", token)
    if cached is False:
        return None

    try:
        response = url_safety.safe_get(
            session, GREENHOUSE_API.format(token=token),
            params={"content": "true"}, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
    except Exception as error:  # noqa: BLE001 — a network hiccup is inconclusive, never a false negative
        logger.debug("greenhouse_enrichment_probe_failed token=%s error=%s", token, type(error).__name__)
        return None

    if response.status_code == 404:
        _cache_set("greenhouse", token, False)
        return None
    if response.status_code != 200:
        return None  # ambiguous (rate limited, 5xx, ...) — not cached, not conclusively rejected

    try:
        payload = response.json()
    except ValueError:
        return None
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list) or not jobs:
        _cache_set("greenhouse", token, False)
        return None

    _cache_set("greenhouse", token, True)
    return jobs


def _fetch_lever_postings(slug: str, session: requests.Session) -> list[dict[str, Any]] | None:
    """Returns the company's posting list if `slug` is a real, non-empty
    Lever company, else None (never raises)."""
    cached = _cache_get("lever", slug)
    if cached is False:
        return None

    try:
        response = url_safety.safe_get(
            session, LEVER_API.format(slug=slug),
            params={"mode": "json"}, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
    except Exception as error:  # noqa: BLE001
        logger.debug("lever_enrichment_probe_failed slug=%s error=%s", slug, type(error).__name__)
        return None

    if response.status_code == 404:
        _cache_set("lever", slug, False)
        return None
    if response.status_code != 200:
        return None

    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, list) or not payload:
        _cache_set("lever", slug, False)
        return None

    _cache_set("lever", slug, True)
    return payload


def _greenhouse_job_to_candidate(job: dict[str, Any], *, matched_company: str) -> EnrichmentCandidateResult | None:
    url = job.get("absolute_url")
    if not url:
        return None
    location = ""
    raw_location = job.get("location")
    if isinstance(raw_location, dict):
        location = raw_location.get("name") or ""
    return EnrichmentCandidateResult(
        url=url, matched_title=strip_html(job.get("title") or "") or job.get("title"),
        matched_company=matched_company, matched_location=location, provider_name="greenhouse_enrichment",
    )


def _lever_job_to_candidate(job: dict[str, Any], *, matched_company: str) -> EnrichmentCandidateResult | None:
    url = job.get("hostedUrl") or job.get("applyUrl")
    if not url:
        return None
    categories = job.get("categories") or {}
    location = categories.get("location", "") if isinstance(categories, dict) else ""
    return EnrichmentCandidateResult(
        url=url, matched_title=job.get("text"),
        matched_company=matched_company, matched_location=location, provider_name="lever_enrichment",
    )


def greenhouse_lever_provider(query: EnrichmentQuery) -> list[EnrichmentCandidateResult]:
    """The EnrichmentProvider callable — bounded (at most
    MAX_CANDIDATES_PER_PROVIDER slug guesses per ATS, sequential, stops at
    the first confirmed board per provider), never parallel, never more
    than one HTTP round-trip per candidate slug. Returns every job on
    whichever board(s) got confirmed as raw candidates — the caller
    (job_enrichment_service.enrich_job) does the actual title/location/
    safety/availability filtering, so a large board is still cheap here
    (no extra network calls beyond the one board fetch)."""
    if not query.company:
        return []

    from app.services.provider_apply_resolver import ATS_LOOKUP_SKIP

    if query.ats_hint in ATS_LOOKUP_SKIP:
        return []

    session = requests.Session()
    try:
        candidates: list[EnrichmentCandidateResult] = []
        slugs = candidate_slugs(query.company)
        if query.ats_board_token and query.ats_board_token not in slugs:
            slugs = [query.ats_board_token, *slugs]

        run_greenhouse = query.ats_hint in {None, "", "greenhouse"}
        run_lever = query.ats_hint in {None, "", "lever"}

        if run_greenhouse:
            for token in slugs[:MAX_CANDIDATES_PER_PROVIDER]:
                jobs = _fetch_greenhouse_jobs(token, session)
                if jobs is None:
                    continue
                candidates.extend(
                    candidate for candidate in (
                        _greenhouse_job_to_candidate(job, matched_company=query.company)
                        for job in jobs if isinstance(job, dict)
                    ) if candidate is not None
                )
                break  # stop guessing more Greenhouse slugs once a board is confirmed

        if run_lever:
            for slug in slugs[:MAX_CANDIDATES_PER_PROVIDER]:
                postings = _fetch_lever_postings(slug, session)
                if postings is None:
                    continue
                candidates.extend(
                    candidate for candidate in (
                        _lever_job_to_candidate(job, matched_company=query.company)
                        for job in postings if isinstance(job, dict)
                    ) if candidate is not None
                )
                break  # stop guessing more Lever slugs once a board is confirmed

        return candidates
    finally:
        session.close()
