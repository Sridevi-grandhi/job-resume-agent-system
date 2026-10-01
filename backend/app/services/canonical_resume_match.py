"""Canonical current original Resume Match — shared by Dashboard and Selected Jobs.

Resume Match / Best Match / Top Job Matches are the latest persisted original
MatchScore per job (highest id), not a historical max and not an ATS optimized
score. This module does not recalculate scores.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.job_model import Job
from app.models.match_model import MatchScore


def is_original_resume_version(resume_version: str | None) -> bool:
    return (resume_version or "original") == "original"


def latest_original_match_score(db: Session, user_id: int, job_id: int) -> MatchScore | None:
    matches = (
        db.query(MatchScore)
        .filter(MatchScore.user_id == user_id, MatchScore.job_id == job_id)
        .order_by(MatchScore.id.desc())
        .all()
    )
    return next((match for match in matches if is_original_resume_version(match.resume_version)), None)


def latest_optimized_match_score(db: Session, user_id: int, job_id: int) -> MatchScore | None:
    matches = (
        db.query(MatchScore)
        .filter(MatchScore.user_id == user_id, MatchScore.job_id == job_id)
        .order_by(MatchScore.id.desc())
        .all()
    )
    return next((match for match in matches if match.resume_version == "optimized"), None)


def canonical_original_matches_for_user(db: Session, user_id: int) -> list[tuple[MatchScore, Job]]:
    """One current original Resume Match per job, ranked by percentage descending."""
    rows = (
        db.query(MatchScore, Job)
        .join(Job, MatchScore.job_id == Job.id)
        .filter(MatchScore.user_id == user_id)
        .order_by(MatchScore.id.desc())
        .all()
    )
    seen_jobs: set[int] = set()
    canonical: list[tuple[MatchScore, Job]] = []
    for match, job in rows:
        if not is_original_resume_version(getattr(match, "resume_version", None)):
            continue
        if job.id in seen_jobs:
            continue
        seen_jobs.add(job.id)
        canonical.append((match, job))
    canonical.sort(key=lambda pair: int(pair[0].match_percentage or 0), reverse=True)
    return canonical
