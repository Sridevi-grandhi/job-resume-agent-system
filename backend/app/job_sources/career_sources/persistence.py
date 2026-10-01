"""Isolated merge/provenance helpers for official company discovery."""
import json
import re
from urllib.parse import urlsplit, parse_qsl, urlencode

from app.models.discovered_job_model import DiscoveredJob


def metadata(job):
    raw = job.get("career_metadata") if isinstance(job, dict) else getattr(job, "career_metadata", None)
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _url(url):
    if not url:
        return ""
    parsed = urlsplit(url)
    # Keep requisition query parameters; only drop known tracking parameters.
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parsed.query) if not k.lower().startswith("utm_") and k.lower() not in {"source", "ref"}))
    return (parsed.hostname or "").lower() + parsed.path.rstrip("/") + ("?" + query if query else "")


def _identity(company, title, location):
    return tuple(re.sub(r"\s+", " ", (value or "").strip()).casefold() for value in (company, title, location))


def find_duplicate(db, job):
    official = bool(job.get("career_metadata"))
    # This extension must not change aggregator-to-aggregator merge behavior.
    query = db.query(DiscoveredJob)
    if not official:
        if job.get("source") not in {"jsearch", "adzuna", "apify_dice", "greenhouse", "lever"}:
            return None
        query = query.filter(DiscoveredJob.career_metadata.isnot(None))
    else:
        exact = query.filter(DiscoveredJob.source == job["source"], DiscoveredJob.source_job_id == job.get("source_job_id")).first()
        if exact:
            return exact
    from sqlalchemy import func
    rows = query.filter(func.lower(DiscoveredJob.company) == job["company"].strip().lower()).all()
    identity = _identity(job["company"], job["title"], job.get("location"))
    urls = {_url(job.get(field)) for field in ("unverified_application_url", "application_url", "direct_url", "source_url")} - {""}
    matches = []
    for row in rows:
        row_urls = {_url(getattr(row, field)) for field in ("unverified_application_url", "application_url", "direct_url", "source_url")} - {""}
        same_identity = identity == _identity(row.company, row.title, row.location)
        from app.services.job_normalization_service import classify_url_specificity
        shared_specific_url = any(classify_url_specificity("https://" + url) == "SPECIFIC_JOB" for url in urls & row_urls)
        if shared_specific_url and same_identity:
            return row
        # Two official requisitions are not merged just because their titles
        # coincide. Ambiguous aggregator matches also remain separate in DB.
        if same_identity and bool(row.career_metadata) != official:
            matches.append(row)
    return matches[0] if len(matches) == 1 else None


def apply_metadata(row, job):
    if not job.get("career_metadata"):
        return
    row.career_metadata = job["career_metadata"]
    row.source = job["source"]
    row.source_job_id = job.get("source_job_id")
    row.external_id = job.get("external_id")
    row.source_url = job.get("source_url") or row.source_url
    row.career_page_url = metadata(job).get("career_url") or row.career_page_url
    if job.get("unverified_application_url"):
        row.unverified_application_url = job["unverified_application_url"]
        row.candidate_application_type = None
    if job.get("posted_at"):
        row.posted_at = job["posted_at"]
