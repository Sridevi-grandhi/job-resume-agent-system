"""Workday public career-site JSON; never credentials or application submission.

This is a website contract, not a guaranteed public integration API. If it
changes, the caller falls back to permitted HTML/browser content.
"""
from urllib.parse import quote, urlsplit

from app.services.job_normalization_service import build_normalized_job, strip_html
from .base import Collection, date
from .http import CollectionError


def collect(client, source, detection, limit):
    parsed = urlsplit(detection.url)
    tenant = parsed.hostname.split(".")[0]
    base = f"https://{parsed.hostname}/wday/cxs/{quote(tenant, safe='')}/{quote(detection.board, safe='')}"
    payload = client.fetch(base + "/jobs", method="POST", payload={"appliedFacets": {}, "limit": min(limit, 20), "offset": 0, "searchText": ""}).json()
    if not isinstance(payload, dict) or not isinstance(payload.get("jobPostings"), list):
        raise CollectionError("invalid_workday_payload")
    jobs = []
    for listing in payload["jobPostings"][:limit]:
        path = listing.get("externalPath")
        if not isinstance(path, str) or not path.startswith("/job/") or ".." in path:
            continue
        row = client.fetch(base + path).json().get("jobPostingInfo") or {}
        job_url = f"https://{parsed.hostname}/{quote(detection.board, safe='')}{path}"
        normalized = build_normalized_job(
            source="company_direct", source_mode="live", source_job_id=str(row.get("jobReqId") or path),
            title=row.get("title") or listing.get("title"), company=source.company_name,
            location=row.get("location") or listing.get("locationsText"), work_mode=row.get("remoteType") or "",
            job_type=row.get("timeType") or "", description=strip_html(row.get("jobDescription")),
            posted_at=date(row.get("startDate")), source_url=job_url, application_url=job_url,
        )
        if normalized:
            jobs.append(normalized)
    return Collection(jobs, isinstance(payload.get("total"), int) and payload["total"] == len(jobs))
