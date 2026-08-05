"""Keyword injection planning — truthful, anti-stuffing placement targets."""

from __future__ import annotations

from app.services.keyword_utils import extract_job_keywords, select_supported_keywords
from app.services.technology_consistency_validator import validate_technology_consistency
from app.services.technology_timeline_validator import validate_technology_timeline


def plan_keyword_injection(
    resume_text: str,
    job,
    employments: list[dict] | None = None,
    projects: list[dict] | None = None,
    verified_skills: list[str] | None = None,
) -> dict:
    extracted = extract_job_keywords(
        getattr(job, "title", "") or "",
        getattr(job, "description", "") or "",
        getattr(job, "skills_required", "") or "",
    )
    jd_keywords = extracted["allKeywords"]
    support = select_supported_keywords(jd_keywords, resume_text, verified_skills=verified_skills)
    tech = validate_technology_consistency(
        jd_keywords=jd_keywords,
        resume_text=resume_text,
        supported_keywords=support["supportedKeywords"],
    )
    timeline = validate_technology_timeline(
        selected_technologies=tech["selectedTechnologies"],
        employments=employments or [],
        projects=projects or [],
    )

    selected = [
        t for t in tech["selectedTechnologies"]
        if t not in timeline["technologiesNotAddedDueToTimeline"]
    ]
    rejected = list(dict.fromkeys(
        tech["rejectedTechnologies"]
        + support["unsupportedKeywords"]
        + timeline["technologiesNotAddedDueToTimeline"]
    ))

    # Placement budgets
    summary_kw = selected[:5]
    skills_kw = selected[:12]
    experience_kw = selected[:8]
    project_kw = selected[:6]

    warnings = []
    if len(selected) < 8:
        warnings.append(
            f"Only {len(selected)} JD keywords can be added truthfully. "
            "More keywords require verified real experience."
        )
    warnings.extend(tech["conflictWarnings"])
    warnings.extend(timeline["timelineWarnings"])

    return {
        "requiredKeywords": extracted["requiredKeywords"],
        "preferredKeywords": extracted["preferredKeywords"],
        "supportedKeywords": selected,
        "unsupportedKeywords": support["unsupportedKeywords"],
        "keywordsInjected": selected[:10],
        "keywordWarnings": warnings,
        "placement": {
            "summary": summary_kw,
            "skills": skills_kw,
            "experience": experience_kw,
            "projects": project_kw,
        },
        "technology_validation": tech,
        "timeline_validation": timeline,
        "rejectedTechnologies": rejected,
    }
