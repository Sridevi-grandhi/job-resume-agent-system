from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.resume_model import Resume
from app.models.job_model import Job
from app.models.match_model import MatchScore
from app.agents.matching_agent import calculate_match_score

router = APIRouter(
    prefix="/api/match",
    tags=["Matching"]
)


@router.post("/calculate/{user_id}")
def calculate_matches(user_id: int, db: Session = Depends(get_db)):
    latest_resume = db.query(Resume).filter(
        Resume.user_id == user_id
    ).order_by(
        Resume.uploaded_at.desc()
    ).first()

    if not latest_resume:
        raise HTTPException(
            status_code=404,
            detail="No resume found for this user. Please upload resume first."
        )

    jobs = db.query(Job).filter(
        Job.user_id == user_id
    ).all()

    if not jobs:
        raise HTTPException(
            status_code=404,
            detail="No jobs found for this user. Please search jobs first."
        )

    saved_scores = []

    for job in jobs:
        job_text = f"{job.title} {job.description} {job.skills_required}"

        result = calculate_match_score(
            latest_resume.parsed_text,
            job_text
        )

        new_score = MatchScore(
            user_id=user_id,
            resume_id=latest_resume.id,
            job_id=job.id,
            match_percentage=result["match_percentage"],
            matched_keywords=", ".join(result["matched_keywords"]),
            missing_keywords=", ".join(result["missing_keywords"]),
            recommendation=result["recommendation"]
        )

        db.add(new_score)
        saved_scores.append((new_score, job, result))

    db.commit()

    for score, job, result in saved_scores:
        db.refresh(score)

    return {
        "message": "Match scores calculated successfully",
        "user_id": user_id,
        "resume_id": latest_resume.id,
        "total_jobs": len(saved_scores),
        "matches": [
            {
                "match_id": score.id,
                "job_id": job.id,
                "job_title": job.title,
                "company": job.company,
                "match_percentage": score.match_percentage,
                "matched_keywords": result["matched_keywords"],
                "missing_keywords": result["missing_keywords"],
                "recommendation": score.recommendation
            }
            for score, job, result in saved_scores
        ]
    }


@router.get("/{user_id}")
def get_matches(user_id: int, db: Session = Depends(get_db)):
    matches = db.query(MatchScore).filter(
        MatchScore.user_id == user_id
    ).order_by(
        MatchScore.match_percentage.desc()
    ).all()

    if not matches:
        raise HTTPException(
            status_code=404,
            detail="No match scores found for this user"
        )

    result = []

    for match in matches:
        job = db.query(Job).filter(Job.id == match.job_id).first()

        result.append({
            "match_id": match.id,
            "resume_id": match.resume_id,
            "job_id": match.job_id,
            "job_title": job.title if job else None,
            "company": job.company if job else None,
            "match_percentage": match.match_percentage,
            "matched_keywords": match.matched_keywords,
            "missing_keywords": match.missing_keywords,
            "recommendation": match.recommendation,
            "created_at": match.created_at
        })

    return {
        "user_id": user_id,
        "total_matches": len(result),
        "matches": result
    }