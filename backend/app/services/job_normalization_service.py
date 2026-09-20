"""Shared normalized-job shape + hashing helpers used by every source adapter.

Every adapter's normalize_job() returns a dict matching NORMALIZED_JOB_FIELDS.
This module owns the identity/change-detection primitives (fingerprint,
content_hash) so job_change_detection_service can stay adapter-agnostic.
"""

from __future__ import annotations

import hashlib
import re
from html import unescape
from typing import Any


NORMALIZED_JOB_FIELDS = (
    "source", "source_mode", "source_job_id", "external_id",
    "company", "company_logo_url", "title", "location", "work_mode", "job_type",
    "description", "requirements", "skills",
    "posted_at", "direct_url", "source_url", "application_url",
)


def strip_html(raw_html: str | None) -> str:
    if not raw_html:
        return ""
    text = str(raw_html)
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def build_normalized_job(
    *,
    source: str,
    source_mode: str,
    source_job_id: str | None,
    company: str,
    title: str,
    company_logo_url: str | None = None,
    location: str = "",
    work_mode: str = "",
    job_type: str = "",
    description: str = "",
    requirements: str = "",
    skills: str = "",
    posted_at=None,
    direct_url: str | None = None,
    source_url: str | None = None,
    application_url: str | None = None,
) -> dict[str, Any] | None:
    title = _clean(title)
    company = _clean(company)
    description = _clean(description)
    if not title or not company or len(description) < 20:
        return None

    external_id = f"{source}:{source_job_id}" if source_job_id else None
    return {
        "source": source,
        "source_mode": source_mode,
        "source_job_id": _clean(source_job_id) or None,
        "external_id": external_id,
        "company": company,
        "company_logo_url": _clean(company_logo_url) or None,
        "title": title,
        "location": _clean(location) or "Not specified",
        "work_mode": _clean(work_mode) or "Not specified",
        "job_type": _clean(job_type) or "Full-time",
        "description": description,
        "requirements": _clean(requirements),
        "skills": _clean(skills),
        "posted_at": posted_at,
        "direct_url": _clean(direct_url) or None,
        "source_url": _clean(source_url) or None,
        "application_url": _clean(application_url) or _clean(direct_url) or None,
    }


def is_safe_application_url(url: str | None) -> bool:
    """Lightweight safety check for a URL the user's BROWSER will open
    directly (never fetched server-side, so no DNS resolution needed here —
    see url_safety.assert_public_http_url for the server-side-fetch case).
    HTTP(S) only, no embedded credentials, and not an obviously local/
    internal hostname or IP literal. Malformed input is simply unsafe, never
    an exception."""
    from ipaddress import ip_address
    from urllib.parse import urlsplit

    if not url or not str(url).strip():
        return False
    try:
        parsed = urlsplit(str(url).strip())
    except ValueError:
        return False
    if parsed.scheme.lower() not in ("http", "https"):
        return False
    if parsed.username or parsed.password:
        return False
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname:
        return False
    if hostname in {"localhost", "0.0.0.0"} or hostname.endswith(".localhost"):
        return False
    try:
        if not ip_address(hostname).is_global:
            return False
    except ValueError:
        pass  # a normal hostname, not an IP literal — fine
    return True


# Which source adapters resolve to a third-party ATS's own hosted page
# (still the employer's genuine application page, just not on the
# employer's own domain) vs a true employer-owned destination. Diagnostic
# classification only — both are shown as "Apply", never "View Listing".
_ATS_HOSTED_SOURCES = {"greenhouse", "lever", "workday"}

# Same ATS-vs-employer distinction, but by the candidate URL's own domain —
# needed for a broad multi-employer provider (e.g. JSearch) whose `source`
# is the provider itself, not a single ATS, so the per-source check above
# can't tell DIRECT_ATS from DIRECT_EMPLOYER for it. A well-known,
# non-exhaustive list of real ATS platforms — never used to fabricate a
# destination, only to classify one already returned by the provider.
_ATS_HOSTED_DOMAINS = {
    "greenhouse.io", "boards.greenhouse.io",
    "lever.co", "jobs.lever.co",
    "myworkday.com", "wd1.myworkdayjobs.com", "myworkdayjobs.com",
    "icims.com", "taleo.net", "smartrecruiters.com", "ashbyhq.com",
    "workable.com", "bamboohr.com", "jobvite.com", "successfactors.com",
    "brassring.com", "recruiting.paylocity.com",
}


def _registrable_domain(url: str | None) -> str:
    """Best-effort registrable-ish domain for a simple suffix match against
    _ATS_HOSTED_DOMAINS — not a full public-suffix-list lookup, just the last
    two labels (fine for the well-known ATS hosts above)."""
    from urllib.parse import urlsplit

    if not url:
        return ""
    hostname = (urlsplit(str(url)).hostname or "").lower().rstrip(".")
    parts = hostname.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else hostname


def _is_ats_hosted_url(url: str | None) -> bool:
    hostname = ""
    try:
        from urllib.parse import urlsplit

        hostname = (urlsplit(str(url)).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return hostname in _ATS_HOSTED_DOMAINS or _registrable_domain(url) in _ATS_HOSTED_DOMAINS


# A well-known, non-exhaustive list of job-board/aggregator domains — a URL
# on one of these is a LISTING page about a job, never the employer's own
# application page, regardless of anything a provider's own "is this a
# direct apply link" flag claims about it (see classify_url_domain's
# docstring for why that flag can't be trusted alone).
_AGGREGATOR_DOMAINS = {
    "linkedin.com", "indeed.com", "dice.com", "ziprecruiter.com", "builtin.com",
    "recruit.net", "jobleads.com", "bebee.com", "techfetch.com", "learn4good.com",
    "lensa.com", "trabajo.org", "glassdoor.com", "monster.com", "careerbuilder.com",
    "simplyhired.com", "careerjet.com", "jobrapido.com", "adzuna.com", "jora.com",
    "talent.com", "snagajob.com", "flexjobs.com", "weworkremotely.com",
    "jobg8.com", "appcast.io", "neuvoo.com",
}

# Remainder text allowed after a matched employer-name/domain prefix for the
# candidate URL's domain label to still count as "the same employer" — a
# career-site suffix word (capitalone -> capitalonecareers) or a generic
# legal-entity abbreviation a formal company name spells out in full but a
# domain shortens (Oshkosh Corporation -> oshkoshcorp.com).
_ACCEPTABLE_DOMAIN_SUFFIXES = {
    "", "careers", "career", "jobs", "job", "recruiting", "recruitment",
    "talent", "hiring", "employment", "corp", "corporation", "inc", "llc",
    "co", "company", "group", "holdings",
}
_LEGAL_SUFFIX_WORDS = {
    "inc", "incorporated", "llc", "corp", "corporation", "co", "company",
    "ltd", "limited", "llp", "plc", "group", "holdings",
}
_NAME_STOPWORDS = {"the", "and", "of", "for"}


def _domain_label(registrable_domain: str) -> str:
    """The part of a registrable domain before its TLD — 'pnc' for 'pnc.com',
    'uline' for 'uline.jobs'."""
    return registrable_domain.rsplit(".", 1)[0] if "." in registrable_domain else registrable_domain


def _label_matches(candidate_label: str, slug: str) -> bool:
    if not candidate_label or not slug or len(slug) < 3:
        return False
    if candidate_label == slug:
        return True
    if candidate_label.startswith(slug):
        return candidate_label[len(slug):] in _ACCEPTABLE_DOMAIN_SUFFIXES
    return False


def _employer_website_matches(candidate_registrable: str, employer_registrable: str) -> bool:
    """True when candidate_registrable is the employer's own domain — exact
    match covers any subdomain of it (career.pnc.com and www.pnc.com both
    reduce to 'pnc.com' before this is ever called), and the fuzzy fallback
    covers a distinct-but-branded career domain (capitalone.com vs
    capitalonecareers.com) without hardcoding any specific employer."""
    if not candidate_registrable or not employer_registrable:
        return False
    if candidate_registrable == employer_registrable:
        return True
    return _label_matches(_domain_label(candidate_registrable), _domain_label(employer_registrable))


def _employer_name_tokens(name: str | None) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    return [word for word in words if word not in _LEGAL_SUFFIX_WORDS and word not in _NAME_STOPWORDS]


def _employer_name_matches_domain(candidate_registrable: str, employer_name: str | None) -> bool:
    """Fallback employer match using only the company's display name — used
    when no employer_website is available (e.g. backfilling already-stored
    rows). Tries each significant word and each left-to-right word prefix of
    the name against the candidate domain's label (schwabjobs.com matches
    "Charles Schwab" via the word "schwab"; nationalacademies.org matches
    "The National Academies of ..." via the two-word prefix), plus an
    initials match for acronym-style domains (gm.com for "General Motors").
    Purely generic tokenization — never a specific employer's name."""
    candidate_label = _domain_label(candidate_registrable)
    tokens = _employer_name_tokens(employer_name)
    if not candidate_label or not tokens:
        return False

    slugs: set[str] = set()
    prefix = ""
    for token in tokens:
        prefix += token
        if len(prefix) >= 3:
            slugs.add(prefix)
        if len(token) >= 4:
            slugs.add(token)
    if any(_label_matches(candidate_label, slug) for slug in slugs):
        return True

    if len(tokens) >= 2:
        initials = "".join(token[0] for token in tokens)
        if len(initials) >= 2 and candidate_label == initials:
            return True
    return False


def classify_url_domain(
    url: str | None, *, employer_website: str | None = None, employer_name: str | None = None,
) -> str | None:
    """Semantic classification of a candidate apply/listing URL's domain —
    deliberately independent of any "is this a direct link" flag a provider
    returns (JSearch's job_apply_is_direct was verified, against live data,
    to be False even for options whose URL was a genuine employer career
    page or ATS-hosted application — so that flag alone cannot be trusted to
    decide Apply vs View Listing).

    Returns "aggregator" (job board / listing site — never Apply),
    "ats" (known third-party applicant-tracking-system domain — Apply),
    "employer" (the employer's own domain, verified against employer_website
    and/or employer_name — Apply), or None when nothing here lets it be
    classified conclusively (caller should fall back to its own remaining
    signals, e.g. a provider's explicit is_direct flag, rather than guessing
    from the URL text alone).
    """
    candidate = _registrable_domain(url)
    if not candidate:
        return None
    if candidate in _AGGREGATOR_DOMAINS:
        return "aggregator"
    if _is_ats_hosted_url(url):
        return "ats"
    employer_domain = _registrable_domain(employer_website) if employer_website else ""
    if employer_domain and _employer_website_matches(candidate, employer_domain):
        return "employer"
    if employer_name and _employer_name_matches_domain(candidate, employer_name):
        return "employer"
    return None


# Priority order for rank_apply_candidates — lower is better. Mirrors the
# exact priority JSearchAdapter._pick_apply_urls has always used (a domain-
# verified employer/ATS match beats a provider's own unverified is_direct
# flag, which beats an aggregator/unclassified listing) — now expressed as
# one shared, inspectable ranking instead of duplicated per-adapter logic.
_CANDIDATE_CONFIDENCE_RANK = {
    "employer": 0, "ats": 1, "provider_flagged_direct": 2,
    "aggregator": 3, "unclassified_listing": 3,
}


def rank_apply_candidates(
    options: list[dict[str, Any]], *, employer_website: str | None = None, employer_name: str | None = None,
) -> list[dict[str, Any]]:
    """Classifies and ranks EVERY safe-looking apply-option URL from a
    provider's raw apply_options-style list — preserving all of them
    (never silently discarding an alternate destination), each annotated
    with WHY it was classified the way it was. A provider's own is_direct
    flag is corroborating evidence only for an otherwise-unclassified
    domain — never enough on its own for a known aggregator (see
    classify_url_domain's docstring).

    `options` — a list of {"apply_link": str, "is_direct": bool,
    "publisher": str | None} dicts (JSearch's apply_options shape; any
    provider that can express its alternates this way can reuse this).

    Returns a list of {"url", "publisher", "domain_class", "confidence"}
    dicts, stably sorted by decreasing confidence — "employer" and "ats"
    (domain-verified direct destinations) first, then
    "provider_flagged_direct" (legacy trust — the provider says it's
    direct but the domain itself couldn't be classified), then
    "aggregator"/"unclassified_listing". Does NOT check URL safety or
    specificity — see is_safe_application_url / classify_url_specificity
    for those, applied downstream by the caller.
    """
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for option in options:
        url = option.get("apply_link") if isinstance(option, dict) else None
        if not url or url in seen:
            continue
        seen.add(url)
        domain_class = classify_url_domain(url, employer_website=employer_website, employer_name=employer_name)
        if domain_class in ("employer", "ats"):
            confidence = domain_class
        elif domain_class == "aggregator":
            confidence = "aggregator"
        elif option.get("is_direct"):
            confidence = "provider_flagged_direct"
        else:
            confidence = "unclassified_listing"
        candidates.append({
            "url": url, "publisher": option.get("publisher"),
            "domain_class": domain_class, "confidence": confidence,
        })
    candidates.sort(key=lambda candidate: _CANDIDATE_CONFIDENCE_RANK[candidate["confidence"]])
    return candidates


def select_apply_urls_from_candidates(
    candidates: list[dict[str, Any]], *, fallback_source_url: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Reduces a rank_apply_candidates() list to the (application_url,
    direct_url, source_url) triple the rest of the pipeline expects — the
    best verified-direct candidate, a second one if any (kept as direct_url
    for provenance), and the best listing candidate (or fallback_source_url
    — typically the provider's primary apply link — when nothing was even
    classifiable as a listing)."""
    direct = [c for c in candidates if c["confidence"] in ("employer", "ats", "provider_flagged_direct")]
    listing = [c for c in candidates if c["confidence"] in ("aggregator", "unclassified_listing")]
    application_url = direct[0]["url"] if direct else None
    direct_url = direct[1]["url"] if len(direct) > 1 else application_url
    source_url = listing[0]["url"] if listing else fallback_source_url
    return application_url, direct_url, source_url


# Generic-page URL path keywords — a path segment that IS one of these
# (normalized: lowercased, hyphens/underscores stripped), with no
# identifier segment anywhere else in the path, marks a careers homepage /
# search / listing page rather than one specific posting.
_GENERIC_PATH_KEYWORDS = {
    "jobs", "job", "careers", "career", "jobsearch", "searchjobs", "search",
    "jobsearchresults", "searchresults", "joblistings", "listings",
    "opportunities", "openings", "jobopenings", "findajob", "browsejobs",
    "careeropportunities", "vacancies", "jobboard",
}

# Query-parameter names that, when present with a non-empty value, are a
# strong signal the URL identifies ONE specific posting (a generic
# search/listing page never carries one of these).
_ID_LIKE_QUERY_KEYS = {
    "jobid", "job_id", "requisitionid", "requisition_id", "reqid", "req_id",
    "positionid", "position_id", "gh_jid", "jvid", "vacancyid", "postingid",
    "posting_id",
}

# A path segment carrying a run of 4+ consecutive digits ANYWHERE within it
# reads as a requisition/job identifier — "R215394", "00070318331",
# "1408174900", "1443152803780-senior-project-manager", and (critically)
# an id embedded mid-slug like "26-158712-java-developer-merrifield-va"
# (verified against live data — anchoring the check to only the start or
# end of the segment missed this real, currently-active posting). 4 digits
# is conservative enough to avoid matching small incidental numbers while
# still catching every real ATS/employer id format observed in practice.
_ID_DIGIT_RUN_RE = re.compile(r"\d{4,}")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _looks_like_id_segment(segment: str) -> bool:
    lowered = segment.lower()
    return bool(_UUID_RE.match(lowered) or _ID_DIGIT_RUN_RE.search(lowered))


def classify_url_specificity(url: str | None) -> str:
    """Deterministic, URL-structure classification of whether a URL points
    at ONE specific job posting vs a generic careers/search/listing page —
    completely independent of AVAILABILITY (see resolve_apply_action's
    docstring for why the two must never be collapsed into one field).
    Domain-agnostic and never hardcoded to a specific employer/ATS.

    A real employer domain returning HTTP 200 is NOT enough evidence for
    Apply on its own — e.g. an employer's generic
    ".../JobSearchResults?keyword=java" page is a real, reachable,
    employer-owned page, but it is not the specific posting a user could
    apply to.

    Returns "SPECIFIC_JOB" (an identifier — query param or path segment —
    was found), "GENERIC_EMPLOYER_PAGE" (the path structurally matches a
    careers homepage / search / listing page with no identifier), or
    "UNKNOWN" (neither could be conclusively determined — never guessed as
    specific just because it isn't obviously generic)."""
    from urllib.parse import parse_qs, urlsplit

    if not url:
        return "UNKNOWN"
    try:
        parsed = urlsplit(str(url))
    except ValueError:
        return "UNKNOWN"

    segments = [segment for segment in parsed.path.split("/") if segment]
    query = parse_qs(parsed.query.lower())

    # Strongest signal: an identifier-shaped query parameter with a value.
    for key, values in query.items():
        if key in _ID_LIKE_QUERY_KEYS and any(value.strip() for value in values):
            return "SPECIFIC_JOB"

    # An identifier-shaped path segment, anywhere in the path.
    if any(_looks_like_id_segment(segment) for segment in segments):
        return "SPECIFIC_JOB"

    # No path at all — a bare domain/homepage is always generic.
    if not segments:
        return "GENERIC_EMPLOYER_PAGE"

    normalized = [re.sub(r"[^a-z0-9]", "", segment.lower()) for segment in segments]
    if all(segment in _GENERIC_PATH_KEYWORDS for segment in normalized if segment):
        return "GENERIC_EMPLOYER_PAGE"
    # A trailing generic keyword (careers.example.com/en/search-jobs) is
    # still generic even when an earlier segment (a language code, etc.)
    # isn't itself in the generic-keyword list.
    if normalized and normalized[-1] in _GENERIC_PATH_KEYWORDS:
        return "GENERIC_EMPLOYER_PAGE"

    return "UNKNOWN"


def resolve_application_destination(job: Any) -> dict[str, Any]:
    """The ONE authoritative Apply-destination resolver. Priority order:
    application_url (explicit apply link) -> direct_url (canonical original
    job/posting URL) -> source_url (source listing page, last resort — e.g.
    Remotive's own job page when no employer URL was ever available). Never
    constructs or guesses a URL — only returns values actually obtained from
    the source, and only after a lightweight safety check. Works against
    either a normalized dict or a DiscoveredJob ORM row.

    Returns {"url", "type", "is_direct"}:
      - type is "DIRECT_EMPLOYER" / "DIRECT_ATS" (application_url or
        direct_url won) or "AGGREGATOR_LISTING" (only source_url was
        available — e.g. Remotive with no employer URL) or None (nothing
        usable at all).
      - is_direct is True only for the first two tiers — the frontend uses
        this to label the action "Apply" vs "View Listing" so an aggregator
        listing is never falsely presented as a direct employer apply.
    """
    get = job.get if isinstance(job, dict) else (lambda key: getattr(job, key, None))
    source = str(get("source") or "").strip().lower()
    source_is_ats = source in _ATS_HOSTED_SOURCES

    for field in ("application_url", "direct_url"):
        candidate = get(field)
        if candidate and is_safe_application_url(candidate):
            tier_type = "DIRECT_ATS" if (source_is_ats or _is_ats_hosted_url(candidate)) else "DIRECT_EMPLOYER"
            return {"url": candidate, "type": tier_type, "is_direct": True}

    source_url = get("source_url")
    if source_url and is_safe_application_url(source_url):
        return {"url": source_url, "type": "AGGREGATOR_LISTING", "is_direct": False}
    return {"url": None, "type": None, "is_direct": False}


def resolve_apply_action(job: Any) -> dict[str, Any]:
    """Composes THREE deliberately independent questions into the ONE
    action contract the frontend renders — the frontend must never
    re-derive any of this itself:
      - TYPE (resolve_application_destination): is the stored URL a genuine
        employer/ATS destination, or only an aggregator listing? Never
        changes based on availability or specificity.
      - SPECIFICITY (classify_url_specificity): does that URL point at ONE
        specific posting, or a generic careers/search/listing page? A real
        employer domain returning HTTP 200 is not enough — a generic
        ".../JobSearchResults?keyword=java" page is a genuine, reachable
        employer page that still isn't something to label "Apply".
      - AVAILABILITY (job.availability_status): is that specific posting
        still open right now? A DIRECT_EMPLOYER URL that 404s is still a
        DIRECT_EMPLOYER, SPECIFIC_JOB URL — for a job that's gone — never
        reclassified as generic or as an aggregator listing.

    "Apply" requires ALL of: TYPE is DIRECT_EMPLOYER/DIRECT_ATS,
    SPECIFICITY is SPECIFIC_JOB, AND availability is ACTIVE. Anything short
    of that (generic page, unproven specificity, unverified/expired
    availability) never claims a verified direct apply — but only a
    CONFIRMED-expired specific job disables the button entirely; a real
    employer page whose specificity/availability just isn't proven still
    gets a real, clickable "Open Employer Site" destination rather than
    nothing.

    Returns everything resolve_application_destination returns
    (url/type/is_direct) plus:
      - availability_status: "ACTIVE" | "EXPIRED" | "UNKNOWN"
      - destination_specificity: "SPECIFIC_JOB" | "GENERIC_EMPLOYER_PAGE" | "UNKNOWN"
      - action_label: "Apply" | "Open Employer Site" | "Job Closed" | "View Listing"
      - can_apply: True ONLY for a verified SPECIFIC_JOB + ACTIVE destination
        (or an aggregator listing, which was never claiming "Apply" in the
        first place — View Listing is still a legitimate navigable action).
      - secondary_action: {"label": "View Source Listing", "url": ...} | None
        — for a DIRECT_EMPLOYER/DIRECT_ATS job, source_url is where this
        posting was ORIGINALLY discovered (often an aggregator). If the
        employer/ATS destination itself is blocked for the user (a 403,
        Cloudflare challenge, geo restriction — none of which CareerBoost
        can bypass), this gives them another real route to the same
        opportunity. Never shown when there's nothing to fall back to
        (source_url missing, unsafe, or identical to the primary URL) or
        when the primary action IS already the listing (AGGREGATOR_LISTING,
        no-url).
    """
    destination = resolve_application_destination(job)
    get = job.get if isinstance(job, dict) else (lambda key: getattr(job, key, None))
    availability_status = get("availability_status") or "UNKNOWN"

    if destination["url"] is None:
        return {
            **destination, "availability_status": availability_status,
            "destination_specificity": "UNKNOWN", "action_label": "Apply", "can_apply": False,
            "secondary_action": None,
        }

    if destination["type"] == "AGGREGATOR_LISTING":
        return {
            **destination, "availability_status": availability_status,
            "destination_specificity": "UNKNOWN", "action_label": "View Listing", "can_apply": True,
            "secondary_action": None,
        }

    source_url = get("source_url")
    secondary_action = None
    if source_url and source_url != destination["url"] and is_safe_application_url(source_url):
        secondary_action = {"label": "View Source Listing", "url": source_url}

    specificity = classify_url_specificity(destination["url"])
    base = {
        **destination, "availability_status": availability_status,
        "destination_specificity": specificity, "secondary_action": secondary_action,
    }

    if specificity == "SPECIFIC_JOB":
        if availability_status == "EXPIRED":
            return {**base, "action_label": "Job Closed", "can_apply": False}
        if availability_status == "ACTIVE":
            return {**base, "action_label": "Apply", "can_apply": True}
        # SPECIFIC_JOB but availability unverified — safe fallback, never
        # claim a verified Apply, but still a real destination to open.
        return {**base, "action_label": "Open Employer Site", "can_apply": False}

    # GENERIC_EMPLOYER_PAGE or UNKNOWN specificity — never a verified Apply
    # regardless of availability (a generic page returning 200 proves the
    # SITE is up, not that a specific posting is), but still a real
    # destination worth sending the user to.
    return {**base, "action_label": "Open Employer Site", "can_apply": False}


def resolve_application_url(job: Any) -> str | None:
    """Authoritative Apply destination URL only — see
    resolve_application_destination() for the full classification (used by
    the frontend to decide "Apply" vs "View Listing")."""
    return resolve_application_destination(job)["url"]


def compute_fingerprint(job: dict[str, Any]) -> str:
    """Stable fallback identity when a source has no source_job_id.

    Prefers the canonical direct_url; otherwise falls back to a normalized
    title+company+location tuple. Never uses title alone.
    """
    basis = _clean(job.get("direct_url"))
    if not basis:
        key = f"{job['source']}|{job['title'].strip().lower()}|{job['company'].strip().lower()}|{job.get('location', '').strip().lower()}"
        basis = key
    return hashlib.sha256(basis.encode("utf-8", errors="ignore")).hexdigest()


def compute_content_hash(job: dict[str, Any]) -> str:
    """Hash of the fields whose change should mark a job UPDATED."""
    basis = "|".join(
        [
            job.get("description", ""),
            job.get("location", ""),
            job.get("work_mode", ""),
            job.get("job_type", ""),
            job.get("direct_url") or "",
        ]
    )
    return hashlib.sha256(basis.encode("utf-8", errors="ignore")).hexdigest()
