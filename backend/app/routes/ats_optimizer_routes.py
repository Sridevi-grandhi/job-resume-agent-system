"""
ATS Resume Optimizer & Match Analyzer API routes.

Thin controllers around ATSOptimizationWorkflowService.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.ats_optimization_workflow_model import AtsOptimizationWorkflow
from app.models.job_model import Job
from app.models.resume_model import Resume
from app.models.user_model import User
from app.services.ats_optimization_workflow_service import (
    execute_workflow,
    run_workflow_job,
    start_or_get_workflow,
    workflow_to_dict,
)
from app.services.job_eligibility import is_generic_search_link_job, job_is_analyzable
from app.services.requirement_extraction_service import (
    extract_job_requirement_analysis,
    group_requirements_vs_resume,
    load_requirement_analysis,
    merge_with_resume_evidence,
    save_requirement_analysis,
)
from app.services.resume_optimization_match_service import optimize_and_match
from app.services.resume_structure_parser import parse_resume_structure
from app.services.skill_confirmation_service import (
    confirmed_skill_to_dict,
    delete_confirmed_skill,
    load_confirmed_skills,
    save_evidence,
)
from app.services.skill_analysis_service import (
    ANALYSIS_VERSION,
    MAVEN_ALIASES,
    content_hash,
    find_skill_evidence,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/ats-optimizer",
    tags=["ATS Optimizer & Match Analyzer"],
)


def _load_user_and_job(db: Session, user_id: int, job_id: int) -> tuple[User, Job]:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found for this user")
    if is_generic_search_link_job(job):
        raise HTTPException(status_code=400, detail="Generic search-link jobs are not eligible for ATS optimization.")
    eligible, reason = job_is_analyzable(job, require_url=False)
    if not eligible:
        raise HTTPException(status_code=400, detail=reason)
    return user, job


def _latest_resume(db: Session, user_id: int) -> Resume | None:
    """Always use the newest resume row for the current user (by id)."""
    return (
        db.query(Resume)
        .filter(Resume.user_id == user_id)
        .order_by(Resume.id.desc())
        .first()
    )


def _analysis_identity(user_id: int, job: Job, resume: Resume | None) -> dict:
    resume_text = (resume.parsed_text if resume else "") or ""
    job_text = "\n".join([
        job.title or "", job.description or "", job.skills_required or "",
        job.location or "", job.work_mode or "", job.job_type or "",
    ])
    return {
        "user_id": user_id,
        "job_id": job.id,
        "resume_id": resume.id if resume else None,
        "resume_hash": content_hash(resume_text),
        "job_hash": content_hash(job_text),
        "analysis_version": ANALYSIS_VERSION,
    }


def _resolve_requirement_analysis(
    db: Session,
    *,
    user_id: int,
    job: Job,
    resume: Resume | None,
    force_refresh: bool = False,
) -> tuple[dict, bool]:
    """Return (analysis, cache_used). Regenerates when identity mismatches or force_refresh."""
    identity = _analysis_identity(user_id, job, resume)
    if force_refresh:
        analysis = extract_job_requirement_analysis(job)
        save_requirement_analysis(db, analysis=analysis, **{k: identity[k] for k in ("user_id", "job_id", "resume_id", "resume_hash", "job_hash")})
        return analysis, False
    cached = load_requirement_analysis(
        db,
        user_id=identity["user_id"],
        job_id=identity["job_id"],
        resume_id=identity["resume_id"],
        resume_hash=identity["resume_hash"],
        job_hash=identity["job_hash"],
    )
    if cached:
        return cached, True
    analysis = extract_job_requirement_analysis(job)
    save_requirement_analysis(db, analysis=analysis, **{k: identity[k] for k in ("user_id", "job_id", "resume_id", "resume_hash", "job_hash")})
    return analysis, False


@router.get("/requirements/{user_id}/{job_id}")
def get_job_requirements(user_id: int, job_id: int, db: Session = Depends(get_db)):
    _user, job = _load_user_and_job(db, user_id, job_id)
    resume = _latest_resume(db, user_id)
    identity = _analysis_identity(user_id, job, resume)
    analysis = extract_job_requirement_analysis(job)
    save_requirement_analysis(
        db,
        analysis=analysis,
        user_id=identity["user_id"],
        job_id=identity["job_id"],
        resume_id=identity["resume_id"],
        resume_hash=identity["resume_hash"],
        job_hash=identity["job_hash"],
    )
    enriched = merge_with_resume_evidence(analysis, resume.parsed_text if resume else "")
    return {**identity, **enriched}


def _known_employers(resume_text: str) -> list[str]:
    structure = parse_resume_structure(resume_text or "")
    return [emp.get("company") for emp in structure.get("employments") or [] if emp.get("company")]


def _resume_employments(resume_text: str) -> list[dict]:
    """Parsed employments for the 'which existing resume position' picker — never
    invented, always sourced straight from the uploaded resume."""
    structure = parse_resume_structure(resume_text or "")
    return [
        {
            "index": index,
            "company": emp.get("company") or "",
            "title": emp.get("title") or "",
            "dates": emp.get("dates") or "",
        }
        for index, emp in enumerate(structure.get("employments") or [])
        if emp.get("company")
    ]


@router.get("/missing-skills/{user_id}/{job_id}")
def get_missing_skills(
    user_id: int,
    job_id: int,
    force_refresh: bool = False,
    db: Session = Depends(get_db),
):
    _user, job = _load_user_and_job(db, user_id, job_id)
    resume = _latest_resume(db, user_id)
    resume_text = (resume.parsed_text if resume else "") or ""
    identity = _analysis_identity(user_id, job, resume)
    analysis, cache_used = _resolve_requirement_analysis(
        db, user_id=user_id, job=job, resume=resume, force_refresh=force_refresh,
    )
    grouped = group_requirements_vs_resume(analysis, resume_text)
    for item in grouped.get("skills") or []:
        item["found_in_resume"] = bool(item.get("found") or item.get("found_in_resume"))
        item["resume_status"] = item.get("resume_status") or item.get("status")
    grouped.update(identity)
    grouped["cache_used"] = cache_used
    grouped["force_refresh"] = force_refresh
    grouped["resume_file_used"] = getattr(resume, "original_file_name", None) if resume else None
    grouped["resume_text_length"] = len(resume_text)
    grouped["resume_employments"] = _resume_employments(resume_text)
    return grouped


@router.get("/debug/skill-evidence/{user_id}/{job_id}")
def debug_skill_evidence(
    user_id: int,
    job_id: int,
    skill: str = "Maven",
    db: Session = Depends(get_db),
):
    """Internal diagnostic — does not return full resume text."""
    _user, job = _load_user_and_job(db, user_id, job_id)
    resume = _latest_resume(db, user_id)
    resume_text = (resume.parsed_text if resume else "") or ""
    identity = _analysis_identity(user_id, job, resume)
    cached = load_requirement_analysis(
        db,
        user_id=identity["user_id"],
        job_id=identity["job_id"],
        resume_id=identity["resume_id"],
        resume_hash=identity["resume_hash"],
        job_hash=identity["job_hash"],
    )
    evidence = find_skill_evidence(skill, resume_text)
    aliases = sorted(MAVEN_ALIASES) if evidence.get("normalized_skill") == "maven" else []
    return {
        "resume_id_used": resume.id if resume else None,
        "resume_file_used": getattr(resume, "original_file_name", None) if resume else None,
        "resume_text_length": len(resume_text),
        "normalized_skill": evidence.get("normalized_skill"),
        "aliases_checked": aliases,
        "found": bool(evidence.get("found")),
        "resume_status": evidence.get("resume_status"),
        "match_type": evidence.get("match_type"),
        "evidence_snippets": evidence.get("evidence_snippets") or [],
        "cache_used": cached is not None,
        "analysis_version": ANALYSIS_VERSION,
        "job_id": job_id,
        "user_id": user_id,
    }


@router.post("/analyze/{user_id}/{job_id}")
def analyze_requirements(
    user_id: int,
    job_id: int,
    force_refresh: bool = True,
    db: Session = Depends(get_db),
):
    """Force-refresh friendly analyze entry used after resume upload/replacement."""
    return get_missing_skills(user_id=user_id, job_id=job_id, force_refresh=force_refresh, db=db)


@router.get("/evidence/{user_id}/{job_id}")
def get_evidence(user_id: int, job_id: int, db: Session = Depends(get_db)):
    _user, _job = _load_user_and_job(db, user_id, job_id)
    rows = load_confirmed_skills(db, user_id=user_id, job_id=job_id)
    return {"user_id": user_id, "job_id": job_id, "evidence": [confirmed_skill_to_dict(r) for r in rows]}


@router.post("/evidence/{user_id}/{job_id}")
def post_evidence(
    user_id: int,
    job_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    _user, job = _load_user_and_job(db, user_id, job_id)
    resume = _latest_resume(db, user_id)
    items = payload.get("items") or payload.get("confirmations") or payload.get("evidence") or []
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="No evidence items were provided.")

    identity = _analysis_identity(user_id, job, resume)
    cached_analysis = load_requirement_analysis(db, **identity)
    analysis = cached_analysis or extract_job_requirement_analysis(job)
    enriched = merge_with_resume_evidence(analysis, resume.parsed_text if resume else "")
    resume_evidence = enriched.get("resume_evidence") or {}
    known_employers = _known_employers(resume.parsed_text if resume else "")

    try:
        saved = save_evidence(
            db,
            user_id=user_id,
            job_id=job_id,
            resume_id=resume.id if resume else None,
            items=items,
            resume_evidence=resume_evidence,
            known_employers=known_employers,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    return {
        "message": "Evidence saved.",
        "user_id": user_id,
        "job_id": job_id,
        "evidence": [confirmed_skill_to_dict(r) for r in saved],
    }


@router.delete("/evidence/{user_id}/{job_id}/{evidence_id}")
def delete_evidence(user_id: int, job_id: int, evidence_id: int, db: Session = Depends(get_db)):
    _user, _job = _load_user_and_job(db, user_id, job_id)
    removed = delete_confirmed_skill(db, user_id=user_id, job_id=job_id, evidence_id=evidence_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Evidence not found for this user and job.")
    return {"message": "Evidence removed.", "evidence_id": evidence_id}


@router.post("/generate/{user_id}/{job_id}")
def generate_ats_optimization(
    user_id: int,
    job_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    force: bool = False,
):
    try:
        workflow = start_or_get_workflow(
            db,
            user_id=user_id,
            job_id=job_id,
            force=force,
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to start ATS workflow user_id=%s job_id=%s", user_id, job_id)
        raise HTTPException(status_code=500, detail="Failed to start ATS workflow.")

    if workflow.workflow_status == "PENDING":
        background_tasks.add_task(run_workflow_job, workflow.id)

    job = db.query(Job).filter(Job.id == job_id).first()
    return {
        "message": "ATS optimization workflow accepted",
        "workflow_id": workflow.id,
        "workflow_status": workflow.workflow_status,
        "result": workflow_to_dict(workflow, job),
    }


@router.get("/status/{workflow_id}")
def get_workflow_status(workflow_id: int, db: Session = Depends(get_db)):
    workflow = db.query(AtsOptimizationWorkflow).filter(AtsOptimizationWorkflow.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    job = db.query(Job).filter(Job.id == workflow.job_id).first()
    return workflow_to_dict(workflow, job)


@router.get("/result/{user_id}/{job_id}")
def get_result_by_job(user_id: int, job_id: int, db: Session = Depends(get_db)):
    workflow = (
        db.query(AtsOptimizationWorkflow)
        .filter(
            AtsOptimizationWorkflow.user_id == user_id,
            AtsOptimizationWorkflow.job_id == job_id,
        )
        .order_by(AtsOptimizationWorkflow.id.desc())
        .first()
    )
    if not workflow:
        raise HTTPException(status_code=404, detail="No ATS workflow result found for this job")
    job = db.query(Job).filter(Job.id == job_id).first()
    return workflow_to_dict(workflow, job)


@router.get("/results/{user_id}")
def list_results(user_id: int, db: Session = Depends(get_db)):
    workflows = (
        db.query(AtsOptimizationWorkflow)
        .filter(AtsOptimizationWorkflow.user_id == user_id)
        .order_by(AtsOptimizationWorkflow.id.desc())
        .limit(50)
        .all()
    )
    jobs = {
        job.id: job
        for job in db.query(Job).filter(Job.user_id == user_id).all()
    }
    return {
        "user_id": user_id,
        "total": len(workflows),
        "results": [workflow_to_dict(w, jobs.get(w.job_id)) for w in workflows],
    }


@router.post("/retry/{workflow_id}")
def retry_workflow(
    workflow_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    workflow = db.query(AtsOptimizationWorkflow).filter(AtsOptimizationWorkflow.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    new_workflow = start_or_get_workflow(
        db,
        user_id=workflow.user_id,
        job_id=workflow.job_id,
        force=True,
    )
    if new_workflow.workflow_status == "PENDING":
        background_tasks.add_task(run_workflow_job, new_workflow.id)
    job = db.query(Job).filter(Job.id == new_workflow.job_id).first()
    return {
        "message": "ATS workflow retry started",
        "workflow_id": new_workflow.id,
        "workflow_status": new_workflow.workflow_status,
        "result": workflow_to_dict(new_workflow, job),
    }


@router.get("/download/{workflow_id}")
def download_generated_resume(workflow_id: int, format: str = "txt", db: Session = Depends(get_db)):
    workflow = db.query(AtsOptimizationWorkflow).filter(AtsOptimizationWorkflow.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if workflow.workflow_status != "COMPLETED" or not workflow.generated_resume_plain_text:
        raise HTTPException(status_code=400, detail="Generated resume is not ready for download")
    if format.lower() not in ("txt", "text", "plain"):
        raise HTTPException(status_code=400, detail="Only format=txt is supported currently")
    filename = f"optimized_resume_job_{workflow.job_id}.txt"
    return PlainTextResponse(
        content=workflow.generated_resume_plain_text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Compatibility routes under /api/ats (same router file historically)
# ---------------------------------------------------------------------------
compat_router = APIRouter(
    prefix="/api/ats",
    tags=["ATS Analyzer"],
)


@compat_router.post("/analyze-optimize/{user_id}/{job_id}")
def analyze_optimize_compat(
    user_id: int,
    job_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Compatibility endpoint.
    Starts the async workflow and, when already completed, returns the combined result.
    For immediate sync needs, falls back to optimize_and_match.
    """
    workflow = start_or_get_workflow(db, user_id=user_id, job_id=job_id, force=False)
    if workflow.workflow_status == "COMPLETED":
        job = db.query(Job).filter(Job.id == job_id).first()
        return workflow_to_dict(workflow, job)
    if workflow.workflow_status == "PENDING":
        # Run synchronously for this legacy endpoint so UI gets a full result.
        execute_workflow(db, workflow.id)
        db.refresh(workflow)
        if workflow.workflow_status == "COMPLETED":
            job = db.query(Job).filter(Job.id == job_id).first()
            return workflow_to_dict(workflow, job)
    # Fallback to previous sync service if needed
    return optimize_and_match(user_id=user_id, job_id=job_id, db=db)
