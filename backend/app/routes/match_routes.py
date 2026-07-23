from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user_model import User
from app.models.resume_model import Resume
from app.models.job_model import Job
from app.models.match_model import MatchScore
from app.agents.matching_agent import calculate_match_score


router = APIRouter(
    prefix="/api/match",
    tags=["Matching Percentage Agent"]
)


@router.post("/calculate/{user_id}")
def calculate_matches_for_user(
    user_id: int,
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    latest_resume = (
        db.query(Resume)
        .filter(Resume.user_id == user_id)
        .order_by(Resume.id.desc())
        .first()
    )

    if not latest_resume:
        raise HTTPException(
            status_code=404,
            detail="Please upload resume before calculating matches"
        )

    jobs = (
        db.query(Job)
        .filter(Job.user_id == user_id)
        .order_by(Job.id.desc())
        .all()
    )

    if not jobs:
        raise HTTPException(
            status_code=404,
            detail="Please search jobs before calculating matches"
        )

    db.query(MatchScore).filter(MatchScore.user_id == user_id).delete()
    db.commit()

    created_matches = []

    for job in jobs:
        job_text = f"""
        {job.title}
        {job.company}
        {job.location}
        {job.work_mode}
        {job.job_type}
        {job.description}
        {job.skills_required}
        """.strip()

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
        db.commit()
        db.refresh(new_match)

        created_matches.append({
            "id": new_match.id,
            "user_id": new_match.user_id,
            "resume_id": new_match.resume_id,
            "job_id": job.id,

            "job_title": job.title,
            "company": job.company,
            "location": job.location,
            "work_mode": job.work_mode,
            "job_type": job.job_type,
            "source": job.source,
            "job_url": job.job_url,

            "match_percentage": new_match.match_percentage,
            "matched_keywords": new_match.matched_keywords,
            "missing_keywords": new_match.missing_keywords,
            "recommendation": new_match.recommendation
        })

    return {
        "message": "Matching percentage calculated successfully",
        "resume_id": latest_resume.id,
        "matches_created": len(created_matches),
        "matches": created_matches
    }


@router.get("/{user_id}")
def get_matches_for_user(
    user_id: int,
    db: Session = Depends(get_db)
):
    matches = (
        db.query(MatchScore, Job)
        .join(Job, MatchScore.job_id == Job.id)
        .filter(MatchScore.user_id == user_id)
        .order_by(MatchScore.match_percentage.desc())
        .all()
    )

    return [
        {
            "id": match.id,
            "user_id": match.user_id,
            "resume_id": match.resume_id,
            "job_id": job.id,

            "job_title": job.title,
            "company": job.company,
            "location": job.location,
            "work_mode": job.work_mode,
            "job_type": job.job_type,
            "source": job.source,
            "job_url": job.job_url,

            "match_percentage": match.match_percentage,
            "matched_keywords": match.matched_keywords,
            "missing_keywords": match.missing_keywords,
            "recommendation": match.recommendation,
            "created_at": str(match.created_at)
        }
        for match, job in matches
    ]