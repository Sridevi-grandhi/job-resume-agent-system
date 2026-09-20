"""Broad structured-search enrichment provider — a generic INTERFACE and
config contract, independent of any single search API. Disabled by default
(JOB_ENRICHMENT_BROAD_PROVIDER unset -> "none"): zero network calls happen
anywhere in this module unless an operator BOTH selects a registered
provider name AND supplies an API key — a deployment/config decision never
made by this code and never written to backend/.env here.

Phase 3 registers the first real implementation, SerpAPI's Google Jobs
engine ("serpapi_google_jobs" — see serpapi_google_jobs_client.py). Adding
another provider later is: implement BroadSearchClient.search() — one call,
raw provider-shaped results out — and register its factory in
_CLIENT_FACTORIES under a chosen name. Nothing else needs to change:
job_enrichment_service.enrich_job_with_fallback() already calls whatever
this module currently exposes, results are normalized into the exact same
EnrichmentCandidateResult shape greenhouse_lever_enrichment_provider
produces, and every candidate — regardless of provider — still has to pass
enrich_job()'s full, unweakened verification pipeline (URL safety, domain
classification, specificity, title/company/location match, availability)
before it is ever persisted as a direct Apply destination. This module
never decides "Apply" — it only ever proposes candidates.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol

from app.services.job_enrichment_service import EnrichmentCandidateResult, EnrichmentQuery

logger = logging.getLogger(__name__)


def broad_provider_name() -> str:
    """JOB_ENRICHMENT_BROAD_PROVIDER — "none" (the default when unset)
    disables this provider entirely. Read the same way every other
    provider in this project reads its config (os.getenv with a safe
    default) — no entry for this exists in backend/.env today, and this
    module never edits that file."""
    return (os.getenv("JOB_ENRICHMENT_BROAD_PROVIDER") or "none").strip().lower()


def broad_provider_api_key() -> str:
    """JOB_ENRICHMENT_BROAD_API_KEY — never logged, never included in any
    EnrichmentCandidateResult/EnrichmentResult, never persisted."""
    return (os.getenv("JOB_ENRICHMENT_BROAD_API_KEY") or "").strip()


def broad_provider_base_url() -> str:
    return (os.getenv("JOB_ENRICHMENT_BROAD_BASE_URL") or "").strip()


def broad_provider_configured() -> bool:
    """True only when a provider name other than "none" AND an API key
    are both present. False by default — neither variable is set in this
    project today, so this always evaluates False out of the box."""
    return broad_provider_name() != "none" and bool(broad_provider_api_key())


class BroadSearchClient(Protocol):
    """What a concrete broad-search provider implements — one structured
    search call, raw provider-shaped results out. A client is never asked to
    return a "verified" or "direct Apply" URL; job_enrichment_service.
    enrich_job() independently verifies every result this returns, exactly
    as it does for Greenhouse/Lever candidates."""

    def search(self, query: EnrichmentQuery) -> list[dict[str, Any]]:
        """Returns raw, provider-shaped result dicts. Each SHOULD supply at
        least {"url": ...}; "title"/"company"/"location" are optional
        corroborating metadata used for identity verification downstream.
        Never expected to pre-filter for correctness — that's
        enrich_job()'s job — but SHOULD NOT return results with no
        relation at all to the query (e.g. don't return every job in a
        general index)."""
        ...


def _serpapi_google_jobs_factory() -> "BroadSearchClient":
    # Local import: keeps this module import-light (no hard `requests`
    # dependency, no SerpAPI-specific code loaded) unless this exact
    # provider is actually selected via JOB_ENRICHMENT_BROAD_PROVIDER.
    from app.services.serpapi_google_jobs_client import SerpApiGoogleJobsClient

    return SerpApiGoogleJobsClient()


# Maps JOB_ENRICHMENT_BROAD_PROVIDER's value to a zero-argument factory
# returning a BroadSearchClient. "serpapi_google_jobs" (Phase 3) is the
# first real implementation; a future provider is: implement
# BroadSearchClient, register `{"some_name": SomeFactory}` here, done — no
# other code in this project changes.
_CLIENT_FACTORIES: dict[str, Any] = {
    "serpapi_google_jobs": _serpapi_google_jobs_factory,
}


def _get_client() -> BroadSearchClient | None:
    factory = _CLIENT_FACTORIES.get(broad_provider_name())
    if factory is None:
        return None
    try:
        return factory()
    except Exception as error:  # noqa: BLE001 — a broken client must never crash discovery
        logger.warning("broad_search_client_init_failed provider=%s error=%s", broad_provider_name(), type(error).__name__)
        return None


def _normalize_result(raw: dict[str, Any], *, provider_name: str) -> EnrichmentCandidateResult | None:
    """Normalizes one provider-shaped raw result into the shared
    EnrichmentCandidateResult shape (Task 5) — no provider-specific
    verification logic exists anywhere else in this project; every
    provider's results funnel through this one shape into the one
    existing pipeline. Never invents a URL: a result with no usable url is
    dropped, not guessed at. `provider_name` is specific to whichever
    broad provider is actually configured (e.g.
    "serpapi_google_jobs_enrichment") — see broad_search_provider — so
    DiscoveredJob.direct_url_source always names the real source, never a
    generic label."""
    url = raw.get("url")
    if not url or not isinstance(url, str):
        return None
    return EnrichmentCandidateResult(
        url=url,
        matched_title=raw.get("title"),
        matched_company=raw.get("company"),
        matched_location=raw.get("location"),
        provider_name=provider_name,
        source_metadata=raw.get("source_metadata") if isinstance(raw.get("source_metadata"), dict) else None,
    )


def broad_search_provider(query: EnrichmentQuery) -> list[EnrichmentCandidateResult]:
    """The EnrichmentProvider callable for the Phase-2/3 broad structured-
    search path — a drop-in for job_enrichment_service.enrich_job(),
    identical in shape to greenhouse_lever_provider. Returns [] (never
    raises) whenever the provider is disabled/unconfigured/unimplemented,
    on any request error, or on a timeout — exactly the same "stay View
    Listing" outcome as a configured provider finding zero verified
    candidates (Task 7)."""
    if not broad_provider_configured():
        return []
    client = _get_client()
    if client is None:
        return []
    try:
        raw_results = client.search(query) or []
    except Exception as error:  # noqa: BLE001 — a flaky/slow provider must never break discovery
        logger.warning("broad_search_provider_failed error=%s", type(error).__name__)
        return []
    provider_name = f"{broad_provider_name()}_enrichment"
    return [
        candidate for candidate in (
            _normalize_result(result, provider_name=provider_name) for result in raw_results if isinstance(result, dict)
        ) if candidate is not None
    ]
