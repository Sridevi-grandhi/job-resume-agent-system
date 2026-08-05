"""
ResumeOptimizationMatchService

One orchestration service that coordinates the existing ATS scoring + resume
generation components. Does not duplicate Job Search agents or create parallel
optimizer/matching systems.

Used by:
- automatic post-save flow for selected / URL-imported jobs
- POST /api/resume/optimize-and-match/{user_id}/{job_id}
- POST /api/ats/analyze-optimize/{user_id}/{job_id}
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.job_model import Job
from app.models.resume_model import Resume
from app.services.ats_analyzer_service import analyze_and_optimize_resume
from app.services.job_eligibility import job_is_analyzable


logger = logging.getLogger(__name__)


def optimize_and_match(
    user_id: int,
    job_id: int,
    db: Session,
    resume_id: int | None = None,
) -> dict[str, Any]:
    """
    Run the full combined workflow and raise HTTPException on hard failures.
    Reuses analyze_and_optimize_resume (existing ATS Analyzer orchestration).
    """
    result = analyze_and_optimize_resume(
        user_id=user_id,
        job_id=job_id,
        db=db,
        resume_id=resume_id,
    )
    return _to_combined_response(result)


def optimize_and_match_safe(
    user_id: int,
    job_id: int,
    db: Session,
) -> dict[str, Any]:
    """
    Soft-fail wrapper for post-save automation.

    The caller must already have committed the job. This never rolls back the job.
    """
    resume = (
        db.query(Resume)
        .filter(Resume.user_id == user_id)
        .order_by(Resume.uploaded_at.desc())
        .first()
    )
    if not resume or not (resume.parsed_text or "").strip():
        return {
            "job_saved": True,
            "optimization_completed": False,
            "message": "Job saved. Upload a resume before saving and optimizing jobs.",
            "reason": "Upload a resume before saving and optimizing jobs.",
            "analysis": None,
        }

    job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
    if not job:
        return {
            "job_saved": True,
            "optimization_completed": False,
            "message": "Job saved. Job could not be reloaded for optimization.",
            "reason": "Job not found after save.",
            "analysis": None,
        }

    eligible, reason = job_is_analyzable(job, require_url=False)
    if not eligible:
        return {
            "job_saved": True,
            "optimization_completed": False,
            "message": f"Job saved. {reason}",
            "reason": reason,
            "analysis": None,
        }

    try:
        analysis = optimize_and_match(user_id=user_id, job_id=job_id, db=db)
        return {
            "job_saved": True,
            "optimization_completed": True,
            "message": "Selected job saved, resume optimized, and both match scores calculated.",
            "reason": None,
            "analysis": {
                "original_match_percentage": analysis["original_match_percentage"],
                "optimized_match_percentage": analysis["optimized_match_percentage"],
                "improvement_percentage": analysis["improvement_percentage"],
                "optimized_resume_id": analysis["optimized_resume_id"],
                "validation_warnings": analysis.get("validation_warnings") or [],
                "original_matched_keywords": analysis.get("original_matched_keywords") or [],
                "original_missing_keywords": analysis.get("original_missing_keywords") or [],
                "optimized_matched_keywords": analysis.get("optimized_matched_keywords") or [],
                "optimized_missing_keywords": analysis.get("optimized_missing_keywords") or [],
            },
            "full_analysis": analysis,
        }
    except HTTPException as error:
        detail = error.detail
        if isinstance(detail, dict):
            reason_text = detail.get("message") or str(detail)
        else:
            reason_text = str(detail)
        logger.error(
            "Optimize-and-match soft failure user_id=%s job_id=%s status=%s",
            user_id,
            job_id,
            error.status_code,
        )
        return {
            "job_saved": True,
            "optimization_completed": False,
            "message": f"Job saved. Optimization did not complete: {reason_text}",
            "reason": reason_text,
            "analysis": None,
        }
    except Exception:
        logger.exception(
            "Optimize-and-match unexpected failure user_id=%s job_id=%s",
            user_id,
            job_id,
        )
        return {
            "job_saved": True,
            "optimization_completed": False,
            "message": "Job saved. Optimization failed due to an unexpected server error.",
            "reason": "Unexpected optimization error.",
            "analysis": None,
        }


def _to_combined_response(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize ATS Analyzer output into the combined optimize-and-match contract."""
    original = result.get("original_analysis") or {}
    optimized = result.get("optimized_analysis") or {}
    final_validation = result.get("final_validation") or {}
    keyword_analysis = result.get("keyword_analysis") or {}
    technology = result.get("technology_validation") or {}
    client = result.get("client_validation") or {}
    timeline = result.get("timeline_validation") or {}

    warnings = []
    warnings.extend(keyword_analysis.get("keywordWarnings") or [])
    warnings.extend(technology.get("conflictWarnings") or [])
    warnings.extend(client.get("clientDomainWarnings") or [])
    warnings.extend(timeline.get("timelineWarnings") or [])
    warnings.extend(final_validation.get("warnings") or [])

    improvement = int(result.get("score_improvement") or 0)
    original_score = int(original.get("match_percentage") or 0)
    optimized_score = int(optimized.get("match_percentage") or 0)
    if optimized_score < original_score:
        warnings.append(
            "Optimized score did not improve over the original score. "
            "Truthful keyword limits may prevent further gains."
        )

    return {
        "message": result.get("message") or "Resume optimized and both match scores calculated.",
        "job_id": result.get("job_id"),
        "original_resume_id": result.get("resume_id"),
        "resume_file_name": result.get("resume_file_name"),
        "optimized_resume_id": result.get("optimized_resume_id"),
        "job": result.get("job") or {},
        "original_match_percentage": original_score,
        "optimized_match_percentage": optimized_score,
        "improvement_percentage": improvement,
        "original_matched_keywords": original.get("matched_keywords") or [],
        "original_missing_keywords": original.get("missing_keywords") or [],
        "optimized_matched_keywords": optimized.get("matched_keywords") or [],
        "optimized_missing_keywords": optimized.get("missing_keywords") or [],
        "optimization_notes": (original.get("suggestions") or []) + (optimized.get("suggestions") or []),
        "validation_warnings": list(dict.fromkeys(warnings)),
        "recommendations": optimized.get("suggestions") or original.get("suggestions") or [],
        "technology_validation": technology,
        "client_validation": client,
        "timeline_validation": timeline,
        "keyword_analysis": keyword_analysis,
        "final_validation": final_validation,
        "score_breakdown": {
            "original": original.get("score_breakdown") or {},
            "optimized": optimized.get("score_breakdown") or {},
        },
        "generated_resume": {
            "plain_text": result.get("generated_resume") or "",
            "formatted_preview": result.get("generated_resume_preview_markdown")
            or result.get("generated_resume")
            or "",
        },
        "original_resume": result.get("original_resume") or "",
        # Keep nested analysis for the existing ATS Analyzer page.
        "original_analysis": original,
        "optimized_analysis": optimized,
        "score_improvement": improvement,
    }


# Public aliases matching the requested service naming.
ResumeOptimizationMatchService = type(
    "ResumeOptimizationMatchService",
    (),
    {
        "optimize_and_match": staticmethod(optimize_and_match),
        "optimize_and_match_safe": staticmethod(optimize_and_match_safe),
    },
)
