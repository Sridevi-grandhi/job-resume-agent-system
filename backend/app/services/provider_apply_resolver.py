"""Bounded outbound-apply resolution for discovery-provider job records.

Discovery providers (JSearch, Adzuna, Dice, …) are resolution *inputs*.
This module never scrapes HTML, never bypasses bot controls, and never
invents a URL. It only:
  1. collects provider-supplied apply/redirect/ATS URLs already on the job
  2. classifies the likely ATS so enrichment can skip unrelated adapters
  3. decides whether a stored unverified URL is already a safe employer/ATS
     destination for this same job

Verification, availability, and persistence stay in job_enrichment_service.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from app.services.job_normalization_service import (
    classify_url_domain,
    classify_url_specificity,
    is_safe_application_url,
)

_LEGAL_SUFFIX_WORDS = {
    "inc", "incorporated", "llc", "corp", "corporation", "co", "company",
    "ltd", "limited", "llp", "plc", "group", "holdings",
}

_ATS_HOST_MARKERS = (
    ("greenhouse.io", "greenhouse"),
    ("lever.co", "lever"),
    ("myworkdayjobs.com", "workday"),
    ("myworkday.com", "workday"),
    ("smartrecruiters.com", "smartrecruiters"),
    ("ashbyhq.com", "ashby"),
    ("icims.com", "icims"),
    ("taleo.net", "taleo"),
    ("workable.com", "workable"),
    ("bamboohr.com", "bamboohr"),
    ("jobvite.com", "jobvite"),
)

ATS_LOOKUP_SKIP = frozenset({
    "workday", "smartrecruiters", "ashby", "icims", "taleo", "workable",
    "bamboohr", "jobvite",
})

_PROVIDER_DISPLAY_NAMES = {
    "apify_dice": "Dice",
    "dice": "Dice",
    "adzuna": "Adzuna",
    "jsearch": "JSearch",
    "remotive": "Remotive",
}

_HOST_DISPLAY_NAMES = {
    "dice.com": "Dice",
    "adzuna.com": "Adzuna",
    "linkedin.com": "LinkedIn",
    "indeed.com": "Indeed",
    "glassdoor.com": "Glassdoor",
    "ziprecruiter.com": "ZipRecruiter",
    "monster.com": "Monster",
    "remotive.com": "Remotive",
    "simplyhired.com": "SimplyHired",
}


def _get(job: Any, key: str):
    return job.get(key) if isinstance(job, dict) else getattr(job, key, None)


def _host(url: str | None) -> str:
    if not url:
        return ""
    try:
        return (urlsplit(str(url)).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _https(url: str | None) -> bool:
    return str(url or "").strip().lower().startswith("https://")


def _company_slug(name: str) -> str:
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    return "".join(word for word in words if word not in _LEGAL_SUFFIX_WORDS)


def ats_platform_from_url(url: str | None) -> str | None:
    host = _host(url)
    if not host:
        return None
    for marker, platform in _ATS_HOST_MARKERS:
        if host == marker or host.endswith("." + marker):
            return platform
    return None


def ats_board_token_from_url(url: str | None) -> str | None:
    """Board token / tenant / slug already present on an ATS URL."""
    if not url:
        return None
    try:
        parsed = urlsplit(str(url))
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    platform = ats_platform_from_url(url)
    if platform in {"greenhouse", "lever", "smartrecruiters", "ashby", "workable"}:
        token = next((part for part in parsed.path.strip("/").split("/") if part), "")
        return token or None
    if platform == "workday" and host.endswith(".myworkdayjobs.com"):
        return host.split(".")[0] or None
    if platform == "icims" and ".icims.com" in host:
        return host.split(".")[0] or None
    return None


def ats_tenant_matches_company(url: str, company: str) -> bool:
    token = ats_board_token_from_url(url)
    if not token or not company:
        return False
    a, b = _company_slug(token), _company_slug(company)
    if not a or not b:
        return False
    return a == b or a.startswith(b) or b.startswith(a)


def detect_ats_from_job(job: Any) -> tuple[str | None, str | None]:
    """Likely ATS platform and board token from URLs already on this record."""
    for field in ("unverified_application_url", "application_url", "direct_url", "source_url"):
        url = _get(job, field)
        platform = ats_platform_from_url(url)
        if platform:
            return platform, ats_board_token_from_url(url)
    return None, None


def is_followable_provider_redirect(url: str | None) -> bool:
    """Provider URLs that are designed to 302 toward the employer/ATS page.

    HTML listing pages (Dice job-detail, LinkedIn view) are not followable —
    they do not redirect, and this resolver does not scrape them.
    """
    if not url or not _https(url) or not is_safe_application_url(url):
        return False
    host = _host(url)
    try:
        path = urlsplit(str(url)).path.lower()
    except ValueError:
        return False
    if host == "ad.doubleclick.net":
        return True
    if host.endswith("adzuna.com"):
        return "/redirect/" in path or "/land/ad/" in path or (
            "/details/" in path and classify_url_specificity(url) == "SPECIFIC_JOB"
        )
    return False


def is_provider_listing_url(url: str | None) -> bool:
    """Whether a URL belongs to a discovery provider rather than an employer.

    Provider listings are useful, safe fallbacks but are never outbound
    application candidates.  This intentionally does not follow a redirect:
    an adapter must supply an already-extracted outbound URL before strict
    employer/ATS verification may inspect it.
    """
    host = _host(url)
    if not host:
        return False
    if classify_url_domain(url) == "aggregator":
        return True
    return any(host == suffix or host.endswith("." + suffix) for suffix in _HOST_DISPLAY_NAMES)


def outbound_application_candidate(job: Any) -> str | None:
    """Return the first provider-supplied non-listing application candidate.

    ``source_url`` deliberately has no role here: it is provenance and the
    View-on-provider fallback.  ``unverified_application_url`` is reserved
    for an extracted employer/ATS/external apply URL; application/direct
    fields are accepted only for legacy normalized sources such as JSearch.
    """
    if str(_get(job, "candidate_application_type") or "").strip().upper() == "APPLY_TO_EMAIL":
        return None
    for field in ("unverified_application_url", "application_url", "direct_url"):
        url = _get(job, field)
        if not url or str(url).strip().lower().startswith("mailto:"):
            continue
        if not is_provider_listing_url(url):
            return str(url)
    return None


def collect_outbound_apply_urls(job: Any) -> list[str]:
    """Provider-supplied outbound URLs eligible for shared verification.

    A provider listing or redirect is deliberately excluded.  Strict
    verification receives only a separately supplied outbound candidate, not
    the discovery page used as the user's fallback.
    """
    urls: list[str] = []
    seen: set[str] = set()
    if str(_get(job, "candidate_application_type") or "").strip().upper() == "APPLY_TO_EMAIL":
        return []

    def add(url: str | None, *, require_followable: bool = False) -> None:
        if not url or url in seen:
            return
        if str(url).strip().lower().startswith("mailto:"):
            return
        if not _https(url) or not is_safe_application_url(url):
            return
        if require_followable and not is_followable_provider_redirect(url):
            return
        seen.add(url)
        urls.append(url)

    for field in ("unverified_application_url", "application_url", "direct_url"):
        url = _get(job, field)
        if not is_provider_listing_url(url):
            add(url)
    return urls


_ROLE_SLUG_WORDS = {"engineer", "developer", "manager", "analyst", "architect", "scientist"}


def _slug_contradicts_title(url: str, title: str) -> bool:
    slug = urlsplit(url).path.rstrip("/").split("/")[-1]
    slug_words = set(re.findall(r"[a-z]+", slug.lower()))
    if len(slug_words) < 2 or not (slug_words & _ROLE_SLUG_WORDS):
        return False
    title_words = set(re.findall(r"[a-z0-9+#.]+", (title or "").lower()))
    return not (slug_words & title_words)


def is_safe_employer_or_ats_destination(url: str | None, job: Any) -> str | None:
    """Return 'employer' or 'ats' when `url` is a same-job employer/ATS page.

    Aggregator/provider hosts never qualify. ATS tenants must match the
    discovered company. No HTTP is performed here.
    """
    if not url or not _https(url) or not is_safe_application_url(url):
        return None
    if str(url).strip().lower().startswith("mailto:"):
        return None
    company = str(_get(job, "company") or "")
    if _slug_contradicts_title(url, str(_get(job, "title") or "")):
        return None
    domain = classify_url_domain(url, employer_name=company)
    if domain == "ats":
        if ats_platform_from_url(url) and not ats_tenant_matches_company(url, company):
            return None
        return "ats"
    if domain == "employer":
        return "employer"
    return None


def provider_display_name(job: Any, url: str | None = None) -> str:
    host = _host(url or _get(job, "source_url"))
    for suffix, name in _HOST_DISPLAY_NAMES.items():
        if host == suffix or host.endswith("." + suffix):
            return name
    source = str(_get(job, "source") or "").strip().lower()
    if source in _PROVIDER_DISPLAY_NAMES:
        return _PROVIDER_DISPLAY_NAMES[source]
    if source:
        return source.replace("_", " ").title()
    return "Provider"


def provider_fallback_label(job: Any, url: str | None) -> str:
    return f"View on {provider_display_name(job, url)}"
