from urllib.parse import quote

from app.services.job_normalization_service import build_normalized_job, strip_html
from .base import Collection, date
from .http import CollectionError


def collect(client, source, detection, limit):
    base = f"https://api.smartrecruiters.com/v1/companies/{quote(detection.board, safe='')}/postings"
    payload = client.fetch(f"{base}?limit={limit}&offset=0").json()
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
        raise CollectionError("invalid_smartrecruiters_payload")
    jobs = []
    for listing in payload["content"][:limit]:
        row = client.fetch(f"{base}/{quote(str(listing['id']), safe='')}").json()
        sections = (row.get("jobAd") or {}).get("sections") or {}
        description = "\n".join(strip_html(section.get("text")) for section in sections.values() if isinstance(section, dict))
        loc = row.get("location") or {}
        normalized = build_normalized_job(
            source="company_direct", source_mode="live", source_job_id=str(row.get("id") or listing["id"]),
            title=row.get("name"), company=source.company_name,
            location=", ".join(str(loc[k]) for k in ("city", "region", "country") if loc.get(k)),
            work_mode="Remote" if loc.get("remote") is True else "", job_type=(row.get("typeOfEmployment") or {}).get("label"),
            description=description, requirements=strip_html((sections.get("qualifications") or {}).get("text")),
            posted_at=date(row.get("releasedDate")), source_url=row.get("postingUrl"), application_url=row.get("applyUrl") or row.get("postingUrl"),
        )
        if normalized:
            normalized.update(department=(row.get("department") or {}).get("label"), compensation=row.get("compensation"))
            jobs.append(normalized)
    return Collection(jobs, isinstance(payload.get("totalFound"), int) and payload["totalFound"] == len(jobs))
