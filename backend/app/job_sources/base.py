"""JobSourceAdapter — the contract every job source (Greenhouse, Lever,
Workday, Remotive, generic feeds) implements.

Keeps source-specific parsing isolated: the discovery/scheduler/matching
services never know about any single provider's response shape.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class SourceHealth:
    source_name: str
    source_type: str
    mode: str          # "live" | "mock" | "unavailable"
    healthy: bool
    checked_at: str
    error: str | None = None


class JobSourceAdapter(ABC):
    source_name: str = "unknown"
    source_type: str = "ats_board"

    @abstractmethod
    def fetch_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return raw provider job records. Raise on hard failure — callers
        catch per-source so one bad source never stops the others."""

    @abstractmethod
    def normalize_job(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """Convert one raw record into the shared normalized job shape
        (see job_normalization_service.build_normalized_job), or None to
        skip a record that cannot be safely normalized."""

    @abstractmethod
    def health_check(self) -> SourceHealth:
        """Cheap reachability / configuration check. Must never raise."""

    def fetch_normalized_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        jobs = []
        for raw in self.fetch_jobs(limit=limit):
            try:
                normalized = self.normalize_job(raw)
            except Exception:
                normalized = None
            if normalized:
                jobs.append(normalized)
        return jobs


class BroadJobProviderAdapter(JobSourceAdapter):
    """A JobSourceAdapter that discovers jobs across many employers from one
    multi-employer search API (e.g. JSearch), instead of one adapter instance
    per manually-configured company (Greenhouse/Lever). Occupies the single
    "primary provider" slot selected by UPDATED_JOB_PRIMARY_PROVIDER — see
    job_source_registry_service.get_broad_provider_adapter — so a future
    provider can be added by implementing this contract without touching the
    per-company adapters or the orchestrator."""
