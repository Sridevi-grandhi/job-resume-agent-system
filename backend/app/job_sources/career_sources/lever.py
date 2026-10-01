from urllib.parse import quote, urlsplit

from app.job_sources.lever import LeverAdapter
from app.services.job_normalization_service import strip_html
from .base import Collection
from .http import CollectionError


def collect(client, source, detection, limit):
    api = "api.eu.lever.co" if urlsplit(detection.url).hostname == "jobs.eu.lever.co" else "api.lever.co"
    rows = client.fetch(f"https://{api}/v0/postings/{quote(detection.board, safe='')}?mode=json&limit={limit + 1}").json()
    if not isinstance(rows, list):
        raise CollectionError("invalid_lever_payload")
    parser, jobs = LeverAdapter(), []
    for row in rows[:limit]:
        normalized = parser.normalize_job({**row, "_company_name": source.company_name, "_company_slug": detection.board})
        if normalized:
            requirements = "\n".join(strip_html(item.get("content")) for item in row.get("lists", []) if isinstance(item, dict))
            normalized["description"] = "\n".join(filter(None, (
                normalized["description"], requirements, strip_html(row.get("additionalPlain") or row.get("additional")),
            )))
            normalized.update(
                skills="", department=(row.get("categories") or {}).get("department") or (row.get("categories") or {}).get("team"),
                work_mode=row.get("workplaceType") or normalized["work_mode"],
                source_url=row.get("hostedUrl") or row.get("applyUrl"),
                requirements=requirements,
            )
            salary = row.get("salaryRange") or {}
            normalized.update(salary_min=salary.get("min"), salary_max=salary.get("max"), salary_currency=salary.get("currency"), salary_interval=salary.get("interval"))
            jobs.append(normalized)
    return Collection(jobs, len(rows) <= limit and len(rows) == len(jobs))
