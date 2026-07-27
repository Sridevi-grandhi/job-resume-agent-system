import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user_model import User
from app.models.user_preference_model import UserPreference
from app.models.job_model import Job
from app.models.match_model import MatchScore
from app.models.optimized_resume_model import OptimizedResume

from app.agents.job_search_agent import get_mock_jobs
from app.agents.updated_job_search_agent import get_updated_jobs_from_remotive


router = APIRouter(
    prefix="/api/jobs",
    tags=["Jobs"]
)


class SelectedJobRequest(BaseModel):
    title: str
    company: str
    location: str
    work_mode: str = "Remote"
    job_type: str = "Full-time"
    description: str
    skills_required: str
    source: str = "User Selected Job"
    job_url: str | None = None


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


def delete_old_auto_jobs_for_user(user_id: int, db: Session):
    auto_jobs = (
        db.query(Job)
        .filter(Job.user_id == user_id)
        .filter(Job.source != "User Selected Job")
        .all()
    )

    auto_job_ids = [job.id for job in auto_jobs]

    if auto_job_ids:
        db.query(MatchScore).filter(
            MatchScore.user_id == user_id,
            MatchScore.job_id.in_(auto_job_ids)
        ).delete(synchronize_session=False)

        db.query(OptimizedResume).filter(
            OptimizedResume.user_id == user_id,
            OptimizedResume.job_id.in_(auto_job_ids)
        ).delete(synchronize_session=False)

        db.query(Job).filter(
            Job.user_id == user_id,
            Job.id.in_(auto_job_ids)
        ).delete(synchronize_session=False)

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
            "job_url": new_job.job_url,
            "candidate_required_location": job_data.get("candidate_required_location", new_job.location),
            "tags": job_data.get("tags", []),
            "publication_date": job_data.get("publication_date"),
            "relevance_score": job_data.get("relevance_score"),
            "matched_preference_skills": job_data.get("matched_preference_skills", []),
            "role_family_match": job_data.get("role_family_match")
        })

    return created_jobs


@router.post("/search/{user_id}")
def search_jobs_with_existing_agent(
    user_id: int,
    db: Session = Depends(get_db)
):
    user, preference = get_user_and_preferences(user_id, db)

    delete_old_auto_jobs_for_user(user_id, db)

    jobs_data = get_mock_jobs(preference)

    created_jobs = save_jobs_to_database(user_id, jobs_data, db)

    return {
        "message": "Jobs generated using existing Job Search Agent",
        "agent_used": "Existing Job Search Agent - LinkedIn, Indeed, Dice, Google Jobs, ZipRecruiter links",
        "jobs_created": len(created_jobs),
        "jobs": created_jobs
    }


@router.post("/search-updated/{user_id}")
def search_jobs_with_updated_agent(
    user_id: int,
    db: Session = Depends(get_db)
):
    user, preference = get_user_and_preferences(user_id, db)

    search_result = get_updated_jobs_from_remotive(preference, max_jobs=10)
    jobs_data = search_result["jobs"]

    if not jobs_data:
        response = {
            "message": f"No relevant Remotive jobs passed the current filters. Raw jobs received: {search_result['total_api_jobs_received']}. Review role aliases or filtering rules.",
            "agent_used": "Updated Job Search Agent - Remotive API",
            "requested_job_title": search_result["requested_job_title"],
            "search_queries_used": search_result["search_queries_used"],
            "total_api_jobs_received": search_result["total_api_jobs_received"],
            "irrelevant_jobs_filtered": search_result["irrelevant_jobs_filtered"],
            "jobs_created": 0,
            "jobs": []
        }
        if os.getenv("ENVIRONMENT", "development").lower() == "development":
            response["rejection_reasons"] = search_result["rejection_reasons"]
        return response

    delete_old_auto_jobs_for_user(user_id, db)
    created_jobs = save_jobs_to_database(user_id, jobs_data, db)

    return {
        "message": f"{len(created_jobs)} relevant jobs found for {preference.job_title} and saved successfully.",
        "agent_used": "Updated Job Search Agent - Remotive API",
        "requested_job_title": search_result["requested_job_title"],
        "search_queries_used": search_result["search_queries_used"],
        "total_api_jobs_received": search_result["total_api_jobs_received"],
        "irrelevant_jobs_filtered": search_result["irrelevant_jobs_filtered"],
        "jobs_created": len(created_jobs),
        "jobs": created_jobs
    }


@router.post("/selected/{user_id}")
def add_selected_job(
    user_id: int,
    selected_job: SelectedJobRequest,
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    new_job = Job(
        user_id=user_id,
        title=selected_job.title,
        company=selected_job.company,
        location=selected_job.location,
        work_mode=selected_job.work_mode,
        job_type=selected_job.job_type,
        description=selected_job.description,
        skills_required=selected_job.skills_required,
        source=selected_job.source,
        job_url=selected_job.job_url
    )

    db.add(new_job)
    db.commit()
    db.refresh(new_job)

    return {
        "message": "Selected job saved successfully",
        "job": {
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
        }
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
