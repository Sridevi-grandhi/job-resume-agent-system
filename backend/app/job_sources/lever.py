"""Lever job postings source adapter — an internal connector.

Retrieves jobs for whichever real employers are configured in the
CompanySource registry with source_type=LEVER (see
job_source_registry_service.sync_company_sources_from_env, driven by
LEVER_COMPANY_SOURCES). Uses Lever's public Postings API
(https://api.lever.co/v0/postings/<company>?mode=json).

No configured company -> no jobs, unless demo_mode is on (local
development/demos only) -> a realistic MOCK catalog. Never fabricates a
real company's postings.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests

from app.job_sources.base import JobSourceAdapter, SourceHealth
from app.job_sources.mock_data import generate_mock_jobs
from app.services import url_safety
from app.services.job_normalization_service import build_normalized_job, strip_html

logger = logging.getLogger(__name__)

API_BASE = "https://api.lever.co/v0/postings/{company}"
USER_AGENT = "CareerBoostAI-JobDiscovery/1.0 (+educational project)"
TIMEOUT = (5, 15)


class LeverAdapter(JobSourceAdapter):
    source_name = "lever"
    source_type = "ats_board"

    def __init__(self, company_sources: list[dict] | None = None, demo_mode: bool = False):
        self.company_sources = company_sources or []
        self.demo_mode = demo_mode

    @property
    def live_enabled(self) -> bool:
        return bool(self.company_sources)

    def fetch_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.live_enabled:
            return generate_mock_jobs(self.source_name, limit) if self.demo_mode else []

        session = requests.Session()
        jobs: list[dict[str, Any]] = []
        for company in self.company_sources:
            slug = company["source_identifier"]
            url = API_BASE.format(company=slug)
            try:
                response = url_safety.safe_get(
                    session, url,
                    params={"mode": "json"},
                    headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                    timeout=TIMEOUT,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as error:
                logger.warning("lever_postings_fetch_failed company=%s error=%s", company["company_name"], type(error).__name__)
                continue
            if isinstance(payload, list):
                for job in payload:
                    if isinstance(job, dict):
                        job = dict(job)
                        job["_company_name"] = company["company_name"]
                        job["_company_logo_url"] = company.get("company_logo_url")
                        job["_company_slug"] = slug
                        jobs.append(job)
            if len(jobs) >= limit:
                break
        session.close()
        return jobs[:limit]

    def normalize_job(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        if "mock_id" in raw:
            return build_normalized_job(
                source=self.source_name,
                source_mode="mock",
                source_job_id=raw["mock_id"],
                company=raw["company"],
                title=raw["title"],
                location=raw.get("location", ""),
                work_mode=raw.get("work_mode", ""),
                job_type=raw.get("job_type", ""),
                description=raw.get("description", ""),
                skills=raw.get("skills", ""),
                posted_at=raw.get("posted_at"),
                direct_url=None,
                source_url=None,
            )

        categories = raw.get("categories") or {}
        location = categories.get("location", "") if isinstance(categories, dict) else ""
        commitment = categories.get("commitment", "") if isinstance(categories, dict) else ""
        team = categories.get("team", "") if isinstance(categories, dict) else ""
        description = strip_html(raw.get("descriptionPlain") or raw.get("description"))
        posted_at = None
        created_at = raw.get("createdAt")
        if created_at:
            try:
                posted_at = datetime.fromtimestamp(int(created_at) / 1000, tz=timezone.utc)
            except (ValueError, TypeError):
                posted_at = None

        company_slug = raw.get("_company_slug", "")
        company_name = raw.get("_company_name") or ""
        title = raw.get("text") or ""
        if not company_name or not title:
            return None
        apply_url = raw.get("applyUrl") or raw.get("hostedUrl")
        return build_normalized_job(
            source=self.source_name,
            source_mode="live",
            source_job_id=str(raw.get("id") or ""),
            company=company_name,
            company_logo_url=raw.get("_company_logo_url"),
            title=title,
            location=location,
            work_mode="Remote" if "remote" in str(location).lower() else "Not specified",
            job_type=commitment or "Full-time",
            description=description,
            skills=team or "",
            posted_at=posted_at,
            direct_url=raw.get("hostedUrl") or raw.get("applyUrl"),
            source_url=f"https://jobs.lever.co/{company_slug}" if company_slug else None,
            application_url=apply_url,
        )

    def health_check(self) -> SourceHealth:
        checked_at = datetime.now(timezone.utc).isoformat()
        if not self.live_enabled:
            return SourceHealth(self.source_name, self.source_type, "mock" if self.demo_mode else "unavailable", self.demo_mode, checked_at)
        try:
            session = requests.Session()
            slug = self.company_sources[0]["source_identifier"]
            response = url_safety.safe_get(
                session, API_BASE.format(company=slug),
                params={"mode": "json"},
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
