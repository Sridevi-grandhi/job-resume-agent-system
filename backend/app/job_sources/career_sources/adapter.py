"""One optional adapter per configured company, behind the existing source API."""
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from importlib import import_module
from urllib.parse import urlsplit

from app.job_sources.base import JobSourceAdapter, SourceHealth
from app.services.job_normalization_service import is_safe_application_url
from app.services.provider_apply_resolver import is_provider_listing_url
from .base import CareerSource
from .detector import detect, embedded_board, same_board
from .http import CollectionError, PublicClient

logger = logging.getLogger(__name__)
PROVIDERS = {"greenhouse", "lever", "ashby", "smartrecruiters", "workday", "icims", "successfactors", "custom"}
API_PROVIDERS = {"greenhouse", "lever", "ashby", "smartrecruiters", "workday"}
METADATA_FIELDS = ("department", "preferred_skills", "salary_min", "salary_max", "salary_currency", "salary_interval", "compensation")


def source_key(source):
    identity = source.company_name.casefold() + "|" + source.career_url.rstrip("/")
    return "company_direct:" + hashlib.sha256(identity.encode()).hexdigest()[:20]


def configured_sources():
    try:
        values = json.loads(os.getenv("COMPANY_CAREER_SOURCES", "[]"))
        if not isinstance(values, list):
            raise ValueError()
    except (TypeError, ValueError):
        logger.warning("company_career_configuration_invalid")
        return []
    sources, seen = [], set()
    for value in values[:20]:
        if not isinstance(value, dict):
            continue
        company = str(value.get("company_name") or "").strip()
        url = str(value.get("career_url") or "").strip()
        provider = str(value.get("provider") or "auto").lower()
        try:
            valid = urlsplit(url).scheme == "https" and is_safe_application_url(url) and not is_provider_listing_url(url)
        except ValueError:
            valid = False
        if not company or len(company) > 200 or not valid or provider not in PROVIDERS | {"auto"}:
            logger.warning("company_career_configuration_entry_invalid")
            continue
        if value.get("enabled", True) is not True:
            continue
        source = CareerSource(company, url, provider)
        key = source_key(source)
        if key not in seen:
            sources.append(source)
            seen.add(key)
    return sources


def sync_companies(db, sources):
    """Reuse the company registry; only this extension's managed rows change."""
    from app.models.company_source_model import CompanySource
    rows = db.query(CompanySource).filter(
        CompanySource.source_type == "COMPANY_CAREER_PAGE",
        CompanySource.source_identifier.startswith("company_direct:"),
    ).all()
    existing = {row.source_identifier: row for row in rows}
    keys = set()
    for source in sources:
        key = source_key(source)
        keys.add(key)
        row = existing.get(key)
        if row is None:
            row = CompanySource(company_name=source.company_name, company_slug=key,
                                source_identifier=key, source_type="COMPANY_CAREER_PAGE")
            db.add(row)
        row.company_name = source.company_name
        row.career_page_url = source.career_url
        row.enabled = True
    for key, row in existing.items():
        if key not in keys:
            row.enabled = False
    db.commit()


class CompanyCareerAdapter(JobSourceAdapter):
    source_type = "company_direct"

    def __init__(self, company):
        self.company = company
        self.source_name = source_key(company)
        self.complete_inventory = False
        self._error = None
        self._platform = "custom"
        self._board_url = company.career_url

    def fetch_jobs(self, limit=100):
        self.complete_inventory = False
        self._error = None
        limit = max(0, min(20, limit))
        if not limit:
            return []
        render = os.getenv("COMPANY_CAREER_PLAYWRIGHT_ENABLED", "false").lower() == "true"
        try:
            with PublicClient() as client:
                detection = detect(self.company.career_url)
                initial = None
                if detection.provider == "custom":
                    initial = client.fetch(self.company.career_url)
                    detection = detect(initial.url)
                    if detection.provider == "custom":
                        detection = embedded_board(initial.text, initial.url) or detection
                # Explicit provider selects a parser, but never allows an API
                # tenant to be guessed from an unrelated custom-domain path.
                if self.company.provider != "auto" and detection.provider != self.company.provider:
                    if self.company.provider in API_PROVIDERS:
                        raise CollectionError("configured_provider_url_mismatch")
                    detection = type(detection)(self.company.provider, detection.url, detection.board)
                self._platform = detection.provider
                self._board_url = detection.url
                module = import_module("app.job_sources.career_sources." + ("generic_html" if detection.provider == "custom" else detection.provider))
                if detection.provider in API_PROVIDERS and detection.board:
                    try:
                        result = module.collect(client, self.company, detection, limit)
                    except CollectionError as error:
                        # Never turn access restrictions or robots refusal into
                        # an alternate path around that restriction.
                        if str(error) not in {"http_404", "invalid_workday_payload"}:
                            raise
                        from .generic_html import collect
                        result = collect(client, self.company, detection, limit, render=render)
                else:
                    result = module.collect(client, self.company, detection, limit, initial=initial, render=render)
                self.complete_inventory = result.complete
                return result.jobs[:limit]
        except Exception as error:
            self._error = str(error) if isinstance(error, CollectionError) else type(error).__name__
            logger.warning("company_career_fetch_failed source=%s reason=%s", self.source_name, self._error)
            return []

    def normalize_job(self, raw):
        listing = raw.get("source_url") or raw.get("direct_url")
        candidate = raw.get("application_url") or raw.get("direct_url") or listing
        if not listing or not is_safe_application_url(listing) or urlsplit(listing).scheme != "https" or is_provider_listing_url(listing):
            self.complete_inventory = False
            return None
        if not raw.get("title") or not raw.get("description"):
            self.complete_inventory = False
            return None
        # An unrelated URL embedded in a public page is not an official job.
        # Hosted ATS postings must belong to the configured/detected tenant.
        if not same_board(self._board_url, listing):
            self.complete_inventory = False
            return None
        # Keep all official apply URLs untrusted until the existing verifier
        # checks identity, title, specificity, redirects and availability.
        external_id = str(raw.get("source_job_id") or listing)
        identifier = external_id if len(external_id) <= 512 else hashlib.sha256(external_id.encode()).hexdigest()
        normalized = {**raw, "source": self.source_name, "source_mode": "live",
                      "source_job_id": identifier, "external_id": f"{self.source_name}:{identifier}",
                      "source_url": listing, "application_url": None, "direct_url": None,
                      "unverified_application_url": candidate,
                      "career_page_url": self.company.career_url}
        metadata = {key: raw.get(key) for key in METADATA_FIELDS if raw.get(key) is not None}
        metadata.update(source="company_direct", source_platform=self._platform,
                        career_url=self.company.career_url, listing_url=listing,
                        board_url=self._board_url, external_job_id=external_id)
        normalized["career_metadata"] = json.dumps(metadata)
        return normalized

    def fetch_normalized_jobs(self, limit=100):
        jobs = super().fetch_normalized_jobs(limit)
        if self.last_counts["rejected"]:
            self.complete_inventory = False
        return jobs

    def health_check(self):
        return SourceHealth(self.source_name, self.source_type, "live", self._error is None,
                            datetime.now(timezone.utc).isoformat(), self._error)
