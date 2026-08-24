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
import time
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
from app.services.ats_scoring import ANALYSIS_VERSION, build_score_view, calculate_weighted_ats_score
from app.services.client_domain_validator import validate_client_domain
from app.services.final_resume_validator import validate_final_resume
from app.services.job_eligibility import is_generic_search_link_job, job_is_analyzable
from app.services.keyword_bold_formatter import bold_important_keywords
from app.services.keyword_injection_service import plan_keyword_injection
from app.services.llm_client import generation_timeout_config, llm_config
from app.services.resume_improvement_report_service import build_improvement_report
from app.services.resume_structure_parser import parse_resume_structure
from app.services.requirement_extraction_service import extract_job_requirement_analysis, group_canonical_score_evidence
from app.services.skill_confirmation_service import (
    build_canonical_supported_evidence,
    evaluate_generation_readiness,
    is_active_confirmation,
    load_confirmed_skills,
    reconcile_confirmations_with_analysis,
    skill_supported_by_evidence,
)
from app.services.skill_analysis_service import content_hash, normalize_skill_name
from app.services.keyword_utils import normalize_keyword
from app.services.template_preservation_service import render_optimized_document, template_mapping


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
    "INELIGIBLE_LOW_SCORE",
    "INELIGIBLE_ROLE_ALIGNMENT",
}

RUNNING_WORKFLOW_STATUSES = {
    "PENDING",
    "ANALYZING_ORIGINAL",
    "GENERATING_RESUME",
    "VALIDATING_RESUME",
    "ANALYZING_OPTIMIZED",
}

# Intermediate runs older than this are treated as stale and replaced.
STALE_RUNNING_SECONDS = int(os.getenv("ATS_STALE_WORKFLOW_SECONDS") or "180")

MINIMUM_ORIGINAL_SCORE = 65.0
TERMINAL_NO_RETRY_STATUSES = {
    "INCOMPLETE_JOB",
    "WAITING_FOR_RESUME",
}


def optimization_eligibility(score: float) -> dict[str, Any]:
    numeric = float(score)
    allowed = numeric >= MINIMUM_ORIGINAL_SCORE
    if numeric < 40:
        tier = "NOT_RECOMMENDED"
    elif numeric < 65:
        tier = "LOW_ALIGNMENT"
    elif numeric < 75:
        tier = "ELIGIBLE_FOR_OPTIMIZATION"
    elif numeric < 90:
        tier = "STRONG_OPTIMIZATION_CANDIDATE"
    else:
        tier = "ALREADY_HIGH_MATCH"
    return {
        "original_score": numeric,
        "optimization_allowed": allowed,
        "minimum_required_score": MINIMUM_ORIGINAL_SCORE,
        "eligibility_tier": tier,
        "eligibility_reason": (
            "Resume is sufficiently aligned for optimization."
            if allowed else "Original resume match is below the 65% minimum threshold."
        ),
        "recommended_action": None if allowed else "Upload a more relevant resume.",
    }


def role_aware_optimization_eligibility(score: float, role_alignment: str | None) -> dict[str, Any]:
    """Combine the unchanged score threshold with canonical role alignment."""
    result = optimization_eligibility(score)
    alignment = role_alignment or "UNKNOWN"
    numeric = result["original_score"]
    allowed = result["optimization_allowed"]
    reason = result["eligibility_reason"]
    warning = None
    if numeric >= MINIMUM_ORIGINAL_SCORE:
        if alignment == "MISMATCH":
            allowed = False
            reason = "Resume and selected job are classified as different role families."
        elif alignment == "PARTIAL_TRANSFERABLE" and numeric < 70:
            allowed = False
            reason = "Transferable role alignment requires an original ATS score of at least 70%."
        elif alignment == "PARTIAL_TRANSFERABLE":
            warning = "Background is transferable, but core role requirements need review."
        elif alignment == "UNKNOWN":
            warning = "Role classification is uncertain; eligibility is based on resume-to-job evidence and ATS score."
    result.update({
        "optimization_allowed": allowed,
        "eligibility_status": "ELIGIBLE" if allowed else "INELIGIBLE_LOW_SCORE" if numeric < MINIMUM_ORIGINAL_SCORE else "INELIGIBLE_ROLE_MISMATCH",
        "eligibility_reason": reason,
        "eligibility_warning": warning,
    })
    return result


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


def _content_hash(
    resume_text: str,
    job,
    prompt_version: str,
    analysis_version: str,
    model: str,
    evidence_fingerprint: str = "",
) -> str:
    raw = "||".join(
        [
            (resume_text or "").strip(),
            (getattr(job, "title", "") or "").strip(),
            (getattr(job, "description", "") or "").strip(),
            (getattr(job, "skills_required", "") or "").strip(),
            prompt_version,
            analysis_version,
            model,
            evidence_fingerprint or "",
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _evidence_fingerprint(db: Session, *, user_id: int, job_id: int, resume_id: int | None) -> str:
    """Fingerprint active confirmations so generation reruns after evidence changes."""
    rows = load_confirmed_skills(db, user_id=user_id, job_id=job_id)
    parts = []
    for row in rows:
        if not is_active_confirmation(row):
            continue
        if resume_id is not None and row.resume_id and row.resume_id != resume_id:
            continue
        parts.append(
            f"{row.normalized_skill_name}:{row.evidence_status}:{row.updated_at or row.id}"
        )
    parts.sort()
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def _latest_resume_for_user(db: Session, user_id: int) -> Resume | None:
    """Newest resume by id — matches ATS analysis/evidence routes."""
    return (
        db.query(Resume)
        .filter(Resume.user_id == user_id)
        .order_by(Resume.id.desc())
        .first()
    )


def _workflow_age_seconds(workflow: AtsOptimizationWorkflow) -> float:
    stamp = workflow.updated_at or workflow.created_at
    if not stamp:
        return 0.0
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return max(0.0, (_now() - stamp).total_seconds())


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
    logger.info("WORKFLOW_STATUS_UPDATED workflow_id=%s status=%s", workflow.id, status)


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
    generation_metadata = {
        "generation_method": workflow.generation_method,
        "llm_used": bool(workflow.llm_used),
        "llm_provider": workflow.llm_provider,
        "llm_model": workflow.llm_model,
        "llm_service": workflow.llm_service,
        "fallback_used": bool(workflow.fallback_used),
        "fallback_reason": workflow.fallback_reason,
    }
    poll = generation_timeout_config()

    payload = {
        "workflow_id": workflow.id,
        "user_id": workflow.user_id,
        "resume_id": workflow.resume_id,
        "job_id": workflow.job_id,
        "optimized_resume_id": workflow.optimized_resume_id,
        "workflow_status": workflow.workflow_status,
        "poll_interval_ms": poll["poll_interval_ms"],
        "poll_timeout_seconds": poll["poll_timeout_seconds"],
        "original_match_score": workflow.original_match_score,
        "original_score": workflow.original_match_score,
        "optimized_match_score": workflow.optimized_match_score,
        "score_improvement": workflow.score_improvement,
        "original_matched_keywords": original_matched,
        "optimized_matched_keywords": optimized_matched,
        "newly_matched_keywords": newly_matched,
        "original_missing_keywords": original.get("missing_keywords") or [],
        "remaining_missing_keywords": remaining_missing,
        "unsupported_keywords_not_added": (
            list((optimized.get("remaining_gaps") or {}).get("unsupported") or [])
            or list(optimized.get("missing_mandatory_skills") or [])
            or [
                tech for tech in (
                    (validation.get("keyword_analysis") or {}).get("unsupportedKeywords")
                    or original.get("unsupported_keywords")
                    or []
                )
                if tech not in (optimized.get("confirmed_skills_used") or [])
            ]
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
        "generation_method": workflow.generation_method,
        "llm_used": bool(workflow.llm_used),
        "llm_service": workflow.llm_service,
        "fallback_used": bool(workflow.fallback_used),
        "fallback_reason": workflow.fallback_reason,
        "generation_metadata": generation_metadata,
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
        "optimization_allowed": bool(workflow.optimization_allowed),
        "eligibility_reason": workflow.eligibility_reason,
        "eligibility_tier": workflow.eligibility_tier,
        "eligibility_status": original.get("eligibility_status") or ("ELIGIBLE" if workflow.optimization_allowed else "INELIGIBLE_LOW_SCORE"),
        "minimum_required_score": MINIMUM_ORIGINAL_SCORE,
        "target_score_range": "90-95" if workflow.optimization_allowed else None,
        "generated_score": workflow.optimized_match_score,
        "template_preserved": bool(optimized.get("template_preserved")),
        "source_file_type": optimized.get("source_file_type"),
        "template_note": optimized.get("template_note"),
        "editable": workflow.workflow_status == "COMPLETED" and bool(workflow.optimized_resume_id),
        "validation_passed": (validation.get("final_validation") or {}).get("status") == "passed",
        "why_not_95": optimized.get("why_not_95") or "",
        "confirmed_skills_used": optimized.get("confirmed_skills_used") or [],
        "confirmed_skills_incorporated": (
            optimized.get("confirmed_skills_incorporated")
            or optimized.get("confirmed_skills_used")
            or []
        ),
        "remaining_gaps": (
            optimized.get("remaining_gaps")
            if isinstance(optimized.get("remaining_gaps"), dict)
            else {
                "unsupported": list(optimized.get("missing_mandatory_skills") or []),
                "preferred_gaps": list(optimized.get("missing_preferred_skills") or []),
            }
        ),
        "optimized_score_view": optimized.get("score_view") or build_score_view(optimized),
        "original_score_view": build_score_view(original),
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

    resume = _latest_resume_for_user(db, user_id)

    config = llm_config()
    evidence_fp = _evidence_fingerprint(
        db, user_id=user_id, job_id=job_id, resume_id=resume.id if resume else None,
    )
    hash_value = _content_hash(
        resume.parsed_text if resume else "",
        job,
        config.get("prompt_version") or "none",
        ANALYSIS_VERSION,
        config.get("model") or "none",
        evidence_fingerprint=evidence_fp,
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
        if existing.workflow_status in RUNNING_WORKFLOW_STATUSES:
            age = _workflow_age_seconds(existing)
            # Reuse only fresh in-flight runs. Stale mid-states (e.g. server restart
            # during GENERATING_RESUME) must not block forever.
            if existing.workflow_status == "PENDING" or age < STALE_RUNNING_SECONDS:
                return existing
            logger.warning(
                "Replacing stale workflow id=%s status=%s age_s=%.0f user_id=%s job_id=%s",
                existing.id, existing.workflow_status, age, user_id, job_id,
            )
        # INELIGIBLE_*/FAILED/VALIDATION_FAILED always start a fresh run so a later
        # corrected score/evidence state is never blocked by stale terminal rows.

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

    if resume is None:
        raise HTTPException(
            status_code=400,
            detail={
                "workflow_status": "WAITING_FOR_RESUME",
                "can_generate_resume": False,
                "blocking_reasons": [{"code": "RESUME_MISSING", "reason": "Upload a resume before generating."}],
                "message": "Upload a resume before saving and optimizing jobs.",
            },
        )

    logger.info(
        "ATS_WORKFLOW_START user_id=%s job_id=%s resume_id=%s analysis_version_len=%s force=%s",
        user_id, job_id, resume.id, len(ANALYSIS_VERSION or ""), force,
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
        # Clear any stale eligibility fields on new runs.
        optimization_allowed=None,
        eligibility_reason=None,
        eligibility_tier=None,
        original_match_score=None,
        optimized_match_score=None,
        optimized_resume_id=None,
    )
    try:
        db.add(workflow)
        db.commit()
        db.refresh(workflow)
    except Exception:
        db.rollback()
        logger.exception(
            "ATS_WORKFLOW_START_FAILED user_id=%s job_id=%s resume_id=%s "
            "analysis_version=%s analysis_version_len=%s llm_provider=%s llm_model=%s",
            user_id,
            job_id,
            resume.id,
            ANALYSIS_VERSION,
            len(ANALYSIS_VERSION or ""),
            config.get("provider"),
            config.get("model"),
        )
        raise
    logger.info(
        "ATS workflow started id=%s user_id=%s job_id=%s resume_id=%s status=%s evidence_fp=%s",
        workflow.id, user_id, job_id, resume.id, status, evidence_fp,
    )
    return workflow


def execute_workflow(db: Session, workflow_id: int) -> AtsOptimizationWorkflow:
    workflow = db.query(AtsOptimizationWorkflow).filter(AtsOptimizationWorkflow.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Only hard-stop statuses that cannot be recalculated on the same row.
    # Stale INELIGIBLE_LOW_SCORE must NOT short-circuit a newly created PENDING run.
    if workflow.workflow_status in TERMINAL_NO_RETRY_STATUSES:
        return workflow
    if workflow.workflow_status == "COMPLETED" and workflow.optimized_resume_id:
        return workflow

    job = db.query(Job).filter(Job.id == workflow.job_id, Job.user_id == workflow.user_id).first()
    resume = db.query(Resume).filter(Resume.id == workflow.resume_id, Resume.user_id == workflow.user_id).first()
    if not job or not resume:
        _set_status(workflow, "FAILED", db, "Job or resume missing")
        return workflow

    try:
        logger.info("GENERATION_REQUEST_RECEIVED workflow_id=%s user_id=%s job_id=%s", workflow.id, workflow.user_id, workflow.job_id)
        logger.info("LOAD_WORKFLOW_OK workflow_id=%s", workflow.id)
        resume_text = resume.parsed_text or ""
        logger.info("LOAD_RESUME_OK workflow_id=%s resume_id=%s", workflow.id, resume.id)
        logger.info("LOAD_JOB_OK workflow_id=%s job_id=%s", workflow.id, job.id)
        structure = parse_resume_structure(resume_text)

        resume_hash = content_hash(resume_text)
        workflow_run_id = (
            f"{workflow.user_id}:{workflow.job_id}:{workflow.resume_id or 0}:"
            f"{resume_hash[:12]}:{ANALYSIS_VERSION}"
        )
        canonical_original = calculate_weighted_ats_score(resume_text, job)
        requirement_groups = group_canonical_score_evidence(canonical_original, job.id)
        # Same authoritative evidence path as Step 4 / generate precheck.
        confirmed_rows = reconcile_confirmations_with_analysis(
            db,
            user_id=workflow.user_id,
            job_id=workflow.job_id,
            resume_id=workflow.resume_id,
            grouped_analysis=requirement_groups,
            workflow_run_id=workflow_run_id,
            resume_hash=resume_hash,
        )
        denied_skills = [
            row.normalized_skill_name for row in confirmed_rows
            if row.evidence_status == "REJECTED"
        ]
        readiness = evaluate_generation_readiness(requirement_groups, confirmed_rows)
        verified_resume_skills = [item["normalized_skill"] for item in requirement_groups["verified_resume_skills"]]
        supported = build_canonical_supported_evidence(
            verified_resume_skills=verified_resume_skills,
            confirmation_rows=confirmed_rows,
        )
        scoring_evidence = supported["scoring_evidence"]
        generation_evidence = supported["generation_evidence"]
        verified_skills = [*verified_resume_skills, *generation_evidence.keys()]
        supported_keys = supported["supported_skill_keys"]
        logger.info(
            "LOAD_EVIDENCE_OK workflow_id=%s confirmed=%s supported=%s",
            workflow.id,
            supported.get("confirmed_skill_names"),
            len(supported_keys),
        )

        logger.info(
            "CURRENT ANALYSIS workflow_id=%s user_id=%s job_id=%s resume_id=%s "
            "original_score=%s can_generate_resume=%s blockers=%s "
            "confirmed_skills=%s supported_keys=%s llm_provider=%s llm_model=%s",
            workflow.id,
            workflow.user_id,
            workflow.job_id,
            workflow.resume_id,
            canonical_original.get("match_percentage"),
            readiness.get("can_generate_resume"),
            [item.get("skill") for item in readiness.get("blocking_reasons") or []],
            supported.get("confirmed_skill_names"),
            sorted(supported_keys)[:40],
            workflow.llm_provider,
            workflow.llm_model,
        )

        # Original score uses ONLY the uploaded resume and the job — never user confirmations.
        _set_status(workflow, "ANALYZING_ORIGINAL", db)
        original = canonical_original
        workflow.original_match_score = original["match_percentage"]
        eligibility = role_aware_optimization_eligibility(workflow.original_match_score, original.get("role_alignment"))
        alignment = original.get("role_alignment")
        job_family = original.get("job_role_family") or "Other"
        resume_families = original.get("resume_role_families") or ["Other"]
        if not eligibility["optimization_allowed"] and alignment in {"EXACT", "STRONG_RELATED"}:
            eligibility["eligibility_reason"] = (
                f"Role alignment is {alignment.lower().replace('_', ' ')}, but ATS score is below the 65% threshold."
            )
        original["resume_role_family"] = resume_families[0]
        original["role_alignment_reason"] = (
            f"Both resume and selected job are classified as {job_family}."
            if alignment == "EXACT"
            else f"{resume_families[0]} is strongly related to {job_family}."
            if alignment == "STRONG_RELATED"
            else f"{resume_families[0]} and {job_family} are different role families."
            if alignment == "MISMATCH"
            else "Role-family evidence is partial or insufficient for a definitive classification."
        )
        original["eligibility_status"] = eligibility["eligibility_status"]
        # Always overwrite stale eligibility from the CURRENT score.
        workflow.optimization_allowed = eligibility["optimization_allowed"]
        workflow.eligibility_reason = eligibility["eligibility_reason"]
        workflow.eligibility_tier = eligibility["eligibility_tier"]
        original.update(eligibility)
        original["can_generate_resume"] = readiness.get("can_generate_resume")
        original["blocking_reasons"] = readiness.get("blocking_reasons") or []
        workflow.original_analysis_json = _dumps(original)
        db.add(workflow)
        db.commit()

        if not workflow.optimization_allowed:
            workflow.optimization_status = eligibility["eligibility_status"]
            workflow.completed_at = _now()
            terminal_status = "INELIGIBLE_LOW_SCORE" if eligibility["eligibility_status"] == "INELIGIBLE_LOW_SCORE" else "INELIGIBLE_ROLE_ALIGNMENT"
            _set_status(workflow, terminal_status, db)
            logger.info(
                "ATS workflow ineligible id=%s status=%s score=%s reason=%s",
                workflow.id, terminal_status, workflow.original_match_score, workflow.eligibility_reason,
            )
            return workflow

        # Unsupported / incomplete mandatory evidence never blocks generation.
        # Those requirements stay in Remaining Gaps and reduce reachable score.
        if readiness.get("generation_warnings"):
            logger.info(
                "ATS workflow proceeding with unsupported gaps id=%s warnings=%s",
                workflow.id,
                [item.get("skill") for item in readiness.get("generation_warnings") or []],
            )

        injection_plan = plan_keyword_injection(
            resume_text=resume_text,
            job=job,
            employments=structure.get("employments") or [],
            projects=structure.get("projects") or [],
            verified_skills=verified_skills,
        )

        _set_status(workflow, "GENERATING_RESUME", db)
        # Original missing + injection unsupported, minus skills covered by resume OR accepted
        # user evidence (including OR groups where one alternative is supported).
        raw_unsupported = list(dict.fromkeys(
            (injection_plan.get("unsupportedKeywords") or [])
            + (original.get("missing_mandatory_skills") or [])
        ))
        unsupported_requirements = [
            tech for tech in raw_unsupported
            if not skill_supported_by_evidence(tech, supported_keys)
        ]
        # Keep generation / validation / Remaining Gaps on one filtered unsupported list.
        # Promote JD-relevant confirmed skills into supportedKeywords for placement.
        confirmed_for_jd = []
        for name in supported.get("confirmed_skill_names") or []:
            display = normalize_keyword(name)
            if not display or display in (injection_plan.get("supportedKeywords") or []):
                continue
            # Relevant when the skill (or an OR-group containing it) was previously unsupported.
            if any(skill_supported_by_evidence(label, {
                normalize_skill_name(display),
                display,
                display.lower(),
                normalize_skill_name(name),
            }) for label in raw_unsupported):
                confirmed_for_jd.append(display)
        injection_plan["unsupportedKeywords"] = unsupported_requirements
        if confirmed_for_jd:
            selected = list(dict.fromkeys(
                (injection_plan.get("supportedKeywords") or []) + confirmed_for_jd
            ))
            injection_plan["supportedKeywords"] = selected
            injection_plan["keywordsInjected"] = selected[:10]
            placement = dict(injection_plan.get("placement") or {})
            placement.update({
                "summary": selected[:5],
                "skills": selected[:12],
                "experience": selected[:8],
                "projects": selected[:6],
            })
            injection_plan["placement"] = placement
        logger.info(
            "UNSUPPORTED_FILTER workflow_id=%s raw=%s filtered=%s confirmed=%s confirmed_for_jd=%s",
            workflow.id,
            raw_unsupported,
            unsupported_requirements,
            supported.get("confirmed_skill_names"),
            confirmed_for_jd,
        )

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

        MAX_OPTIMIZATION_PASSES = 2
        generated = None
        plain = ""
        markdown = ""
        final_validation: dict[str, Any] = {}
        client_validation: dict[str, Any] = {}
        content_changed = False
        optimized: dict[str, Any] = {}
        improvement = 0
        original_hash = hashlib.sha256((resume_text or "").strip().encode("utf-8")).hexdigest()
        budgets = generation_timeout_config()
        deadline = time.monotonic() + budgets["generation_timeout_seconds"]
        skip_llm = False
        logger.info(
            "GENERATION_STARTED workflow_id=%s llm_timeout_s=%s overall_timeout_s=%s",
            workflow.id,
            budgets["llm_timeout_seconds"],
            budgets["generation_timeout_seconds"],
        )

        for pass_index in range(MAX_OPTIMIZATION_PASSES):
            remaining = deadline - time.monotonic()
            if remaining <= 8:
                skip_llm = True
            elif remaining < budgets["llm_timeout_seconds"] + 5:
                skip_llm = True
            correction = None
            if pass_index > 0:
                correction = (
                    "Previous pass did not produce a meaningful, score-improving rewrite. "
                    "Rewrite the Professional Summary for the target role, reorder Technical Skills "
                    "by job-description priority, strengthen experience bullets with action verbs and "
                    "supported technologies, and incorporate accepted_user_evidence. "
                    "Do not invent facts. The output must differ from the original resume."
                )
                if generated and not content_changed:
                    correction += " The previous output was identical to the original resume."
                elif improvement <= 0:
                    correction += " The previous optimized score did not beat the original score."

            candidate = generate_with_llm_or_fallback(
                resume_text=resume_text,
                job=job,
                injection_plan=injection_plan,
                structure=structure,
                confirmed_evidence=generation_evidence,
                denied_skills=denied_skills,
                verified_resume_skills=verified_resume_skills,
                correction_instructions=correction,
                skip_llm=skip_llm,
            )
            if candidate.get("fallback_used") and candidate.get("fallback_reason") in {
                "timeout", "connection_failed", "connection_refused", "model_not_found", "llm_skipped",
            }:
                skip_llm = True
            if not candidate or not str(candidate.get("plain_text") or "").strip():
                raise RuntimeError("Resume generation returned empty content after LLM/fallback")

            plain, markdown, checks = _run_validation(candidate)
            final_validation = checks["final"]
            client_validation = checks["client"]
            generated = candidate

            if final_validation.get("status") == "failed" and pass_index == 0:
                # One validation-repair attempt before scoring comparison.
                repair = (
                    "Your previous output failed validation with these errors: "
                    + "; ".join(final_validation.get("errors") or [])
                    + ". Fix these specific issues without inventing any new facts."
                )
                repaired = generate_with_llm_or_fallback(
                    resume_text=resume_text,
                    job=job,
                    injection_plan=injection_plan,
                    structure=structure,
                    confirmed_evidence=generation_evidence,
                    denied_skills=denied_skills,
                    verified_resume_skills=verified_resume_skills,
                    correction_instructions=repair,
                    skip_llm=skip_llm or (deadline - time.monotonic() < budgets["llm_timeout_seconds"] + 5),
                )
                if repaired.get("fallback_used") and repaired.get("fallback_reason") in {
                    "timeout", "connection_failed", "connection_refused", "model_not_found", "llm_skipped",
                }:
                    skip_llm = True
                plain, markdown, checks = _run_validation(repaired)
                final_validation = checks["final"]
                client_validation = checks["client"]
                generated = repaired

            if final_validation.get("status") == "failed":
                break

            optimized_hash = hashlib.sha256((plain or "").strip().encode("utf-8")).hexdigest()
            content_changed = original_hash != optimized_hash
            optimized = calculate_weighted_ats_score(
                plain,
                job,
                confirmed_skills=scoring_evidence,
            )
            improvement = int(optimized["match_percentage"]) - int(original["match_percentage"])
            logger.info(
                "optimization pass=%s workflow_id=%s content_changed=%s improvement=%s method=%s",
                pass_index + 1,
                workflow.id,
                content_changed,
                improvement,
                generated.get("generation_method"),
            )
            # Stop early when we have a truthful content change and score gain.
            if content_changed and improvement > 0:
                break
            # No further truthful levers available (no supported keywords / confirmations).
            has_levers = bool(
                (injection_plan.get("supportedKeywords") or [])
                or generation_evidence
            )
            if not has_levers:
                break

        if not generated or not str(generated.get("plain_text") or "").strip():
            raise RuntimeError("Resume generation returned empty content after LLM/fallback")
        logger.info(
            "generation path workflow_id=%s method=%s llm_used=%s fallback_used=%s",
            workflow.id,
            generated.get("generation_method"),
            generated.get("llm_used"),
            generated.get("fallback_used"),
        )

        _set_status(workflow, "VALIDATING_RESUME", db)

        if final_validation.get("status") == "failed":
            logger.info(
                "GENERATED_RESUME_VALIDATION_FAILED workflow_id=%s provider=%s model=%s",
                workflow.id, generated.get("llm_provider"), generated.get("llm_model"),
            )
        else:
            logger.info(
                "GENERATED_RESUME_VALIDATION_PASSED workflow_id=%s provider=%s model=%s generation_method=%s",
                workflow.id, generated.get("llm_provider"), generated.get("llm_model"),
                generated.get("generation_method"),
            )

        workflow.llm_provider = generated.get("llm_provider")
        workflow.llm_model = generated.get("llm_model")
        workflow.llm_service = generated.get("llm_service")
        workflow.generation_method = generated.get("generation_method")
        workflow.llm_used = bool(generated.get("llm_used"))
        workflow.fallback_used = bool(generated.get("fallback_used"))
        workflow.fallback_reason = generated.get("fallback_reason")
        workflow.prompt_version = generated.get("prompt_version")
        workflow.generated_resume_plain_text = plain
        workflow.generated_resume_markdown = markdown

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
            "generation_metadata": generated.get("generation_metadata") or {},
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
        if not optimized:
            optimized = calculate_weighted_ats_score(
                plain,
                job,
                confirmed_skills=scoring_evidence,
            )
            improvement = int(optimized["match_percentage"]) - int(original["match_percentage"])
        # Attach validator warnings into optimized analysis display fields
        optimized["technology_warnings"] = (
            injection_plan.get("technology_validation") or {}
        ).get("conflictWarnings") or []
        optimized["timeline_warnings"] = (
            injection_plan.get("timeline_validation") or {}
        ).get("timelineWarnings") or []
        optimized["client_domain_warnings"] = client_validation.get("clientDomainWarnings") or []
        # Confirmed skills actually present in the generated resume (JD-relevant incorporation).
        from app.services.keyword_utils import contains_keyword

        incorporated = []
        for name in supported.get("confirmed_skill_names") or []:
            if contains_keyword(plain or "", name):
                incorporated.append(normalize_keyword(name) or name)
        optimized["confirmed_skills_used"] = list(dict.fromkeys(
            (optimized.get("confirmed_skills_used") or []) + incorporated
        ))
        optimized["confirmed_skills_incorporated"] = optimized["confirmed_skills_used"]
        optimized["unsupported_keywords"] = list(optimized.get("missing_mandatory_skills") or [])
        optimized["remaining_gaps"] = optimized.get("remaining_gaps") or {
            "unsupported": optimized["unsupported_keywords"],
            "preferred_gaps": optimized.get("missing_preferred_skills") or [],
        }
        optimized["score_view"] = build_score_view(optimized)

        improvement = int(optimized["match_percentage"]) - int(original["match_percentage"])
        if not content_changed:
            optimized.setdefault("suggestions", []).append(
                "Your resume is already strongly aligned with this job. No additional "
                "evidence-backed changes were available to safely increase the score."
            )
            if workflow.optimization_status != "VALIDATION_FAILED":
                workflow.optimization_status = "NO_MEANINGFUL_CHANGE"
        elif improvement <= 0:
            workflow.optimization_status = "NO_IMPROVEMENT"
            optimized.setdefault("suggestions", []).append(
                "Your resume is already strongly aligned with this job. No additional "
                "evidence-backed changes were available to safely increase the score."
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
        rendered = render_optimized_document(
            resume.stored_file_path, resume.file_type, plain,
            output_path=getattr(optimized_resume, "generated_file_path", None),
        )
        optimized_resume.generated_resume_text = plain
        optimized_resume.final_resume_text = plain
        optimized_resume.generated_resume_json = _dumps({"structure": structure, "template_mapping": template_mapping(resume.stored_file_path, resume.file_type)})
        optimized_resume.original_score = original["match_percentage"]
        optimized_resume.generated_score = optimized["match_percentage"]
        optimized_resume.template_preserved = rendered["template_preserved"]
        optimized_resume.source_file_type = resume.file_type
        optimized_resume.generated_file_path = rendered["file_path"]
        optimized_resume.final_file_path = rendered["file_path"]
        optimized_resume.validation_status = final_validation.get("status")
        optimized["template_preserved"] = rendered["template_preserved"]
        optimized["source_file_type"] = resume.file_type
        optimized["template_note"] = rendered["template_note"]
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
        logger.info("OPTIMIZED_RESUME_SAVED workflow_id=%s optimized_resume_id=%s", workflow.id, optimized_resume.id)
        logger.info("WORKFLOW_STATUS_UPDATED workflow_id=%s status=COMPLETED", workflow.id)

        logger.info(
            "GENERATION_COMPLETE workflow_id=%s user_id=%s job_id=%s original=%s optimized=%s improvement=%s mode=%s",
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
        _set_status(workflow, "FAILED", db, "Resume generation could not be completed.")
        logger.info("GENERATION_COMPLETE workflow_id=%s status=FAILED reason=%s", workflow_id, type(error).__name__)
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
