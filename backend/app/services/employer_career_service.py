"""Reusable official-career metadata for Updated Job Search.

A provider listing is never stored here. A URL is remembered only when it
is already a safe employer or ATS destination for that company. The same
normalized company reuses the URL for later jobs instead of asking every
provider again. Entries older than the revalidation window are ignored
until fresh evidence arrives.
"""

from __future__ import annotations

import time
from typing import Any

from app.services.job_normalization_service import (
    _canonical_employer_alias_key,
    classify_url_specificity,
)
from app.services.provider_apply_resolver import is_safe_employer_or_ats_destination

_REVALIDATE_SECONDS = 7 * 24 * 3600
_cache: dict[str, tuple[float, str]] = {}


def clear_career_cache() -> None:
    _cache.clear()


def _key(company: str) -> str:
    return _canonical_employer_alias_key(company)


def _accepted(url: str | None, company: str, title: str = "") -> str | None:
    """A reusable careers destination: same employer, not one job posting."""
    if is_safe_employer_or_ats_destination(url, {"company": company, "title": title}) is None:
        return None
    if classify_url_specificity(url) == "SPECIFIC_JOB":
        return None
    return url


def matching_career_url(url: str | None, company: str) -> str | None:
    return _accepted(url, company)


def remember_career_page(company: str, url: str | None) -> None:
    accepted = _accepted(url, company)
    key = _key(company)
    if not accepted or not key:
        return
    _cache[key] = (time.time() + _REVALIDATE_SECONDS, accepted)


def cached_career_page(company: str) -> str | None:
    key = _key(company)
    entry = _cache.get(key)
    if not entry:
        return None
    expires_at, url = entry
    if time.time() > expires_at:
        _cache.pop(key, None)
        return None
    return url


def career_page_for(db: Any, company: str) -> str | None:
    """Memory first, then one exact-company lookup of a previously stored page."""
    cached = cached_career_page(company)
    if cached:
        return cached
    if db is None or not company:
        return None
    from app.models.discovered_job_model import DiscoveredJob

    row = (
        db.query(DiscoveredJob.career_page_url)
        .filter(DiscoveredJob.company == company, DiscoveredJob.career_page_url.isnot(None))
        .order_by(DiscoveredJob.id.desc())
        .first()
    )
    url = row[0] if row else None
    accepted = _accepted(url, company)
    if accepted:
        remember_career_page(company, accepted)
    return accepted


def official_career_url(job: Any) -> str | None:
    """Best official employer/ATS page already attached to this job.

    Provider listing URLs are ignored. Nothing is invented from the company
    name alone, and the cross-job cache is applied by persistence before
    this runs.
    """
    get = job.get if isinstance(job, dict) else (lambda key: getattr(job, key, None))
    company = str(get("company") or "")
    title = str(get("title") or "")
    for field in ("career_page_url", "employer_website", "unverified_application_url"):
        accepted = _accepted(get(field), company, title)
        if accepted:
            return accepted
    return None
