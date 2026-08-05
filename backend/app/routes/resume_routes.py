from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user_model import User
from app.models.resume_model import Resume
from app.models.job_model import Job
from app.models.optimized_resume_model import OptimizedResume
from app.utils.resume_parser import parse_resume_file
from app.agents.resume_optimization_agent import optimize_resume_for_job
from app.services.ats_optimization_workflow_service import (
    max_optimizations_per_run,
    start_or_get_workflow,
    start_workflow_for_saved_job,
    run_workflow_job,
    workflow_to_dict,
)
from app.services.job_eligibility import job_is_analyzable


router = APIRouter(
    prefix="/api/resume",
    tags=["Resume"]
)

UPLOAD_DIR = Path(__file__).resolve().parents[1] / "uploads" / "resumes"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/upload")
async def upload_resume(
    background_tasks: BackgroundTasks,
    user_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    original_file_name = file.filename

    if not original_file_name:
        raise HTTPException(
            status_code=400,
            detail="File name is missing"
        )

    file_extension = Path(original_file_name).suffix.lower()

    if file_extension not in [".pdf", ".docx", ".txt"]:
        raise HTTPException(
            status_code=400,
            detail="Only PDF, DOCX, and TXT files are allowed"
        )

    unique_file_name = f"{uuid4()}{file_extension}"
    stored_file_path = UPLOAD_DIR / unique_file_name

    file_content = await file.read()

    with open(stored_file_path, "wb") as saved_file:
        saved_file.write(file_content)

    try:
        parsed_text = parse_resume_file(str(stored_file_path))
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to parse resume: {str(error)}"
        )

    if not parsed_text:
        raise HTTPException(
            status_code=400,
            detail="Resume text could not be extracted"
        )

    new_resume = Resume(
        user_id=user_id,
        original_file_name=original_file_name,
        stored_file_path=str(stored_file_path),
        file_type=file_extension.replace(".", ""),
        parsed_text=parsed_text
    )

    db.add(new_resume)
    db.commit()
    db.refresh(new_resume)

    # New resume version: optionally start ATS workflows for recent complete jobs.
    # Does not overwrite historical results for previous resume_id values.
    started = []
    recent_jobs = (
        db.query(Job)
        .filter(Job.user_id == user_id)
        .order_by(Job.id.desc())
        .limit(20)
        .all()
    )
    for job in recent_jobs:
        if not job_is_analyzable(job, require_url=False)[0]:
            continue
        if len(started) >= max_optimizations_per_run():
            break
        result = start_workflow_for_saved_job(
            db,
            user_id=user_id,
            job_id=job.id,
            background_tasks=background_tasks,
        )
        if result.get("workflow_id"):
            started.append(result)

    return {
        "message": "Resume uploaded and parsed successfully",
        "resume_id": new_resume.id,
        "user_id": new_resume.user_id,
        "file_name": new_resume.original_file_name,
        "file_type": new_resume.file_type,
        "text_preview": parsed_text[:500],
        "ats_workflows_started": started,
    }


@router.get("/latest/{user_id}")
def get_latest_resume(user_id: int, db: Session = Depends(get_db)):
    resume = db.query(Resume).filter(
        Resume.user_id == user_id
    ).order_by(
        Resume.uploaded_at.desc()
    ).first()

    if not resume:
        raise HTTPException(
            status_code=404,
            detail="No resume found for this user"
        )

    return {
        "resume_id": resume.id,
        "user_id": resume.user_id,
        "file_name": resume.original_file_name,
        "file_type": resume.file_type,
        "uploaded_at": resume.uploaded_at,
        "text_preview": resume.parsed_text[:1000],
    }


@router.post("/optimize/{user_id}/{job_id}")
def optimize_resume(user_id: int, job_id: int, db: Session = Depends(get_db)):
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

    job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found for this user")

    optimization_result = optimize_resume_for_job(latest_resume.parsed_text, job)

    optimized_resume = OptimizedResume(
        user_id=user_id,
        resume_id=latest_resume.id,
        job_id=job.id,
        optimized_text=optimization_result["optimized_text"],
        optimization_notes=optimization_result["optimization_notes"],
    )
    db.add(optimized_resume)
    db.commit()
    db.refresh(optimized_resume)

    return {
        "message": "Resume optimized successfully",
        "optimized_resume_id": optimized_resume.id,
        "user_id": user_id,
        "resume_id": latest_resume.id,
        "job_id": job.id,
        "job_title": job.title,
        "company": job.company,
        "job_url": job.job_url,
        "match_percentage": optimization_result["match_percentage"],
        "original_match_percentage": optimization_result["original_match_percentage"],
        "optimized_match_percentage": optimization_result["match_percentage"],
        "score_improvement": optimization_result["score_improvement"],
        "role_alignment": optimization_result.get("role_alignment"),
        "score_status": optimization_result.get("score_status"),
        "optimization_allowed": optimization_result.get("optimization_allowed", False),
        "safe_to_apply": optimization_result.get("safe_to_apply", False),
        "matched_core_skills": optimization_result.get("matched_core_skills", []),
        "matched_transferable_skills": optimization_result.get("matched_transferable_skills", []),
        "critical_missing_skills": optimization_result.get("missing_mandatory_skills", []),
        "warning": optimization_result.get("warning_message", ""),
        "matched_keywords": optimization_result["matched_keywords"],
        "missing_keywords": optimization_result["missing_keywords"],
        "optimization_notes": optimized_resume.optimization_notes,
        "optimized_text_preview": optimized_resume.optimized_text[:1500]
    }


@router.post("/optimize-and-match/{user_id}/{job_id}")
def optimize_and_match_resume(
    user_id: int,
    job_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    force: bool = False,
):
    """
    Combined Resume Optimization + Match Comparison.

    Starts the shared ATSOptimizationWorkflowService (same as automatic job-save
    and /api/ats-optimizer/generate). Returns quickly with workflow_id for polling.
    """
    workflow = start_or_get_workflow(
        db,
        user_id=user_id,
        job_id=job_id,
        force=force,
    )
    if workflow.workflow_status == "PENDING":
        background_tasks.add_task(run_workflow_job, workflow.id)

    job = db.query(Job).filter(Job.id == job_id).first()
    return {
        "message": "ATS optimize-and-match workflow accepted",
        "workflow_id": workflow.id,
        "workflow_status": workflow.workflow_status,
        **workflow_to_dict(workflow, job),
    }


@router.get("/optimized/{user_id}")
def get_optimized_resumes(user_id: int, db: Session = Depends(get_db)):
    optimized_resumes = db.query(OptimizedResume).filter(
        OptimizedResume.user_id == user_id
    ).order_by(
        OptimizedResume.created_at.desc()
    ).all()

    if not optimized_resumes:
        raise HTTPException(
            status_code=404,
            detail="No optimized resumes found for this user"
        )

    result = []

    for optimized_resume in optimized_resumes:
        job = db.query(Job).filter(Job.id == optimized_resume.job_id).first()

        result.append({
            "optimized_resume_id": optimized_resume.id,
            "resume_id": optimized_resume.resume_id,
            "job_id": optimized_resume.job_id,
            "job_title": job.title if job else None,
            "company": job.company if job else None,
            "optimization_notes": optimized_resume.optimization_notes,
            "optimized_text_preview": optimized_resume.optimized_text[:1000],
            "created_at": optimized_resume.created_at,
        })

    return {
        "user_id": user_id,
        "total_optimized_resumes": len(result),
        "optimized_resumes": result,
    }
