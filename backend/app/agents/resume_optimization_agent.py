"""
Resume Optimization Agent

Backward-compatible wrapper around the ATS Analyzer generator.
Existing /api/resume/optimize endpoint continues to work.
"""

from app.services.ats_scoring import calculate_weighted_ats_score
from app.services.keyword_injection_service import plan_keyword_injection
from app.services.resume_generator import generate_optimized_resume
from app.services.resume_structure_parser import parse_resume_structure


def optimize_resume_for_job(resume_text: str, job):
    structure = parse_resume_structure(resume_text or "")
    injection_plan = plan_keyword_injection(
        resume_text=resume_text or "",
        job=job,
        employments=structure.get("employments") or [],
        projects=structure.get("projects") or [],
    )
    generated = generate_optimized_resume(resume_text or "", job, injection_plan)
    plain = generated["plain_text"]

    original = calculate_weighted_ats_score(resume_text or "", job)
    # The optimized document may surface evidence, but the original resume remains
    # the only source allowed to prove a candidate claim.
    optimized = calculate_weighted_ats_score(
        plain,
        job,
        evidence_source_text=resume_text or "",
    )

    matched_keywords = optimized.get("matched_keywords") or []
    missing_keywords = optimized.get("missing_keywords") or []

    optimization_notes = (
        f"Original Match Percentage: {original['match_percentage']}%\n"
        f"Optimized Match Percentage: {optimized['match_percentage']}%\n"
        f"Matched Keywords: {', '.join(matched_keywords) if matched_keywords else 'None'}\n"
        f"Missing Keywords: {', '.join(missing_keywords) if missing_keywords else 'None'}\n"
        f"Recommendation: {optimized.get('recommendation', '')}\n"
        f"Selected Technologies: {', '.join(injection_plan.get('supportedKeywords') or [])}\n"
        f"Rejected Technologies: {', '.join(injection_plan.get('rejectedTechnologies') or [])}"
    )

    return {
        "optimized_text": plain,
        "optimization_notes": optimization_notes,
        "match_percentage": optimized["match_percentage"],
        "matched_keywords": matched_keywords,
        "missing_keywords": missing_keywords,
        "original_match_percentage": original["match_percentage"],
        "score_improvement": optimized["match_percentage"] - original["match_percentage"],
        "role_alignment": original.get("role_alignment"),
        "score_status": optimized.get("score_status"),
        "safe_to_apply": optimized.get("safe_to_apply", False),
        "optimization_allowed": original.get("optimization_allowed", False),
        "matched_core_skills": original.get("matched_core_skills", []),
        "matched_transferable_skills": original.get("matched_transferable_skills", []),
        "missing_mandatory_skills": original.get("missing_mandatory_skills", []),
        "warning_message": original.get("warning_message", ""),
        "supported_keywords": injection_plan.get("supportedKeywords") or [],
        "unsupported_keywords": injection_plan.get("unsupportedKeywords") or [],
    }
