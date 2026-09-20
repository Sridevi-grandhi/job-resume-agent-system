"""Workday job source adapter — internal connector, MOCK-capable only.

Workday tenants do not expose a uniform, unauthenticated public JSON API the
way Greenhouse/Lever do (endpoints vary per tenant and typically require a
session/CSRF token from the careers site itself), so per the project's
"never bypass anti-bot protections / never pretend a mock source is live"
rule this adapter can never retrieve real employer data. In normal
operation (demo_mode off) it contributes NO jobs — never a fabricated
employer. Only with UPDATED_JOB_DEMO_MODE=true (local development/demos)
does it produce a realistic mock catalog, still with no real company names.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.job_sources.base import JobSourceAdapter, SourceHealth
from app.job_sources.mock_data import generate_mock_jobs
from app.services.job_normalization_service import build_normalized_job


class WorkdayAdapter(JobSourceAdapter):
    source_name = "workday"
    source_type = "ats_board"

    def __init__(self, demo_mode: bool = False):
        self.demo_mode = demo_mode

    def fetch_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.demo_mode:
            return []
        return generate_mock_jobs(self.source_name, limit)

    def normalize_job(self, raw: dict[str, Any]) -> dict[str, Any] | None:
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

    def health_check(self) -> SourceHealth:
        return SourceHealth(
            self.source_name, self.source_type, "mock" if self.demo_mode else "unavailable", self.demo_mode,
            datetime.now(timezone.utc).isoformat(),
        )
