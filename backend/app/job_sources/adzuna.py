"""Adzuna broad job-discovery adapter.

The adapter is intentionally not registered for polling in Phase 1: JSearch
remains the active primary provider.  It implements the shared broad-provider
contract so a later, explicitly approved phase can combine providers without
changing either provider's discovery semantics.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

import requests

from app.job_sources.base import BroadJobProviderAdapter, SourceHealth
from app.services import url_safety
from app.services.job_normalization_service import build_normalized_job, strip_html

logger = logging.getLogger(__name__)
DEFAULT_COUNTRY = "us"
BASE_URL = "https://api.adzuna.com/v1/api/jobs"
TIMEOUT = (5, 15)


def app_id() -> str:
    return (os.getenv("ADZUNA_APP_ID") or "").strip()


def app_key() -> str:
    return (os.getenv("ADZUNA_APP_KEY") or "").strip()


def max_pages_per_query() -> int:
    return max(1, min(3, int(os.getenv("ADZUNA_MAX_PAGES_PER_QUERY") or "1")))


def _posted_at(value: Any):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


class AdzunaAdapter(BroadJobProviderAdapter):
    source_name = "adzuna"
    source_type = "broad_provider"

    def __init__(self, preference_queries: list[dict[str, str]] | None = None):
        self.preference_queries = preference_queries or []
        self._last_fetch_ok, self._last_fetch_error = True, None

    @property
    def is_configured(self) -> bool:
        return bool(app_id() and app_key())

    def fetch_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.is_configured:
            logger.warning("ADZUNA_NOT_CONFIGURED")
            return []
        records: list[dict[str, Any]] = []
        session = requests.Session()
        try:
            for spec in self.preference_queries:
                title = (spec.get("job_title") or "").strip()
                if not title:
                    continue
                country = (spec.get("country") or DEFAULT_COUNTRY).lower()
                if len(country) != 2 or not country.isalpha():
                    country = DEFAULT_COUNTRY
                for page in range(1, max_pages_per_query() + 1):
                    params = {"app_id": app_id(), "app_key": app_key(), "what": title,
                              "results_per_page": min(50, max(1, limit - len(records)))}
                    location = (spec.get("location") or "").strip()
                    if location and location.lower() not in {"remote", "anywhere"}:
                        params["where"] = location
                    try:
                        response = url_safety.safe_get(session, f"{BASE_URL}/{country}/search/{page}", params=params,
                                                       headers={"Accept": "application/json"}, timeout=TIMEOUT)
                        if response.status_code == 429:
                            self._last_fetch_ok = False
                            self._last_fetch_error = "rate_limited"; return records
                        response.raise_for_status()
                        payload = response.json()
                    except Exception as error:
                        self._last_fetch_ok, self._last_fetch_error = False, type(error).__name__
                        logger.warning("adzuna_fetch_failed page=%s error=%s", page, type(error).__name__)
                        break
                    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                        break
                    self._last_fetch_ok, self._last_fetch_error = True, None
                    rows = [dict(row, _query_spec=spec) for row in payload["results"] if isinstance(row, dict)]
                    records.extend(rows)
                    if not rows or len(records) >= limit:
                        return records[:limit]
        finally:
            session.close()
        return records[:limit]

    def normalize_job(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        company = raw.get("company", {}) if isinstance(raw.get("company"), dict) else {}
        location = raw.get("location", {}) if isinstance(raw.get("location"), dict) else {}
        redirect = str(raw.get("redirect_url") or "").strip() or None
        contract = " ".join(str(raw.get(k) or "").replace("_", " ").strip() for k in ("contract_type", "contract_time")).strip()
        # Adzuna's redirect is listing provenance only.  It is not an
        # employer/ATS URL and must never become a strict-verification input.
        # Keep a separately returned external apply field, when present, as
        # the untrusted candidate; no provider-page scraping is introduced.
        from app.services.provider_apply_resolver import is_provider_listing_url
        outbound = next((
            str(raw.get(field) or "").strip() or None
            for field in ("apply_url", "application_url", "direct_url", "external_apply_url")
            if str(raw.get(field) or "").strip()
        ), None)
        if is_provider_listing_url(outbound):
            outbound = None
        return build_normalized_job(source=self.source_name, source_mode="live", source_job_id=str(raw.get("id")) if raw.get("id") is not None else None,
            company=company.get("display_name") or "", title=raw.get("title") or "", location=location.get("display_name") or "",
            work_mode="Remote" if "remote" in str(location.get("display_name") or "").lower() else "", job_type=contract,
            description=strip_html(raw.get("description")), requirements="", skills="", posted_at=_posted_at(raw.get("created")),
            direct_url=None, application_url=None, source_url=redirect,
            unverified_application_url=outbound)

    def health_check(self) -> SourceHealth:
        now = datetime.now().astimezone().isoformat()
        if not self.is_configured:
            return SourceHealth(self.source_name, self.source_type, "unavailable", False, now, "ADZUNA_NOT_CONFIGURED")
        return SourceHealth(self.source_name, self.source_type, "live", self._last_fetch_ok, now, self._last_fetch_error)
