"""Optional, pluggable direct-employer-posting enrichment for
AGGREGATOR_LISTING discovered jobs — attempts to find the EXACT employer/ATS
job page for a posting a discovery source (JSearch, etc.) only ever
supplied via an aggregator (LinkedIn/Indeed/Dice/...).

No real search provider is wired up by default — none is currently
configured or approved for this project. `enrich_job()` is a safe no-op
(returns PROVIDER_UNAVAILABLE) unless a provider callable is explicitly
passed in. A real provider would be a STRUCTURED search/lookup API — e.g.
a job-search-specific API (Google Custom Search's structured job results,
Bing Web Search, a dedicated ATS-aggregation API) that can be queried with
(company, title, location) and returns candidate URLs. This module
NEVER scrapes LinkedIn/Indeed/Dice or an arbitrary employer site itself —
that's explicitly out of scope; a provider is a lookup call, not a scraper.

Verification pipeline (Step 5 of the coverage-enhancement spec) — every
candidate a provider returns must pass ALL of:
  1. URL safety (is_safe_application_url)
  2. employer/ATS domain classification (classify_url_domain)
  3. specificity — a real job page, not a generic careers/search page
     (classify_url_specificity)
  4. job identity — same employer AND a strong title match AND a
     compatible location (never "same company, different role" or
     "same title, different employer")
  5. availability — one bounded HTTP check confirming the page is
     actually open right now (job_availability_service)
Only a candidate that passes every gate is ever written back as
direct_url/application_url. A candidate that merely LOOKS plausible but
fails any gate is rejected — this module never invents or guesses a URL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.services import job_availability_service as availability
from app.services.job_normalization_service import (
    classify_url_domain,
    classify_url_specificity,
    is_safe_application_url,
    resolve_application_destination,
)


@dataclass(frozen=True)
class EnrichmentQuery:
    """What a provider is asked to find an exact posting for."""
    company: str
    title: str
    location: str = ""
    requisition_id: str | None = None


@dataclass(frozen=True)
class EnrichmentCandidateResult:
    """What a provider returns per candidate — only `url` is required; the
    rest are corroborating metadata used for identity verification (Step 5).
    A provider that can't supply matched_company/matched_title/matched_location
    will simply have its candidates verified more conservatively (an ATS
    domain with no matched_company is rejected outright — see
    _candidate_passes_verification). `provider_name`, if set, becomes the
    winning EnrichmentResult's provenance tag (see apply_enrichment_result /
    DiscoveredJob.direct_url_source) — e.g. "greenhouse_enrichment"."""
    url: str
    matched_title: str | None = None
    matched_company: str | None = None
    matched_location: str | None = None
    provider_name: str | None = None
    # Small, generic diagnostic context (e.g. which specific apply option a
    # multi-option result was picked from, the provider's own result id) —
    # never verification input, never persisted to the DB, never a raw API
    # response or a secret. Purely for logs/debugging.
    source_metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class EnrichmentResult:
    status: str  # "ENRICHED" | "NOT_APPLICABLE" | "REJECTED" | "PROVIDER_UNAVAILABLE" | "PROVIDER_ERROR"
    reason: str
    url: str | None = None
    availability_status: str | None = None
    availability_reason: str | None = None
    provider_name: str | None = None


EnrichmentProvider = Callable[[EnrichmentQuery], "list[EnrichmentCandidateResult]"]


def enrichment_provider() -> EnrichmentProvider | None:
    """The production provider — Phase 1: Greenhouse + Lever's
    already-integrated, key-free public job-board APIs (see
    greenhouse_lever_enrichment_provider.py). No paid or newly-approved
    external search API is used; this only reuses endpoints already safely
    wired into app/job_sources/greenhouse.py and lever.py, now applied to
    companies that were never manually configured there. A future,
    broader-coverage provider (Phase 2 — a real structured search API) can
    replace or be composed with this by changing only this one function;
    every other piece (verification, availability check, persistence)
    already works against any provider matching EnrichmentProvider."""
    from app.services.greenhouse_lever_enrichment_provider import greenhouse_lever_provider

    return greenhouse_lever_provider


_STOPWORDS = {
    "and", "or", "the", "a", "an", "of", "for", "with", "in", "to", "on", "at",
    "senior", "sr", "junior", "jr", "lead", "principal", "staff", "i", "ii", "iii",
}
_LEGAL_SUFFIX_WORDS = {
    "inc", "incorporated", "llc", "corp", "corporation", "co", "company",
    "ltd", "limited", "llp", "plc", "group", "holdings",
}


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9+#.]+", (text or "").lower())
    return {word for word in words if word and word not in _STOPWORDS and len(word) > 1}


def _titles_match(job_title: str, candidate_title: str | None) -> bool:
    """Strong overlap required in BOTH directions — rejects "Java
    Developer" loosely matching "Senior Java Platform Architect Manager"
    just because both contain "java"."""
    if not candidate_title:
        return False
    job_tokens = _tokenize(job_title)
    candidate_tokens = _tokenize(candidate_title)
    if not job_tokens or not candidate_tokens:
        return False
    overlap = len(job_tokens & candidate_tokens)
    return overlap / len(job_tokens) >= 0.6 and overlap / len(candidate_tokens) >= 0.5


def _company_slug(name: str) -> str:
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    return "".join(word for word in words if word not in _LEGAL_SUFFIX_WORDS)


def _company_names_match(job_company: str, candidate_company: str | None) -> bool:
    if not candidate_company:
        return False
    a, b = _company_slug(job_company), _company_slug(candidate_company)
    if not a or not b:
        return False
    return a == b or a.startswith(b) or b.startswith(a)


def _locations_compatible(job_location: str, candidate_location: str | None) -> bool:
    if not candidate_location:
        # No location metadata from the provider is not disqualifying on
        # its own — title + employer identity still have to match.
        return True
    job_location = (job_location or "").strip().lower()
    candidate_location = candidate_location.strip().lower()
    if not job_location:
        return True
    if "remote" in job_location and "remote" in candidate_location:
        return True
    return job_location in candidate_location or candidate_location in job_location


def _candidate_passes_verification(
    candidate: EnrichmentCandidateResult, job: Any, *, employer_website: str | None = None,
) -> tuple[bool, str]:
    """Steps 4-5's match requirements: same employer identity AND strong
    title match AND compatible location, never a generic page, never an
    unsafe URL. Returns (passes, reason)."""
    if not is_safe_application_url(candidate.url):
        return False, "unsafe_url"

    get = job.get if isinstance(job, dict) else (lambda key: getattr(job, key, None))
    job_company = get("company") or ""
    job_title = get("title") or ""
    job_location = get("location") or ""

    domain_class = classify_url_domain(candidate.url, employer_website=employer_website, employer_name=job_company)
    if domain_class == "employer":
        # classify_url_domain's employer branch already required this
        # candidate's domain to match the job's employer_website/name —
        # that IS the employer-identity check for this branch.
        pass
    elif domain_class == "ats":
        # An ATS board hosts many employers — the domain alone proves
        # nothing about WHICH employer, so the provider must have told us.
        if not _company_names_match(job_company, candidate.matched_company):
            return False, "ats_candidate_missing_employer_confirmation"
    else:
        return False, "not_employer_or_ats_domain"

    specificity = classify_url_specificity(candidate.url)
    if specificity != "SPECIFIC_JOB":
        return False, "not_specific_job_page"

    if not _titles_match(job_title, candidate.matched_title):
        return False, "title_mismatch"

    if candidate.matched_company and not _company_names_match(job_company, candidate.matched_company):
        return False, "company_mismatch"

    if not _locations_compatible(job_location, candidate.matched_location):
        return False, "location_mismatch"

    return True, "verified"


def enrich_job(
    job: Any, *, provider: EnrichmentProvider | None = None, employer_website: str | None = None,
) -> EnrichmentResult:
    """Attempts to find a verified, exact direct employer/ATS destination
    for a job that currently only resolves to AGGREGATOR_LISTING. Never
    called for a job that already has a direct destination (Step 4 — this
    is additive coverage, not a re-resolution of jobs that already work).

    Uses `provider` if given, else the production enrichment_provider()
    (None today — see its docstring). A candidate is only ever accepted
    after passing every gate in _candidate_passes_verification AND a fresh
    availability check confirms it's actually open right now — this module
    never persists a destination it couldn't itself verify is reachable."""
    active_provider = provider if provider is not None else enrichment_provider()
    if active_provider is None:
        return EnrichmentResult(status="PROVIDER_UNAVAILABLE", reason="no_enrichment_provider_configured")

    destination = resolve_application_destination(job)
    if destination["type"] != "AGGREGATOR_LISTING":
        return EnrichmentResult(status="NOT_APPLICABLE", reason="already_has_a_direct_destination")

    get = job.get if isinstance(job, dict) else (lambda key: getattr(job, key, None))
    query = EnrichmentQuery(company=get("company") or "", title=get("title") or "", location=get("location") or "")

    try:
        raw_candidates = active_provider(query) or []
    except Exception as error:  # noqa: BLE001 — a flaky external provider must never break the discovery pipeline
        return EnrichmentResult(status="PROVIDER_ERROR", reason=f"provider_error:{type(error).__name__}")

    for candidate in raw_candidates:
        passes, reason = _candidate_passes_verification(candidate, job, employer_website=employer_website)
        if not passes:
            continue
        availability_result = availability.check_url_availability(candidate.url)
        if availability_result["status"] != "ACTIVE":
            # Don't promote a job from "View Listing" to a direct
            # destination we can't even confirm is currently open —
            # strictly more conservative than a provider-supplied direct
            # URL (see module docstring).
            continue
        return EnrichmentResult(
            status="ENRICHED", reason="verified_exact_match", url=candidate.url,
            availability_status=availability_result["status"], availability_reason=availability_result["reason"],
            provider_name=candidate.provider_name,
        )

    return EnrichmentResult(status="REJECTED", reason="no_candidate_passed_verification")


def enrich_job_with_fallback(job: Any, *, employer_website: str | None = None) -> EnrichmentResult:
    """Provider-agnostic orchestration (Phase 2): tries Greenhouse/Lever
    (Phase 1 — always available, no configuration required) first, and
    ONLY if that finds nothing tries the broad structured-search provider
    (Phase 2 — disabled/unconfigured by default; see
    broad_search_enrichment_provider.py). Each attempt is a completely
    independent, full run of enrich_job()'s verification pipeline — no
    duplicated matching/safety logic, no weakened checks anywhere. The
    broad provider's own lookup (real network call in a future phase once
    one is configured) is never even attempted once Greenhouse/Lever
    already produced a verified match, and is skipped entirely when
    unconfigured — this function then behaves identically to a plain
    enrich_job(job, provider=greenhouse_lever_provider) call, i.e. no
    behavior change from Phase 1."""
    from app.services.greenhouse_lever_enrichment_provider import greenhouse_lever_provider

    result = enrich_job(job, provider=greenhouse_lever_provider, employer_website=employer_website)
    if result.status == "ENRICHED":
        return result

    from app.services.broad_search_enrichment_provider import broad_provider_configured, broad_search_provider

    if not broad_provider_configured():
        return result
    return enrich_job(job, provider=broad_search_provider, employer_website=employer_website)


def apply_enrichment_result(job: Any, result: EnrichmentResult) -> None:
    """Mutates a DiscoveredJob row in place with a successful enrichment —
    caller is responsible for db.commit(). No-op for anything but
    status="ENRICHED" (never partially applies a rejected/unavailable
    result)."""
    if result.status != "ENRICHED" or not result.url:
        return
    job.direct_url = result.url
    job.application_url = result.url
    job.direct_url_source = result.provider_name or "enrichment"
    job.availability_status = result.availability_status or "ACTIVE"
    job.availability_checked_at = datetime.now(timezone.utc)
    job.availability_reason = (result.availability_reason or "")[:300]
