from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db

from app.models.user_model import User
from app.models.user_preference_model import UserPreference
from app.models.resume_model import Resume
from app.models.job_model import Job
from app.models.match_model import MatchScore
from app.models.email_log_model import EmailLog

# Existing Job Search Agent — unchanged search-link generation
from app.agents.job_search_agent import get_mock_jobs
from app.agents.email_agent import build_job_summary_email
from app.services.ats_analyzer_service import analyze_and_optimize_resume
from app.services.job_eligibility import job_is_analyzable

router = APIRouter(
    prefix="/api/scheduler",
    tags=["Scheduler Workflow"]
)


@router.post("/run/{user_id}")
def run_full_workflow(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    preference = db.query(UserPreference).filter(
        UserPreference.user_id == user_id
    ).first()

    if not preference:
        raise HTTPException(
            status_code=404,
            detail="User preferences not found. Please save preferences first."
        )

    latest_resume = db.query(Resume).filter(
        Resume.user_id == user_id
    ).order_by(
        Resume.uploaded_at.desc()
    ).first()

    if not latest_resume:
        raise HTTPException(
            status_code=404,
            detail="No resume found. Please upload resume first."
        )

    # 1. Existing Job Search Agent (search links only — unchanged)
    mock_jobs = get_mock_jobs(preference)

    saved_jobs = []

    for job_data in mock_jobs:
        new_job = Job(
            user_id=user_id,
            title=job_data["title"],
            company=job_data["company"],
            location=job_data["location"],
            work_mode=job_data["work_mode"],
            job_type=job_data["job_type"],
            description=job_data["description"],
            skills_required=job_data["skills_required"],
            source=job_data["source"],
            job_url=job_data["job_url"]
        )

        db.add(new_job)
        saved_jobs.append(new_job)

    db.commit()

    for job in saved_jobs:
        db.refresh(job)

    # 2-6. ATS Analyzer on complete jobs only (never on generic search links)
    analyzable_jobs = [
        job
        for job in db.query(Job).filter(Job.user_id == user_id).order_by(Job.id.desc()).all()
        if job_is_analyzable(job, require_url=False)[0]
    ][:3]

    ats_results = []
    for job in analyzable_jobs:
        try:
            result = analyze_and_optimize_resume(user_id=user_id, job_id=job.id, db=db)
            ats_results.append(result)
        except Exception:
            # Do not fail the full workflow if one ATS run fails.
            continue

    # 7. Email summary using original + optimized scores when available
    matches = (
        db.query(MatchScore)
        .filter(MatchScore.user_id == user_id)
        .order_by(MatchScore.match_percentage.desc())
        .all()
    )
    by_job = {}
    for match in matches:
        bucket = by_job.setdefault(match.job_id, {})
        version = getattr(match, "resume_version", "original") or "original"
        bucket[version] = match

    matches_with_jobs = []
    for job_id, versions in by_job.items():
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            continue
        original = versions.get("original") or next(iter(versions.values()), None)
        if not original:
            continue
        matches_with_jobs.append({
            "match": original,
            "optimized_match": versions.get("optimized"),
            "job": job,
        })

    matches_with_jobs.sort(
        key=lambda item: item["match"].match_percentage,
        reverse=True,
    )

    email_content = build_job_summary_email(
        user=user,
        matches_with_jobs=matches_with_jobs[:5]
    )

    email_log = EmailLog(
        user_id=user_id,
        recipient_email=user.email,
        subject=email_content["subject"],
        body=email_content["body"],
        status="generated"
    )

    db.add(email_log)
    db.commit()
    db.refresh(email_log)

    return {
        "message": "Full workflow completed successfully",
        "user_id": user_id,
        "resume_id": latest_resume.id,
        "jobs_created": len(saved_jobs),
        "ats_analyses_completed": len(ats_results),
        "matches_calculated": len(matches_with_jobs),
        "optimized_resumes_created": len(ats_results),
        "email_log_id": email_log.id,
        "email_status": email_log.status,
        "top_matches": [
            {
                "job_id": item["job"].id,
                "job_title": item["job"].title,
                "company": item["job"].company,
                "original_match_percentage": item["match"].match_percentage,
                "optimized_match_percentage": (
                    item["optimized_match"].match_percentage
                    if item.get("optimized_match")
                    else None
                ),
            }
            for item in matches_with_jobs[:5]
        ],
        "email_preview": email_log.body[:1500]
    }
