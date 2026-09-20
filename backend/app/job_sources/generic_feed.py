"""Generic public career-page feed adapter — internal connector, MOCK,
disabled by default.

Represents the "Generic Career Feed" connector slot for a future
company-specific JSON/RSS career feed. Scraping arbitrary public career
pages reliably and safely (without bypassing anti-bot protections) needs a
per-target integration, so this stays mock-only; it only produces jobs when
BOTH GENERIC_FEED_ENABLED=true AND UPDATED_JOB_DEMO_MODE=true — normal
operation never fabricates a job from this connector.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from app.job_sources.base import JobSourceAdapter, SourceHealth
from app.job_sources.mock_data import generate_mock_jobs
from app.services.job_normalization_service import build_normalized_job


class GenericCareerFeedAdapter(JobSourceAdapter):
    source_name = "generic_feed"
    source_type = "career_page"

    def __init__(self, demo_mode: bool = False):
        self.demo_mode = demo_mode

    @property
    def enabled(self) -> bool:
        return (os.getenv("GENERIC_FEED_ENABLED") or "false").strip().lower() == "true"

    def fetch_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.enabled or not self.demo_mode:
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
        checked_at = datetime.now(timezone.utc).isoformat()
        if not self.enabled or not self.demo_mode:
            return SourceHealth(self.source_name, self.source_type, "unavailable", False, checked_at, error="disabled")
        return SourceHealth(self.source_name, self.source_type, "mock", True, checked_at)
