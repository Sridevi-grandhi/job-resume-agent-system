from datetime import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user_model import User
from app.models.user_preference_model import UserPreference


router = APIRouter(
    prefix="/api/preferences",
    tags=["Preferences"]
)


class PreferenceRequest(BaseModel):
    user_id: int
    job_title: str
    location: str
    experience: str
    work_mode: str
    job_type: str
    skills: str
    schedule_time: time
    timezone: str = "America/Chicago"


@router.post("")
def save_or_update_preferences(
    preference_data: PreferenceRequest,
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == preference_data.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    existing_preference = (
        db.query(UserPreference)
        .filter(UserPreference.user_id == preference_data.user_id)
        .first()
    )

    if existing_preference:
        existing_preference.job_title = preference_data.job_title
        existing_preference.location = preference_data.location
        existing_preference.experience = preference_data.experience
        existing_preference.work_mode = preference_data.work_mode
        existing_preference.job_type = preference_data.job_type
        existing_preference.skills = preference_data.skills
        existing_preference.schedule_time = preference_data.schedule_time
        existing_preference.timezone = preference_data.timezone

        db.commit()
        db.refresh(existing_preference)

        return {
            "message": "Preferences updated successfully",
            "preference_id": existing_preference.id,
            "user_id": existing_preference.user_id,
            "job_title": existing_preference.job_title,
            "location": existing_preference.location,
            "experience": existing_preference.experience,
            "work_mode": existing_preference.work_mode,
            "job_type": existing_preference.job_type,
            "skills": existing_preference.skills,
            "schedule_time": str(existing_preference.schedule_time),
            "timezone": existing_preference.timezone
        }

    new_preference = UserPreference(
        user_id=preference_data.user_id,
        job_title=preference_data.job_title,
        location=preference_data.location,
        experience=preference_data.experience,
        work_mode=preference_data.work_mode,
        job_type=preference_data.job_type,
        skills=preference_data.skills,
        schedule_time=preference_data.schedule_time,
        timezone=preference_data.timezone
    )

    db.add(new_preference)
    db.commit()
    db.refresh(new_preference)

    return {
        "message": "Preferences saved successfully",
        "preference_id": new_preference.id,
        "user_id": new_preference.user_id,
        "job_title": new_preference.job_title,
        "location": new_preference.location,
        "experience": new_preference.experience,
        "work_mode": new_preference.work_mode,
        "job_type": new_preference.job_type,
        "skills": new_preference.skills,
        "schedule_time": str(new_preference.schedule_time),
        "timezone": new_preference.timezone
    }


@router.get("/{user_id}")
def get_preferences(
    user_id: int,
    db: Session = Depends(get_db)
):
    preference = (
        db.query(UserPreference)
        .filter(UserPreference.user_id == user_id)
        .first()
    )

    if not preference:
        raise HTTPException(status_code=404, detail="Preferences not found for this user")

    return {
        "id": preference.id,
        "user_id": preference.user_id,
        "job_title": preference.job_title,
        "location": preference.location,
        "experience": preference.experience,
        "work_mode": preference.work_mode,
        "job_type": preference.job_type,
        "skills": preference.skills,
        "schedule_time": str(preference.schedule_time),
        "timezone": preference.timezone
    }