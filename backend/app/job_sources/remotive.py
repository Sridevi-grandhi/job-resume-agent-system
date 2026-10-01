"""Remotive source adapter for Updated Job Search's new pipeline.

Wraps Remotive's public API (https://remotive.com/api/remote-jobs) as a
clean JobSourceAdapter — LIVE, no API key required. This is a fresh,
generic implementation (fetches broadly, no preference-coupled query
building) so it plugs into the same source-adapter pipeline as
Greenhouse/Lever/Workday.

Independent of app.agents.updated_job_search_agent, which is kept in place
untouched (deprecated internally, unreachable from the UI — see README).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests

from app.job_sources.base import JobSourceAdapter, SourceHealth
from app.services import url_safety
from app.services.job_normalization_service import build_normalized_job, strip_html

logger = logging.getLogger(__name__)

API_URL = "https://remotive.com/api/remote-jobs"
USER_AGENT = "CareerBoostAI-JobDiscovery/1.0 (+educational project)"
TIMEOUT = (5, 15)


class RemotiveAdapter(JobSourceAdapter):
    source_name = "remotive"
    source_type = "aggregator"

    def fetch_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        session = requests.Session()
        try:
            response = url_safety.safe_get(
                session, API_URL,
                params={"limit": str(min(limit, 200))},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as error:
            logger.warning("remotive_fetch_failed error=%s", type(error).__name__)
            raise
        finally:
            session.close()
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        return [job for job in (jobs or []) if isinstance(job, dict)][:limit]

    def normalize_job(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        job_url = raw.get("url")
        if not job_url or not str(job_url).startswith(("http://", "https://")):
            return None

        tags = raw.get("tags") or []
        skills = ", ".join(str(tag).strip() for tag in tags if str(tag).strip()) if isinstance(tags, list) else str(tags)
        job_type = str(raw.get("job_type") or "").replace("_", " ").title() or "Not specified"

        posted_at = None
        publication_date = raw.get("publication_date")
        if publication_date:
            try:
                posted_at = datetime.fromisoformat(str(publication_date).replace("Z", "+00:00"))
            except ValueError:
                posted_at = None

        # Remotive's public API only ever exposes ITS OWN job detail page in
        # "url" — never a distinct employer/original-application URL. Storing
        # it as direct_url (or leaving application_url to fall back to it)
        # is exactly what made Apply silently open remotive.com instead of
        # the employer's real application page. It belongs in source_url
        # only — a discovery/listing reference, not an apply destination —
        # so resolve_application_destination() correctly classifies this job
        # as AGGREGATOR_LISTING ("View Listing") rather than a false Direct
        # Apply. Never invent an employer URL Remotive doesn't provide.
        return build_normalized_job(
            source=self.source_name,
            source_mode="live",
            source_job_id=str(raw.get("id")) if raw.get("id") is not None else None,
            company=raw.get("company_name") or "",
            title=raw.get("title") or "",
            location=raw.get("candidate_required_location") or "Remote",
            work_mode="Remote",
            job_type=job_type,
            description=strip_html(raw.get("description")),
            skills=skills,
            posted_at=posted_at,
            direct_url=None,
            source_url=job_url,
        )

    def health_check(self) -> SourceHealth:
        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            session = requests.Session()
            response = url_safety.safe_get(
                session, API_URL,
                params={"limit": "1"},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=(5, 8),
            )
            session.close()
            healthy = response.status_code == 200
            return SourceHealth(
                self.source_name, self.source_type, "live", healthy, checked_at,
                error=None if healthy else f"HTTP {response.status_code}",
            )
        except Exception as error:
            return SourceHealth(self.source_name, self.source_type, "live", False, checked_at, error=type(error).__name__)
