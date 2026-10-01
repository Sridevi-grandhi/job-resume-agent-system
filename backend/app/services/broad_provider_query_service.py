"""Builds broad-provider (JSearch) search queries from real, logged-in
users' saved job preferences — never a hardcoded role/title/company.

Distinct preference combinations are deduplicated so a poll cycle issues one
provider request per unique (title, location, work_mode, job_type) tuple
rather than one per user — several users with the same preferences share a
single API call. This is what keeps a metered provider's quota usable when
many users are configured (see UPDATED_JOB_PRIMARY_PROVIDER docs) and avoids
firing a fresh provider call per browser/page view: both the scheduler and
the manual "Refresh Jobs" button go through the exact same deduplicated
query set via job_source_registry_service.get_adapters.
"""

from __future__ import annotations

import os

from sqlalchemy.orm import Session

from app.models.user_preference_model import UserPreference


def max_queries_per_poll() -> int:
    return max(1, int(os.getenv("JSEARCH_MAX_QUERIES_PER_POLL") or "8"))


def get_active_preference_queries(db: Session, max_queries: int | None = None, *, user_id: int | None = None) -> list[dict[str, str]]:
    """Returns up to max_queries deduplicated query specs, one per distinct
    preference combination across all users who have saved preferences.
    A preference row with no job title contributes nothing — there is no
    safe way to build a real query from it."""
    limit = max_queries if max_queries is not None else max_queries_per_poll()
    query = db.query(UserPreference)
    if user_id is not None:
        query = query.filter(UserPreference.user_id == user_id)
    rows = query.order_by(UserPreference.id.desc()).all()

    seen: set[tuple[str, str, str, str]] = set()
    queries: list[dict[str, str]] = []
    from app.services.role_family_service import discovery_search_titles

    for pref in rows:
        job_title = (pref.job_title or "").strip()
        if not job_title:
            continue
        location = (pref.location or "").strip()
        work_mode = (pref.work_mode or "").strip()
        job_type = (pref.job_type or "").strip()
        skills = (pref.skills or "").strip()
        # Exact title first, then at most one role-family title. The shared
        # max_queries cap still stops a refresh from multiplying provider calls.
        for title in discovery_search_titles(job_title, skills, extra_limit=1):
            key = (title.lower(), location.lower(), work_mode.lower(), job_type.lower())
            if key in seen:
                continue
            seen.add(key)
            queries.append({
                "job_title": title,
                "location": location,
                "work_mode": work_mode,
                "job_type": job_type,
                "skills": skills,
            })
            if len(queries) >= limit:
                return queries
    return queries
