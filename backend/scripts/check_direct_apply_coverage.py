"""Safe, read-only coverage diagnostics for the direct-apply-coverage
enhancement (Step 11). Reuses the existing stored DiscoveredJob rows —
makes NO JSearch calls and NO new outbound HTTP requests of any kind
(availability/specificity are both derived from already-stored data).

Usage:
    python scripts/check_direct_apply_coverage.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.models.discovered_job_model import DiscoveredJob  # noqa: E402
from app.services.job_normalization_service import (  # noqa: E402
    classify_url_specificity,
    resolve_application_destination,
)


def run() -> None:
    db = SessionLocal()
    try:
        rows = db.query(DiscoveredJob).filter(DiscoveredJob.source == "jsearch").all()
        total = len(rows)

        counts: Counter[str] = Counter()
        direct_url_source_counts: Counter[str] = Counter()

        for row in rows:
            destination = resolve_application_destination(row)
            if destination["type"] == "AGGREGATOR_LISTING":
                counts["AGGREGATOR_ONLY"] += 1
                continue
            if destination["type"] is None:
                counts["NO_DESTINATION"] += 1
                continue

            specificity = classify_url_specificity(destination["url"])
            if specificity == "GENERIC_EMPLOYER_PAGE":
                counts["GENERIC_EMPLOYER_PAGE"] += 1
            elif specificity != "SPECIFIC_JOB":
                counts["UNKNOWN_SPECIFICITY"] += 1
            elif row.availability_status == "ACTIVE":
                counts["DIRECT_APPLY_ACTIVE"] += 1
            elif row.availability_status == "EXPIRED":
                counts["DIRECT_EXPIRED"] += 1
            else:
                counts["DIRECT_UNKNOWN"] += 1

            if row.direct_url_source:
                direct_url_source_counts[row.direct_url_source] += 1

        # ENRICHMENT_FAILED requires a configured provider to have actually
        # attempted enrichment on an aggregator-only job — see
        # job_enrichment_service.enrichment_provider() (returns None today,
        # no provider is configured/approved). Reported as 0 rather than
        # omitted so the metric's meaning stays explicit.
        counts.setdefault("DIRECT_ENRICHED", direct_url_source_counts.get("enrichment", 0))
        counts.setdefault("ENRICHMENT_FAILED", 0)

        denominator = total - counts["DIRECT_EXPIRED"]
        coverage_pct = (counts["DIRECT_APPLY_ACTIVE"] / denominator * 100) if denominator else 0.0

        print(f"TOTAL: {total}")
        print(f"DIRECT_APPLY_ACTIVE: {counts['DIRECT_APPLY_ACTIVE']}")
        print(f"DIRECT_UNKNOWN: {counts['DIRECT_UNKNOWN']}")
        print(f"DIRECT_EXPIRED: {counts['DIRECT_EXPIRED']}")
        print(f"AGGREGATOR_ONLY: {counts['AGGREGATOR_ONLY']}")
        print(f"DIRECT_ENRICHED: {counts['DIRECT_ENRICHED']}")
        print(f"ENRICHMENT_FAILED: {counts['ENRICHMENT_FAILED']}")
        print(f"GENERIC_EMPLOYER_PAGE: {counts['GENERIC_EMPLOYER_PAGE']}")
        if counts["UNKNOWN_SPECIFICITY"] or counts["NO_DESTINATION"]:
            print(f"UNKNOWN_SPECIFICITY: {counts['UNKNOWN_SPECIFICITY']}")
            print(f"NO_DESTINATION: {counts['NO_DESTINATION']}")
        print()
        print(f"direct_apply_coverage_percentage: {coverage_pct:.1f}% "
              f"(DIRECT_APPLY_ACTIVE / (TOTAL - DIRECT_EXPIRED) = "
              f"{counts['DIRECT_APPLY_ACTIVE']} / {denominator})")
    finally:
        db.close()


if __name__ == "__main__":
    run()
