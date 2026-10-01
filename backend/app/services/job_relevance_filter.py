"""Stage 1 — cheap, deterministic, preference-based relevance filtering.

Decides which discovered jobs are even worth the (resume-based) Stage 2
match calculation, and — more importantly — which ones belong in this
user's search at all.

Title/role family comes first (see role_family_service): the saved job
title is the strongest discovery criterion, so a clearly different
occupation is rejected no matter how much tooling its description shares.
Skills, location, work mode, employment type and experience are applied
afterwards, as refining signals that rank an already-relevant job — never
as an admission rule that can override a wrong occupation.

This stays generic: the role taxonomy is data, and a preferred title
outside it still gets a title-first decision. Nothing is persisted — each
call evaluates the stored jobs against ONE user's current preference, so
preference changes re-evaluate visibility immediately and users with
different preferences see different results from the same stored pool.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.role_family_service import RoleDecision, evaluate_role_alignment

_STOPWORDS = {
    "and", "or", "the", "a", "an", "of", "for", "with", "in", "to", "on", "at",
    "senior", "sr", "junior", "jr", "lead", "principal", "staff", "i", "ii", "iii",
    "engineer", "developer", "specialist",
}

# Alignment kinds ordered by how directly the job answers the saved title.
_ALIGNMENT_BASE_SCORE = {
    "TITLE_MATCH": 60.0,
    "TOKEN_MATCH": 55.0,
    "ADJACENT_ROLE": 45.0,
    "JD_EVIDENCE": 35.0,
    "NO_PREFERENCE": 30.0,
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


def _work_mode_compatible(job_work_mode: str, preferred_work_mode: str) -> bool:
    preferred = (preferred_work_mode or "").strip().lower()
    if not preferred:
        return True
    return preferred in (job_work_mode or "").strip().lower()


def _job_type_compatible(job_type: str, preferred_job_type: str) -> bool:
    """"Full-time" must still match a source that spells it "Full Time" or
    lists "Full-time and Part-time"."""
    preferred = re.sub(r"[^a-z]", "", (preferred_job_type or "").lower())
    if not preferred:
        return True
    return preferred in re.sub(r"[^a-z]", "", (job_type or "").lower())


def _required_years(description: str) -> int | None:
    found = re.search(r"(\d{1,2})\s*\+?\s*(?:to|-)?\s*\d{0,2}\s*years?", (description or "")[:4000], re.I)
    return int(found.group(1)) if found else None


def _experience_compatible(description: str, preferred_experience: str) -> bool:
    """Soft signal only — a candidate with more experience than a posting asks
    for is still relevant, and most postings state no number at all."""
    years = re.search(r"\d{1,2}", str(preferred_experience or ""))
    required = _required_years(description)
    if not years or required is None:
        return True
    return required <= int(years.group(0))


@dataclass(frozen=True)
class RelevanceDecision:
    """Per-user, per-request discovery verdict for one stored job."""

    relevant: bool
    score: float
    reason: str
    alignment: str
    role: RoleDecision


def evaluate_relevance(job, preference) -> RelevanceDecision:
    """Role gate first, then the refining preference signals.

    Cheap by design: no resume text and no LLM, so it is safe to run over
    every stored job on every request."""
    role = evaluate_role_alignment(
        job_title=job.title or "",
        job_description=job.description or "",
        preferred_title=getattr(preference, "job_title", "") or "",
    )
    if not role.relevant:
        return RelevanceDecision(False, 0.0, role.reason, role.alignment, role)

    score = _ALIGNMENT_BASE_SCORE.get(role.alignment, 30.0)

    pref_skills = _skill_tokens(getattr(preference, "skills", "") or "")
    job_skills = _skill_tokens(job.skills or "") | _tokenize((job.description or "")[:2000])
    score += min(20.0, 7.0 * len(pref_skills & job_skills))

    if _location_compatible(
        job.location or "", job.work_mode or "",
        getattr(preference, "location", "") or "", getattr(preference, "work_mode", "") or "",
    ):
        score += 8.0
    if _work_mode_compatible(job.work_mode or "", getattr(preference, "work_mode", "") or ""):
        score += 5.0
    if _job_type_compatible(job.job_type or "", getattr(preference, "job_type", "") or ""):
        score += 4.0
    if _experience_compatible(job.description or "", getattr(preference, "experience", "") or ""):
        score += 3.0

    return RelevanceDecision(True, min(100.0, score), role.reason, role.alignment, role)


def relevance_score(job, preference) -> float:
    """0-100 preference-relevance score. 0 means the job is not this user's
    occupation at all — ranking never rescues a rejected role."""
    return evaluate_relevance(job, preference).score


def filter_relevant_jobs(jobs, preference, top_n: int = 40, min_score: float = 12.0):
    """Up to top_n jobs that genuinely belong to the user's saved role,
    ranked by relevance. Jobs the role gate rejects are dropped outright —
    they are still stored, still visible to other users with matching
    preferences, and become visible to this user again if the preference
    changes to match them."""
    scored = []
    for job in jobs:
        decision = evaluate_relevance(job, preference)
        if decision.relevant and decision.score >= min_score:
            scored.append((job, decision.score))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [job for job, _score in scored[:top_n]]


_ACTION_RANK = {
    "Apply": 5,
    "Open Employer Site": 4,
    "View Listing": 2,
    "Job Closed": 1,
}


def _action_rank_value(label: str | None) -> int:
    if not label:
        return 0
    if label in _ACTION_RANK:
        return _ACTION_RANK[label]
    if label.startswith("View on "):
        return 3
    return 0


def _canonical_url(url: str) -> str:
    return (url or "").strip().rstrip("/").split("?", 1)[0].lower()


def _role_identity(job) -> tuple[str, str, str] | None:
    company = (job.company or "").strip().lower()
    title = (job.title or "").strip().lower()
    if not company or not title:
        return None
    location = (job.location or "").strip().lower()
    return (company, title, location)


def _display_rank(job) -> tuple:
    from app.services.job_normalization_service import resolve_apply_action

    action = resolve_apply_action(job)
    posted = getattr(job, "posted_at", None) or getattr(job, "first_seen_at", None)
    stamp = posted.isoformat() if hasattr(posted, "isoformat") else str(posted or "")
    return (
        _action_rank_value(action.get("action_label")),
        1 if action.get("is_direct") else 0,
        stamp,
    )


def dedupe_visible_jobs(jobs):
    """Collapse the same opportunity when it appears from more than one
    source, keeping the record with the best verified destination.

    Display-only: stored rows are never deleted or globally marked. Identity
    is URL first, then company + title + location."""
    if not jobs:
        return jobs

    claimed_urls: set[str] = set()
    claimed_roles: set[tuple[str, str, str]] = set()
    ranked = sorted(enumerate(jobs), key=lambda pair: (_display_rank(pair[1]), -pair[0]), reverse=True)

    kept: set[int] = set()
    for index, job in ranked:
        urls = [
            _canonical_url(url) for url in
            (getattr(job, "direct_url", None), getattr(job, "application_url", None))
            if _canonical_url(url)
        ]
        role = _role_identity(job)
        if any(url in claimed_urls for url in urls) or (role and role in claimed_roles):
            continue
        kept.add(index)
        claimed_urls.update(urls)
        if role:
            claimed_roles.add(role)

    return [job for index, job in enumerate(jobs) if index in kept]
