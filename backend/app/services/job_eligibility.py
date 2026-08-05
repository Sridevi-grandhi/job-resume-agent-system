"""Helpers to decide whether a job is eligible for ATS analysis / optimize-and-match."""

from __future__ import annotations

import re

GENERIC_SEARCH_SOURCES = {
    "LinkedIn Jobs",
    "Indeed",
    "Dice",
    "Google Jobs",
    "ZipRecruiter",
}

INCOMPLETE_DESCRIPTION_MARKERS = (
    "captcha",
    "unusual traffic",
    "verify you are a human",
    "sign in to continue",
    "log in to continue",
    "login to continue",
    "create an account to view",
    "please enable cookies",
    "access denied",
    "robot check",
    "are you a robot",
    "job search results",
    "showing results for",
    "filter by",
)


def is_generic_search_link_job(job) -> bool:
    source = (getattr(job, "source", "") or "").strip()
    if source in GENERIC_SEARCH_SOURCES:
        return True
    description = (getattr(job, "description", "") or "").strip().lower()
    # Existing search-agent descriptions are short “Search … openings …” blurbs
    if description.startswith("search ") and "openings" in description:
        return True
    return False


def _looks_like_blocked_page(description: str) -> bool:
    lowered = (description or "").lower()
    return any(marker in lowered for marker in INCOMPLETE_DESCRIPTION_MARKERS)


def job_is_analyzable(job, require_url: bool = True) -> tuple[bool, str]:
    """
    Validate that a saved job is complete enough for optimize-and-match.

    require_url:
      - True for manual ATS runs / Remotive (prefer direct URL)
      - False for selected-job post-save automation when URL may be absent
    """
    if job is None:
        return False, "Job not found"
    title = (getattr(job, "title", "") or "").strip()
    company = (getattr(job, "company", "") or "").strip()
    description = (getattr(job, "description", "") or "").strip()
    job_url = (getattr(job, "job_url", "") or "").strip()
    job_id = getattr(job, "id", None)
    user_id = getattr(job, "user_id", None)

    if not job_id:
        return False, "Job id is required"
    if is_generic_search_link_job(job):
        return False, "Complete job description is required before generating an optimized resume."
    if user_id is None:
        return False, "User id is required for job ownership validation."
    if not title or not company:
        return False, "Job title and company are required before generating an optimized resume."
    if not description or len(description) < 40:
        return False, "Complete job description is required before generating an optimized resume."
    if _looks_like_blocked_page(description):
        return False, "Complete job description is required before generating an optimized resume."
    # Reject near-empty boilerplate that is mostly whitespace/punctuation
    alpha_chars = len(re.findall(r"[A-Za-z]", description))
    if alpha_chars < 30:
        return False, "Complete job description is required before generating an optimized resume."
    if require_url and not job_url:
        return False, "A direct job URL is required for ATS analysis."
    return True, ""
