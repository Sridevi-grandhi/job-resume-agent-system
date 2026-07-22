from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user_model import User
from app.models.job_model import Job
from app.models.match_model import MatchScore
from app.models.email_log_model import EmailLog
from app.agents.email_agent import build_job_summary_email

router = APIRouter(
    prefix="/api/email",
    tags=["Email"]
)


@router.post("/generate/{user_id}")
def generate_email_summary(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    matches = db.query(MatchScore).filter(
        MatchScore.user_id == user_id
    ).order_by(
        MatchScore.match_percentage.desc()
    ).limit(5).all()

    if not matches:
        raise HTTPException(
            status_code=404,
            detail="No match scores found. Please calculate matches first."
        )

    matches_with_jobs = []

    for match in matches:
        job = db.query(Job).filter(Job.id == match.job_id).first()

        if job:
            matches_with_jobs.append({
                "match": match,
                "job": job
            })

    if not matches_with_jobs:
        raise HTTPException(
            status_code=404,
            detail="No jobs found for the match scores."
        )

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
        "message": "Email summary generated successfully",
        "email_log_id": email_log.id,
        "user_id": user_id,
        "recipient_email": email_log.recipient_email,
        "subject": email_log.subject,
        "status": email_log.status,
        "body_preview": email_log.body[:2000]
    }


@router.get("/logs/{user_id}")
def get_email_logs(user_id: int, db: Session = Depends(get_db)):
    logs = db.query(EmailLog).filter(
        EmailLog.user_id == user_id
    ).order_by(
        EmailLog.created_at.desc()
    ).all()

    if not logs:
        raise HTTPException(
            status_code=404,
            detail="No email logs found for this user"
        )

    return {
        "user_id": user_id,
        "total_logs": len(logs),
        "logs": [
            {
                "email_log_id": log.id,
                "recipient_email": log.recipient_email,
                "subject": log.subject,
                "status": log.status,
                "body_preview": log.body[:1000],
                "created_at": log.created_at
            }
            for log in logs
        ]
    }