"""Stage 1 — cheap, deterministic, preference-based relevance filtering.

Decides which discovered jobs are even worth the (resume-based) Stage 2
match calculation. Generic on purpose: no role-family branching, no
hardcoded lists of "SDET titles" or "DevOps titles" — just token/skill
overlap against whatever the user's preferences say. This is what keeps the
feature working for any technology role instead of only the roles someone
thought to hardcode.
"""

from __future__ import annotations

import re

_STOPWORDS = {
    "and", "or", "the", "a", "an", "of", "for", "with", "in", "to", "on", "at",
    "senior", "sr", "junior", "jr", "lead", "principal", "staff", "i", "ii", "iii",
    "engineer", "developer", "specialist",
}


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9+#.]+", (text or "").lower())
    return {word for word in words if word and word not in _STOPWORDS and len(word) > 1}


def _skill_tokens(skills_csv: str) -> set[str]:
    parts = re.split(r"[,;/|\n]+", skills_csv or "")
    return {part.strip().lower() for part in parts if part.strip()}


def _location_compatible(job_location: str, job_work_mode: str, preferred_location: str, preferred_work_mode: str) -> bool:
    preferred_location = (preferred_location or "").strip().lower()
    preferred_work_mode = (preferred_work_mode or "").strip().lower()
    job_location = (job_location or "").strip().lower()
    job_work_mode = (job_work_mode or "").strip().lower()

    if not preferred_location and not preferred_work_mode:
        return True
    if preferred_work_mode and preferred_work_mode == "remote":
        if job_work_mode == "remote" or "remote" in job_location or "anywhere" in job_location or "worldwide" in job_location:
            return True
    if not preferred_location:
        return True
    if preferred_location in job_location or job_location in preferred_location:
        return True
    if job_work_mode == "remote":
        return True
    return False


def relevance_score(job, preference) -> float:
    """0-100 preference-relevance score for one discovered job. Cheap: no
    resume text, no LLM — pure text overlap so it's safe to run over every
    fetched job before the more expensive Stage 2 pass."""
    title_tokens = _tokenize(getattr(preference, "job_title", "") or "")
    job_title_tokens = _tokenize(job.title or "")
    job_desc_tokens = _tokenize((job.description or "")[:2000])
    pref_skills = _skill_tokens(getattr(preference, "skills", "") or "")
    job_skills = _skill_tokens(job.skills or "") | job_desc_tokens

    title_overlap = len(title_tokens & job_title_tokens)
    title_score = min(60, title_overlap * 20)
    if title_tokens and title_tokens.issubset(job_title_tokens):
        title_score = 60
    elif job_title_tokens and job_title_tokens.issubset(title_tokens):
        title_score = max(title_score, 45)

    skill_overlap = len(pref_skills & job_skills)
    skill_score = min(35, skill_overlap * 7)

    location_bonus = 5 if _location_compatible(
        job.location or "", job.work_mode or "",
        getattr(preference, "location", "") or "", getattr(preference, "work_mode", "") or "",
    ) else 0

    return float(min(100, title_score + skill_score + location_bonus))


def filter_relevant_jobs(jobs, preference, top_n: int = 40, min_score: float = 12.0):
    """Return up to top_n jobs sorted by relevance_score desc, dropping
    anything below min_score (near-zero title/skill signal)."""
    scored = [(job, relevance_score(job, preference)) for job in jobs]
    scored = [(job, score) for job, score in scored if score >= min_score]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [job for job, _score in scored[:top_n]]
