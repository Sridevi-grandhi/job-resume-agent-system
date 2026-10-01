"""Greenhouse job board source adapter — an internal connector.

Retrieves jobs for whichever real employers are configured in the
CompanySource registry with source_type=GREENHOUSE (see
job_source_registry_service.sync_company_sources_from_env, driven by
GREENHOUSE_COMPANY_SOURCES). Uses Greenhouse's public, unauthenticated Job
Board API (https://developers.greenhouse.io/job-board.html).

No configured company -> no jobs, unless demo_mode is on (local
development/demos only) -> a realistic MOCK catalog. Never fabricates a
real company's postings — a company only appears when its board was
actually configured and successfully fetched.
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

API_BASE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
USER_AGENT = "CareerBoostAI-JobDiscovery/1.0 (+educational project)"
TIMEOUT = (5, 15)


class GreenhouseAdapter(JobSourceAdapter):
    source_name = "greenhouse"
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
            token = company["source_identifier"]
            url = API_BASE.format(token=token)
            try:
                response = url_safety.safe_get(
                    session, url,
                    params={"content": "true"},
                    headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                    timeout=TIMEOUT,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as error:
                logger.warning("greenhouse_board_fetch_failed company=%s error=%s", company["company_name"], type(error).__name__)
                continue
            for job in payload.get("jobs", []) if isinstance(payload, dict) else []:
                if isinstance(job, dict):
                    job = dict(job)
                    job["_company_name"] = company["company_name"]
                    job["_company_logo_url"] = company.get("company_logo_url")
                    job["_board_token"] = token
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

        job_id = raw.get("id")
        title = raw.get("title") or ""
        location = (raw.get("location") or {}).get("name", "") if isinstance(raw.get("location"), dict) else ""
        content = strip_html(raw.get("content"))
        departments = raw.get("departments") or []
        skills = ", ".join(
            str(department.get("name")) for department in departments if isinstance(department, dict) and department.get("name")
        )
        posted_at = None
        updated_at = raw.get("updated_at")
        if updated_at:
            try:
                posted_at = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
            except ValueError:
                posted_at = None

        board_token = raw.get("_board_token", "")
        company_name = raw.get("_company_name") or ""
        if not company_name or not title:
            return None
        apply_url = raw.get("absolute_url")
        return build_normalized_job(
            source=self.source_name,
            source_mode="live",
            source_job_id=str(job_id) if job_id is not None else None,
            company=company_name,
            company_logo_url=raw.get("_company_logo_url"),
            title=title,
            location=location,
            work_mode="Remote" if "remote" in location.lower() else "Not specified",
            job_type="",
            description=content,
            skills=skills,
            posted_at=posted_at,
            direct_url=apply_url,
            source_url=f"https://boards.greenhouse.io/{board_token}" if board_token else None,
            application_url=apply_url,
        )

    def health_check(self) -> SourceHealth:
        checked_at = datetime.now(timezone.utc).isoformat()
        if not self.live_enabled:
            return SourceHealth(self.source_name, self.source_type, "mock" if self.demo_mode else "unavailable", self.demo_mode, checked_at)
        try:
            session = requests.Session()
            token = self.company_sources[0]["source_identifier"]
            response = url_safety.safe_get(
                session, API_BASE.format(token=token),
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
