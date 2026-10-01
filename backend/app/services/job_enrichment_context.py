"""Per-refresh quota/diagnostic context; no process-global result cache or polling."""
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import os


@dataclass
class EnrichmentBudget:
    """Per-refresh enrichment quota, split into two priority lanes.

    A provider-supplied but not-yet-verified direct apply candidate (e.g.
    Dice's applyUrl) must reach shared verification even when a
    high-volume listing-only source (e.g. Adzuna, which never supplies a
    candidate URL at all) is processed first — adapters run one at a time,
    in a fixed order, so without a reserved lane a large listing-only
    source could claim every slot in the shared pool before a later
    source's direct candidates were ever attempted. `listing_seen` is
    capped at `max_listing_jobs` (at most half the total, rounded down) so
    the rest of `max_jobs` always stays available for direct candidates,
    regardless of which source runs first. `seen` remains the union of
    both lanes for any caller that only needs "was this identity handled
    this refresh".
    """
    max_jobs: int = 5
    seen: set = field(default_factory=set)
    direct_seen: set = field(default_factory=set)
    listing_seen: set = field(default_factory=set)
    counters: Counter = field(default_factory=Counter)
    events: list = field(default_factory=list)

    @property
    def max_listing_jobs(self) -> int:
        if self.max_jobs <= 0:
            return 0
        reserved_for_direct = -(-self.max_jobs // 2)  # ceil(max_jobs / 2)
        return max(1, self.max_jobs - reserved_for_direct)

    def reserve(self, identity: tuple, *, is_direct: bool) -> bool:
        """Attempts to claim one budget slot for `identity` in the
        appropriate lane. Returns False when the identity was already
        handled this refresh, the shared total is exhausted, or (for a
        listing-only identity) the listing lane's own cap is exhausted —
        the caller must then skip enrichment for this job this refresh."""
        if identity in self.direct_seen or identity in self.listing_seen:
            return False
        if len(self.direct_seen) + len(self.listing_seen) >= self.max_jobs:
            return False
        if not is_direct and len(self.listing_seen) >= self.max_listing_jobs:
            return False
        (self.direct_seen if is_direct else self.listing_seen).add(identity)
        self.seen.add(identity)
        return True


current_budget = ContextVar("discovery_enrichment_budget", default=None)


@contextmanager
def enrichment_scope():
    try:
        limit = max(0, min(20, int(os.getenv("JOB_ENRICHMENT_MAX_JOBS_PER_REFRESH", "5"))))
    except ValueError:
        limit = 5
    budget = EnrichmentBudget(max_jobs=limit)
    token = current_budget.set(budget)
    try:
        yield budget
    finally:
        current_budget.reset(token)


def record_event(name, **safe_fields):
    budget = current_budget.get()
    if budget is not None:
        budget.counters[name] += 1
        # Callers supply fixed codes, HTTP statuses and hostname only.
        budget.events.append({"event": name, **safe_fields})
