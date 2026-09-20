from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user_model import User
from app.models.user_preference_model import UserPreference
from app.models.job_model import Job
from app.models.match_model import MatchScore
from app.models.optimized_resume_model import OptimizedResume
from app.models.ats_optimization_workflow_model import AtsOptimizationWorkflow

# Existing Job Search Agent — LinkedIn / Indeed / Dice / Google Jobs / ZipRecruiter search links
from app.agents.job_search_agent import get_mock_jobs

# Updated Job Search Agent — Remotive API individual job postings
from app.agents.updated_job_search_agent import get_updated_jobs_from_remotive
from app.agents.job_url_import_agent import (
    JobUrlImportError,
    SOURCE_VALUES,
    build_job_preview,
    normalize_job_url,
)
from app.services.ats_optimization_workflow_service import (
    max_optimizations_per_run,
    start_workflow_for_saved_job,
    RUNNING_WORKFLOW_STATUSES,
)


router = APIRouter(
    prefix="/api/jobs",
    tags=["Jobs"]
)

SELECTED_JOB_SOURCES = {
    "User Selected Job",
    "Dice Imported",
    "LinkedIn Imported",
    "Indeed Imported",
    "Imported Job",
}


def _selected_job_filter():
    return or_(
        Job.source.in_(SELECTED_JOB_SOURCES),
        Job.source.endswith(" Imported"),
    )


def _is_selected_job(job: Job) -> bool:
    source = str(job.source or "")
    return source in SELECTED_JOB_SOURCES or source.endswith(" Imported")


def _selected_job_payload(job: Job, db: Session) -> dict:
    matches = (
        db.query(MatchScore)
        .filter(MatchScore.user_id == job.user_id, MatchScore.job_id == job.id)
        .order_by(MatchScore.id.desc())
        .all()
    )
    original = next(
        (match for match in matches if (match.resume_version or "original") == "original"),
        None,
    )
    optimized = next(
        (match for match in matches if match.resume_version == "optimized"),
        None,
    )
    original_match_percentage = original.match_percentage if original else None
    optimized_match_percentage = optimized.match_percentage if optimized else None

    # match_status lets the frontend render an explicit state (complete /
    # calculating / unavailable / failed) instead of treating every null
    # score the same way. No new scoring happens here — this only reads
    # already-persisted data, in the same priority the existing workflow
    # already establishes: a real MatchScore row first, then the latest
    # workflow's own original_match_score (already computed, just not yet
    # mirrored into MatchScore — see execute_workflow), then the workflow's
    # own status for anything still running or terminally failed.
    latest_workflow = (
        db.query(AtsOptimizationWorkflow)
        .filter(AtsOptimizationWorkflow.user_id == job.user_id, AtsOptimizationWorkflow.job_id == job.id)
        .order_by(AtsOptimizationWorkflow.id.desc())
        .first()
    )
    match_status = "UNAVAILABLE"
    if original_match_percentage is not None:
        match_status = "COMPLETE"
    elif latest_workflow is not None:
        if latest_workflow.workflow_status in RUNNING_WORKFLOW_STATUSES:
            match_status = "CALCULATING"
        elif latest_workflow.original_match_score is not None:
            original_match_percentage = latest_workflow.original_match_score
            match_status = "COMPLETE"
        elif latest_workflow.workflow_status in {"FAILED", "VALIDATION_FAILED"}:
            match_status = "FAILED"
        # WAITING_FOR_RESUME / INCOMPLETE_JOB (no score ever computed,
        # deliberately not attempted) fall through to UNAVAILABLE.

    return {
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
        "discovered_at": str(job.discovered_at) if job.discovered_at else None,
        "original_match_percentage": original_match_percentage,
        "optimized_match_percentage": optimized_match_percentage,
        "match_status": match_status,
    }


class SelectedJobRequest(BaseModel):
    title: str
    company: str
    location: str
    work_mode: str = "Remote"
    job_type: str = "Full-time"
    description: str
    skills_required: str = ""
    source: str = "User Selected Job"
    job_url: str | None = None


class ImportJobUrlRequest(BaseModel):
    job_url: str


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


def _delete_jobs_and_related(user_id: int, job_ids: list[int], db: Session) -> None:
    """Deletes a Job row plus every dependent row that has a foreign key to
    jobs.id — otherwise the final Job delete below raises an uncaught
    IntegrityError (verified live: job_requirement_analyses_job_id_fkey,
    e.g., is NOT NULL and blocks the delete for any job that went through
    ATS analysis or skill confirmation).

    Two different intents, by table:
    - Artifacts that exist ONLY "for" this specific job (workflow runs,
      match scores, optimized resumes, cached requirement analysis, skill
      confirmation evidence) are meaningless once the job is gone, so they
      are deleted alongside it — same as the existing behavior this
      function already had for ats_optimization_workflows/match_scores/
      optimized_resumes.
    - Genuine user HISTORY that happens to reference the job (tracked
      applications, Gmail-derived email events) must survive "Remove Job"
      intact — these rows are detached (their nullable job_id/matched_job_id
      is set to NULL, matching the fallback state these models already
      define for "matched to email evidence only, no Job row") rather than
      deleted, per the product's existing "remove from Selected Jobs, not
      destroy application/email history" intent.
    """
    if not job_ids:
        return

    # Clear dependent ATS workflow rows first when that table exists.
    try:
        from app.models.ats_optimization_workflow_model import AtsOptimizationWorkflow

        db.query(AtsOptimizationWorkflow).filter(
            AtsOptimizationWorkflow.user_id == user_id,
            AtsOptimizationWorkflow.job_id.in_(job_ids),
        ).delete(synchronize_session=False)
    except Exception:
        # Table may not exist yet in older environments; continue safely.
        pass

    db.query(MatchScore).filter(
        MatchScore.user_id == user_id,
        MatchScore.job_id.in_(job_ids),
    ).delete(synchronize_session=False)

    db.query(OptimizedResume).filter(
        OptimizedResume.user_id == user_id,
        OptimizedResume.job_id.in_(job_ids),
    ).delete(synchronize_session=False)

    # Per-job derived artifacts — deleted alongside the job (same intent as
    # match_scores/optimized_resumes above): a cached requirement analysis
    # or skill-confirmation evidence for a job that no longer exists has no
    # other purpose and is not user-facing history on its own.
    try:
        from app.models.skill_confirmation_model import JobRequirementAnalysis, UserConfirmedSkill

        db.query(JobRequirementAnalysis).filter(
            JobRequirementAnalysis.user_id == user_id,
            JobRequirementAnalysis.job_id.in_(job_ids),
        ).delete(synchronize_session=False)

        db.query(UserConfirmedSkill).filter(
            UserConfirmedSkill.user_id == user_id,
            UserConfirmedSkill.job_id.in_(job_ids),
        ).delete(synchronize_session=False)
    except Exception:
        pass

    # Genuine user history — detached, never deleted, by design (both
    # columns are nullable specifically to support "no matched Job row").
    try:
        from app.models.application_tracking_model import ApplicationTracking

        db.query(ApplicationTracking).filter(
            ApplicationTracking.user_id == user_id,
            ApplicationTracking.job_id.in_(job_ids),
        ).update({ApplicationTracking.job_id: None}, synchronize_session=False)
    except Exception:
        pass

    try:
        from app.models.job_email_event_model import JobEmailEvent

        db.query(JobEmailEvent).filter(
            JobEmailEvent.user_id == user_id,
            JobEmailEvent.matched_job_id.in_(job_ids),
        ).update({JobEmailEvent.matched_job_id: None}, synchronize_session=False)
    except Exception:
        pass

    db.query(Job).filter(
        Job.user_id == user_id,
        Job.id.in_(job_ids),
    ).delete(synchronize_session=False)


def delete_old_auto_jobs_for_user(user_id: int, db: Session):
    """Used by Existing Job Search Agent — replaces prior auto-generated search-link jobs."""
    auto_jobs = (
        db.query(Job)
        .filter(Job.user_id == user_id)
        .filter(Job.source != "User Selected Job")
        .filter(~Job.source.endswith(" Imported"))
        .all()
    )
    auto_job_ids = [job.id for job in auto_jobs]
    if auto_job_ids:
        _delete_jobs_and_related(user_id, auto_job_ids, db)
        db.commit()


def delete_old_remotive_jobs_for_user(user_id: int, db: Session):
    """
    Used by Updated Job Search Agent only.
    Replaces prior Remotive API jobs while preserving:
    - User Selected Job
    - Imported jobs
    - Existing Job Search Agent link results (LinkedIn/Indeed/Dice/etc.)
    """
    remotive_jobs = (
        db.query(Job)
        .filter(Job.user_id == user_id, Job.source == "Remotive API")
        .all()
    )
    remotive_job_ids = [job.id for job in remotive_jobs]
    if remotive_job_ids:
        _delete_jobs_and_related(user_id, remotive_job_ids, db)
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
            "discovered_at": str(new_job.discovered_at) if getattr(new_job, "discovered_at", None) else None,
            "candidate_required_location": job_data.get("candidate_required_location", new_job.location),
            "tags": job_data.get("tags", []),
            "publication_date": job_data.get("publication_date"),
            "relevance_score": job_data.get("relevance_score"),
            "matched_preference_skills": job_data.get("matched_preference_skills", []),
            "role_family_match": job_data.get("role_family_match")
        })

    return created_jobs


# ---------------------------------------------------------------------------
# Existing Job Search Agent
# Endpoint: POST /api/jobs/search/{user_id}
# Uses job_search_agent.py — search-page links only (unchanged)
# ---------------------------------------------------------------------------
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


def _public_job_payload(job: dict) -> dict:
    """Strip diagnostic fields; return the contract fields for API clients."""
    payload = {
        "id": job["id"],
        "user_id": job["user_id"],
        "title": job["title"],
        "company": job["company"],
        "location": job["location"],
        "work_mode": job["work_mode"],
        "job_type": job["job_type"],
        "description": job["description"],
        "skills_required": job["skills_required"],
        "source": job["source"],
        "job_url": job["job_url"],
    }
    if job.get("discovered_at"):
        payload["discovered_at"] = job["discovered_at"]
    return payload


# ---------------------------------------------------------------------------
# Updated Job Search Agent
# Endpoint: POST /api/jobs/search-updated/{user_id}
# Uses updated_job_search_agent.py — Remotive API individual job postings
# ---------------------------------------------------------------------------
@router.post("/search-updated/{user_id}")
def search_jobs_with_updated_agent(
    user_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    user, preference = get_user_and_preferences(user_id, db)

    try:
        search_result = get_updated_jobs_from_remotive(preference, max_jobs=10)
    except Exception:
        # Never crash FastAPI; preserve existing DB jobs and report clearly.
        return {
            "message": "Remotive API is temporarily unavailable. Existing jobs were preserved.",
            "agent_used": "Updated Job Search Agent - Remotive API",
            "jobs_created": 0,
            "jobs": [],
        }

    jobs_data = search_result.get("jobs") or []
    api_status = search_result.get("api_status")

    if not jobs_data:
        if api_status == "unavailable":
            message = "Remotive API is temporarily unavailable. Existing jobs were preserved."
        else:
            message = (
                "No relevant jobs were found in the Remotive public API for the current preferences. "
                "Existing search-link jobs were preserved. "
                "The Remotive website may show different jobs than the public API."
            )
        # Include compact diagnostics for Swagger/dev tracing (no descriptions).
        return {
            "message": message,
            "agent_used": "Updated Job Search Agent - Remotive API",
            "jobs_created": 0,
            "jobs": [],
            "diagnostics": search_result.get("diagnostics"),
        }

    # Safer sequence: only replace previous Remotive rows after valid results exist.
    try:
        delete_old_remotive_jobs_for_user(user_id, db)
        created_jobs = save_jobs_to_database(user_id, jobs_data, db)
    except Exception:
        db.rollback()
        return {
            "message": "Failed to save Remotive jobs. Existing jobs were preserved.",
            "agent_used": "Updated Job Search Agent - Remotive API",
            "jobs_created": 0,
            "jobs": [],
        }

    # Start ATS workflows for a limited number of complete Remotive jobs (background).
    workflows = []
    for job in created_jobs[: max_optimizations_per_run()]:
        result = start_workflow_for_saved_job(
            db,
            user_id=user_id,
            job_id=job["id"],
            background_tasks=background_tasks,
        )
        workflows.append(result)

    return {
        "message": "Accurate jobs fetched using Updated Job Search Agent",
        "agent_used": "Updated Job Search Agent - Remotive API",
        "jobs_created": len(created_jobs),
        "jobs": [_public_job_payload(job) for job in created_jobs],
        "ats_workflows": workflows,
    }


@router.post("/selected/{user_id}")
def add_selected_job(
    user_id: int,
    selected_job: SelectedJobRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if selected_job.source.endswith(" Imported"):
        missing = [
            field
            for field in ("title", "company", "description", "job_url")
            if not str(getattr(selected_job, field, "") or "").strip()
        ]
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"Imported jobs require: {', '.join(missing)}"
            )
        try:
            detected_source, normalized_url, _ = normalize_job_url(selected_job.job_url)
        except JobUrlImportError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        selected_job.source = SOURCE_VALUES[detected_source]
        selected_job.job_url = normalized_url
        duplicate = db.query(Job).filter(
            Job.user_id == user_id,
            Job.job_url == normalized_url
        ).first()
        if duplicate:
            raise HTTPException(
                status_code=409,
                detail={
                    "success": False,
                    "duplicate": True,
                    "message": "This job has already been imported."
                }
            )

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

    # Automatic ATS workflow (background). Job save already committed.
    workflow_info = start_workflow_for_saved_job(
        db,
        user_id=user_id,
        job_id=new_job.id,
        background_tasks=background_tasks,
    )

    return {
        "message": workflow_info.get("message") or "Selected job saved successfully",
        "job_saved": True,
        "workflow_id": workflow_info.get("workflow_id"),
        "workflow_status": workflow_info.get("workflow_status"),
        "optimization_completed": workflow_info.get("workflow_status") == "COMPLETED",
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
        },
        "analysis": None,
    }


@router.post("/import-url/{user_id}")
def import_selected_job_by_url(
    user_id: int,
    request: ImportJobUrlRequest,
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        preview = build_job_preview(request.job_url)
    except JobUrlImportError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    duplicate = db.query(Job).filter(
        Job.user_id == user_id,
        Job.job_url == preview["normalized_job_url"]
    ).first()
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail={
                "success": False,
                "duplicate": True,
                "message": "This job has already been imported."
            }
        )
    return preview


@router.get("/selected/{user_id}")
def get_selected_jobs(user_id: int, db: Session = Depends(get_db)):
    if not db.query(User).filter(User.id == user_id).first():
        raise HTTPException(status_code=404, detail="User not found")

    jobs = (
        db.query(Job)
        .filter(Job.user_id == user_id, _selected_job_filter())
        .order_by(Job.id.desc())
        .all()
    )
    return [_selected_job_payload(job, db) for job in jobs]


@router.delete("/selected/{user_id}/{job_id}")
def delete_selected_job(user_id: int, job_id: int, db: Session = Depends(get_db)):
    if not db.query(User).filter(User.id == user_id).first():
        raise HTTPException(status_code=404, detail="User not found")

    job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
    if not job or not _is_selected_job(job):
        raise HTTPException(status_code=404, detail="Selected job not found")

    _delete_jobs_and_related(user_id, [job_id], db)
    db.commit()
    return {"message": "Selected job removed successfully", "job_id": job_id}


@router.get("/detail/{user_id}/{job_id}")
def get_job_detail(user_id: int, job_id: int, db: Session = Depends(get_db)):
    if not db.query(User).filter(User.id == user_id).first():
        raise HTTPException(status_code=404, detail="User not found")

    job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _selected_job_payload(job, db)


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
