"""
ATSOptimizationWorkflowService

Orchestrates:
  original analysis → LLM/deterministic generation → validators → optimized analysis

Uses background-friendly status transitions. Does not change Job Search agents.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.ats_optimization_workflow_model import AtsOptimizationWorkflow
from app.models.job_model import Job
from app.models.match_model import MatchScore
from app.models.optimized_resume_model import OptimizedResume
from app.models.resume_model import Resume
from app.models.user_model import User
from app.services.ats_resume_generation_service import generate_with_llm_or_fallback
from app.services.ats_scoring import ANALYSIS_VERSION, calculate_weighted_ats_score
from app.services.client_domain_validator import validate_client_domain
from app.services.final_resume_validator import validate_final_resume
from app.services.job_eligibility import is_generic_search_link_job, job_is_analyzable
from app.services.keyword_bold_formatter import bold_important_keywords
from app.services.keyword_injection_service import plan_keyword_injection
from app.services.llm_client import llm_config
from app.services.resume_improvement_report_service import build_improvement_report
from app.services.resume_structure_parser import parse_resume_structure
from app.services.skill_confirmation_service import (
    load_confirmed_skills,
    to_generation_evidence,
    to_scoring_evidence,
)


logger = logging.getLogger(__name__)

STATUSES = {
    "PENDING",
    "WAITING_FOR_RESUME",
    "INCOMPLETE_JOB",
    "ANALYZING_ORIGINAL",
    "GENERATING_RESUME",
    "VALIDATING_RESUME",
    "VALIDATION_FAILED",
    "ANALYZING_OPTIMIZED",
    "COMPLETED",
    "FAILED",
}


def should_run_ats_workflow(job, resume: Resume | None = None) -> tuple[bool, str]:
    if job is None:
        return False, "Job not found"
    if is_generic_search_link_job(job):
        return False, "Generic search-link jobs are not eligible for ATS optimization."
    eligible, reason = job_is_analyzable(job, require_url=False)
    if not eligible:
        return False, reason
    if resume is None or not (resume.parsed_text or "").strip():
        return False, "WAITING_FOR_RESUME"
    return True, ""


def _content_hash(resume_text: str, job, prompt_version: str, analysis_version: str, model: str) -> str:
    raw = "||".join(
        [
            (resume_text or "").strip(),
            (getattr(job, "title", "") or "").strip(),
            (getattr(job, "description", "") or "").strip(),
            (getattr(job, "skills_required", "") or "").strip(),
            prompt_version,
            analysis_version,
            model,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc)


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str)


def _loads(value: str | None, default=None):
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _set_status(workflow: AtsOptimizationWorkflow, status: str, db: Session, error: str | None = None):
    workflow.workflow_status = status
    workflow.updated_at = _now()
    if error:
        workflow.error_message = error[:500]
    db.add(workflow)
    db.commit()
    db.refresh(workflow)


def _upsert_match_score(
    db: Session,
    *,
    user_id: int,
    resume_id: int,
    job_id: int,
    score_result: dict,
    resume_version: str,
    optimized_resume_id: int | None = None,
) -> MatchScore:
    existing = (
        db.query(MatchScore)
        .filter(
            MatchScore.user_id == user_id,
            MatchScore.resume_id == resume_id,
            MatchScore.job_id == job_id,
            MatchScore.resume_version == resume_version,
        )
        .first()
    )
    matched = ", ".join(score_result.get("matched_keywords") or [])
    missing = ", ".join(score_result.get("missing_keywords") or [])
    suggestions = "\n".join(score_result.get("suggestions") or [])
    breakdown = _dumps(score_result.get("score_breakdown") or {})
    percentage = int(score_result.get("match_percentage") or score_result.get("ats_score") or 0)

    if existing:
        existing.match_percentage = percentage
        existing.matched_keywords = matched
        existing.missing_keywords = missing
        existing.recommendation = score_result.get("recommendation")
        existing.suggestions = suggestions
        existing.score_breakdown = breakdown
        existing.optimized_resume_id = optimized_resume_id
        db.add(existing)
        db.flush()
        return existing

    record = MatchScore(
        user_id=user_id,
        resume_id=resume_id,
        job_id=job_id,
        match_percentage=percentage,
        matched_keywords=matched,
        missing_keywords=missing,
        recommendation=score_result.get("recommendation"),
        resume_version=resume_version,
        optimized_resume_id=optimized_resume_id,
        score_breakdown=breakdown,
        suggestions=suggestions,
    )
    db.add(record)
    db.flush()
    return record


def _upsert_optimized_resume(
    db: Session,
    *,
    user_id: int,
    resume_id: int,
    job_id: int,
    plain_text: str,
    notes: str,
) -> OptimizedResume:
    existing = (
        db.query(OptimizedResume)
        .filter(
            OptimizedResume.user_id == user_id,
            OptimizedResume.resume_id == resume_id,
            OptimizedResume.job_id == job_id,
        )
        .order_by(OptimizedResume.id.desc())
        .first()
    )
    if existing:
        existing.optimized_text = plain_text
        existing.optimization_notes = notes
        db.add(existing)
        db.flush()
        return existing
    record = OptimizedResume(
        user_id=user_id,
        resume_id=resume_id,
        job_id=job_id,
        optimized_text=plain_text,
        optimization_notes=notes,
    )
    db.add(record)
    db.flush()
    return record


def workflow_to_dict(workflow: AtsOptimizationWorkflow, job: Job | None = None) -> dict[str, Any]:
    original = _loads(workflow.original_analysis_json, {})
    optimized = _loads(workflow.optimized_analysis_json, {})
    validation = _loads(workflow.validation_report_json, {})
    original_matched = original.get("matched_keywords") or []
    optimized_matched = optimized.get("matched_keywords") or []
    newly_matched = [k for k in optimized_matched if k not in original_matched]
    remaining_missing = optimized.get("missing_keywords") or []

    payload = {
        "workflow_id": workflow.id,
        "user_id": workflow.user_id,
        "resume_id": workflow.resume_id,
        "job_id": workflow.job_id,
        "optimized_resume_id": workflow.optimized_resume_id,
        "workflow_status": workflow.workflow_status,
        "original_match_score": workflow.original_match_score,
        "optimized_match_score": workflow.optimized_match_score,
        "score_improvement": workflow.score_improvement,
        "original_matched_keywords": original_matched,
        "optimized_matched_keywords": optimized_matched,
        "newly_matched_keywords": newly_matched,
        "original_missing_keywords": original.get("missing_keywords") or [],
        "remaining_missing_keywords": remaining_missing,
        "unsupported_keywords_not_added": (
            (validation.get("keyword_analysis") or {}).get("unsupportedKeywords")
            or original.get("unsupported_keywords")
            or []
        ),
        "original_suggestions": original.get("suggestions") or [],
        "final_suggestions": optimized.get("suggestions") or [],
        "final_recommendation": optimized.get("recommendation") or "",
        "validation_report": validation,
        "generated_resume": {
            "plain_text": workflow.generated_resume_plain_text or "",
            "formatted_preview": workflow.generated_resume_markdown or "",
        },
        "llm_provider": workflow.llm_provider,
        "llm_model": workflow.llm_model,
        "prompt_version": workflow.prompt_version,
        "analysis_version": workflow.analysis_version,
        "error_message": workflow.error_message,
        "missing_job_fields": _loads(workflow.missing_job_fields, []),
        "original_resume_hash": workflow.original_resume_hash,
        "optimized_resume_hash": workflow.optimized_resume_hash,
        "original_text_length": workflow.original_text_length,
        "optimized_text_length": workflow.optimized_text_length,
        "content_changed": workflow.content_changed,
        "optimization_status": workflow.optimization_status,
        "why_not_95": optimized.get("why_not_95") or "",
        "confirmed_skills_used": optimized.get("confirmed_skills_used") or [],
        "improvement_report": _loads(workflow.improvement_report_json, {}),
        "created_at": str(workflow.created_at) if workflow.created_at else None,
        "updated_at": str(workflow.updated_at) if workflow.updated_at else None,
        "completed_at": str(workflow.completed_at) if workflow.completed_at else None,
        # Compatibility aliases for existing ATS page
        "original_match_percentage": workflow.original_match_score,
        "optimized_match_percentage": workflow.optimized_match_score,
        "improvement_percentage": workflow.score_improvement,
        "original_analysis": original,
        "optimized_analysis": optimized,
        "generated_resume_plain": workflow.generated_resume_plain_text,
        "generated_resume_preview_markdown": workflow.generated_resume_markdown,
        "validation_warnings": (validation.get("final_validation") or {}).get("warnings") or [],
        "final_validation": validation.get("final_validation") or {},
        "technology_validation": validation.get("technology_validation") or {},
        "client_validation": validation.get("client_validation") or {},
        "timeline_validation": validation.get("timeline_validation") or {},
        "recommendations": optimized.get("suggestions") or original.get("suggestions") or [],
        "optimization_notes": optimized.get("suggestions") or [],
        "role_alignment": original.get("role_alignment"),
        "resume_role_families": original.get("resume_role_families") or [],
        "target_job_role": original.get("job_role_family"),
        "score_status": optimized.get("score_status") or original.get("score_status"),
        "optimization_allowed": original.get("optimization_allowed", False),
        "safe_to_apply": optimized.get("safe_to_apply", False),
        "matched_core_skills": original.get("matched_core_skills") or [],
        "matched_transferable_skills": original.get("matched_transferable_skills") or [],
        "critical_missing_skills": original.get("missing_mandatory_skills") or [],
        "warning": original.get("warning_message") or "",
        "gap_analysis": {
            "score_cap": original.get("score_cap_applied"),
            "score_cap_reason": original.get("score_cap_reason"),
            "missing_mandatory_skills": original.get("missing_mandatory_skills") or [],
            "weak_core_skills": original.get("weak_core_skills") or [],
            "recommendation": original.get("recommendation"),
        },
        "message": (
            f"ATS workflow {workflow.workflow_status}"
            if workflow.workflow_status != "COMPLETED"
            else "ATS optimize-and-match completed"
        ),
    }
    if job:
        payload["job"] = {
            "id": job.id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "source": job.source,
            "job_url": job.job_url,
        }
    return payload


def start_or_get_workflow(
    db: Session,
    *,
    user_id: int,
    job_id: int,
    force: bool = False,
) -> AtsOptimizationWorkflow:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found for this user")

    resume = (
        db.query(Resume)
        .filter(Resume.user_id == user_id)
        .order_by(Resume.uploaded_at.desc())
        .first()
    )

    config = llm_config()
    hash_value = _content_hash(
        resume.parsed_text if resume else "",
        job,
        config.get("prompt_version") or "none",
        ANALYSIS_VERSION,
        config.get("model") or "none",
    )

    existing = (
        db.query(AtsOptimizationWorkflow)
        .filter(
            AtsOptimizationWorkflow.user_id == user_id,
            AtsOptimizationWorkflow.job_id == job_id,
            AtsOptimizationWorkflow.resume_id == (resume.id if resume else -1),
        )
        .order_by(AtsOptimizationWorkflow.id.desc())
        .first()
    )

    if existing and not force:
        if existing.workflow_status == "COMPLETED" and existing.content_hash == hash_value:
            return existing
        if existing.workflow_status in {
            "PENDING",
            "ANALYZING_ORIGINAL",
            "GENERATING_RESUME",
            "VALIDATING_RESUME",
            "ANALYZING_OPTIMIZED",
        }:
            return existing

    can_run, reason = should_run_ats_workflow(job, resume)
    status = "PENDING"
    missing_fields = []
    error_message = None

    if reason == "WAITING_FOR_RESUME":
        status = "WAITING_FOR_RESUME"
        error_message = "Upload a resume before saving and optimizing jobs."
    elif not can_run:
        status = "INCOMPLETE_JOB"
        error_message = reason
        if not (job.title or "").strip():
            missing_fields.append("title")
        if not (job.company or "").strip():
            missing_fields.append("company")
        if not (job.description or "").strip() or len((job.description or "").strip()) < 40:
            missing_fields.append("description")

    if not resume:
        # Use placeholder resume_id impossible FK — better to require resume row.
        # Create workflow still, but WAITING_FOR_RESUME without resume_id fails FK.
        # So only create when resume exists OR mark incomplete without insert when no resume.
        # Prefer inserting only when resume exists; otherwise raise soft payload via caller.
        pass

    if resume is None:
        # Create a lightweight incomplete workflow is not possible without resume_id FK.
        # Return a synthetic-like DB row is not available; raise structured HTTP for start endpoint,
        # while job-save path handles WAITING_FOR_RESUME without workflow row.
        raise HTTPException(
            status_code=400,
            detail={
                "workflow_status": "WAITING_FOR_RESUME",
                "message": "Upload a resume before saving and optimizing jobs.",
            },
        )

    workflow = AtsOptimizationWorkflow(
        user_id=user_id,
        resume_id=resume.id,
        job_id=job_id,
        workflow_status=status,
        content_hash=hash_value,
        llm_provider=config.get("provider"),
        llm_model=config.get("model"),
        prompt_version=config.get("prompt_version"),
        analysis_version=ANALYSIS_VERSION,
        error_message=error_message,
        missing_job_fields=_dumps(missing_fields) if missing_fields else None,
    )
    db.add(workflow)
    db.commit()
    db.refresh(workflow)
    return workflow


def execute_workflow(db: Session, workflow_id: int) -> AtsOptimizationWorkflow:
    workflow = db.query(AtsOptimizationWorkflow).filter(AtsOptimizationWorkflow.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    if workflow.workflow_status in {"INCOMPLETE_JOB", "WAITING_FOR_RESUME", "COMPLETED"}:
        return workflow

    job = db.query(Job).filter(Job.id == workflow.job_id, Job.user_id == workflow.user_id).first()
    resume = db.query(Resume).filter(Resume.id == workflow.resume_id, Resume.user_id == workflow.user_id).first()
    if not job or not resume:
        _set_status(workflow, "FAILED", db, "Job or resume missing")
        return workflow

    try:
        resume_text = resume.parsed_text or ""
        structure = parse_resume_structure(resume_text)

        confirmed_rows = load_confirmed_skills(db, user_id=workflow.user_id, job_id=workflow.job_id)
        scoring_evidence = to_scoring_evidence(confirmed_rows)
        generation_evidence = to_generation_evidence(confirmed_rows)
        verified_skills = list(generation_evidence.keys())

        # Original score uses ONLY the uploaded resume and the job — never user confirmations.
        _set_status(workflow, "ANALYZING_ORIGINAL", db)
        original = calculate_weighted_ats_score(resume_text, job)
        workflow.original_match_score = original["match_percentage"]
        workflow.original_analysis_json = _dumps(original)
        db.add(workflow)
        db.commit()

        injection_plan = plan_keyword_injection(
            resume_text=resume_text,
            job=job,
            employments=structure.get("employments") or [],
            projects=structure.get("projects") or [],
            verified_skills=verified_skills,
        )

        _set_status(workflow, "GENERATING_RESUME", db)
        generated = generate_with_llm_or_fallback(
            resume_text=resume_text,
            job=job,
            injection_plan=injection_plan,
            structure=structure,
            confirmed_evidence=generation_evidence,
        )

        _set_status(workflow, "VALIDATING_RESUME", db)
        unsupported_requirements = list(dict.fromkeys(
            (injection_plan.get("unsupportedKeywords") or [])
            + (original.get("missing_mandatory_skills") or [])
        ))

        def _run_validation(candidate: dict) -> tuple[str, dict, dict]:
            plain_text = candidate["plain_text"]
            markdown_text = bold_important_keywords(
                plain_text,
                injection_plan.get("keywordsInjected") or injection_plan.get("supportedKeywords") or [],
            )
            client_val = validate_client_domain(
                employments=structure.get("employments") or [],
                projects=structure.get("projects") or [],
                generated_text=plain_text,
            )
            final_val = validate_final_resume(
                original_text=resume_text,
                generated_text=plain_text,
                original_structure=structure,
                generated_sections=candidate.get("sections") or [],
                unsupported_keywords=unsupported_requirements,
                selected_technologies=injection_plan.get("supportedKeywords") or [],
            )
            return plain_text, markdown_text, {"client": client_val, "final": final_val}

        plain, markdown, checks = _run_validation(generated)
        final_validation = checks["final"]
        client_validation = checks["client"]

        if final_validation.get("status") == "failed":
            correction = (
                "Your previous output failed validation with these errors: "
                + "; ".join(final_validation.get("errors") or [])
                + ". Fix these specific issues without inventing any new facts."
            )
            retried = generate_with_llm_or_fallback(
                resume_text=resume_text,
                job=job,
                injection_plan=injection_plan,
                structure=structure,
                confirmed_evidence=generation_evidence,
                correction_instructions=correction,
            )
            plain, markdown, checks = _run_validation(retried)
            final_validation = checks["final"]
            client_validation = checks["client"]
            generated = retried

        workflow.llm_provider = generated.get("llm_provider")
        workflow.llm_model = generated.get("llm_model")
        workflow.prompt_version = generated.get("prompt_version")
        workflow.generated_resume_plain_text = plain
        workflow.generated_resume_markdown = markdown

        original_hash = hashlib.sha256((resume_text or "").strip().encode("utf-8")).hexdigest()
        optimized_hash = hashlib.sha256((plain or "").strip().encode("utf-8")).hexdigest()
        content_changed = original_hash != optimized_hash
        workflow.original_resume_hash = original_hash
        workflow.optimized_resume_hash = optimized_hash
        workflow.original_text_length = len((resume_text or "").strip())
        workflow.optimized_text_length = len((plain or "").strip())
        workflow.content_changed = content_changed

        validation_report = {
            "final_validation": final_validation,
            "client_validation": client_validation,
            "technology_validation": injection_plan.get("technology_validation") or {},
            "timeline_validation": injection_plan.get("timeline_validation") or {},
            "keyword_analysis": {
                "supportedKeywords": injection_plan.get("supportedKeywords") or [],
                "unsupportedKeywords": unsupported_requirements,
                "keywordsInjected": injection_plan.get("keywordsInjected") or [],
                "keywordWarnings": injection_plan.get("keywordWarnings") or [],
                "keyword_evidence": generated.get("keyword_evidence") or [],
            },
            "generation_warnings": generated.get("generation_warnings") or [],
            "generation_mode": generated.get("generation_mode"),
        }
        workflow.validation_report_json = _dumps(validation_report)
        db.add(workflow)
        db.commit()

        if final_validation.get("status") == "failed":
            workflow.optimization_status = "VALIDATION_FAILED"
            _set_status(
                workflow,
                "VALIDATION_FAILED",
                db,
                "; ".join(final_validation.get("errors") or ["Validation failed"])[:500],
            )
            return workflow

        if not content_changed:
            workflow.optimization_status = "NO_MEANINGFUL_CHANGE"
        else:
            workflow.optimization_status = "OPTIMIZED"

        _set_status(workflow, "ANALYZING_OPTIMIZED", db)
        optimized = calculate_weighted_ats_score(
            plain,
            job,
            evidence_source_text=resume_text,
            confirmed_skills=scoring_evidence,
        )
        # Attach validator warnings into optimized analysis display fields
        optimized["technology_warnings"] = (
            injection_plan.get("technology_validation") or {}
        ).get("conflictWarnings") or []
        optimized["timeline_warnings"] = (
            injection_plan.get("timeline_validation") or {}
        ).get("timelineWarnings") or []
        optimized["client_domain_warnings"] = client_validation.get("clientDomainWarnings") or []

        improvement = int(optimized["match_percentage"]) - int(original["match_percentage"])
        if not content_changed:
            optimized.setdefault("suggestions", []).append(
                "The generated resume is identical to the original resume. "
                "No unsupported keywords or claims were added, so the score could not change."
            )
            if workflow.optimization_status != "VALIDATION_FAILED":
                workflow.optimization_status = "NO_MEANINGFUL_CHANGE"
        elif improvement <= 0:
            workflow.optimization_status = "NO_IMPROVEMENT"
            optimized.setdefault("suggestions", []).append(
                "Optimized score did not improve. Truthfulness constraints prevented unsupported keyword additions."
            )

        improvement_report = build_improvement_report(
            original_score_result=original,
            optimized_score_result=optimized,
            original_text=resume_text,
            optimized_text=plain,
            validation_report=validation_report,
            content_changed=content_changed,
        )
        workflow.improvement_report_json = _dumps(improvement_report)

        notes = _dumps(
            {
                "original_score": original["match_percentage"],
                "optimized_score": optimized["match_percentage"],
                "score_improvement": improvement,
                "validation": final_validation,
                "generation_mode": generated.get("generation_mode"),
            }
        )
        optimized_resume = _upsert_optimized_resume(
            db,
            user_id=workflow.user_id,
            resume_id=workflow.resume_id,
            job_id=workflow.job_id,
            plain_text=plain,
            notes=notes,
        )
        _upsert_match_score(
            db,
            user_id=workflow.user_id,
            resume_id=workflow.resume_id,
            job_id=workflow.job_id,
            score_result=original,
            resume_version="original",
        )
        _upsert_match_score(
            db,
            user_id=workflow.user_id,
            resume_id=workflow.resume_id,
            job_id=workflow.job_id,
            score_result=optimized,
            resume_version="optimized",
            optimized_resume_id=optimized_resume.id,
        )

        workflow.optimized_resume_id = optimized_resume.id
        workflow.optimized_match_score = optimized["match_percentage"]
        workflow.score_improvement = improvement
        workflow.optimized_analysis_json = _dumps(optimized)
        workflow.workflow_status = "COMPLETED"
        workflow.completed_at = _now()
        workflow.error_message = None
        workflow.updated_at = _now()
        db.add(workflow)
        db.commit()
        db.refresh(workflow)

        logger.info(
            "ATS workflow completed id=%s user_id=%s job_id=%s original=%s optimized=%s improvement=%s mode=%s",
            workflow.id,
            workflow.user_id,
            workflow.job_id,
            workflow.original_match_score,
            workflow.optimized_match_score,
            workflow.score_improvement,
            generated.get("generation_mode"),
        )
        return workflow

    except Exception as error:
        logger.exception("ATS workflow failed id=%s", workflow_id)
        _set_status(workflow, "FAILED", db, f"{type(error).__name__}: {error}"[:500])
        return workflow


def run_workflow_job(workflow_id: int) -> None:
    """Background entrypoint using a fresh DB session."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        execute_workflow(db, workflow_id)
    finally:
        db.close()


def start_workflow_for_saved_job(
    db: Session,
    *,
    user_id: int,
    job_id: int,
    background_tasks=None,
) -> dict[str, Any]:
    """
    Soft-start after job save. Never rolls back the saved job.
    Returns workflow metadata for the API response.
    """
    job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
    if not job:
        return {
            "job_saved": True,
            "workflow_started": False,
            "workflow_status": "FAILED",
            "message": "Job saved, but workflow could not locate the job.",
        }

    if is_generic_search_link_job(job):
        return {
            "job_saved": True,
            "workflow_started": False,
            "workflow_status": "SKIPPED_GENERIC_SEARCH_LINK",
            "message": "Job saved. Generic search-link records are not optimized.",
        }

    resume = (
        db.query(Resume)
        .filter(Resume.user_id == user_id)
        .order_by(Resume.uploaded_at.desc())
        .first()
    )
    can_run, reason = should_run_ats_workflow(job, resume)
    if reason == "WAITING_FOR_RESUME":
        return {
            "job_saved": True,
            "workflow_started": False,
            "workflow_status": "WAITING_FOR_RESUME",
            "message": "Job saved. Upload a resume to generate optimization and match scores.",
        }
    if not can_run:
        return {
            "job_saved": True,
            "workflow_started": False,
            "workflow_status": "INCOMPLETE_JOB",
            "message": f"Job saved. {reason}",
            "reason": reason,
        }

    try:
        workflow = start_or_get_workflow(db, user_id=user_id, job_id=job_id, force=False)
    except HTTPException as error:
        detail = error.detail if isinstance(error.detail, dict) else {"message": str(error.detail)}
        return {
            "job_saved": True,
            "workflow_started": False,
            "workflow_status": detail.get("workflow_status") or "FAILED",
            "message": detail.get("message") or "Job saved. Workflow could not start.",
        }

    if workflow.workflow_status == "PENDING" and background_tasks is not None:
        background_tasks.add_task(run_workflow_job, workflow.id)
    elif workflow.workflow_status == "PENDING":
        # Synchronous fallback when no background tasks object is provided
        execute_workflow(db, workflow.id)
        db.refresh(workflow)

    return {
        "job_saved": True,
        "workflow_started": workflow.workflow_status not in {"INCOMPLETE_JOB", "WAITING_FOR_RESUME"},
        "workflow_id": workflow.id,
        "workflow_status": workflow.workflow_status,
        "message": (
            "Job saved. ATS optimization workflow started."
            if workflow.workflow_status in {
                "PENDING",
                "ANALYZING_ORIGINAL",
                "GENERATING_RESUME",
                "VALIDATING_RESUME",
                "ANALYZING_OPTIMIZED",
                "COMPLETED",
            }
            else f"Job saved. Workflow status: {workflow.workflow_status}"
        ),
    }


def max_optimizations_per_run() -> int:
    return int(os.getenv("MAX_OPTIMIZATIONS_PER_RUN") or "3")
