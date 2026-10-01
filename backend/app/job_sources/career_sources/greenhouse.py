from urllib.parse import quote

from app.job_sources.greenhouse import GreenhouseAdapter
from .base import Collection
from .http import CollectionError


def collect(client, source, detection, limit):
    payload = client.fetch(f"https://boards-api.greenhouse.io/v1/boards/{quote(detection.board, safe='')}/jobs?content=true").json()
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise CollectionError("invalid_greenhouse_payload")
    rows = payload["jobs"]
    jobs = []
    parser = GreenhouseAdapter()
    for row in rows[:limit]:
        normalized = parser.normalize_job({**row, "_company_name": source.company_name, "_board_token": detection.board})
        if normalized:
            # Department is metadata, never an invented skill.
            normalized["skills"] = ""
            normalized["department"] = ", ".join(d.get("name", "") for d in row.get("departments", []) if isinstance(d, dict))
            normalized["source_url"] = row.get("absolute_url")
            jobs.append(normalized)
    return Collection(jobs, len(rows) <= limit and len(jobs) == len(rows))
