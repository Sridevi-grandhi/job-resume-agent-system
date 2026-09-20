"""One-off, bounded availability check for the existing JSearch
DIRECT_EMPLOYER / DIRECT_ATS DiscoveredJob rows (Steps 4 + 14 of the
end-to-end job-availability fix).

Does NOT call JSearch — reuses the 45 rows already in Postgres. Only makes
outbound requests to the employer/ATS destinations themselves (via
job_availability_service.check_url_availability, which reuses url_safety's
SSRF protections), sequentially with a short delay between requests so
employer sites are never hammered. AGGREGATOR_LISTING rows are never
checked — there is nothing employer-specific to verify for those.

Usage (inside the backend container or any environment with DATABASE_URL
configured):
    python scripts/check_jsearch_availability.py
"""

from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.models.discovered_job_model import DiscoveredJob  # noqa: E402
from app.services import job_availability_service as availability  # noqa: E402
from app.services.job_normalization_service import resolve_application_destination  # noqa: E402

# Polite pacing between sequential requests to real employer/ATS sites.
DELAY_BETWEEN_CHECKS_SECONDS = 1.0


def _hostname(url: str | None) -> str:
    if not url:
        return ""
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def run() -> None:
    db = SessionLocal()
    try:
        rows = db.query(DiscoveredJob).filter(DiscoveredJob.source == "jsearch").all()
        total = len(rows)

        direct_rows: list[tuple[DiscoveredJob, dict]] = []
        aggregator_count = 0
        for row in rows:
            destination = resolve_application_destination(row)
            if destination["type"] in ("DIRECT_EMPLOYER", "DIRECT_ATS"):
                direct_rows.append((row, destination))
            elif destination["type"] == "AGGREGATOR_LISTING":
                aggregator_count += 1

        status_counts: Counter[str] = Counter()
        expired_examples: list[tuple[str, str, str, str]] = []
        unknown_examples: list[tuple[str, str, str, str]] = []
        active_domains: Counter[str] = Counter()

        for index, (row, destination) in enumerate(direct_rows):
            result = availability.check_url_availability(destination["url"])
            availability.apply_result(row, result)
            db.commit()

            status = result["status"]
            status_counts[status] += 1
            host = _hostname(destination["url"])
            if status == "EXPIRED":
                expired_examples.append((row.company, row.title, host, result["reason"]))
            elif status == "UNKNOWN":
                unknown_examples.append((row.company, row.title, host, result["reason"]))
            else:
                active_domains[host] += 1

            if index < len(direct_rows) - 1:
                time.sleep(DELAY_BETWEEN_CHECKS_SECONDS)

        print(f"Total JSearch jobs: {total}")
        print(f"Direct jobs checked: {len(direct_rows)}")
        print(f"ACTIVE: {status_counts.get('ACTIVE', 0)}")
        print(f"EXPIRED: {status_counts.get('EXPIRED', 0)}")
        print(f"UNKNOWN: {status_counts.get('UNKNOWN', 0)}")
        print(f"AGGREGATOR_LISTING: {aggregator_count}")
        print()
        print("ACTIVE employer/ATS domains:")
        for host, count in sorted(active_domains.items()):
            print(f"  {host} {count}")
        print()
        print("EXPIRED:")
        for company, title, host, reason in expired_examples:
            print(f"  {company} | {title} | {host} | {reason}")
        print()
        print("UNKNOWN:")
        for company, title, host, reason in unknown_examples:
            print(f"  {company} | {title} | {host} | {reason}")
    finally:
        db.close()


if __name__ == "__main__":
    run()
