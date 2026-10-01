"""Opt-in Dice discovery using Apify's synchronous JSON dataset endpoint.

Contracts: https://apify.com/abotapi/dice-com-scraper and
https://docs.apify.com/api/v2/actor-run-sync-get-dataset-items-post
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import requests

from app.job_sources.base import BroadJobProviderAdapter, SourceHealth
from app.services.job_normalization_service import build_normalized_job, strip_html

logger = logging.getLogger(__name__)
MAX_LISTINGS = 20
# Bound the Actor below the endpoint's 300-second maximum, with time for the
# synchronous response to arrive. A timeout must NOT trigger another paid run.
RUN_TIMEOUT_SECONDS = 120
TIMEOUT = (5, RUN_TIMEOUT_SECONDS + 10)
HTTP_REASONS = {
    400: "invalid_request", 401: "authentication_failed",
    403: "insufficient_permission", 404: "actor_or_resource_not_found",
    408: "actor_timeout", 429: "rate_limited",
}
# Never echo arbitrary provider messages, codes, exception text, headers, or
# URLs. These documented error types are safe diagnostic identifiers only.
SAFE_ERROR_CODES = frozenset({
    "invalid-input", "invalid-input-schema", "invalid-parameter", "invalid-id",
    "invalid-token", "invalid-credentials", "missing-api-token",
    "insufficient-permissions", "missing-actor-rights", "actor-not-found",
    "record-not-found", "rate-limit-exceeded", "too-many-requests",
    "actor-run-failed", "run-failed", "run-timeout-exceeded",
    "internal-server-error", "actor-is-not-rented", "actor-not-public",
    "apify-plan-required-to-use-paid-actor", "not-enough-usage-to-run-paid-actor",
    "monthly-usage-limit-too-low", "default-dataset-not-found",
})


def token():
    return (os.getenv("APIFY_API_TOKEN") or "").strip()


def actor_id():
    return (os.getenv("APIFY_DICE_ACTOR_ID") or "").strip()


def enabled():
    return (os.getenv("APIFY_DICE_ENABLED") or "false").strip().lower() == "true"


def _api_actor_id(value: str) -> str:
    """Accept owner/name, owner~name or an opaque ID, never URLs or queries."""
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+(?:[~/][A-Za-z0-9_-]+)?", value):
        raise ValueError("invalid_actor_id")
    return value.replace("/", "~")


def _date(v):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00")) if v else None
    except ValueError:
        return None


class ApifyDiceAdapter(BroadJobProviderAdapter):
    source_name = "apify_dice"
    source_type = "broad_provider"

    def __init__(self, preference_queries=None):
        self.preference_queries = preference_queries or []
        self._last_fetch_ok = True
        self._last_fetch_error = None

    @property
    def is_configured(self):
        return enabled() and bool(token() and actor_id())

    def _fail(self, reason: str, status: int | None = None, code: str = "unreported") -> None:
        self._last_fetch_ok = False
        self._last_fetch_error = f"http_status={status} provider_code={code} reason={reason}"
        logger.warning("apify_dice_fetch_failed provider=apify_dice %s", self._last_fetch_error)

    def _http_failure(self, status: int, data: Any) -> None:
        error = data.get("error") if isinstance(data, dict) else None
        raw_code = error.get("type") if isinstance(error, dict) else None
        code = "unreported"
        if isinstance(raw_code, str) and raw_code in SAFE_ERROR_CODES:
            if not token() or token() not in raw_code:
                code = raw_code
        reason = HTTP_REASONS.get(status, "server_error" if status >= 500 else "http_error")
        if code in {"actor-run-failed", "run-failed"}:
            reason = "actor_execution_failed"
        elif code == "run-timeout-exceeded":
            reason = "actor_timeout"
        self._fail(reason, status, code)

    def fetch_jobs(self, limit=100):
        if not self.is_configured or limit <= 0:
            return []
        self._last_fetch_ok, self._last_fetch_error = True, None
        try:
            api_id = _api_actor_id(actor_id())
        except ValueError:
            self._fail("invalid_actor_id")
            return []

        endpoint = f"https://api.apify.com/v2/actors/{api_id}/run-sync-get-dataset-items"
        cap = min(MAX_LISTINGS, limit)
        rows = []
        with requests.Session() as session:
            for spec in self.preference_queries:
                title = (spec.get("job_title") or "").strip()
                if not title:
                    continue  # Never run the Actor's default role query.
                remaining = cap - len(rows)
                if remaining <= 0:
                    break
                payload = {
                    "mode": "search", "queries": [title], "maxPages": 1,
                    "maxListings": remaining, "fetchDetails": True,
                }
                if (spec.get("location") or "").strip():
                    payload["location"] = spec["location"].strip()
                response = None
                try:
                    response = session.post(
                        endpoint,
                        params={
                            "format": "json", "limit": remaining,
                            "timeout": RUN_TIMEOUT_SECONDS, "restartOnError": "false",
                        },
                        json=payload,
                        headers={"Accept": "application/json", "Authorization": f"Bearer {token()}"},
                        timeout=TIMEOUT,
                        allow_redirects=False,
                    )
                    status = response.status_code
                    try:
                        data = response.json()
                    except ValueError:
                        data = None
                    if not 200 <= status < 300 or (isinstance(data, dict) and "error" in data):
                        self._http_failure(status, data)
                        break
                    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
                        self._fail("malformed_dataset", status)
                        break
                    rows.extend(data[:remaining])
                    logger.info(
                        "apify_dice_fetch_completed provider=apify_dice http_status=%s outcome=%s jobs_returned=%s",
                        status, "success" if data else "empty_dataset", min(len(data), remaining),
                    )
                except requests.Timeout:
                    self._fail("request_timeout")
                    break
                except requests.RequestException:
                    self._fail("network_error")
                    break
                except Exception:
                    # Optional discovery stays isolated; exception text can
                    # contain credentials, so never log it or a traceback.
                    self._fail("client_error")
                    break
                finally:
                    if response is not None:
                        response.close()
        return rows

    def normalize_job(self, raw: dict[str, Any]):
        # Preserve the existing normalization and untrusted-candidate path.
        company = raw.get("company") or raw.get("companyName") or ""
        title = raw.get("title") or raw.get("jobTitle") or ""
        city = raw.get("city") or ""
        state = raw.get("state") or ""
        country = raw.get("country") or ""
        location = raw.get("location") or ", ".join(x for x in (city, state, country) if x)
        listing = raw.get("url") or raw.get("jobUrl") or raw.get("listingUrl")
        workplace = " ".join(raw.get("workplaceTypes") or []) if isinstance(raw.get("workplaceTypes"), list) else str(raw.get("workplaceType") or "")
        candidate = raw.get("applyUrl") if str(raw.get("applyType") or "").upper() == "APPLY_TO_URL" else None
        return build_normalized_job(
            source=self.source_name, source_mode="live",
            source_job_id=str(raw.get("id") or raw.get("jobId") or "") or None,
            company=company, title=title, location=location,
            work_mode="Remote" if raw.get("remote") or "remote" in workplace.lower() else "",
            job_type=str(raw.get("employmentType") or ""),
            description=strip_html(raw.get("description") or raw.get("summary") or ""),
            posted_at=_date(raw.get("postedDate") or raw.get("datePosted")),
            direct_url=None, application_url=None, source_url=listing,
            unverified_application_url=candidate,
            candidate_application_type=raw.get("applyType"),
        )

    def health_check(self):
        return SourceHealth(
            self.source_name, self.source_type,
            "live" if self.is_configured else "unavailable",
            self._last_fetch_ok if self.is_configured else False,
            datetime.now(timezone.utc).isoformat(),
            self._last_fetch_error if self.is_configured else "APIFY_DICE_NOT_CONFIGURED",
        )
