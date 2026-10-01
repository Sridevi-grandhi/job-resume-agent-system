from urllib.parse import quote

from app.services.job_normalization_service import build_normalized_job, strip_html
from .base import Collection, date
from .http import CollectionError


def collect(client, source, detection, limit):
    payload = client.fetch(f"https://api.ashbyhq.com/posting-api/job-board/{quote(detection.board, safe='')}?includeCompensation=true").json()
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise CollectionError("invalid_ashby_payload")
    rows, jobs = payload["jobs"], []
    for row in rows[:limit]:
        if row.get("isListed") is False:
            continue
        normalized = build_normalized_job(
            source="company_direct", source_mode="live", source_job_id=row.get("id"),
            company=source.company_name, title=row.get("title"), location=row.get("location"),
            work_mode=row.get("workplaceType") or ("Remote" if row.get("isRemote") else ""),
            job_type=row.get("employmentType"), description=strip_html(row.get("descriptionPlain") or row.get("descriptionHtml")),
            posted_at=date(row.get("publishedAt")), source_url=row.get("jobUrl"), application_url=row.get("applyUrl") or row.get("jobUrl"),
        )
        if normalized:
            normalized["department"] = row.get("department") or row.get("team")
            normalized["compensation"] = row.get("compensation")
            jobs.append(normalized)
    return Collection(jobs, len(rows) <= limit and len(jobs) == len(rows))
