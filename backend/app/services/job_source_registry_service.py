"""Job source registry: internal connectors + the company registry they
retrieve real employer jobs for.

Two distinct registries live here on purpose:
  - JobSource: per-connector health/status (Greenhouse, Lever, Workday,
    Remotive, generic feed) — internal/diagnostic only (see
    list_sources_status; never rendered as job-card identity).
  - CompanySource: which real employers are configured, and which connector
    retrieves each one's jobs (the ConnectorResolver input). Populated only
    from operator configuration (env vars) — never hardcoded here.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.job_sources.base import JobSourceAdapter, SourceHealth
from app.job_sources.generic_feed import GenericCareerFeedAdapter
from app.job_sources.greenhouse import GreenhouseAdapter
from app.job_sources.jsearch import JSearchAdapter, api_key as jsearch_api_key
from app.job_sources.lever import LeverAdapter
from app.job_sources.remotive import RemotiveAdapter
from app.job_sources.workday import WorkdayAdapter
from app.models.company_source_model import CompanySource
from app.models.job_source_model import JobSource
from app.services.broad_provider_query_service import get_active_preference_queries


def demo_mode_enabled() -> bool:
    """When off (the default), connectors with no configured real company
    (or with no safe live retrieval method at all, like Workday) contribute
    NO jobs rather than fabricated ones — see job_sources/*.py. Flip on only
    for local development/demos, never in normal user-facing operation."""
    return (os.getenv("UPDATED_JOB_DEMO_MODE") or "false").strip().lower() == "true"


def primary_provider_name() -> str:
    """Which BroadJobProviderAdapter fills the "search many employers
    without configuring them one by one" slot. Only "jsearch" is implemented
    today; any other/unset value means no broad provider runs — Greenhouse/
    Lever/Remotive/generic-feed still work as independent, optional
    supplemental sources regardless of this setting."""
    return (os.getenv("UPDATED_JOB_PRIMARY_PROVIDER") or "jsearch").strip().lower()


def remotive_enabled() -> bool:
    """Off by default under the broad-provider architecture (JSearch already
    covers the "many real companies" need) — Remotive's adapter code stays
    intact and fully usable, just not part of the default source list. See
    UPDATED_JOB_REMOTIVE_ENABLED."""
    return (os.getenv("UPDATED_JOB_REMOTIVE_ENABLED") or "false").strip().lower() == "true"


def jsearch_configured() -> bool:
    return bool(jsearch_api_key())


def provider_config_status() -> dict:
    """Safe, secret-free configuration/diagnostics summary — never includes
    the API key itself, only whether one is present."""
    from app.scheduler.updated_job_scheduler_service import poll_interval_minutes, tracker_enabled

    provider = primary_provider_name()
    configured = jsearch_configured() if provider == "jsearch" else False
    return {
        "primary_provider": provider,
        "configured": configured,
        "tracker_enabled": tracker_enabled(),
        "poll_interval_minutes": poll_interval_minutes(),
        "demo_mode": demo_mode_enabled(),
        "remotive_enabled": remotive_enabled(),
        "sources": {
            "jsearch": {"configured": jsearch_configured()},
            "greenhouse": {"configured": bool(os.getenv("GREENHOUSE_COMPANY_SOURCES", "").strip())},
            "lever": {"configured": bool(os.getenv("LEVER_COMPANY_SOURCES", "").strip())},
            "workday": {"available": False},  # no safe live retrieval method exists — see job_sources/workday.py
            "remotive": {"enabled": remotive_enabled()},
        },
    }


# Sources with no safe live retrieval method at all, regardless of
# configuration — an "unavailable" mode from these is expected, permanent
# production behavior, never a failure. See job_sources/workday.py.
_INTENTIONALLY_MOCK_ONLY_SOURCES = {"workday"}


def classify_source_status(source_name: str, enabled: bool, mode: str) -> str:
    """Turns a JobSource row's raw (enabled, mode) into one of the 5
    statuses production diagnostics should distinguish. `mode="error"` is
    written ONLY by the poll orchestrator's own exception handler (see
    updated_job_discovery_service.run_poll_cycle) when an adapter's
    fetch/normalize/persist genuinely raised — never by an adapter's own
    health_check(), so this never confuses "a real failure occurred" with
    "this optional source simply isn't configured"."""
    if not enabled:
        return "DISABLED"
    if mode == "error":
        return "LIVE_ERROR"
    if mode == "live":
        return "ENABLED_AND_CONFIGURED"
    if mode == "mock":
        return "ENABLED_AND_CONFIGURED" if demo_mode_enabled() else "INTENTIONALLY_UNAVAILABLE"
    if source_name in _INTENTIONALLY_MOCK_ONLY_SOURCES:
        return "INTENTIONALLY_UNAVAILABLE"
    return "OPTIONAL_NOT_CONFIGURED"


def get_broad_provider_adapter(db: Session) -> JobSourceAdapter | None:
    provider = primary_provider_name()
    if provider == "jsearch":
        return JSearchAdapter(preference_queries=get_active_preference_queries(db))
    return None


# ---------------------------------------------------------------------------
# Company registry — real employers, sourced only from operator configuration
# ---------------------------------------------------------------------------

def _parse_company_pairs(raw: str) -> list[tuple[str, str]]:
    """Parses "Company Name:slug,Another Company:slug2" — never invents a
    company; a malformed pair is skipped, not guessed at."""
    pairs = []
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        name, slug = chunk.split(":", 1)
        name, slug = name.strip(), slug.strip()
        if name and slug:
            pairs.append((name, slug))
    return pairs


def sync_company_sources_from_env(db: Session) -> None:
    """Upserts CompanySource rows from GREENHOUSE_COMPANY_SOURCES /
    LEVER_COMPANY_SOURCES, and disables (never deletes — same soft-close
    pattern as DiscoveredJob.status=INACTIVE) any previously-configured row
    whose slug is no longer present in the current env var value.

    Without this prune step, removing a company from the env var — e.g. a
    temporary diagnostic entry like "Favor:askfavor" — left its CompanySource
    row `enabled=True` forever, so the connector kept fetching a company the
    operator had explicitly removed. Idempotent — safe to call on every poll.
    """
    for env_var, source_type in (
        ("GREENHOUSE_COMPANY_SOURCES", "GREENHOUSE"),
        ("LEVER_COMPANY_SOURCES", "LEVER"),
    ):
        configured_pairs = _parse_company_pairs(os.getenv(env_var, ""))
        configured_slugs = {slug for _name, slug in configured_pairs}

        for company_name, slug in configured_pairs:
            existing = (
                db.query(CompanySource)
                .filter(CompanySource.source_type == source_type, CompanySource.company_slug == slug)
                .first()
            )
            if existing:
                existing.company_name = company_name
                existing.source_identifier = slug
                existing.enabled = True
            else:
                db.add(CompanySource(
                    company_name=company_name,
                    company_slug=slug,
                    source_type=source_type,
                    source_identifier=slug,
                    enabled=True,
                ))

        stale_query = db.query(CompanySource).filter(
            CompanySource.source_type == source_type, CompanySource.enabled.is_(True)
        )
        if configured_slugs:
            stale_query = stale_query.filter(~CompanySource.company_slug.in_(configured_slugs))
        for stale in stale_query.all():
            stale.enabled = False
    db.commit()


def get_company_sources(db: Session, source_type: str) -> list[dict]:
    rows = (
        db.query(CompanySource)
        .filter(CompanySource.source_type == source_type, CompanySource.enabled.is_(True))
        .order_by(CompanySource.company_name)
        .all()
    )
    return [
        {
            "company_name": row.company_name,
            "company_slug": row.company_slug,
            "source_identifier": row.source_identifier,
            "company_logo_url": row.company_logo_url,
            "career_page_url": row.career_page_url,
        }
        for row in rows
    ]


def count_configured_companies(db: Session) -> int:
    return db.query(CompanySource).filter(CompanySource.enabled.is_(True)).count()


# ---------------------------------------------------------------------------
# Connector adapters — internal retrieval mechanism, resolved per company
# ---------------------------------------------------------------------------

def get_adapters(db: Session) -> list[JobSourceAdapter]:
    """ConnectorResolver: builds one adapter instance per connector, each
    already carrying the real, operator-configured companies it should
    retrieve. The discovery orchestrator never needs to know which
    connector produced a given job — only DiscoveredJob.company matters
    downstream."""
    sync_company_sources_from_env(db)
    demo = demo_mode_enabled()
    adapters: list[JobSourceAdapter] = [
        GreenhouseAdapter(company_sources=get_company_sources(db, "GREENHOUSE"), demo_mode=demo),
        LeverAdapter(company_sources=get_company_sources(db, "LEVER"), demo_mode=demo),
        WorkdayAdapter(demo_mode=demo),
        GenericCareerFeedAdapter(demo_mode=demo),
    ]
    if remotive_enabled():
        adapters.append(RemotiveAdapter())
    broad_provider = get_broad_provider_adapter(db)
    if broad_provider is not None:
        adapters.append(broad_provider)
    return adapters


def _get_or_create(db: Session, source_name: str, source_type: str) -> JobSource:
    row = db.query(JobSource).filter(JobSource.source_name == source_name).first()
    if row is None:
        row = JobSource(source_name=source_name, source_type=source_type, enabled=True, mode="mock")
        db.add(row)
        db.flush()
    return row


def sync_registry(db: Session) -> list[JobSource]:
    rows = []
    current_names = set()
    for adapter in get_adapters(db):
        enabled = getattr(adapter, "enabled", True)
        row = _get_or_create(db, adapter.source_name, adapter.source_type)
        row.enabled = bool(enabled)
        rows.append(row)
        current_names.add(adapter.source_name)

    # A source no longer returned by get_adapters() at all (e.g. Remotive
    # while UPDATED_JOB_REMOTIVE_ENABLED=false) is DISABLED, not merely
    # unavailable — without this, its row keeps whatever enabled/mode it
    # last had while it was still active, misclassifying it as a live or
    # failed source in diagnostics long after it stopped running.
    if current_names:
        for stale in db.query(JobSource).filter(~JobSource.source_name.in_(current_names)).all():
            stale.enabled = False
            rows.append(stale)

    db.commit()
    return rows


def record_health(db: Session, health: SourceHealth) -> JobSource:
    row = _get_or_create(db, health.source_name, health.source_type)
    row.mode = health.mode
    row.last_checked_at = datetime.now(timezone.utc)
    if health.healthy:
        row.last_success_at = datetime.now(timezone.utc)
        row.last_error = None
    else:
        row.last_error = health.error
    db.commit()
    return row


def list_sources_status(db: Session) -> list[dict]:
    """Internal/diagnostic connector status — never rendered as the primary
    job-card identity in the main UI (see job_discovery_routes). `status`
    distinguishes a real failure (LIVE_ERROR) from a source that is simply
    optional and unconfigured or permanently mock-only in production — only
    LIVE_ERROR should ever surface as a user-facing "sources unavailable"
    notice (see classify_source_status)."""
    sync_registry(db)
    rows = db.query(JobSource).order_by(JobSource.source_name).all()
    return [
        {
            "source_name": row.source_name,
            "source_type": row.source_type,
            "enabled": row.enabled,
            "mode": row.mode,
            "status": classify_source_status(row.source_name, row.enabled, row.mode),
            "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
            "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
            "last_error": None,  # Never surface raw technical errors to end users.
            "healthy": bool(row.last_success_at) and row.mode not in ("unavailable", "error"),
        }
        for row in rows
    ]
