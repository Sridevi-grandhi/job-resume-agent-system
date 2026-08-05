"""
ATS Analyzer & Resume Optimizer — orchestration service.

Coordinates original analysis, truthful optimization, optimized analysis,
validation, and persistence. Does not duplicate Job Search agents.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.job_model import Job
from app.models.match_model import MatchScore
from app.models.optimized_resume_model import OptimizedResume
from app.models.resume_model import Resume
from app.models.user_model import User
from app.services.ats_scoring import calculate_weighted_ats_score
from app.services.client_domain_validator import validate_client_domain
from app.services.final_resume_validator import validate_final_resume
from app.services.job_eligibility import job_is_analyzable
from app.services.keyword_bold_formatter import bold_important_keywords
from app.services.keyword_injection_service import plan_keyword_injection
from app.services.resume_generator import generate_optimized_resume
from app.services.resume_structure_parser import parse_resume_structure


logger = logging.getLogger(__name__)


def _analysis_payload(score_result: dict) -> dict:
    return {
        "match_percentage": score_result["match_percentage"],
        "total_score": score_result.get("total_score", score_result["match_percentage"]),
        "score_breakdown": score_result.get("score_breakdown", {}),
        "matched_keywords": score_result.get("matched_keywords", []),
        "missing_keywords": score_result.get("missing_keywords", []),
        "suggestions": score_result.get("suggestions", []),
        "recommendation": score_result.get("recommendation", ""),
        "score_status": score_result.get("score_status"),
        "confidence": score_result.get("confidence"),
        "role_alignment": score_result.get("role_alignment"),
        "resume_role_families": score_result.get("resume_role_families", []),
        "job_role_family": score_result.get("job_role_family"),
        "component_scores": score_result.get("component_scores", {}),
        "score_cap_applied": score_result.get("score_cap_applied"),
        "score_cap_reason": score_result.get("score_cap_reason"),
        "matched_core_skills": score_result.get("matched_core_skills", []),
        "matched_transferable_skills": score_result.get("matched_transferable_skills", []),
        "missing_mandatory_skills": score_result.get("missing_mandatory_skills", []),
        "warning_message": score_result.get("warning_message", ""),
        "safe_to_apply": score_result.get("safe_to_apply", False),
    }


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
    query = (
        db.query(MatchScore)
        .filter(
            MatchScore.user_id == user_id,
            MatchScore.resume_id == resume_id,
            MatchScore.job_id == job_id,
            MatchScore.resume_version == resume_version,
        )
    )
    existing = query.first()
    suggestions_text = "\n".join(score_result.get("suggestions") or [])
    breakdown_json = json.dumps(score_result.get("score_breakdown") or {})
    matched = ", ".join(score_result.get("matched_keywords") or [])
    missing = ", ".join(score_result.get("missing_keywords") or [])

    if existing:
        existing.match_percentage = score_result["match_percentage"]
        existing.matched_keywords = matched
        existing.missing_keywords = missing
        existing.recommendation = score_result.get("recommendation")
        existing.suggestions = suggestions_text
        existing.score_breakdown = breakdown_json
        existing.optimized_resume_id = optimized_resume_id
        db.add(existing)
        db.flush()
        return existing

    record = MatchScore(
        user_id=user_id,
        resume_id=resume_id,
        job_id=job_id,
        match_percentage=score_result["match_percentage"],
        matched_keywords=matched,
        missing_keywords=missing,
        recommendation=score_result.get("recommendation"),
        resume_version=resume_version,
        optimized_resume_id=optimized_resume_id,
        score_breakdown=breakdown_json,
        suggestions=suggestions_text,
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
    optimized_text: str,
    optimization_notes: str,
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
        existing.optimized_text = optimized_text
        existing.optimization_notes = optimization_notes
        db.add(existing)
        db.flush()
        return existing

    record = OptimizedResume(
        user_id=user_id,
        resume_id=resume_id,
        job_id=job_id,
        optimized_text=optimized_text,
        optimization_notes=optimization_notes,
    )
    db.add(record)
    db.flush()
    return record


def analyze_and_optimize_resume(
    user_id: int,
    job_id: int,
    db: Session,
    resume_id: int | None = None,
) -> dict[str, Any]:
    started = time.time()

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if resume_id:
        resume = (
            db.query(Resume)
            .filter(Resume.id == resume_id, Resume.user_id == user_id)
            .first()
        )
    else:
        resume = (
            db.query(Resume)
            .filter(Resume.user_id == user_id)
            .order_by(Resume.uploaded_at.desc())
            .first()
        )

    if not resume:
        raise HTTPException(
            status_code=404,
            detail="No resume found for this user. Please upload a resume first.",
        )

    job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found for this user")

    eligible, reason = job_is_analyzable(job, require_url=False)
    if not eligible:
        raise HTTPException(status_code=400, detail=reason)

    resume_text = resume.parsed_text or ""
    if not resume_text.strip():
        raise HTTPException(status_code=400, detail="Resume text is empty or could not be parsed.")

    # 1) Original analysis
    original_score = calculate_weighted_ats_score(resume_text, job)

    # 2) Structure + keyword plan
    structure = parse_resume_structure(resume_text)
    injection_plan = plan_keyword_injection(
        resume_text=resume_text,
        job=job,
        employments=structure.get("employments") or [],
        projects=structure.get("projects") or [],
    )

    # 3) Generate optimized resume (plain)
    generated = generate_optimized_resume(resume_text, job, injection_plan)
    plain_resume = generated["plain_text"]

    # 4) Client domain validation on generated text
    client_validation = validate_client_domain(
        employments=structure.get("employments") or [],
        projects=structure.get("projects") or [],
        generated_text=plain_resume,
    )

    # 5) Final validation — do not save on critical failure
    unsupported_requirements = list(dict.fromkeys(
        (injection_plan.get("unsupportedKeywords") or [])
        + (original_score.get("missing_mandatory_skills") or [])
    ))
    final_validation = validate_final_resume(
        original_text=resume_text,
        generated_text=plain_resume,
        original_structure=structure,
        generated_sections=generated.get("sections") or [],
        unsupported_keywords=unsupported_requirements,
        selected_technologies=injection_plan.get("supportedKeywords") or [],
    )
    if final_validation["status"] == "failed":
        logger.error(
            "ATS validation failed user_id=%s resume_id=%s job_id=%s errors=%s",
            user_id,
            resume.id,
            job.id,
            len(final_validation.get("errors") or []),
        )
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Optimized resume failed validation and was not saved",
                "final_validation": final_validation,
            },
        )

    # 6) Optimized analysis on plain text (no markdown)
    optimized_score = calculate_weighted_ats_score(
        plain_resume,
        job,
        evidence_source_text=resume_text,
    )
    score_improvement = (
        optimized_score["match_percentage"] - original_score["match_percentage"]
    )

    # 7) Preview markdown with bold keywords
    markdown_preview = bold_important_keywords(
        plain_resume,
        injection_plan.get("keywordsInjected") or injection_plan.get("supportedKeywords") or [],
    )

    notes = {
        "original_score": original_score["match_percentage"],
        "optimized_score": optimized_score["match_percentage"],
        "score_improvement": score_improvement,
        "selected_technologies": injection_plan.get("supportedKeywords") or [],
        "rejected_technologies": injection_plan.get("rejectedTechnologies") or [],
        "keyword_warnings": injection_plan.get("keywordWarnings") or [],
        "final_validation": final_validation,
        "markdown_preview": markdown_preview,
        "role_alignment": original_score.get("role_alignment"),
        "optimization_allowed": original_score.get("optimization_allowed", False),
        "safe_to_apply": optimized_score.get("safe_to_apply", False),
        "unsupported_requirements": unsupported_requirements,
    }
    optimization_notes = json.dumps(notes)

    # 8) Persist optimized resume (upsert by user/resume/job)
    optimized_resume = _upsert_optimized_resume(
        db,
        user_id=user_id,
        resume_id=resume.id,
        job_id=job.id,
        optimized_text=plain_resume,
        optimization_notes=optimization_notes,
    )

    # 9) Persist both scores
    _upsert_match_score(
        db,
        user_id=user_id,
        resume_id=resume.id,
        job_id=job.id,
        score_result=original_score,
        resume_version="original",
        optimized_resume_id=None,
    )
    _upsert_match_score(
        db,
        user_id=user_id,
        resume_id=resume.id,
        job_id=job.id,
        score_result=optimized_score,
        resume_version="optimized",
        optimized_resume_id=optimized_resume.id,
    )

    db.commit()
    db.refresh(optimized_resume)

    duration_ms = int((time.time() - started) * 1000)
    logger.info(
        "ATS analyze-optimize complete user_id=%s resume_id=%s job_id=%s "
        "original=%s optimized=%s improvement=%s selected=%s rejected=%s "
        "validation=%s duration_ms=%s saved=1",
        user_id,
        resume.id,
        job.id,
        original_score["match_percentage"],
        optimized_score["match_percentage"],
        score_improvement,
        len(injection_plan.get("supportedKeywords") or []),
        len(injection_plan.get("rejectedTechnologies") or []),
        final_validation["status"],
        duration_ms,
    )

    return {
        "message": (
            "Optimization completed with major alignment warnings"
            if not original_score.get("optimization_allowed", False)
            else "ATS analysis and resume optimization completed successfully"
        ),
        "user_id": user_id,
        "job_id": job.id,
        "resume_id": resume.id,
        "resume_file_name": resume.original_file_name,
        "optimized_resume_id": optimized_resume.id,
        "job": {
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "source": job.source,
            "job_url": job.job_url,
        },
        "original_analysis": _analysis_payload(original_score),
        "optimized_analysis": _analysis_payload(optimized_score),
        "role_alignment": original_score.get("role_alignment"),
        "resume_role_families": original_score.get("resume_role_families", []),
        "target_job_role": original_score.get("job_role_family"),
        "optimization_allowed": original_score.get("optimization_allowed", False),
        "safe_to_apply": optimized_score.get("safe_to_apply", False),
        "score_status": optimized_score.get("score_status"),
        "critical_missing_skills": original_score.get("missing_mandatory_skills", []),
        "matched_core_skills": original_score.get("matched_core_skills", []),
        "matched_transferable_skills": original_score.get("matched_transferable_skills", []),
        "warning": original_score.get("warning_message", ""),
        "gap_analysis": {
            "missing_mandatory_skills": original_score.get("missing_mandatory_skills", []),
            "weak_core_skills": original_score.get("weak_core_skills", []),
            "score_cap": original_score.get("score_cap_applied"),
            "score_cap_reason": original_score.get("score_cap_reason"),
            "recommendation": original_score.get("recommendation"),
        },
        "score_improvement": score_improvement,
        "technology_validation": injection_plan.get("technology_validation") or {
            "selectedTechnologies": injection_plan.get("supportedKeywords") or [],
            "rejectedTechnologies": injection_plan.get("rejectedTechnologies") or [],
            "conflictWarnings": [],
        },
        "client_validation": client_validation,
        "timeline_validation": injection_plan.get("timeline_validation") or {
            "timelineWarnings": [],
            "technologiesNotAddedDueToTimeline": [],
            "technologyTimelineValidationStatus": "passed",
        },
        "keyword_analysis": {
            "requiredKeywords": injection_plan.get("requiredKeywords") or [],
            "preferredKeywords": injection_plan.get("preferredKeywords") or [],
            "supportedKeywords": injection_plan.get("supportedKeywords") or [],
            "unsupportedKeywords": injection_plan.get("unsupportedKeywords") or [],
            "keywordsInjected": injection_plan.get("keywordsInjected") or [],
            "keywordWarnings": injection_plan.get("keywordWarnings") or [],
        },
        "final_validation": final_validation,
        "generated_resume": plain_resume,
        "generated_resume_preview_markdown": markdown_preview,
        "original_resume": resume_text,
    }
