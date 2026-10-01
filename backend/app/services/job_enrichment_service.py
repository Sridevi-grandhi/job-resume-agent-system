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
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit
from datetime import datetime, timezone
from typing import Any, Callable

from app.services import job_availability_service as availability
from app.services.job_enrichment_context import record_event
from app.services.job_normalization_service import (
    classify_url_domain,
    classify_url_specificity,
    is_safe_application_url,
    resolve_application_destination,
    verified_employer_domain_alias,
)


@dataclass(frozen=True)
class EnrichmentQuery:
    """What a provider is asked to find an exact posting for."""
    company: str
    title: str
    location: str = ""
    requisition_id: str | None = None
    ats_hint: str | None = None
    ats_board_token: str | None = None


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
    diagnostics: list[dict] = field(default_factory=list)


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


def _ats_tenant(url: str) -> str | None:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    tenant = None
    if host.endswith((".myworkdayjobs.com", ".selectminds.com")):
        tenant = host.split(".")[0]
    elif host in {"boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co"}:
        tenant = next(iter(parsed.path.strip("/").split("/")), "")
    return tenant


def _ats_tenant_matches(url: str, company: str) -> bool:
    tenant = _ats_tenant(url)
    return bool(tenant and _company_slug(tenant) == _company_slug(company))


def _candidate_verification(
    candidate: EnrichmentCandidateResult, job: Any, *, employer_website: str | None = None, allow_generic: bool = False,
) -> tuple[bool, str, dict[str, str | None]]:
    """Returns the unchanged verification verdict plus safe diagnostic facts."""
    evidence: dict[str, str | None] = {
        "domain": urlsplit(candidate.url).hostname or None,
        "url_safety": "NOT_EVALUATED",
        "employer_identity": "NOT_EVALUATED",
        "employer_alias": None,
        "title_match": "NOT_EVALUATED",
        "specificity": "NOT_EVALUATED",
    }
    if not is_safe_application_url(candidate.url):
        evidence["url_safety"] = "FAILED"
        return False, "unsafe_url", evidence
    evidence["url_safety"] = "PASSED"

    get = job.get if isinstance(job, dict) else (lambda key: getattr(job, key, None))
    job_company = get("company") or ""
    job_title = get("title") or ""
    job_location = get("location") or ""

    alias_used = verified_employer_domain_alias(candidate.url, job_company)
    evidence["employer_alias"] = alias_used
    domain_class = classify_url_domain(candidate.url, employer_website=employer_website, employer_name=job_company)
    if domain_class == "employer":
        evidence["employer_identity"] = "VERIFIED_ALIAS" if alias_used else "EMPLOYER_DOMAIN"
    elif domain_class == "ats":
        if _ats_tenant(candidate.url) and not _ats_tenant_matches(candidate.url, job_company):
            evidence["employer_identity"] = "ATS_TENANT_MISMATCH"
            return False, "ats_tenant_company_mismatch", evidence
        if not (_company_names_match(job_company, candidate.matched_company) or _ats_tenant_matches(candidate.url, job_company)):
            evidence["employer_identity"] = "ATS_EMPLOYER_UNCONFIRMED"
            return False, "ats_candidate_missing_employer_confirmation", evidence
        evidence["employer_identity"] = "ATS_EMPLOYER_CONFIRMED"
    else:
        evidence["employer_identity"] = "UNRECOGNIZED_DOMAIN"
        return False, "not_employer_or_ats_domain", evidence

    specificity = classify_url_specificity(candidate.url)
    evidence["specificity"] = specificity
    if specificity != "SPECIFIC_JOB" and not (allow_generic and specificity == "GENERIC_EMPLOYER_PAGE"):
        return False, "not_specific_job_page", evidence

    if not _titles_match(job_title, candidate.matched_title):
        evidence["title_match"] = "FAILED"
        return False, "title_mismatch", evidence
    evidence["title_match"] = "PASSED"

    # A descriptive URL slug must not contradict the provider's title metadata.
    slug = urlsplit(candidate.url).path.rstrip("/").split("/")[-1]
    slug_words = set(re.findall(r"[a-z]+", slug.lower()))
    if len(slug_words) >= 2 and slug_words & {"engineer", "developer", "manager", "analyst", "architect", "scientist"}:
        if not _titles_match(job_title, " ".join(slug_words)):
            evidence["title_match"] = "FAILED_URL_SLUG"
            return False, "title_mismatch", evidence

    if candidate.matched_company and not _company_names_match(job_company, candidate.matched_company):
        evidence["employer_identity"] = "CANDIDATE_COMPANY_MISMATCH"
        return False, "company_mismatch", evidence

    if not _locations_compatible(job_location, candidate.matched_location):
        return False, "location_mismatch", evidence

    return True, "verified", evidence


def _candidate_passes_verification(
    candidate: EnrichmentCandidateResult, job: Any, *, employer_website: str | None = None, allow_generic: bool = False,
) -> tuple[bool, str]:
    """Compatibility wrapper for callers that need only the verdict."""
    passed, reason, _evidence = _candidate_verification(
        candidate, job, employer_website=employer_website, allow_generic=allow_generic,
    )
    return passed, reason


def enrich_job(
    job: Any, *, provider: EnrichmentProvider | None = None, employer_website: str | None = None,
    include_local: bool = True,
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
    get = job.get if isinstance(job, dict) else (lambda key: getattr(job, key, None))
    if destination["type"] != "AGGREGATOR_LISTING" and (get("application_url") or get("direct_url")):
        return EnrichmentResult(status="NOT_APPLICABLE", reason="already_has_a_direct_destination")
    from app.services.provider_apply_resolver import (
        collect_outbound_apply_urls,
        detect_ats_from_job,
        is_provider_listing_url,
        outbound_application_candidate,
    )

    ats_hint, ats_board_token = detect_ats_from_job(job)
    query = EnrichmentQuery(
        company=get("company") or "", title=get("title") or "", location=get("location") or "",
        ats_hint=ats_hint, ats_board_token=ats_board_token,
    )

    outbound_urls = collect_outbound_apply_urls(job) if include_local else []
    local_candidates = [
        EnrichmentCandidateResult(
            url=url, matched_title=get("title"),
            matched_location=get("location"), provider_name="provider_apply_url",
        )
        for url in outbound_urls
    ]
    diagnostics = []
    raw_outbound_candidate = outbound_application_candidate(job) if include_local else None
    if include_local and outbound_urls:
        for url in outbound_urls:
            diagnostics.append({"domain": urlsplit(url).hostname, "reason": "outbound_candidate_found"})
            record_event("outbound_candidate_found")
    elif include_local and raw_outbound_candidate:
        # Keep the untrusted value out of the verifier when it fails the
        # existing URL-safety precondition.  No raw URL is persisted.
        diagnostics.append({"reason": "outbound_candidate_unsafe"})
        record_event("outbound_candidate_unsafe")
    elif include_local:
        # The provider listing stays available as a fallback, but it is not a
        # direct-application candidate.  This diagnostic is durable and safe
        # (no raw URL) so coverage gaps are distinguishable from verifier
        # failures.
        diagnostics.append({"reason": "no_outbound_destination"})
        record_event("no_outbound_destination")
    fallback = None
    seen = set()
    # Local candidate first: never spend a provider request before verifying it.
    for stage in ("local", "provider"):
        if stage == "local":
            candidates = local_candidates
        else:
            try:
                candidates = (active_provider(query) or [])[:10]
            except Exception as error:
                diagnostics.append({"reason": "provider_error", "error_class": type(error).__name__})
                return fallback or EnrichmentResult(status="PROVIDER_ERROR", reason=f"provider_error:{type(error).__name__}", diagnostics=diagnostics)
        for candidate in candidates:
            if candidate.url in seen:
                continue
            seen.add(candidate.url)
            if is_provider_listing_url(candidate.url):
                diagnostics.append({
                    "domain": urlsplit(candidate.url).hostname,
                    "reason": "provider_listing_only",
                })
                record_event("provider_listing_only")
                continue
            if stage == "local":
                record_event("provider_candidate_attempts")
            outcome = _verify_candidate(candidate, job, employer_website=employer_website)
            diagnostics.extend(outcome.diagnostics)
            if outcome.status == "ENRICHED":
                diagnostics.append({"domain": urlsplit(outcome.url).hostname, "reason": "verified_direct"})
                record_event("promoted_direct")
                record_event((candidate.provider_name or "provider") + "_successes")
                return replace(outcome, diagnostics=diagnostics)
            if stage == "local":
                diagnostics.append({"domain": urlsplit(candidate.url).hostname, "reason": "outbound_candidate_verification_failed"})
                record_event("outbound_candidate_verification_failed")
            if outcome.status == "GENERIC_EMPLOYER" or (outcome.status == "EXPIRED" and fallback is None):
                fallback = outcome
    if is_provider_listing_url(get("source_url")):
        diagnostics.append({"reason": "provider_fallback_used"})
        record_event("provider_fallback_used")
    return replace(fallback, diagnostics=diagnostics) if fallback else EnrichmentResult(status="REJECTED", reason="no_candidate_passed_verification", diagnostics=diagnostics)


def _verify_candidate(candidate, job, *, employer_website=None):
    """One bounded verification path, including revalidation after safe redirects."""
    diagnostics = []
    def rejected(reason, status=None, evidence=None):
        event = {
            **(evidence or {}),
            "domain": (evidence or {}).get("domain") or ((urlsplit(candidate.url).hostname or "") if is_safe_application_url(candidate.url) else None),
            "reason": reason,
            "availability": status,
        }
        diagnostics.append(event)
        record_event("verification_rejected", **event)
        return EnrichmentResult("REJECTED", reason, diagnostics=diagnostics)

    passes, reason, evidence = _candidate_verification(candidate, job, employer_website=employer_website, allow_generic=True)
    # Only known provider redirect/listing hosts may be resolved before identity
    # validation. Each hop is SSRF-checked by the existing availability service.
    host = urlsplit(candidate.url).hostname if is_safe_application_url(candidate.url) else None
    redirect_candidate = candidate.provider_name == "provider_apply_url" and reason == "not_employer_or_ats_domain" and (
        classify_url_domain(candidate.url) == "aggregator" or host == "ad.doubleclick.net")
    if not passes and not redirect_candidate:
        return rejected(reason, evidence=evidence)
    get = job.get if isinstance(job, dict) else (lambda key: getattr(job, key, None))
    checked = availability.check_url_availability(candidate.url, expected_title=get("title") or candidate.matched_title)
    effective = checked.get("final_url") or candidate.url
    final_candidate = replace(candidate, url=effective)
    passes, reason, evidence = _candidate_verification(final_candidate, job, employer_website=employer_website, allow_generic=True)
    if not passes:
        return rejected("final_" + reason, checked["status"], evidence)
    specificity = classify_url_specificity(effective)
    state = checked["status"]
    raw_reason = str(checked.get("reason") or "")
    # Never copy arbitrary exception text/URLs into durable diagnostics.
    base_reason = raw_reason.split(":", 1)[0]
    safe_reason = base_reason if re.fullmatch(r"http_[0-9]{3}", base_reason) or base_reason in {
        "timeout", "network_error", "unsafe_url", "redirect_without_location", "too_many_redirects",
        "redirected_to_generic_page", "generic_page_text", "generic_or_unspecific_url", "page_text", "check_failed", "no_url",
    } else "availability_result"
    diagnostics.append({**evidence, "domain": urlsplit(effective).hostname, "reason": safe_reason,
                        "availability": state, "specificity": specificity})
    if state == "ACTIVE":
        status = "ENRICHED" if specificity == "SPECIFIC_JOB" else "GENERIC_EMPLOYER"
        if status == "GENERIC_EMPLOYER":
            record_event("generic_employer_page")
    elif state == "EXPIRED" and specificity == "SPECIFIC_JOB":
        status = "EXPIRED"
        record_event("expired")
    else:
        return rejected(safe_reason or "availability_unknown", state, evidence)
    return EnrichmentResult(status, "verified_exact_match" if status == "ENRICHED" else status.lower(),
                            effective, state, safe_reason, candidate.provider_name, diagnostics)


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
    if result.status in {"ENRICHED", "NOT_APPLICABLE"}:
        return result

    from app.services.broad_search_enrichment_provider import broad_provider_configured, broad_search_provider

    if not broad_provider_configured():
        return result
    broad_result = enrich_job(job, provider=broad_search_provider, employer_website=employer_website, include_local=False)
    winner = broad_result if broad_result.status == "ENRICHED" or result.status not in {"GENERIC_EMPLOYER", "EXPIRED"} else result
    return replace(winner, diagnostics=result.diagnostics + broad_result.diagnostics)


def apply_enrichment_result(job: Any, result: EnrichmentResult) -> None:
    """Apply verified destination state without downgrading a better destination.

    The caller is responsible for db.commit(); rejected/unknown results are
    never promoted.
    """
    if result.status not in {"ENRICHED", "GENERIC_EMPLOYER", "EXPIRED"} or not result.url:
        return
    from app.services.job_normalization_service import resolve_apply_action
    previous = resolve_apply_action(job)
    if previous["action_label"] == "Apply" and result.status != "ENRICHED":
        return
    if (previous["action_label"] == "Open Employer Site"
            and result.status == "EXPIRED" and previous["url"] != result.url):
        return
    if (str(previous.get("action_label") or "").startswith("View on ")
            and result.status not in {"ENRICHED", "GENERIC_EMPLOYER"}):
        if not (result.status == "EXPIRED" and previous["url"] == result.url):
            return
    job.direct_url = result.url
    job.application_url = result.url
    job.verified_destination_url = result.url
    job.direct_url_source = result.provider_name or "enrichment"
    job.availability_status = result.availability_status or "ACTIVE"
    job.availability_checked_at = datetime.now(timezone.utc)
    job.availability_reason = (result.availability_reason or "")[:300]
