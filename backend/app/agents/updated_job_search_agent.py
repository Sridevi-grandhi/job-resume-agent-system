"""
Updated Job Search Agent — Remotive public API only.

Existing Job Search Agent (job_search_agent.py) must remain separate.
This module must not import or call job_search_agent.
"""

from __future__ import annotations

import logging
import re
from html import unescape
from typing import Any
from urllib.parse import urlparse

import requests

from app.agents.relevance_filter import (
    build_alternative_queries,
    calculate_relevance_rank,
    candidate_location_compatible,
    deduplicate_ranked_jobs,
    get_role_aliases,
    is_role_relevant,
    java_full_stack_title_match,
    normalize_job_type,
    normalize_text,
    passes_skill_relevance,
)


REMOTIVE_API_URL = "https://remotive.com/api/remote-jobs"
REQUEST_TIMEOUT_SECONDS = 20
MAX_SEARCH_QUERIES = 8
USER_AGENT = "JobResumeAgent/1.0 (Updated Job Search Agent; educational project)"

logger = logging.getLogger(__name__)

_SESSION: requests.Session | None = None

try:
    from bs4 import BeautifulSoup  # type: ignore

    _HAS_BS4 = True
except ImportError:  # pragma: no cover
    BeautifulSoup = None
    _HAS_BS4 = False


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
    if not raw_html:
        return ""
    text = str(raw_html)
    if _HAS_BS4 and BeautifulSoup is not None:
        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        for br in soup.find_all("br"):
            br.replace_with("\n")
        for block in soup.find_all(["p", "li", "div", "h1", "h2", "h3", "h4", "tr"]):
            block.append("\n")
        text = soup.get_text(separator=" ")
    else:
        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", text)
        text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_valid_job_url(job: dict) -> str | None:
    """Prefer job['url']; fall back to job['job_url']. Never invent URLs."""
    raw = job.get("url")
    if raw is None or str(raw).strip() == "":
        raw = job.get("job_url")
    if raw is None:
        return None

    url = str(raw).strip()
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return None
    if "example.com" in url.lower():
        return None

    try:
        parsed = urlparse(url)
    except ValueError:
        return None

    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").rstrip("/")
    query = (parsed.query or "").lower()

    if host in {"remotive.com", "www.remotive.com", "remotive.io", "www.remotive.io"}:
        if path in {"", "/remote-jobs"}:
            return None
        if ("search" in query or "category" in query) and (
            "/remote-jobs/" not in path or path.count("/") < 2
        ):
            return None
        if "/remote-jobs/" not in path:
            return None
        detail = path.split("/remote-jobs/", 1)[-1]
        if not detail or "/" not in detail:
            return None
        if detail.split("/", 1)[0] in {"search", "categories", "category"}:
            return None
    return url


def fetch_jobs_from_remotive(search_query: str) -> tuple[list[dict], str | None, int | None]:
    """
    Call Remotive with params={'search': query}.
    Returns (jobs, error_code, http_status).
    """
    session = _get_session()
    try:
        response = session.get(
            REMOTIVE_API_URL,
            params={"search": search_query},
            timeout=(10, REQUEST_TIMEOUT_SECONDS),
        )
    except requests.Timeout:
        logger.error("Remotive timeout query=%r", search_query)
        return [], "timeout", None
    except requests.ConnectionError:
        logger.error("Remotive connection error query=%r", search_query)
        return [], "connection_error", None
    except requests.RequestException as error:
        logger.error("Remotive request failed query=%r error=%s", search_query, type(error).__name__)
        return [], "request_error", None

    status = response.status_code
    if status != 200:
        logger.error("Remotive non-200 status=%s query=%r", status, search_query)
        return [], f"http_{status}", status

    try:
        payload = response.json()
    except ValueError:
        logger.error("Remotive invalid JSON query=%r", search_query)
        return [], "invalid_json", status

    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        logger.error("Remotive missing jobs array query=%r", search_query)
        return [], "missing_jobs", status

    valid = [job for job in jobs if isinstance(job, dict)]
    titles = [str(job.get("title") or "")[:100] for job in valid[:15]]
    logger.info(
        "Remotive query=%r status=%s raw=%s titles=%s",
        search_query,
        status,
        len(valid),
        titles,
    )
    return valid, None, status


def build_search_queries(preference) -> list[str]:
    requested_title = _preference_value(preference, "job_title")
    return build_alternative_queries(requested_title, max_queries=MAX_SEARCH_QUERIES)


def normalize_remotive_job(job: dict, preference) -> dict[str, Any] | None:
    del preference
    job_url = extract_valid_job_url(job)
    if not job_url:
        return None

    title = str(job.get("title") or "").strip()
    company = str(job.get("company_name") or "").strip()
    if not title or not company:
        return None

    location = str(job.get("candidate_required_location") or "").strip() or "Remote"
    description = clean_html(job.get("description"))
    tags = job.get("tags") or []
    if isinstance(tags, list):
        skills_required = ", ".join(str(tag).strip() for tag in tags if str(tag).strip())
    else:
        skills_required = str(tags).strip()

    job_type = normalize_job_type(job.get("job_type") or "")
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


def _reject_reason(raw_job: dict, preference, aliases: list[str]) -> str:
    if not extract_valid_job_url(raw_job):
        return "invalid_url"
    if not is_role_relevant(raw_job, _preference_value(preference, "job_title"), aliases):
        return "title_filter"
    if not candidate_location_compatible(
        raw_job.get("candidate_required_location") or "",
        _preference_value(preference, "location"),
    ):
        return "location_filter"
    # Job type: never hard-reject on formatting; only log mismatch later.
    skill_ok, _ = passes_skill_relevance(raw_job, preference, aliases)
    if not skill_ok:
        return "skill_filter"
    if not normalize_remotive_job(raw_job, preference):
        return "normalize_failed"
    return "other"


def evaluate_remotive_job(raw_job: dict, preference, aliases: list[str]) -> dict | None:
    requested_title = _preference_value(preference, "job_title")
    if not requested_title:
        return None
    if not extract_valid_job_url(raw_job):
        return None
    if not is_role_relevant(raw_job, requested_title, aliases):
        return None
    if not candidate_location_compatible(
        raw_job.get("candidate_required_location") or "",
        _preference_value(preference, "location"),
    ):
        return None
    skill_ok, matched_skills = passes_skill_relevance(raw_job, preference, aliases)
    if not skill_ok:
        return None
    normalized = normalize_remotive_job(raw_job, preference)
    if not normalized:
        return None
    # Job-type formatting must not eliminate strong matches.
    tier, score = calculate_relevance_rank(raw_job, preference, aliases, matched_skills)
    return {
        "job": normalized,
        "relevance_tier": tier,
        "relevance_score": score,
        "matched_preference_skills": matched_skills,
    }


def get_updated_jobs_from_remotive(preference, max_jobs: int = 10) -> dict:
    requested_title = _preference_value(preference, "job_title")
    location = _preference_value(preference, "location")
    experience = _preference_value(preference, "experience")
    work_mode = _preference_value(preference, "work_mode")
    job_type = _preference_value(preference, "job_type")
    aliases = get_role_aliases(requested_title)
    search_queries = build_search_queries(preference)

    logger.info(
        "Updated Job Search prefs title=%r location=%r experience=%r work_mode=%r job_type=%r queries=%s",
        requested_title,
        location,
        experience,
        work_mode,
        job_type,
        search_queries,
    )

    total_received = 0
    candidates = []
    seen_urls = set()
    seen_title_company = set()
    api_errors: list[str] = []
    successful_queries = 0
    query_stats: list[dict[str, Any]] = []
    stage = {
        "raw": 0,
        "valid_url": 0,
        "after_dedupe": 0,
        "after_title_filter": 0,
        "after_location_filter": 0,
        "after_skill_filter": 0,
        "accepted": 0,
    }
    rejection_samples: list[dict[str, str]] = []

    for query in search_queries:
        raw_jobs, error, status = fetch_jobs_from_remotive(query)
        if error:
            api_errors.append(error)
            query_stats.append({"query": query, "status": status, "raw": 0, "error": error})
            continue

        successful_queries += 1
        total_received += len(raw_jobs)
        stage["raw"] += len(raw_jobs)
        query_accepted = 0

        for raw_job in raw_jobs:
            url = extract_valid_job_url(raw_job)
            if not url:
                if len(rejection_samples) < 40:
                    rejection_samples.append(
                        {"title": str(raw_job.get("title") or "")[:120], "reason": "invalid_url"}
                    )
                continue
            stage["valid_url"] += 1

            url_key = normalize_text(url)
            tc_key = (
                normalize_text(raw_job.get("title")),
                normalize_text(raw_job.get("company_name")),
            )
            if url_key in seen_urls or tc_key in seen_title_company:
                continue
            seen_urls.add(url_key)
            seen_title_company.add(tc_key)
            stage["after_dedupe"] += 1

            if not is_role_relevant(raw_job, requested_title, aliases):
                if len(rejection_samples) < 40:
                    rejection_samples.append(
                        {"title": str(raw_job.get("title") or "")[:120], "reason": "title_filter"}
                    )
                continue
            stage["after_title_filter"] += 1

            if not candidate_location_compatible(
                raw_job.get("candidate_required_location") or "",
                location,
            ):
                if len(rejection_samples) < 40:
                    rejection_samples.append(
                        {"title": str(raw_job.get("title") or "")[:120], "reason": "location_filter"}
                    )
                continue
            stage["after_location_filter"] += 1

            skill_ok, matched_skills = passes_skill_relevance(raw_job, preference, aliases)
            if not skill_ok:
                if len(rejection_samples) < 40:
                    rejection_samples.append(
                        {"title": str(raw_job.get("title") or "")[:120], "reason": "skill_filter"}
                    )
                continue
            stage["after_skill_filter"] += 1

            normalized = normalize_remotive_job(raw_job, preference)
            if not normalized:
                continue

            tier, score = calculate_relevance_rank(
                raw_job, preference, aliases, matched_skills
            )
            # Boost known strong Java Full Stack title shapes.
            if java_full_stack_title_match(
                raw_job.get("title") or "",
                raw_job.get("description") or "",
                raw_job.get("tags") or [],
            ):
                score += 50
            candidates.append(
                {
                    "job": normalized,
                    "relevance_tier": tier,
                    "relevance_score": score,
                    "matched_preference_skills": matched_skills,
                }
            )
            stage["accepted"] += 1
            query_accepted += 1

        query_stats.append(
            {
                "query": query,
                "status": status,
                "raw": len(raw_jobs),
                "accepted": query_accepted,
                "titles": [str(j.get("title") or "")[:100] for j in raw_jobs[:12]],
            }
        )

        if len(candidates) >= max_jobs:
            break

    ranked = deduplicate_ranked_jobs(candidates)
    jobs = [item["job"] for item in ranked[:max_jobs]]

    zero_stage = "none"
    if stage["raw"] == 0:
        zero_stage = "api_empty_or_error"
    elif stage["valid_url"] == 0:
        zero_stage = "url_validation"
    elif stage["after_dedupe"] == 0:
        zero_stage = "deduplication"
    elif stage["after_title_filter"] == 0:
        zero_stage = "title_filter"
    elif stage["after_location_filter"] == 0:
        zero_stage = "location_filter"
    elif stage["after_skill_filter"] == 0:
        zero_stage = "skill_filter"
    elif stage["accepted"] == 0:
        zero_stage = "normalize_or_ranking"
    elif not jobs:
        zero_stage = "post_rank_empty"

    api_status = "ok"
    if successful_queries == 0 and api_errors:
        api_status = "unavailable"
    elif not jobs:
        api_status = "no_relevant_jobs"

    logger.info(
        "Updated Job Search summary title=%r stages=%s zero_at=%s final=%s rejections=%s",
        requested_title,
        stage,
        zero_stage if not jobs else "n/a",
        len(jobs),
        rejection_samples[:20],
    )

    return {
        "requested_job_title": requested_title,
        "search_queries_used": search_queries,
        "total_api_jobs_received": total_received,
        "irrelevant_jobs_filtered": max(0, total_received - len(jobs)),
        "api_status": api_status,
        "api_error": api_errors[0] if api_errors and successful_queries == 0 else None,
        "diagnostics": {
            "preferences": {
                "job_title": requested_title,
                "location": location,
                "experience": experience,
                "work_mode": work_mode,
                "job_type": job_type,
            },
            "query_stats": query_stats,
            "stages": stage,
            "zero_count_stage": zero_stage if not jobs else None,
            "rejection_samples": rejection_samples[:20],
        },
        "jobs": jobs,
    }
