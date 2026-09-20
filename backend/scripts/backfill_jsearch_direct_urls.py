"""One-off, idempotent backfill for JSearch DiscoveredJob rows persisted
before the semantic URL-classification fix (see app/job_sources/jsearch.py
_pick_apply_urls and app/services/job_normalization_service.classify_url_domain).

Those rows have direct_url = application_url = NULL even when their stored
source_url is conclusively an employer career page or ATS-hosted application
page, because the old logic trusted only JSearch's own (unreliable)
job_apply_is_direct flag.

Why a standalone script, not a normal poll-cycle update:
  - compute_fingerprint() prefers direct_url when present. These rows were
    stored with a fingerprint based on the title+company+location fallback
    (since direct_url was NULL at insert time). If we only fixed the
    adapter and waited for the next poll, a freshly classified direct_url
    would change what compute_fingerprint() produces, so
    job_change_detection_service._find_existing() would no longer match the
    old row by fingerprint (JSearch has no stable source_job_id) and the
    next poll would silently INSERT a duplicate row instead of updating the
    existing one.
  - This script updates the existing row directly (same primary key) and
    recomputes its fingerprint/content_hash to match what a future poll
    would now produce for that same posting, so the *next* poll correctly
    recognizes it as the same job (no duplicate) and applies its own
    updates on top.

Only ever PROMOTES a row (source_url -> also direct_url/application_url)
when classify_url_domain conclusively says "employer" or "ats" for that
exact stored source_url, using the row's own `company` name as the employer
signal (no employer_website is stored on DiscoveredJob). Aggregator and
unclassifiable rows are left untouched — never guessed from URL text alone.

Usage (run inside the backend container or any environment with
DATABASE_URL configured):
    python scripts/backfill_jsearch_direct_urls.py            # apply
    python scripts/backfill_jsearch_direct_urls.py --dry-run  # report only
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.models.discovered_job_model import DiscoveredJob  # noqa: E402
from app.services.job_normalization_service import (  # noqa: E402
    classify_url_domain,
    compute_content_hash,
    compute_fingerprint,
)


def _hostname(url: str | None) -> str:
    if not url:
        return ""
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def run(dry_run: bool) -> None:
    db = SessionLocal()
    try:
        rows = (
            db.query(DiscoveredJob)
            .filter(DiscoveredJob.source == "jsearch")
            .filter(DiscoveredJob.direct_url.is_(None))
            .filter(DiscoveredJob.application_url.is_(None))
            .filter(DiscoveredJob.source_url.isnot(None))
            .all()
        )

        promoted_domains: Counter[str] = Counter()
        skipped_aggregator_domains: Counter[str] = Counter()
        skipped_unclassified_domains: Counter[str] = Counter()

        for row in rows:
            classification = classify_url_domain(row.source_url, employer_name=row.company)
            host = _hostname(row.source_url)
            if classification not in ("employer", "ats"):
                if classification == "aggregator":
                    skipped_aggregator_domains[host] += 1
                else:
                    skipped_unclassified_domains[host] += 1
                continue

            promoted_domains[f"{host} [{classification}]"] += 1
            if dry_run:
                continue

            row.direct_url = row.source_url
            row.application_url = row.source_url
            job_dict = {
                "source": row.source,
                "title": row.title,
                "company": row.company,
                "location": row.location or "",
                "description": row.description or "",
                "work_mode": row.work_mode or "",
                "job_type": row.job_type or "",
                "direct_url": row.direct_url,
            }
            row.fingerprint = compute_fingerprint(job_dict)
            row.content_hash = compute_content_hash(job_dict)

        if not dry_run:
            db.commit()

        print(f"{'[DRY RUN] ' if dry_run else ''}Reclassifiable rows scanned: {len(rows)}")
        print(f"Promoted (source_url -> direct_url + application_url): {sum(promoted_domains.values())}")
        for label, count in sorted(promoted_domains.items()):
            print(f"  {label} {count}")
        print(f"Skipped (aggregator, kept as listing): {sum(skipped_aggregator_domains.values())}")
        for host, count in sorted(skipped_aggregator_domains.items()):
            print(f"  {host} {count}")
        print(f"Skipped (unclassifiable, left untouched): {sum(skipped_unclassified_domains.values())}")
        for host, count in sorted(skipped_unclassified_domains.items()):
            print(f"  {host} {count}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing to the DB.")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
