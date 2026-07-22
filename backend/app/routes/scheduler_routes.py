from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db

from app.models.user_model import User
from app.models.user_preference_model import UserPreference
from app.models.resume_model import Resume
from app.models.job_model import Job
from app.models.match_model import MatchScore
from app.models.optimized_resume_model import OptimizedResume
from app.models.email_log_model import EmailLog

from app.agents.job_search_agent import get_mock_jobs
from app.agents.matching_agent import calculate_match_score
from app.agents.resume_optimization_agent import optimize_resume_for_job
from app.agents.email_agent import build_job_summary_email

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

    # 1. Job Search Agent
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

    # 2. Matching Percentage Agent
    saved_matches = []

    for job in saved_jobs:
        job_text = f"{job.title} {job.description} {job.skills_required}"

        match_result = calculate_match_score(
            latest_resume.parsed_text,
            job_text
        )

        new_match = MatchScore(
            user_id=user_id,
            resume_id=latest_resume.id,
            job_id=job.id,
            match_percentage=match_result["match_percentage"],
            matched_keywords=", ".join(match_result["matched_keywords"]),
            missing_keywords=", ".join(match_result["missing_keywords"]),
            recommendation=match_result["recommendation"]
        )

        db.add(new_match)

        saved_matches.append({
            "match": new_match,
            "job": job,
            "match_result": match_result
        })

    db.commit()

    for item in saved_matches:
        db.refresh(item["match"])

    # 3. Resume Optimization Agent
    saved_matches.sort(
        key=lambda item: item["match"].match_percentage,
        reverse=True
    )

    optimized_resumes = []

    for item in saved_matches[:3]:
        job = item["job"]

        optimization_result = optimize_resume_for_job(
            latest_resume.parsed_text,
            job
        )

        optimized_resume = OptimizedResume(
            user_id=user_id,
            resume_id=latest_resume.id,
            job_id=job.id,
            optimized_text=optimization_result["optimized_text"],
            optimization_notes=optimization_result["optimization_notes"]
        )

        db.add(optimized_resume)
        optimized_resumes.append(optimized_resume)

    db.commit()

    for optimized_resume in optimized_resumes:
        db.refresh(optimized_resume)

    # 4. Email Agent
    matches_with_jobs = [
        {
            "match": item["match"],
            "job": item["job"]
        }
        for item in saved_matches
    ]

    email_content = build_job_summary_email(
        user=user,
        matches_with_jobs=matches_with_jobs
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
        "matches_calculated": len(saved_matches),
        "optimized_resumes_created": len(optimized_resumes),
        "email_log_id": email_log.id,
        "email_status": email_log.status,
        "top_matches": [
            {
                "job_id": item["job"].id,
                "job_title": item["job"].title,
                "company": item["job"].company,
                "match_percentage": item["match"].match_percentage
            }
            for item in saved_matches
        ],
        "email_preview": email_log.body[:1500]
    }