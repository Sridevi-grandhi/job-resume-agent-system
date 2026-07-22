from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user_model import User
from app.models.user_preference_model import UserPreference
from app.models.job_model import Job
from app.models.match_model import MatchScore
from app.models.optimized_resume_model import OptimizedResume

from app.agents.job_search_agent import get_mock_jobs
from app.agents.real_job_search_agent import get_real_jobs_from_remotive


router = APIRouter(
    prefix="/api/jobs",
    tags=["Jobs"]
)


def delete_old_jobs_for_user(user_id: int, db: Session):
    db.query(MatchScore).filter(MatchScore.user_id == user_id).delete()
    db.query(OptimizedResume).filter(OptimizedResume.user_id == user_id).delete()
    db.query(Job).filter(Job.user_id == user_id).delete()
    db.commit()


def save_jobs_to_database(user_id: int, jobs_data, db: Session):
    created_jobs = []

    for job_data in jobs_data:
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
        db.commit()
        db.refresh(new_job)

        created_jobs.append({
            "id": new_job.id,
            "user_id": new_job.user_id,
            "title": new_job.title,
            "company": new_job.company,
            "location": new_job.location,
            "work_mode": new_job.work_mode,
            "job_type": new_job.job_type,
            "description": new_job.description,
            "skills_required": new_job.skills_required,
            "source": new_job.source,
            "job_url": new_job.job_url
        })

    return created_jobs


def get_user_and_preferences(user_id: int, db: Session):
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    preference = (
        db.query(UserPreference)
        .filter(UserPreference.user_id == user_id)
        .first()
    )

    if not preference:
        raise HTTPException(
            status_code=404,
            detail="Please save preferences before searching jobs"
        )

    return user, preference


@router.post("/search/{user_id}")
def search_mock_jobs_for_user(
    user_id: int,
    db: Session = Depends(get_db)
):
    user, preference = get_user_and_preferences(user_id, db)

    delete_old_jobs_for_user(user_id, db)

    jobs_data = get_mock_jobs(preference)

    created_jobs = save_jobs_to_database(user_id, jobs_data, db)

    return {
        "message": "Mock jobs generated successfully",
        "agent_used": "Mock Job Search Agent",
        "jobs_created": len(created_jobs),
        "jobs": created_jobs
    }


@router.post("/search-real/{user_id}")
def search_real_jobs_for_user(
    user_id: int,
    db: Session = Depends(get_db)
):
    user, preference = get_user_and_preferences(user_id, db)

    delete_old_jobs_for_user(user_id, db)

    jobs_data = get_real_jobs_from_remotive(preference, max_jobs=10)

    if not jobs_data:
        return {
            "message": "No real jobs found from Remotive API. Try a broader title like QA Automation Engineer, Software Engineer in Test, or Java Developer.",
            "agent_used": "Real Job Search Agent - Remotive API",
            "jobs_created": 0,
            "jobs": []
        }

    created_jobs = save_jobs_to_database(user_id, jobs_data, db)

    return {
        "message": "Real jobs fetched from Remotive API and saved successfully",
        "agent_used": "Real Job Search Agent - Remotive API",
        "jobs_created": len(created_jobs),
        "jobs": created_jobs
    }


@router.get("/{user_id}")
def get_jobs_for_user(
    user_id: int,
    db: Session = Depends(get_db)
):
    jobs = (
        db.query(Job)
        .filter(Job.user_id == user_id)
        .order_by(Job.id.desc())
        .all()
    )

    return [
        {
            "id": job.id,
            "user_id": job.user_id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "work_mode": job.work_mode,
            "job_type": job.job_type,
            "description": job.description,
            "skills_required": job.skills_required,
            "source": job.source,
            "job_url": job.job_url,
            "discovered_at": str(job.discovered_at)
        }
        for job in jobs
    ]