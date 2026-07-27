"""
Updated Job Search Agent — Remotive API only.

Existing Job Search Agent lives in job_search_agent.py and must remain separate.
This module must not call or import job_search_agent.
"""

from __future__ import annotations

import logging
import re
from html import unescape
from typing import Any

import requests

from app.agents.relevance_filter import (
    build_alternative_queries,
    calculate_relevance_rank,
    deduplicate_ranked_jobs,
    get_role_aliases,
    is_role_relevant,
    normalize_job_type,
    normalize_text,
    passes_skill_relevance,
)


# Updated Job Search Agent — Remotive API
REMOTIVE_API_URL = "https://remotive.com/api/remote-jobs"
REQUEST_TIMEOUT_SECONDS = 20
USER_AGENT = "JobResumeAgent/1.0 (Updated Job Search Agent; educational project)"

logger = logging.getLogger(__name__)

_SESSION: requests.Session | None = None


def _get_session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            }
        )
        _SESSION = session
    return _SESSION


def _preference_value(preference, field: str) -> str:
    return str(getattr(preference, field, "") or "").strip()


def clean_html(raw_html: str | None) -> str:
    """Remove HTML tags, decode entities, and normalize whitespace."""
    if not raw_html:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(raw_html))
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_valid_job_url(job: dict) -> str | None:
    """
    Prefer Remotive job['url']; fall back to job['job_url'].
    Reject blank, non-http(s), homepage, example.com, and non-detail pages.
    """
    raw = job.get("url")
    if raw is None or str(raw).strip() == "":
        raw = job.get("job_url")
    if raw is None:
        return None

    url = str(raw).strip()
    if not url:
        return None
    if not (url.startswith("http://") or url.startswith("https://")):
        return None

    lowered = url.lower().rstrip("/")
    blocked_hosts = {
        "http://example.com",
        "https://example.com",
        "http://www.example.com",
        "https://www.example.com",
        "http://remotive.com",
        "https://remotive.com",
        "http://www.remotive.com",
        "https://www.remotive.com",
        "http://remotive.io",
        "https://remotive.io",
        "http://www.remotive.io",
        "https://www.remotive.io",
    }
    if lowered in blocked_hosts or "example.com" in lowered:
        return None

    # Individual Remotive job pages look like /remote-jobs/<category>/<slug>-<id>
    if "remotive.com" in lowered or "remotive.io" in lowered:
        if "/remote-jobs/" not in lowered:
            return None
        detail = lowered.split("/remote-jobs/", 1)[-1]
        if not detail or "/" not in detail:
            return None

    return url


def fetch_jobs_from_remotive(search_query: str) -> list[dict]:
    """
    Call Remotive with a supported `search` parameter only.
    Handles timeout, connection errors, non-200, and invalid JSON safely.
    """
    session = _get_session()
    try:
        response = session.get(
            REMOTIVE_API_URL,
            params={"search": search_query},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.Timeout:
        logger.error("Remotive API timeout for search query length=%s", len(search_query or ""))
        return []
    except requests.ConnectionError:
        logger.error("Remotive API connection error")
        return []
    except requests.RequestException as error:
        logger.error("Remotive API request failed: %s", type(error).__name__)
        return []

    if response.status_code != 200:
        logger.error("Remotive API non-200 status: %s", response.status_code)
        return []

    try:
        payload = response.json()
    except ValueError:
        logger.error("Remotive API returned invalid JSON")
        return []

    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        logger.error("Remotive API response missing jobs list")
        return []
    return [job for job in jobs if isinstance(job, dict)]


def build_search_queries(preference) -> list[str]:
    requested_title = _preference_value(preference, "job_title")
    return build_alternative_queries(requested_title)


def normalize_remotive_job(job: dict, preference) -> dict[str, Any] | None:
    """Normalize a Remotive record into the exact job structure used by this agent."""
    job_url = extract_valid_job_url(job)
    if not job_url:
        return None

    title = str(job.get("title") or "").strip()
    company = str(job.get("company_name") or "").strip()
    if not title or not company:
        return None

    location = str(job.get("candidate_required_location") or "").strip() or "Remote"
    description = clean_html(job.get("description"))
    if not description and job.get("description"):
        description = clean_html(str(job.get("description")))

    tags = job.get("tags") or []
    if isinstance(tags, list):
        skills_required = ", ".join(str(tag).strip() for tag in tags if str(tag).strip())
    else:
        skills_required = str(tags).strip()
    if not skills_required:
        skills_required = _preference_value(preference, "skills") or "Not specified"

    job_type = normalize_job_type(job.get("job_type") or _preference_value(preference, "job_type"))
    if not job_type:
        job_type = "Full-time"

    return {
        "title": title,
        "company": company,
        "location": location,
        "work_mode": "Remote",
        "job_type": job_type,
        "description": description or "No description provided.",
        "skills_required": skills_required,
        "source": "Remotive API",
        "job_url": job_url,
    }


def evaluate_remotive_job(raw_job: dict, preference, aliases: list[str]) -> dict | None:
    """Apply relevance + skill filters, then normalize. Returns ranked candidate or None."""
    requested_title = _preference_value(preference, "job_title")
    if not requested_title:
        return None

    if not extract_valid_job_url(raw_job):
        return None

    if not is_role_relevant(raw_job, requested_title, aliases):
        return None

    skill_ok, matched_skills = passes_skill_relevance(raw_job, preference, aliases)
    if not skill_ok:
        return None

    normalized = normalize_remotive_job(raw_job, preference)
    if not normalized:
        return None

    tier, score = calculate_relevance_rank(raw_job, preference, aliases, matched_skills)
    return {
        "job": normalized,
        "relevance_tier": tier,
        "relevance_score": score,
        "matched_preference_skills": matched_skills,
    }


def get_updated_jobs_from_remotive(preference, max_jobs: int = 10) -> dict:
    """
    Updated Job Search Agent entry point.
    Fetches Remotive jobs for the user's preferred title (+ safe aliases),
    filters for relevance, and returns up to max_jobs unique direct-link jobs.
    """
    requested_title = _preference_value(preference, "job_title")
    aliases = get_role_aliases(requested_title)
    search_queries = build_search_queries(preference)

    # Read remaining preferences so they are available for future filters / logging.
    _ = {
        "location": _preference_value(preference, "location"),
        "experience": _preference_value(preference, "experience"),
        "work_mode": _preference_value(preference, "work_mode"),
        "job_type": _preference_value(preference, "job_type"),
        "skills": _preference_value(preference, "skills"),
    }

    total_received = 0
    candidates = []
    seen_raw_urls = set()

    for query in search_queries:
        raw_jobs = fetch_jobs_from_remotive(query)
        total_received += len(raw_jobs)
        for raw_job in raw_jobs:
            url_key = normalize_text(extract_valid_job_url(raw_job) or "")
            if url_key and url_key in seen_raw_urls:
                continue
            if url_key:
                seen_raw_urls.add(url_key)

            evaluated = evaluate_remotive_job(raw_job, preference, aliases)
            if evaluated:
                candidates.append(evaluated)

        # Stop early once we have enough high-quality unique candidates.
        if len(candidates) >= max_jobs * 3:
            break

    ranked = deduplicate_ranked_jobs(candidates)
    jobs = [candidate["job"] for candidate in ranked[:max_jobs]]

    return {
        "requested_job_title": requested_title,
        "search_queries_used": search_queries,
        "total_api_jobs_received": total_received,
        "irrelevant_jobs_filtered": max(0, total_received - len(jobs)),
        "jobs": jobs,
    }
