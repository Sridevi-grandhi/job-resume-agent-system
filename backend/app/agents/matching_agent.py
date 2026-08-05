"""
Matching Percentage Agent

Uses the shared weighted ATS scorer. Kept for backward compatibility with
existing /api/match endpoints and workflow code.
"""

from app.services.ats_scoring import calculate_weighted_ats_score


def extract_keywords(text: str):
    """Backward-compatible keyword extraction via weighted scorer helpers."""
    from app.services.keyword_utils import find_keywords_in_text

    return set(k.lower() for k in find_keywords_in_text(text or ""))


def calculate_match_score(resume_text: str, job_text: str):
    """
    Backward-compatible match API.

    Accepts either a job-like object or a combined job text string.
    """
    if hasattr(job_text, "title"):
        result = calculate_weighted_ats_score(resume_text, job_text)
    else:
        class _JobShim:
            title = ""
            description = str(job_text or "")
            skills_required = ""

        # Prefer first line as title when callers pass combined text
        lines = [line.strip() for line in str(job_text or "").splitlines() if line.strip()]
        shim = _JobShim()
        if lines:
            shim.title = lines[0][:200]
            shim.description = "\n".join(lines[1:]) if len(lines) > 1 else lines[0]
        result = calculate_weighted_ats_score(resume_text, shim)

    return {
        "match_percentage": result["match_percentage"],
        "matched_keywords": result["matched_keywords"],
        "missing_keywords": result["missing_keywords"],
        "recommendation": result["recommendation"],
        "suggestions": result.get("suggestions", []),
        "score_breakdown": result.get("score_breakdown", {}),
        "total_score": result.get("total_score", result["match_percentage"]),
        "score_status": result.get("score_status"),
        "confidence": result.get("confidence"),
        "role_alignment": result.get("role_alignment"),
        "resume_role_families": result.get("resume_role_families", []),
        "job_role_family": result.get("job_role_family"),
        "score_cap_applied": result.get("score_cap_applied"),
        "score_cap_reason": result.get("score_cap_reason"),
        "matched_core_skills": result.get("matched_core_skills", []),
        "matched_transferable_skills": result.get("matched_transferable_skills", []),
        "missing_mandatory_skills": result.get("missing_mandatory_skills", []),
        "warning_message": result.get("warning_message", ""),
        "safe_to_apply": result.get("safe_to_apply", False),
    }
