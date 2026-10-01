"""Small, provider-independent collector contract. No database or matching code."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class CareerSource:
    company_name: str
    career_url: str
    provider: str = "auto"
    enabled: bool = True


@dataclass
class Collection:
    jobs: list[dict[str, Any]] = field(default_factory=list)
    # Only a successful, complete inventory can age missing jobs.
    # A bounded/partial HTML crawl must never claim authoritative absence.
    complete: bool = False


def date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def expired(value):
    stamp = date(value)
    return bool(stamp and stamp.replace(tzinfo=stamp.tzinfo or timezone.utc) < datetime.now(timezone.utc))
