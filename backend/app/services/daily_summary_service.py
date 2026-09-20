"""Shared, read-only-data Daily Summary generation helpers."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.email_agent import build_job_summary_email
from app.models.email_log_model import EmailLog
from app.models.job_model import Job
from app.models.match_model import MatchScore


class DailySummaryUnavailable(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def build_summary_content(db: Session, *, user):
    matches = db.query(MatchScore).filter(MatchScore.user_id == user.id).order_by(MatchScore.match_percentage.desc()).all()
    if not matches:
        raise DailySummaryUnavailable("no_match_scores")

    by_job = {}
    for match in matches:
        bucket = by_job.setdefault(match.job_id, {})
        version = getattr(match, "resume_version", "original") or "original"
        bucket[version] = match

    matches_with_jobs = []
    for job_id, versions in by_job.items():
        job = db.query(Job).filter(Job.id == job_id, Job.user_id == user.id).first()
        if not job:
            continue
        original = versions.get("original") or next(iter(versions.values()), None)
        if original:
            matches_with_jobs.append({"match": original, "optimized_match": versions.get("optimized"), "job": job})
    if not matches_with_jobs:
        raise DailySummaryUnavailable("no_jobs")
    matches_with_jobs.sort(key=lambda item: item["match"].match_percentage, reverse=True)
    return build_job_summary_email(user=user, matches_with_jobs=matches_with_jobs[:5])


def new_generated_summary(*, user_id: int, recipient_email: str, content: dict) -> EmailLog:
    return EmailLog(
        user_id=user_id, recipient_email=recipient_email, subject=content["subject"], body=content["body"],
        status="generated", event_type="summary_generated",
    )
