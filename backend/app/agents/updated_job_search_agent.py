import re
from collections import Counter
from html import unescape

import requests

from app.agents.relevance_filter import (
    deduplicate_ranked_jobs,
    is_java_full_stack_request,
    normalize_job_type,
    normalize_text,
    score_java_full_stack_job,
)


REMOTIVE_API_URL = "https://remotive.com/api/remote-jobs"

ROLE_ALIASES = {
    "java full stack": (
        "Java Full Stack",
        "Java Fullstack",
        "Full Stack Java",
        "Fullstack Java",
        "Java React",
        "Java Angular",
        "Java Full Stack Developer",
        "Full Stack Java Developer",
        "Java Software Engineer",
        "Java Developer",
    ),
    "data engineer": (
        "Data Engineer",
        "Data Platform Engineer",
        "ETL Engineer",
        "Big Data Engineer",
        "Analytics Engineer",
    ),
    "sdet": (
        "SDET",
        "Software Engineer in Test",
        "Software Development Engineer in Test",
        "Test Automation Engineer",
        "QA Automation Engineer",
    ),
    "devops engineer": (
        "DevOps Engineer",
        "Cloud Engineer",
        "Site Reliability Engineer",
        "Platform Engineer",
        "Infrastructure Engineer",
    ),
}

GENERIC_TITLE_WORDS = {
    "and", "or", "the", "a", "an", "of", "for", "with",
    "senior", "sr", "junior", "jr", "lead", "principal",
    "full", "stack", "software", "engineer", "developer",
}


def clean_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", raw_html))).strip()


def _preference_value(preference, field: str) -> str:
    return str(getattr(preference, field, "") or "").strip()


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized_phrase = normalize_text(phrase)
    return bool(normalized_phrase and re.search(rf"\b{re.escape(normalized_phrase)}\b", text))


def get_role_aliases(job_title: str):
    normalized = normalize_text(job_title)
    if is_java_full_stack_request(job_title):
        return list(ROLE_ALIASES["java full stack"])
    return list(ROLE_ALIASES.get(normalized, (job_title,)))


def build_search_queries(preference):
    requested_title = _preference_value(preference, "job_title")
    aliases = get_role_aliases(requested_title)
    if is_java_full_stack_request(requested_title):
        broader_query = "Java Developer"
        queries = [
            requested_title,
            *(alias for alias in aliases if normalize_text(alias) != normalize_text(broader_query)),
            broader_query,
        ]
    else:
        queries = [requested_title, *aliases]
    unique_queries = []
    seen = set()
    for query in queries:
        normalized = normalize_text(query)
        if normalized and normalized not in seen:
            unique_queries.append(query)
            seen.add(normalized)
    return unique_queries


def fetch_jobs_from_remotive(search_query: str):
    response = requests.get(
        REMOTIVE_API_URL,
        params={"search": search_query},
        timeout=20,
    )
    response.raise_for_status()
    return response.json().get("jobs", [])


def _important_title_keywords(requested_title: str):
    words = normalize_text(requested_title).split()
    specific = [word for word in words if word not in GENERIC_TITLE_WORDS and len(word) > 1]
    return specific if specific else [word for word in words if len(word) > 1]


def passes_strict_title_relevance(job, requested_title: str, aliases) -> bool:
    returned_title = normalize_text(job.get("title"))
    normalized_requested = normalize_text(requested_title)
    if not returned_title or not normalized_requested:
        return False
    if _contains_phrase(returned_title, normalized_requested):
        return True
    if any(_contains_phrase(returned_title, alias) for alias in aliases):
        return True
    keywords = _important_title_keywords(requested_title)
    if len(normalized_requested.split()) == 1:
        return _contains_phrase(returned_title, normalized_requested)
    return len(keywords) >= 2 and sum(_contains_phrase(returned_title, word) for word in keywords) >= 2


def calculate_relevance_score(job, preference, aliases) -> int:
    requested_title = _preference_value(preference, "job_title")
    returned_title = normalize_text(job.get("title"))
    requested = normalize_text(requested_title)
    category = normalize_text(job.get("category"))
    description = normalize_text(clean_html(job.get("description", "")))
    tags = normalize_text(job.get("tags"))
    score = 100 if returned_title == requested else 80 if _contains_phrase(returned_title, requested) else 0
    score += 70 if any(_contains_phrase(returned_title, alias) for alias in aliases) else 0
    score += 20 * sum(_contains_phrase(returned_title, word) for word in _important_title_keywords(requested_title))
    phrases = {requested, *(normalize_text(alias) for alias in aliases)}
    score += 20 if any(_contains_phrase(category, phrase) for phrase in phrases) else 0
    score += 10 if any(_contains_phrase(description, phrase) for phrase in phrases) else 0
    skills = [
        normalize_text(skill)
        for skill in re.split(r"[,;\n]+", _preference_value(preference, "skills"))
        if normalize_text(skill)
    ]
    score += 8 * sum(_contains_phrase(tags, skill) for skill in skills)
    score += 2 * min(10, sum(_contains_phrase(description, skill) for skill in skills))
    return score


def normalize_remotive_job(job, preference, filter_result=None):
    tags = job.get("tags") or []
    candidate_location = job.get("candidate_required_location") or "Remote"
    result = filter_result or {}
    return {
        "title": job.get("title") or "Remote Job",
        "company": job.get("company_name") or "Unknown Company",
        "location": candidate_location,
        "candidate_required_location": candidate_location,
        "work_mode": "Remote",
        "job_type": normalize_job_type(job.get("job_type") or _preference_value(preference, "job_type")),
        "description": clean_html(job.get("description", "")),
        "skills_required": ", ".join(tags) if tags else _preference_value(preference, "skills"),
        "tags": tags,
        "source": "Remotive API",
        "job_url": job.get("url") or job.get("job_url"),
        "publication_date": job.get("publication_date"),
        "relevance_score": result.get("relevance_score"),
        "matched_preference_skills": result.get("matched_preference_skills", []),
        "role_family_match": result.get("role_family_match", True),
    }


def get_updated_jobs_from_remotive(preference, max_jobs: int = 10):
    requested_title = _preference_value(preference, "job_title")
    aliases = get_role_aliases(requested_title)
    search_queries = build_search_queries(preference)
    java_family = is_java_full_stack_request(requested_title)
    total_received = 0
    rejection_reasons = Counter()
    candidates = []

    for query in search_queries:
        try:
            raw_jobs = fetch_jobs_from_remotive(query)
            total_received += len(raw_jobs)
            for raw_job in raw_jobs:
                if java_family:
                    filter_result = score_java_full_stack_job(raw_job, preference)
                    if not filter_result["accepted"]:
                        rejection_reasons.update(filter_result["rejection_reasons"])
                        continue
                    relevance_score = filter_result["relevance_score"]
                else:
                    if not passes_strict_title_relevance(raw_job, requested_title, aliases):
                        rejection_reasons["title did not pass strict relevance"] += 1
                        continue
                    relevance_score = calculate_relevance_score(raw_job, preference, aliases)
                    if relevance_score < 45:
                        rejection_reasons["relevance score is below 45"] += 1
                        continue
                    filter_result = {
                        "relevance_score": relevance_score,
                        "matched_preference_skills": [],
                        "role_family_match": True,
                    }

                candidates.append({
                    "job": normalize_remotive_job(raw_job, preference, filter_result),
                    "relevance_score": relevance_score,
                })
        except (requests.RequestException, ValueError) as error:
            rejection_reasons[f"API error for query '{query}': {error}"] += 1

    ranked = deduplicate_ranked_jobs(candidates)
    jobs = [candidate["job"] for candidate in ranked[:max_jobs]]
    return {
        "requested_job_title": requested_title,
        "search_queries_used": search_queries,
        "total_api_jobs_received": total_received,
        "irrelevant_jobs_filtered": total_received - len(ranked),
        "rejection_reasons": dict(rejection_reasons),
        "jobs": jobs,
    }
