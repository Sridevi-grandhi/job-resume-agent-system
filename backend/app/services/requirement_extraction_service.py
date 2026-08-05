"""Structured job requirements backed by the authoritative skill engine."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.skill_confirmation_model import JobRequirementAnalysis
from app.services.ats_scoring import extract_structured_requirements
from app.services.keyword_utils import extract_job_keywords
from app.services.skill_analysis_service import (
    SKILL_ANALYSIS_VERSION,
    compare_job_requirements_to_resume,
    normalize_skill_name,
)
from app.services.skill_question_bank import interview_prompts_for_skill

_CATEGORY_FIELDS = [
    ("languages", "Programming Language"), ("frameworks", "Framework"),
    ("cloud_skills", "Cloud"), ("database_skills", "Database"),
    ("testing_skills", "Testing"), ("frontend_skills", "Frontend"),
    ("devops_skills", "DevOps"),
]


def _now():
    return datetime.now(timezone.utc)


def _category_for(skill: str, requirements: dict[str, Any]) -> str:
    normalized = normalize_skill_name(skill)
    if normalized in {"maven", "gradle"}:
        return "Build Tool"
    if normalized in {"scrum", "agile"}:
        return "Methodology"
    for field, label in _CATEGORY_FIELDS:
        if skill in (requirements.get(field) or []):
            return label
    return "Other"


def _deduplicated_items(skills: list[str], importance: str, requirements: dict[str, Any]) -> list[dict[str, Any]]:
    output, seen = [], set()
    for skill in skills:
        normalized = normalize_skill_name(skill)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        output.append({
            "skill": skill,
            "category": _category_for(skill, requirements),
            "importance": importance,
            "evidence_expected": "Professional experience or project",
        })
    return output


def extract_job_requirement_analysis(job) -> dict[str, Any]:
    requirements = extract_structured_requirements(job)
    keywords = extract_job_keywords(
        getattr(job, "title", "") or "",
        getattr(job, "description", "") or "",
        getattr(job, "skills_required", "") or "",
    )
    mandatory = _deduplicated_items(requirements["required_core_skills"], "MANDATORY", requirements)
    mandatory_normalized = {normalize_skill_name(item["skill"]) for item in mandatory}
    preferred = _deduplicated_items(requirements["preferred_skills"], "PREFERRED", requirements)
    preferred = [item for item in preferred if normalize_skill_name(item["skill"]) not in mandatory_normalized]
    return {
        "job_id": getattr(job, "id", None),
        "target_role": requirements["target_role"],
        "job_role_family": requirements["job_role_family"],
        "mandatory_skills": mandatory,
        "preferred_skills": preferred,
        "responsibilities": keywords.get("responsibilityKeywords") or [],
        "minimum_experience_years": requirements["experience_years_required"],
        "education_requirements": requirements["education_requirements"],
        "certification_requirements": requirements["certification_requirements"],
        "work_mode": requirements.get("work_mode"),
        "employment_type": requirements.get("employment_type"),
        "location_requirement": requirements.get("location_requirement"),
    }


def save_requirement_analysis(
    db: Session, *, user_id: int, job_id: int, analysis: dict[str, Any],
    resume_id: int | None = None, resume_hash: str = "", job_hash: str = "",
) -> JobRequirementAnalysis:
    existing = (
        db.query(JobRequirementAnalysis)
        .filter(JobRequirementAnalysis.user_id == user_id, JobRequirementAnalysis.job_id == job_id)
        .order_by(JobRequirementAnalysis.id.desc()).first()
    )
    analysis = {**analysis, "cache_identity": {
        "user_id": user_id, "job_id": job_id, "resume_id": resume_id,
        "resume_hash": resume_hash, "job_hash": job_hash,
        "analysis_version": SKILL_ANALYSIS_VERSION,
    }}
    payload = json.dumps(analysis, default=str)
    if existing:
        existing.analysis_json = payload
        existing.updated_at = _now()
        record = existing
    else:
        record = JobRequirementAnalysis(user_id=user_id, job_id=job_id, analysis_json=payload)
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def load_requirement_analysis(
    db: Session, *, user_id: int, job_id: int, resume_id: int | None = None,
    resume_hash: str = "", job_hash: str = "",
) -> dict[str, Any] | None:
    existing = (
        db.query(JobRequirementAnalysis)
        .filter(JobRequirementAnalysis.user_id == user_id, JobRequirementAnalysis.job_id == job_id)
        .order_by(JobRequirementAnalysis.id.desc()).first()
    )
    if not existing:
        return None
    try:
        analysis = json.loads(existing.analysis_json)
    except json.JSONDecodeError:
        return None
    expected = {
        "user_id": user_id, "job_id": job_id, "resume_id": resume_id,
        "resume_hash": resume_hash, "job_hash": job_hash,
        "analysis_version": SKILL_ANALYSIS_VERSION,
    }
    return analysis if analysis.get("cache_identity") == expected else None


def why_skill_matters(skill_info: dict[str, Any]) -> str:
    skill = skill_info.get("skill") or "This skill"
    category = (skill_info.get("category") or "skill").lower()
    if (skill_info.get("requirement_type") or skill_info.get("importance")) == "MANDATORY":
        return f"{skill} is a mandatory {category} requirement for this role."
    return f"{skill} is a preferred {category} skill that strengthens the match."


def merge_with_resume_evidence(analysis: dict[str, Any], resume_text: str) -> dict[str, Any]:
    requirements = [
        {**item, "requirement_type": "MANDATORY"} for item in analysis.get("mandatory_skills", [])
    ] + [
        {**item, "requirement_type": "PREFERRED"} for item in analysis.get("preferred_skills", [])
    ]
    compared = compare_job_requirements_to_resume(requirements, resume_text or "")
    by_normalized = {item["normalized_skill"]: item for item in compared["skills"]}

    def attach(items: list[dict], importance: str) -> list[dict]:
        output = []
        for item in items:
            result = by_normalized.get(normalize_skill_name(item["skill"]))
            if result:
                output.append({**item, **result, "importance": importance, "found_in_resume": result["found"]})
        return output

    mandatory = attach(analysis.get("mandatory_skills", []), "MANDATORY")
    preferred = attach(analysis.get("preferred_skills", []), "PREFERRED")
    return {
        **analysis,
        "mandatory_skills": mandatory,
        "preferred_skills": preferred,
        "skills": compared["skills"],
        "summary": compared["summary"],
        "analysis_version": compared["analysis_version"],
        "resume_evidence": {item["skill"]: item for item in compared["skills"]},
    }


def group_requirements_vs_resume(analysis: dict[str, Any], resume_text: str) -> dict[str, Any]:
    enriched = merge_with_resume_evidence(analysis, resume_text)
    skills = enriched["skills"]
    for item in skills:
        item["found_in_resume"] = bool(item.get("found") or item.get("found_in_resume"))
        item["resume_status"] = item.get("resume_status") or item.get("status") or "MISSING"
        item["why_it_matters"] = why_skill_matters(item)
        if item["resume_status"] != "VERIFIED":
            item["interview"] = interview_prompts_for_skill(item["skill"])
    # Deduplicate again by normalized key so Maven/Apache Maven cannot appear twice.
    deduped, seen = [], set()
    for item in skills:
        key = item.get("normalized_skill") or normalize_skill_name(item.get("skill", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    skills = deduped
    return {
        "job_id": analysis.get("job_id"),
        "skills": skills,
        "summary": enriched["summary"],
        "verified_resume_skills": [item for item in skills if item["resume_status"] == "VERIFIED"],
        "weak_or_indirect_skills": [item for item in skills if item["resume_status"] in {"WEAK", "INDIRECT"}],
        "missing_mandatory_skills": [item for item in skills if item["resume_status"] == "MISSING" and item["requirement_type"] == "MANDATORY"],
        "missing_preferred_skills": [item for item in skills if item["resume_status"] == "MISSING" and item["requirement_type"] == "PREFERRED"],
        "analysis_version": enriched["analysis_version"],
    }
