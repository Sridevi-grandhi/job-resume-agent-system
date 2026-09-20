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
from app.models.user_preference_model import UserPreference
from app.services.ats_optimization_workflow_service import (
    execute_workflow,
    recover_orphaned_workflows,
    run_workflow_job,
    start_or_get_workflow,
    start_or_get_workflow_with_decision,
    workflow_to_dict,
    optimization_eligibility,
    role_aware_optimization_eligibility,
)
from app.services.ats_scoring import ANALYSIS_VERSION, calculate_weighted_ats_score, classify_role
from app.services.job_eligibility import is_generic_search_link_job, job_is_analyzable
from app.services.requirement_extraction_service import (
    extract_job_requirement_analysis,
    group_requirements_vs_resume,
    group_canonical_score_evidence,
    load_requirement_analysis,
    merge_with_resume_evidence,
    save_requirement_analysis,
)
from app.services.llm_client import generation_timeout_config
from app.services.resume_structure_parser import parse_resume_structure
from app.services.skill_confirmation_service import (
    delete_confirmed_skill,
    evaluate_generation_readiness,
    reconcile_confirmations_with_analysis,
    save_evidence,
    summarize_active_evidence,
)
from app.services.skill_analysis_service import (
    MAVEN_ALIASES,
    content_hash,
    find_skill_evidence,
    normalize_skill_name,
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


def _analysis_identity(db: Session, user_id: int, job: Job, resume: Resume | None) -> dict:
    resume_text = (resume.parsed_text if resume else "") or ""
    preference = db.query(UserPreference).filter(UserPreference.user_id == user_id).first()
    job_text = "\n".join([
        job.title or "", job.description or "", job.skills_required or "",
        job.location or "", job.work_mode or "", job.job_type or "",
        # Context-only cache discriminator. This value is never passed to the
        # resume classifier or scorer as candidate evidence.
        getattr(preference, "job_title", "") or "",
        getattr(preference, "skills", "") or "",
        str(getattr(preference, "updated_at", "") or getattr(preference, "created_at", "") or ""),
    ])
    resume_hash = content_hash(resume_text)
    job_hash = content_hash(job_text)
    resume_id = resume.id if resume else None
    # Stable current-analysis scope key (not only user+job).
    workflow_run_id = f"{user_id}:{job.id}:{resume_id or 0}:{resume_hash[:12]}:{job_hash[:12]}:{ANALYSIS_VERSION}"
    return {
        "user_id": user_id,
        "job_id": job.id,
        "resume_id": resume_id,
        "resume_hash": resume_hash,
        "job_hash": job_hash,
        "analysis_version": ANALYSIS_VERSION,
        "analysis_id": workflow_run_id,
        "workflow_run_id": workflow_run_id,
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
    identity = _analysis_identity(db, user_id, job, resume)
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
    identity = _analysis_identity(db, user_id, job, resume)
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
    if resume:
        score = calculate_weighted_ats_score(resume.parsed_text or "", job)
        grouped = group_canonical_score_evidence(score, job.id)
        return {**identity, **analysis, **grouped}
    enriched = merge_with_resume_evidence(analysis, "")
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


def _attach_confirmation_workflow_state(
    db: Session,
    *,
    user_id: int,
    job_id: int,
    resume: Resume | None,
    grouped: dict,
    workflow_run_id: str | None = None,
    resume_hash: str | None = None,
) -> dict:
    """Reconcile stale confirmations and attach generation readiness to analysis payload."""
    rows = reconcile_confirmations_with_analysis(
        db,
        user_id=user_id,
        job_id=job_id,
        resume_id=resume.id if resume else None,
        grouped_analysis=grouped,
        workflow_run_id=workflow_run_id or grouped.get("workflow_run_id"),
        resume_hash=resume_hash or grouped.get("resume_hash"),
    )
    readiness = evaluate_generation_readiness(grouped, rows)
    summary = summarize_active_evidence(grouped, rows)
    # Evidence gaps never block generation — keep can_generate_resume true for valid analysis.
    readiness["can_generate_resume"] = True
    readiness["blocking_reasons"] = []
    grouped.update(readiness)
    grouped["evidence_summary"] = summary
    grouped["evidence"] = summary["evidence"]
    grouped["evidence_history"] = summary.get("evidence_history") or []
    grouped["active_confirmation_counts"] = summary["counts"]
    warnings = readiness.get("generation_warnings") or []
    if warnings:
        skills = ", ".join(item["skill"] for item in warnings if item.get("skill"))
        grouped["generation_warning_message"] = (
            f"{skills} {'is' if len(warnings) == 1 else 'are'} not supported and will remain "
            "a gap. Generation can continue; unsupported requirements will be excluded."
        )
        # Legacy field: do not phrase as a hard block — UI treats this as a warning.
        grouped["generation_blocked_message"] = ""
    else:
        grouped["generation_warning_message"] = ""
        grouped["generation_blocked_message"] = ""
    return grouped


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
    identity = _analysis_identity(db, user_id, job, resume)
    jd_text = (job.description or "").strip()
    skills_field = (job.skills_required or "").strip()
    jd_length = len(jd_text) + len(skills_field)

    logger.info(
        "ANALYSIS_START user_id=%s resume_id=%s job_id=%s analysis_id=%s jd_len=%s resume_len=%s",
        user_id,
        identity.get("resume_id"),
        job_id,
        identity.get("analysis_id"),
        jd_length,
        len(resume_text),
    )

    analysis, cache_used = _resolve_requirement_analysis(
        db, user_id=user_id, job=job, resume=resume, force_refresh=force_refresh,
    )
    # Keep analysis for observability; scoring regenerates the authoritative requirements.
    score = calculate_weighted_ats_score(resume_text, job)
    requirements = score.get("requirements") or {}
    mandatory_count = len(requirements.get("required_core_skills") or [])
    preferred_count = len(requirements.get("preferred_skills") or [])
    or_group_count = len(requirements.get("or_groups") or [])

    # Empty extraction on a non-trivial JD is an analysis failure — never a valid 0/0/0/0 success.
    if jd_length >= 80 and mandatory_count == 0 and preferred_count == 0:
        logger.error(
            "REQUIREMENT_EXTRACTION_EMPTY user_id=%s job_id=%s resume_id=%s jd_len=%s cached_analysis_keys=%s",
            user_id,
            job_id,
            identity.get("resume_id"),
            jd_length,
            list((analysis or {}).keys())[:12],
        )
        return {
            **identity,
            "analysis_status": "REQUIREMENT_EXTRACTION_FAILED",
            "analysis_error": "We could not extract requirements from this job description. Please retry.",
            "cache_used": cache_used,
            "force_refresh": force_refresh,
            "resume_file_used": getattr(resume, "original_file_name", None) if resume else None,
            "resume_text_length": len(resume_text),
            "jd_text_length": jd_length,
            "verified_resume_skills": [],
            "weak_or_indirect_skills": [],
            "missing_mandatory_skills": [],
            "missing_preferred_skills": [],
            "skills": [],
            "summary": {
                "total_requirements": 0,
                "verified": 0,
                "weak_or_indirect": 0,
                "missing_mandatory": 0,
                "missing_preferred": 0,
            },
            "can_generate_resume": False,
            "blocking_reasons": [{"skill": "analysis", "reason": "Requirement extraction failed"}],
            "generation_blocked_message": "We could not extract requirements from this job description. Please retry.",
            "optimization_allowed": False,
            "eligibility_blocked_reason": "ANALYSIS_FAILED",
            "canonical_analysis": score,
        }

    grouped = group_canonical_score_evidence(score, job.id)
    summary = grouped.get("summary") or {}
    if (
        summary.get("total_requirements", 0) > 0
        and sum(summary.get(key, 0) for key in ("verified", "weak_or_indirect", "missing_mandatory", "missing_preferred")) == 0
    ):
        logger.error(
            "ANALYSIS_CONSISTENCY_ERROR user_id=%s job_id=%s summary=%s",
            user_id, job_id, summary,
        )
        grouped["analysis_status"] = "ANALYSIS_CONSISTENCY_FAILED"
        grouped["analysis_error"] = "Analysis produced inconsistent requirement counts. Please retry."
    else:
        grouped["analysis_status"] = "SUCCESS"

    for item in grouped.get("skills") or []:
        item["found_in_resume"] = bool(item.get("found") or item.get("found_in_resume"))
        item["resume_status"] = item.get("resume_status") or item.get("status")
    grouped.update(identity)
    grouped["cache_used"] = cache_used
    grouped["force_refresh"] = force_refresh
    grouped["resume_file_used"] = getattr(resume, "original_file_name", None) if resume else None
    grouped["resume_text_length"] = len(resume_text)
    grouped["jd_text_length"] = jd_length
    grouped["resume_employments"] = _resume_employments(resume_text)
    grouped["original_match_percentage"] = score.get("match_percentage")
    grouped["component_scores"] = score.get("component_details") or score.get("component_scores") or {}
    grouped["score_explanation"] = {
        "match_percentage": score.get("match_percentage"),
        "role_alignment": score.get("role_alignment"),
        "missing_mandatory_skills": score.get("missing_mandatory_skills") or [],
        "missing_preferred_skills": score.get("missing_preferred_skills") or [],
        "why_not_95": score.get("why_not_95"),
        "component_scores": score.get("component_details") or score.get("component_scores") or {},
    }
    logger.info(
        "ANALYSIS_COMPLETE user_id=%s job_id=%s resume_id=%s mandatory=%s preferred=%s or_groups=%s "
        "verified=%s possible=%s missing_m=%s missing_p=%s score=%s status=%s",
        user_id,
        job_id,
        identity.get("resume_id"),
        mandatory_count,
        preferred_count,
        or_group_count,
        summary.get("verified"),
        summary.get("weak_or_indirect"),
        summary.get("missing_mandatory"),
        summary.get("missing_preferred"),
        score.get("match_percentage"),
        grouped.get("analysis_status"),
    )
    return _attach_confirmation_workflow_state(
        db,
        user_id=user_id,
        job_id=job_id,
        resume=resume,
        grouped=grouped,
        workflow_run_id=identity.get("workflow_run_id"),
        resume_hash=identity.get("resume_hash"),
    )


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
    identity = _analysis_identity(db, user_id, job, resume)
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


@router.get("/eligibility/{user_id}/{job_id}")
def get_optimization_eligibility(user_id: int, job_id: int, db: Session = Depends(get_db)):
    _user, job = _load_user_and_job(db, user_id, job_id)
    resume = _latest_resume(db, user_id)
    if not resume:
        raise HTTPException(status_code=400, detail="Upload a resume before checking optimization eligibility.")
    jd_length = len((job.description or "").strip()) + len((job.skills_required or "").strip())
    score = calculate_weighted_ats_score(resume.parsed_text or "", job)
    requirements = score.get("requirements") or {}
    mandatory_count = len(requirements.get("required_core_skills") or [])
    preferred_count = len(requirements.get("preferred_skills") or [])
    if jd_length >= 80 and mandatory_count == 0 and preferred_count == 0:
        logger.error(
            "REQUIREMENT_EXTRACTION_EMPTY eligibility user_id=%s job_id=%s resume_id=%s jd_len=%s",
            user_id, job_id, resume.id, jd_length,
        )
        return {
            "optimization_allowed": False,
            "eligibility_status": "ANALYSIS_FAILED",
            "eligibility_tier": "ANALYSIS_FAILED",
            "eligibility_reason": "Analysis could not be completed. We could not extract requirements from this job description.",
            "reason": "Analysis could not be completed. We could not extract requirements from this job description.",
            "original_score": None,
            "analysis_status": "REQUIREMENT_EXTRACTION_FAILED",
            "resume_id": resume.id,
            "job_id": job.id,
            "jd_text_length": jd_length,
            "missing_mandatory_skills": [],
            "missing_preferred_skills": [],
            "component_scores": {},
            "requirement_evidence": [],
        }
    preference = db.query(UserPreference).filter(UserPreference.user_id == user_id).first()
    preference_role = classify_role("", getattr(preference, "job_title", "") or "")
    eligibility = role_aware_optimization_eligibility(score["match_percentage"], score.get("role_alignment"))
    resume_families = score.get("resume_role_families") or ["Other"]
    job_family = score.get("job_role_family") or "Other"
    alignment = score.get("role_alignment") or "UNKNOWN"
    allowed = eligibility["optimization_allowed"]
    eligibility_status = eligibility["eligibility_status"]
    if alignment == "EXACT":
        alignment_reason = f"Both resume and selected job are classified as {job_family}."
    elif alignment == "STRONG_RELATED":
        alignment_reason = f"{resume_families[0]} is strongly related to {job_family}."
    elif alignment == "MISMATCH":
        alignment_reason = f"{resume_families[0]} and {job_family} are different role families."
    else:
        alignment_reason = "Role-family evidence is partial or insufficient for a definitive classification."
    reason = eligibility["eligibility_reason"]
    if not allowed and score["match_percentage"] < 65 and alignment in {"EXACT", "STRONG_RELATED"}:
        if not score.get("missing_mandatory_skills") and score.get("weak_core_skills"):
            reason = "Your resume contains the required technologies, but much of the evidence is limited to skill-list mentions rather than detailed professional usage."
        elif not score.get("missing_mandatory_skills"):
            reason = "The required technologies are present, but other score components such as responsibility or experience evidence remain below their maximums."
        else:
            reason = f"Role alignment is {alignment.lower().replace('_', ' ')}, but ATS score is below the 65% threshold."
    return {
        **eligibility,
        "eligibility_reason": reason,
        "reason": reason,
        "eligibility_status": eligibility_status,
        "analysis_status": "SUCCESS",
        "original_score": score["match_percentage"],
        "resume_id": resume.id,
        "job_id": job.id,
        "resume_role_families": resume_families,
        "resume_role_family": score.get("resume_role_family_id") or "UNKNOWN",
        "resume_role_family_display": resume_families[0],
        "resume_role_confidence": score.get("resume_role_confidence"),
        "job_role_family": score.get("job_role_family_id") or "UNKNOWN",
        "job_role_family_display": job_family,
        "target_job_title": job.title,
        "job_role_confidence": score.get("job_role_confidence"),
        "preference_role_family": preference_role["family_id"],
        "preference_role_confidence": preference_role["confidence"],
        "role_alignment": alignment,
        "role_alignment_reason": alignment_reason,
        "missing_core_skills": score.get("missing_core_skills") or [],
        "missing_mandatory_skills": score.get("missing_mandatory_skills") or [],
        "missing_preferred_skills": score.get("missing_preferred_skills") or [],
        "component_scores": score.get("component_details") or {},
        "education_category_applicable": score.get("education_category_applicable", True),
        "strong_evidence_skills": [item["skill"] for item in score.get("requirement_evidence") or [] if item.get("evidence_strength") == "strong"],
        "weak_evidence_skills": [item["skill"] for item in score.get("requirement_evidence") or [] if item.get("evidence_strength") in {"weak", "medium"}],
        "score_cap": score.get("score_cap_applied"),
        "score_cap_reason": score.get("score_cap_reason"),
        "requirement_evidence": score.get("requirement_evidence") or [],
        "diagnostic_warnings": score.get("diagnostic_warnings") or [],
        "major_mismatch_reasons": (
            score.get("role_mismatch_reasons") or ([score.get("warning_message")] if score.get("warning_message") else [])
            if alignment == "MISMATCH" else []
        ),
        "suggested_role_family": score.get("suggested_role_family") or resume_families[0],
        "role_classification": score.get("role_classification") or {},
        "gap_analysis": {
            "missing_keywords": score.get("missing_keywords") or [],
            "suggestions": score.get("suggestions") or [],
        },
        "jd_text_length": jd_length,
    }


@router.get("/evidence/{user_id}/{job_id}")
def get_evidence(user_id: int, job_id: int, db: Session = Depends(get_db)):
    _user, job = _load_user_and_job(db, user_id, job_id)
    resume = _latest_resume(db, user_id)
    resume_text = (resume.parsed_text if resume else "") or ""
    identity = _analysis_identity(db, user_id, job, resume)
    score = calculate_weighted_ats_score(resume_text, job)
    grouped = group_canonical_score_evidence(score, job.id)
    grouped.update(identity)
    grouped = _attach_confirmation_workflow_state(
        db,
        user_id=user_id,
        job_id=job_id,
        resume=resume,
        grouped=grouped,
        workflow_run_id=identity.get("workflow_run_id"),
        resume_hash=identity.get("resume_hash"),
    )
    return {
        "user_id": user_id,
        "job_id": job_id,
        "resume_id": identity.get("resume_id"),
        "analysis_id": identity.get("analysis_id"),
        "workflow_run_id": identity.get("workflow_run_id"),
        "evidence": grouped.get("evidence") or [],
        "evidence_history": grouped.get("evidence_history") or [],
        "evidence_summary": grouped.get("evidence_summary") or {},
        "can_generate_resume": grouped.get("can_generate_resume", False),
        "blocking_reasons": grouped.get("blocking_reasons") or [],
        "generation_warnings": grouped.get("generation_warnings") or [],
        "current_required_confirmations": grouped.get("current_required_confirmations") or [],
        "current_optional_confirmations": grouped.get("current_optional_confirmations") or [],
        "preferred_unresolved": grouped.get("preferred_unresolved") or [],
        "generation_blocked_message": grouped.get("generation_blocked_message") or "",
        "generation_warning_message": grouped.get("generation_warning_message") or "",
        "active_confirmation_counts": grouped.get("active_confirmation_counts") or {},
    }


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

    identity = _analysis_identity(db, user_id, job, resume)
    cached_analysis = load_requirement_analysis(
        db, **{k: identity[k] for k in ("user_id", "job_id", "resume_id", "resume_hash", "job_hash")}
    )
    analysis = cached_analysis or extract_job_requirement_analysis(job)
    enriched = merge_with_resume_evidence(analysis, resume.parsed_text if resume else "")
    resume_evidence = enriched.get("resume_evidence") or {}
    known_employers = _known_employers(resume.parsed_text if resume else "")

    try:
        saved_rows = save_evidence(
            db,
            user_id=user_id,
            job_id=job_id,
            resume_id=resume.id if resume else None,
            items=items,
            resume_evidence=resume_evidence,
            known_employers=known_employers,
            workflow_run_id=identity.get("workflow_run_id"),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    score = calculate_weighted_ats_score(resume.parsed_text if resume else "", job)
    grouped = group_canonical_score_evidence(score, job.id)
    grouped.update(identity)
    grouped = _attach_confirmation_workflow_state(
        db,
        user_id=user_id,
        job_id=job_id,
        resume=resume,
        grouped=grouped,
        workflow_run_id=identity.get("workflow_run_id"),
        resume_hash=identity.get("resume_hash"),
    )
    saved_effective = [
        {
            "skill": row.skill_name,
            "normalized_skill": normalize_skill_name(row.normalized_skill_name or row.skill_name or ""),
            "effective_status": row.evidence_status,
            "evidence_id": row.id,
            "resume_id": row.resume_id,
            "is_active": bool(getattr(row, "is_active", True)),
            "workflow_run_id": getattr(row, "workflow_run_id", None),
        }
        for row in saved_rows
    ]
    return {
        "message": "Evidence saved.",
        "user_id": user_id,
        "job_id": job_id,
        "resume_id": identity.get("resume_id"),
        "analysis_id": identity.get("analysis_id"),
        "workflow_run_id": identity.get("workflow_run_id"),
        "evidence": grouped.get("evidence") or [],
        "evidence_history": grouped.get("evidence_history") or [],
        "evidence_summary": grouped.get("evidence_summary") or {},
        "can_generate_resume": grouped.get("can_generate_resume", False),
        "blocking_reasons": grouped.get("blocking_reasons") or [],
        "generation_warnings": grouped.get("generation_warnings") or [],
        "current_required_confirmations": grouped.get("current_required_confirmations") or [],
        "current_optional_confirmations": grouped.get("current_optional_confirmations") or [],
        "preferred_unresolved": grouped.get("preferred_unresolved") or [],
        "generation_blocked_message": grouped.get("generation_blocked_message") or "",
        "generation_warning_message": grouped.get("generation_warning_message") or "",
        "active_confirmation_counts": grouped.get("active_confirmation_counts") or {},
        "saved": saved_effective,
        "effective_status": (saved_effective[0]["effective_status"] if len(saved_effective) == 1 else None),
    }


@router.delete("/evidence/{user_id}/{job_id}/{evidence_id}")
def delete_evidence(user_id: int, job_id: int, evidence_id: int, db: Session = Depends(get_db)):
    _user, job = _load_user_and_job(db, user_id, job_id)
    removed = delete_confirmed_skill(db, user_id=user_id, job_id=job_id, evidence_id=evidence_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Evidence not found for this user and job.")
    resume = _latest_resume(db, user_id)
    identity = _analysis_identity(db, user_id, job, resume)
    score = calculate_weighted_ats_score((resume.parsed_text if resume else "") or "", job)
    grouped = group_canonical_score_evidence(score, job.id)
    grouped.update(identity)
    grouped = _attach_confirmation_workflow_state(
        db,
        user_id=user_id,
        job_id=job_id,
        resume=resume,
        grouped=grouped,
        workflow_run_id=identity.get("workflow_run_id"),
        resume_hash=identity.get("resume_hash"),
    )
    return {
        "message": "Evidence removed.",
        "evidence_id": evidence_id,
        "evidence": grouped.get("evidence") or [],
        "evidence_history": grouped.get("evidence_history") or [],
        "evidence_summary": grouped.get("evidence_summary") or {},
        "can_generate_resume": grouped.get("can_generate_resume", False),
        "blocking_reasons": grouped.get("blocking_reasons") or [],
        "generation_warnings": grouped.get("generation_warnings") or [],
        "current_required_confirmations": grouped.get("current_required_confirmations") or [],
        "current_optional_confirmations": grouped.get("current_optional_confirmations") or [],
        "generation_blocked_message": grouped.get("generation_blocked_message") or "",
        "generation_warning_message": grouped.get("generation_warning_message") or "",
        "active_confirmation_counts": grouped.get("active_confirmation_counts") or {},
    }


@router.post("/generate/{user_id}/{job_id}")
def generate_ats_optimization(
    user_id: int,
    job_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    force: bool = False,
):
    """Start ATS resume generation for the CURRENT user/job/resume/evidence state."""
    logger.info("GENERATION_REQUEST_RECEIVED user_id=%s job_id=%s force=%s", user_id, job_id, force)
    _user, job = _load_user_and_job(db, user_id, job_id)
    resume = _latest_resume(db, user_id)
    if not resume:
        raise HTTPException(
            status_code=400,
            detail={
                "can_generate_resume": False,
                "blocking_reasons": [{"code": "RESUME_MISSING", "reason": "Upload a resume before generating."}],
                "message": "Upload a resume before generating.",
            },
        )

    identity = _analysis_identity(db, user_id, job, resume)
    resume_text = (resume.parsed_text or "") or ""
    score = calculate_weighted_ats_score(resume_text, job)
    grouped = group_canonical_score_evidence(score, job.id)
    grouped.update(identity)
    grouped = _attach_confirmation_workflow_state(
        db,
        user_id=user_id,
        job_id=job_id,
        resume=resume,
        grouped=grouped,
        workflow_run_id=identity.get("workflow_run_id"),
        resume_hash=identity.get("resume_hash"),
    )
    eligibility = role_aware_optimization_eligibility(
        score.get("match_percentage") or 0,
        score.get("role_alignment"),
    )

    logger.info(
        "GENERATE REQUEST user_id=%s job_id=%s resume_id=%s analysis_id=%s "
        "score=%s eligibility=%s can_generate_resume=%s blockers=%s force=%s",
        user_id,
        job_id,
        resume.id,
        identity.get("analysis_id"),
        score.get("match_percentage"),
        eligibility.get("eligibility_status"),
        grouped.get("can_generate_resume"),
        [item.get("skill") for item in (grouped.get("blocking_reasons") or [])],
        force,
    )

    if not eligibility.get("optimization_allowed"):
        code = (
            "INELIGIBLE_LOW_SCORE"
            if eligibility.get("eligibility_status") == "INELIGIBLE_LOW_SCORE"
            else "INELIGIBLE_ROLE_ALIGNMENT"
        )
        raise HTTPException(
            status_code=400,
            detail={
                "can_generate_resume": False,
                "blocking_reasons": [{
                    "code": code,
                    "reason": eligibility.get("eligibility_reason"),
                    "original_score": eligibility.get("original_score"),
                    "minimum_required_score": eligibility.get("minimum_required_score"),
                }],
                "eligibility_status": eligibility.get("eligibility_status"),
                "original_score": eligibility.get("original_score"),
                "message": (
                    f"Current score {eligibility.get('original_score')}%. "
                    f"Minimum required {eligibility.get('minimum_required_score')}%."
                    if code == "INELIGIBLE_LOW_SCORE"
                    else (eligibility.get("eligibility_reason") or "Optimization not allowed for this role alignment.")
                ),
            },
        )

    # Mandatory evidence gaps are warnings only — never HTTP 400 blockers.
    # True blockers remain: missing resume (above), role eligibility (above),
    # and downstream workflow/system failures.
    if grouped.get("generation_warnings"):
        logger.info(
            "GENERATE WITH GAPS user_id=%s job_id=%s warnings=%s",
            user_id,
            job_id,
            [item.get("skill") for item in grouped.get("generation_warnings") or []],
        )

    try:
        decision = start_or_get_workflow_with_decision(
            db,
            user_id=user_id,
            job_id=job_id,
            force=force,
        )
        workflow = decision.workflow
    except HTTPException:
        raise
    except Exception as error:
        try:
            db.rollback()
        except Exception:
            pass
        logger.exception(
            "DATABASE_DATA_ERROR stage=ATS_WORKFLOW_START user_id=%s job_id=%s resume_id=%s",
            user_id, job_id, resume.id,
        )
        orig = getattr(error, "orig", None)
        pgcode = getattr(orig, "pgcode", None) or getattr(orig, "sqlstate", None)
        detail = {
            "error_code": "DATABASE_DATA_ERROR",
            "stage": "ATS_WORKFLOW_START",
            "can_generate_resume": True,
            "message": (
                "Resume generation could not be saved due to a database data error. "
                "Please retry Generate Optimized Resume. "
                f"(DATABASE_DATA_ERROR · ATS_WORKFLOW_START · {type(error).__name__})"
            ),
        }
        if pgcode:
            detail["db_code"] = str(pgcode)
        raise HTTPException(status_code=500, detail=detail) from error

    logger.info(
        "ATS_GENERATE_REQUEST workflow_id=%s user_id=%s job_id=%s resume_id=%s "
        "created_new=%s reuse_reason=%s force=%s status=%s",
        workflow.id,
        user_id,
        job_id,
        resume.id,
        decision.created_new,
        decision.reuse_reason or "none",
        force,
        workflow.workflow_status,
    )

    # Always (re)schedule PENDING work. Fresh PENDING after restart must not sit idle.
    if workflow.workflow_status == "PENDING":
        logger.info("ATS_BACKGROUND_TASK_SUBMITTED workflow_id=%s", workflow.id)
        background_tasks.add_task(run_workflow_job, workflow.id)
    elif not decision.created_new and workflow.workflow_status in {
        "ANALYZING_ORIGINAL",
        "GENERATING_RESUME",
        "VALIDATING_RESUME",
        "ANALYZING_OPTIMIZED",
    }:
        logger.info(
            "ATS_GENERATE_REUSED_IN_FLIGHT workflow_id=%s status=%s reuse_reason=%s",
            workflow.id,
            workflow.workflow_status,
            decision.reuse_reason,
        )

    result = workflow_to_dict(workflow, job)
    poll = generation_timeout_config()
    status = "accepted"
    if workflow.workflow_status == "COMPLETED":
        status = "generated"
    return {
        "message": "ATS optimization workflow accepted",
        "status": status,
        "workflow_id": workflow.id,
        "created_new": decision.created_new,
        "reuse_reason": decision.reuse_reason,
        "workflow_status": workflow.workflow_status,
        "poll_interval_ms": poll["poll_interval_ms"],
        "poll_timeout_seconds": poll["poll_timeout_seconds"],
        "optimized_resume_id": workflow.optimized_resume_id,
        "generation_method": workflow.generation_method,
        "llm_used": bool(workflow.llm_used),
        "fallback_used": bool(workflow.fallback_used),
        "validation_status": (result.get("final_validation") or {}).get("status") or "pending",
        "next_step": "validate_resume" if workflow.workflow_status == "COMPLETED" else "poll_status",
        "can_generate_resume": True,
        "blocking_reasons": [],
        "resume_id": resume.id,
        "analysis_id": identity.get("analysis_id"),
        "workflow_run_id": identity.get("workflow_run_id"),
        "original_score": score.get("match_percentage"),
        "eligibility_status": eligibility.get("eligibility_status"),
        "result": result,
    }


@router.get("/status/{workflow_id}")
def get_workflow_status(workflow_id: int, db: Session = Depends(get_db)):
    workflow = db.query(AtsOptimizationWorkflow).filter(AtsOptimizationWorkflow.id == workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    job = db.query(Job).filter(Job.id == workflow.job_id).first()
    return workflow_to_dict(workflow, job)


@router.get("/result/{user_id}/{job_id}")
def get_result_by_job(
    user_id: int,
    job_id: int,
    workflow_id: int | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(AtsOptimizationWorkflow).filter(
        AtsOptimizationWorkflow.user_id == user_id,
        AtsOptimizationWorkflow.job_id == job_id,
    )
    if workflow_id is not None:
        workflow = query.filter(AtsOptimizationWorkflow.id == workflow_id).first()
    else:
        workflow = query.order_by(AtsOptimizationWorkflow.id.desc()).first()
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
    poll = generation_timeout_config()
    return {
        "message": "ATS workflow retry started",
        "workflow_id": new_workflow.id,
        "workflow_status": new_workflow.workflow_status,
        "poll_interval_ms": poll["poll_interval_ms"],
        "poll_timeout_seconds": poll["poll_timeout_seconds"],
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
