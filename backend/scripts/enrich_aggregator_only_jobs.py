"""One-off run of the enrichment provider chain (Phase 1: Greenhouse+Lever,
then Phase 2: the broad structured-search provider if one is ever
configured — see job_enrichment_service.enrich_job_with_fallback) against
the existing AGGREGATOR_LISTING JSearch rows already in Postgres. Does NOT
call JSearch. The broad provider is disabled by default (Phase 2 ships no
real implementation yet), so today this only makes bounded, key-free
Greenhouse/Lever requests — a small deterministic slug set per company,
stopping at the first confirmed board per provider (see
greenhouse_lever_enrichment_provider.py).

Usage:
    python scripts/enrich_aggregator_only_jobs.py            # apply
    python scripts/enrich_aggregator_only_jobs.py --dry-run  # report only
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
from app.services.job_enrichment_service import apply_enrichment_result, enrich_job_with_fallback  # noqa: E402
from app.services.job_normalization_service import resolve_application_destination  # noqa: E402


def run(dry_run: bool) -> None:
    db = SessionLocal()
    try:
        rows = db.query(DiscoveredJob).filter(DiscoveredJob.source == "jsearch").all()
        aggregator_rows = [row for row in rows if resolve_application_destination(row)["type"] == "AGGREGATOR_LISTING"]

        results: Counter[str] = Counter()
        enriched_rows: list[tuple[DiscoveredJob, str]] = []

        for row in aggregator_rows:
            result = enrich_job_with_fallback(row)
            results[result.status] += 1
            if result.status == "ENRICHED":
                enriched_rows.append((row, result.provider_name or "enrichment"))
                if not dry_run:
                    apply_enrichment_result(row, result)

        if not dry_run and enriched_rows:
            db.commit()

        greenhouse_matches = sum(1 for _row, provider in enriched_rows if provider == "greenhouse_enrichment")
        lever_matches = sum(1 for _row, provider in enriched_rows if provider == "lever_enrichment")
        broad_matches = sum(1 for _row, provider in enriched_rows if provider == "broad_search_enrichment")

        print(f"{'[DRY RUN] ' if dry_run else ''}Aggregator-only rows scanned: {len(aggregator_rows)}")
        print(f"Exact Greenhouse job matches: {greenhouse_matches}")
        print(f"Exact Lever job matches: {lever_matches}")
        print(f"Exact broad-search job matches: {broad_matches}")
        print(f"New direct Apply jobs: {len(enriched_rows)}")
        print(f"Remaining View Listing: {len(aggregator_rows) - len(enriched_rows)}")
        print(f"Rejected (no verified match): {results.get('REJECTED', 0)}")
        print()
        print("Newly enriched jobs (company | title | provider | direct domain):")
        for row, provider_name in enriched_rows:
            host = (urlsplit(row.direct_url or "").hostname or "") if not dry_run else ""
            print(f"  {row.company} | {row.title} | {provider_name} | {host}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing to the DB.")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
