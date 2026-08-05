"""Deterministic, evidence-based ATS scoring.

Source-of-truth boundaries:
- ORIGINAL_RESUME is the only evidence of candidate experience.
- JOB_DESCRIPTION and skills_required describe employer requirements only.
- USER_PREFERENCES are intentionally not accepted by this module.
- An optimized resume may surface original evidence, but cannot create evidence.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.keyword_utils import extract_job_keywords, normalize_keyword
from app.services.resume_structure_parser import parse_resume_structure
from app.services.skill_analysis_service import (
    extract_resume_skill_evidence,
    get_skill_evidence,
)

ANALYSIS_VERSION = "evidence-role-gated-v5-skill-analysis-v1"

# Generic/transferable skills carry low scoring influence and can never
# substitute for missing mandatory technologies (spec Part 7). Specific
# vendor technologies (AWS, PostgreSQL, Docker, Kubernetes, etc.) are NOT
# generic — they score at full credit like any other required skill.
GENERIC_SKILLS = {
    "Agile", "Scrum", "Git", "CI/CD", "Jira", "API", "REST APIs",
    "teamwork", "communication", "cloud", "database",
}
GENERIC_SKILL_CREDIT_CAP = 0.4

ROLE_FAMILIES = {
    "Java Full Stack": {
        "core": ["Java", "Spring Boot"],
        "frameworks": ["Spring", "Spring Boot", "Hibernate", "JPA", "Maven", "Gradle", "J2EE"],
        "frontend": ["React", "Angular", "JavaScript", "TypeScript"],
        "indicators": ["Java", "Spring", "Spring Boot", "Hibernate", "JPA", "Maven", "Gradle", "Microservices", "React", "Angular", "JVM", "J2EE"],
    },
    "Java Backend": {
        "core": ["Java", "Spring Boot"],
        "frameworks": ["Spring", "Spring Boot", "Hibernate", "JPA", "Maven", "Gradle", "J2EE"],
        "frontend": [],
        "indicators": ["Java", "Spring", "Spring Boot", "Hibernate", "JPA", "Maven", "Gradle", "Microservices", "JVM", "J2EE"],
    },
    "Python Full Stack": {
        "core": ["Python"],
        "frameworks": ["Django", "Flask", "FastAPI"],
        "frontend": ["React", "Angular", "JavaScript", "TypeScript"],
        "indicators": ["Python", "Django", "Flask", "FastAPI", "React", "Angular", "Celery", "SQLAlchemy"],
    },
    "Python Backend": {
        "core": ["Python"],
        "frameworks": ["Django", "Flask", "FastAPI", "Celery", "SQLAlchemy"],
        "frontend": [],
        "indicators": ["Python", "Django", "Flask", "FastAPI", "Celery", "SQLAlchemy"],
    },
    "Data Engineering": {
        "core": ["Spark", "PySpark", "ETL"],
        "frameworks": ["Kafka", "Airflow", "Databricks", "Snowflake"],
        "frontend": [],
        "indicators": ["ETL", "Spark", "PySpark", "Kafka", "Airflow", "Databricks", "Snowflake", "data pipeline", "data warehouse"],
    },
    "Data Science": {
        "core": ["Python"],
        "frameworks": ["Pandas", "NumPy"],
        "frontend": [],
        "indicators": ["Python", "Pandas", "NumPy", "statistics", "data science"],
    },
    "Machine Learning": {
        "core": ["Python", "machine learning"],
        "frameworks": ["TensorFlow", "PyTorch", "scikit-learn"],
        "frontend": [],
        "indicators": ["machine learning", "TensorFlow", "PyTorch", "scikit-learn", "model training"],
    },
    "DevOps": {
        "core": ["CI/CD"],
        "frameworks": ["Docker", "Kubernetes", "Terraform", "Jenkins"],
        "frontend": [],
        "indicators": ["DevOps", "CI/CD", "Docker", "Kubernetes", "Terraform", "Jenkins", "Ansible"],
    },
    "Cloud Engineering": {
        "core": ["AWS", "Azure", "GCP"],
        "frameworks": ["Terraform", "CloudFormation", "Kubernetes"],
        "frontend": [],
        "indicators": ["cloud engineer", "AWS", "Azure", "GCP", "Terraform", "CloudFormation"],
    },
    "SDET / QA Automation": {
        "core": ["Selenium", "Cypress", "Playwright", "Appium"],
        "frameworks": ["Cucumber", "TestNG", "JUnit", "XCTest", "XCUITest"],
        "frontend": [],
        "indicators": ["SDET", "QA Automation", "Selenium", "Cypress", "Playwright", "Appium", "Cucumber", "TestNG", "XCTest", "XCUITest"],
    },
    "Frontend": {
        "core": ["JavaScript", "TypeScript"],
        "frameworks": ["React", "Angular", "Vue"],
        "frontend": ["React", "Angular", "Vue"],
        "indicators": ["frontend", "JavaScript", "TypeScript", "React", "Angular", "Vue", "HTML", "CSS"],
    },
    "Mobile iOS": {"core": ["Swift"], "frameworks": ["Xcode", "XCTest", "XCUITest"], "frontend": [], "indicators": ["iOS", "Swift", "Xcode", "XCTest", "XCUITest"]},
    "Mobile Android": {"core": ["Kotlin", "Java"], "frameworks": ["Android SDK"], "frontend": [], "indicators": ["Android", "Kotlin", "Android SDK"]},
    "Database / SQL": {"core": ["SQL"], "frameworks": ["PostgreSQL", "MySQL", "Oracle", "SQL Server"], "frontend": [], "indicators": ["database", "SQL", "PostgreSQL", "MySQL", "Oracle", "SQL Server"]},
    "Business Analysis": {"core": ["business analysis"], "frameworks": ["Jira"], "frontend": [], "indicators": ["business analyst", "requirements gathering", "business analysis", "stakeholder"]},
}

def _has(text: str, skill: str) -> bool:
    evidence = get_skill_evidence(skill, text or "")
    return evidence["match_type"] in {"EXACT", "ALIAS", "CONTEXTUAL"}


def classify_role_families(text: str, title: str = "") -> list[str]:
    blob = f"{title}\n{text}"
    scores: list[tuple[str, int]] = []
    for family, definition in ROLE_FAMILIES.items():
        if "Full Stack" in family and not (
            re.search(r"\bfull[\s-]*stack\b", title, re.I)
            or any(_has(blob, skill) for skill in definition.get("frontend", []))
        ):
            continue
        score = sum(1 for item in definition["indicators"] if _has(blob, item))
        family_words = family.lower().replace("/", " ").split()
        score += 3 * sum(1 for word in family_words if len(word) > 3 and re.search(rf"\b{re.escape(word)}\b", title.lower()))
        if "Full Stack" in family and re.search(r"\bfull[\s-]*stack\b", title, re.I):
            score += 5
        if score:
            scores.append((family, score))
    if not scores:
        return ["Other"]
    best = max(score for _, score in scores)
    return [family for family, score in sorted(scores, key=lambda item: -item[1]) if score >= max(2, best * 0.6)][:3]


def _split_requirement_phrases(text: str) -> tuple[str, str]:
    preferred_parts = re.findall(
        r"(?is)(?:preferred|nice to have|bonus|plus)[^.\n:]*[:\-]?\s*([^.\n]+)",
        text or "",
    )
    preferred = " ".join(preferred_parts)
    mandatory = re.sub(
        r"(?is)(?:preferred|nice to have|bonus|plus)[^.\n:]*[:\-]?\s*[^.\n]+",
        " ",
        text or "",
    )
    return mandatory, preferred


def extract_structured_requirements(job) -> dict[str, Any]:
    title = str(getattr(job, "title", "") or "")
    description = str(getattr(job, "description", "") or "")
    skills_field = str(getattr(job, "skills_required", "") or "")
    mandatory_text, preferred_text = _split_requirement_phrases(description)
    extracted = extract_job_keywords(title, description, skills_field)
    job_family = classify_role_families(f"{skills_field}\n{description}", title)[0]
    family = ROLE_FAMILIES.get(job_family, {"core": [], "frameworks": [], "frontend": []})

    explicit_required = [
        normalize_keyword(part.strip())
        for part in re.split(r"[,;/|\n]+", skills_field)
        if len(part.strip()) > 1
    ]
    mandatory_detected = [
        skill for skill in extracted.get("allKeywords", [])
        if _has(mandatory_text, skill) or _has(skills_field, skill)
    ]
    required = list(dict.fromkeys(
        family["core"]
        + [skill for skill in family["frameworks"] if _has(f"{title} {skills_field} {mandatory_text}", skill)]
        + [skill for skill in family["frontend"] if _has(f"{title} {skills_field} {mandatory_text}", skill)]
        + explicit_required
        + mandatory_detected
    ))
    if "Spring Boot" in required and "Spring" in required:
        required.remove("Spring")
    preferred = list(dict.fromkeys(
        [skill for skill in extracted.get("allKeywords", []) if _has(preferred_text, skill) and skill not in required]
        + [skill for skill in extracted.get("preferredKeywords", []) if skill not in required]
    ))
    years_match = re.search(r"\b(\d+)\+?\s+years?\b", description, re.I)

    categories = {
        "languages": ["Java", "Python", "JavaScript", "TypeScript", "Kotlin", "Swift"],
        "cloud_skills": ["AWS", "Azure", "GCP"],
        "database_skills": ["SQL", "PostgreSQL", "MySQL", "Oracle", "MongoDB", "SQL Server"],
        "frontend_skills": ["React", "Angular", "Vue", "HTML", "CSS", "JavaScript", "TypeScript"],
        "testing_skills": ["Selenium", "Cypress", "Playwright", "Appium", "JUnit", "TestNG", "PyTest", "XCTest", "XCUITest"],
        "devops_skills": ["Docker", "Kubernetes", "Jenkins", "Terraform", "Ansible", "CI/CD"],
    }
    all_terms = list(dict.fromkeys(required + preferred))
    result: dict[str, Any] = {
        "target_role": title,
        "job_role_family": job_family,
        "required_core_skills": required,
        "preferred_skills": preferred,
        "frameworks": [term for term in all_terms if term in family["frameworks"]],
        "domain_requirements": [],
        "education_requirements": re.findall(r"(?i)(?:bachelor|master|phd|degree)[^.\n]*", description),
        "certification_requirements": re.findall(r"(?i)(?:certification|certified)[^.\n]*", description),
        "experience_years_required": int(years_match.group(1)) if years_match else None,
        "location_requirement": getattr(job, "location", None),
        "work_mode": getattr(job, "work_mode", None),
        "employment_type": getattr(job, "job_type", None),
    }
    result.update({name: [term for term in all_terms if term in values] for name, values in categories.items()})
    return result


def extract_resume_evidence(resume_text: str, skills: list[str]) -> dict[str, dict[str, Any]]:
    authoritative = extract_resume_skill_evidence(resume_text or "", skills)
    evidence: dict[str, dict[str, Any]] = {}
    for skill in skills:
        item = authoritative.get(get_skill_evidence(skill, "")["normalized_skill"], {})
        strength = item.get("evidence_strength", "none")
        sections = item.get("evidence_sections", [])
        evidence[skill] = {
            **item,
            "skill": skill,
            "evidence_strength": "absent" if strength == "none" else strength,
            "evidence_category": (
                "professional" if any(re.search(r"experience|employment", heading, re.I) for heading in sections)
                else "project" if any(re.search(r"project", heading, re.I) for heading in sections)
                else None
            ),
            "recent_usage": strength == "strong",
            "years_claimed": None,
        }
    return evidence


def _resume_credit(evidence: dict) -> float:
    strength = evidence["evidence_strength"]
    if strength == "strong":
        # Strong professional (experience/employment) evidence: 100%.
        # Strong project-only evidence in the original resume: 80%.
        return 1.0 if evidence.get("evidence_category") == "professional" else 0.80
    return {"medium": 0.7, "weak": 0.3, "absent": 0.0}[strength]


def _combined_credit(
    skill: str, evidence_entry: dict, confirmed_skills: dict[str, dict] | None
) -> tuple[float, str]:
    """Best truthful credit for a skill: resume evidence vs. accepted user-confirmed evidence.

    Confirmed evidence is only used to raise credit, never to lower resume-backed credit,
    and generic/transferable skills are capped so they cannot substitute for missing
    mandatory technologies (spec Part 7).
    """
    resume_credit = _resume_credit(evidence_entry)
    confirmed_entry = (confirmed_skills or {}).get(skill)
    confirmed_credit = float(confirmed_entry["credit"]) if confirmed_entry else 0.0

    if skill in GENERIC_SKILLS:
        resume_credit = min(resume_credit, GENERIC_SKILL_CREDIT_CAP)
        confirmed_credit = min(confirmed_credit, GENERIC_SKILL_CREDIT_CAP)

    if confirmed_credit > resume_credit:
        return confirmed_credit, "confirmed"
    if resume_credit > 0:
        return resume_credit, "resume"
    return 0.0, "none"


def _status(score: int) -> str:
    if score >= 90:
        return "EXCELLENT_MATCH"
    if score >= 75:
        return "STRONG_MATCH"
    if score >= 60:
        return "PARTIAL_MATCH"
    if score >= 40:
        return "WEAK_MATCH"
    return "NOT_RECOMMENDED"


def meets_high_score_requirements(
    *, alignment: str, mandatory_credit_ratio: float, missing_count: int, threshold: int
) -> bool:
    """Defensive gate for Part 8's >=90 / >=95 checklists. Never used to raise a score —
    only to confirm a score the deterministic math already produced is truthfully earned."""
    if threshold >= 95:
        return alignment in {"EXACT", "STRONG_RELATED"} and mandatory_credit_ratio >= 0.95 and missing_count == 0
    if threshold >= 90:
        return alignment in {"EXACT", "STRONG_RELATED"} and mandatory_credit_ratio >= 0.90 and missing_count == 0
    return True


def explain_score_gap(result: dict[str, Any]) -> str:
    """Part 20's 'Why not 95%?' explanation. Empty string once score >= 95."""
    score = result.get("match_percentage") or 0
    if score >= 95:
        return ""
    reasons = []
    missing = result.get("missing_mandatory_skills") or []
    weak = result.get("weak_core_skills") or []
    confirmed_used = result.get("confirmed_skills_used") or []
    if missing:
        reasons.append(f"no evidenced experience with {', '.join(missing)}")
    if weak:
        reasons.append(f"only weak evidence for {', '.join(weak)}")
    cap_reason = result.get("score_cap_reason")
    if cap_reason and not missing and not weak:
        reasons.append(cap_reason.lower())
    requirements = result.get("requirements") or {}
    min_years = requirements.get("experience_years_required")
    if min_years and result.get("role_alignment") not in {"EXACT", "STRONG_RELATED"}:
        reasons.append(f"role alignment does not strongly match the required {min_years}+ years profile")
    if not reasons and confirmed_used:
        # Every mandatory skill is evidenced, but some rely on your confirmed answers
        # rather than pre-existing resume documentation — those score slightly below
        # the maximum resume-native credit, which is why 95%+ isn't reached yet.
        reasons.append(
            "some of that evidence comes from your confirmed answers rather than the "
            f"original resume ({', '.join(confirmed_used)}) — add these to a future resume update "
            "or provide stronger supporting detail to close the remaining gap"
        )
    if not reasons:
        reasons.append("some mandatory requirements are not yet strongly evidenced")
    return f"Your optimized resume reached {score}%. It did not reach 95% because " + "; ".join(reasons) + "."


def calculate_weighted_ats_score(
    resume_text: str,
    job,
    *,
    evidence_source_text: str | None = None,
    confirmed_skills: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Score visible resume content, but prove every claim from evidence_source_text
    or from accepted user-confirmed evidence (confirmed_skills, keyed by normalized skill name)."""
    evidence_text = evidence_source_text if evidence_source_text is not None else resume_text
    requirements = extract_structured_requirements(job)
    required = requirements["required_core_skills"]
    preferred = requirements["preferred_skills"]
    all_requirements = list(dict.fromkeys(required + preferred))
    evidence = extract_resume_evidence(evidence_text, all_requirements)

    combined = {
        skill: _combined_credit(skill, evidence[skill], confirmed_skills) for skill in all_requirements
    }
    confirmed_skills_used = [skill for skill, (_, source) in combined.items() if source == "confirmed"]

    resume_families = classify_role_families(evidence_text)
    job_family = requirements["job_role_family"]
    exact = job_family in resume_families
    related_pairs = {
        frozenset(("Java Backend", "Java Full Stack")),
        frozenset(("Python Backend", "Python Full Stack")),
        frozenset(("Data Engineering", "Data Science")),
        frozenset(("DevOps", "Cloud Engineering")),
    }
    related = any(frozenset((family, job_family)) in related_pairs for family in resume_families)
    if exact:
        alignment = "EXACT"
        role_points = 20.0
    elif related:
        alignment = "STRONG_RELATED"
        role_points = 16.0
    elif job_family == "Other" or resume_families == ["Other"]:
        alignment = "UNKNOWN"
        role_points = 8.0
    elif any(
        set(ROLE_FAMILIES.get(family, {}).get("core", []))
        & set(ROLE_FAMILIES.get(job_family, {}).get("core", []))
        for family in resume_families
    ):
        alignment = "PARTIAL_TRANSFERABLE"
        role_points = 10.0
    else:
        alignment = "MISMATCH"
        role_points = 2.0

    mandatory_credits = [combined[skill][0] for skill in required]
    mandatory_credit_ratio = (sum(mandatory_credits) / len(required)) if required else 1.0
    mandatory_points = 35 * mandatory_credit_ratio
    strong_context = sum(
        1 for skill in required
        if evidence[skill]["evidence_strength"] == "strong" or combined[skill][1] == "confirmed" and combined[skill][0] >= 0.65
    )
    experience_points = 20 * (strong_context / len(required) if required else 1)
    preferred_points = 8 * (
        sum(combined[skill][0] for skill in preferred) / len(preferred) if preferred else 1
    )

    jd_keywords = extract_job_keywords(
        str(getattr(job, "title", "") or ""),
        str(getattr(job, "description", "") or ""),
        str(getattr(job, "skills_required", "") or ""),
    )
    responsibility_terms = list(dict.fromkeys(
        (jd_keywords.get("responsibilityKeywords") or []) + (jd_keywords.get("qualificationKeywords") or [])
    ))
    if responsibility_terms:
        responsibility_matches = sum(1 for term in responsibility_terms if _has(evidence_text, term))
        responsibilities_points = 7 * (responsibility_matches / len(responsibility_terms))
    else:
        responsibilities_points = 7.0

    structure = parse_resume_structure(evidence_text)
    sections = structure.get("sections") or []
    has_education = any(re.search(r"education|academic", section.get("heading", ""), re.I) for section in sections)
    has_certification = any(re.search(r"certif|license", section.get("heading", ""), re.I) for section in sections)
    education_points = 2.5 * int(has_education) + 2.5 * int(has_certification)
    location_points = 5.0  # No candidate location source exists; keep this neutral and explicit.

    component_scores = {
        "role_alignment": round(role_points, 2),
        "mandatory_core_skills": round(mandatory_points, 2),
        "experience_evidence": round(experience_points, 2),
        "preferred_skills": round(preferred_points, 2),
        "responsibilities_domain": round(responsibilities_points, 2),
        "education_certifications": round(education_points, 2),
        "location_employment": round(location_points, 2),
    }
    raw_score = round(sum(component_scores.values()))

    missing = [skill for skill in required if combined[skill][0] == 0.0]
    matched_core = [
        skill for skill in required
        if skill not in GENERIC_SKILLS and combined[skill][0] >= 0.5
    ]
    weak_core = [skill for skill in required if 0.0 < combined[skill][0] < 0.5]
    transferable = [
        skill for skill in all_requirements if skill in GENERIC_SKILLS and combined[skill][0] > 0.0
    ]
    missing_ratio = len(missing) / len(required) if required else 0
    score_cap = 100
    cap_reasons = []

    family_definition = ROLE_FAMILIES.get(job_family, {})
    primary = family_definition.get("core", [])
    frameworks = family_definition.get("frameworks", [])
    primary_supported = any(combined.get(skill, (0.0, "none"))[0] >= 0.5 for skill in primary)
    framework_supported = any(combined.get(skill, (0.0, "none"))[0] >= 0.5 for skill in frameworks)
    if alignment == "MISMATCH":
        score_cap = min(score_cap, 35)
        cap_reasons.append("Resume and target job belong to different role families")
    if primary and not primary_supported:
        score_cap = min(score_cap, 35)
        cap_reasons.append("Primary language or technology lacks professional evidence")
    elif frameworks and not framework_supported and job_family in {"Java Full Stack", "Java Backend", "Python Full Stack", "Python Backend"}:
        score_cap = min(score_cap, 45)
        cap_reasons.append("Required primary framework lacks sufficient evidence")
    if missing_ratio > 0.5:
        score_cap = min(score_cap, 45)
        cap_reasons.append("More than half of mandatory core skills are absent")

    # A mandatory skill whose ONLY evidence is a learning-only/training-only confirmation
    # (i.e. absent from the resume, bridged solely by low-tier confirmed evidence) caps the
    # score — that gap is real, even if other mandatory skills are strongly evidenced.
    learning_only_statuses = {"LEARNING_ONLY", "USER_CONFIRMED_TRAINING"}
    mandatory_only_learning = any(
        combined[skill][1] == "confirmed"
        and (confirmed_skills or {}).get(skill, {}).get("evidence_status") in learning_only_statuses
        for skill in required
    )
    if mandatory_only_learning:
        score_cap = min(score_cap, 55)
        cap_reasons.append("Only learning/coursework evidence exists for mandatory skills")

    if not meets_high_score_requirements(
        alignment=alignment, mandatory_credit_ratio=mandatory_credit_ratio,
        missing_count=len(missing), threshold=90,
    ):
        score_cap = min(score_cap, 89)
    elif not meets_high_score_requirements(
        alignment=alignment, mandatory_credit_ratio=mandatory_credit_ratio,
        missing_count=len(missing), threshold=95,
    ):
        score_cap = min(score_cap, 94)

    final_score = max(0, min(raw_score, score_cap))
    status = _status(final_score)
    if missing_ratio > 0.5 or alignment == "MISMATCH":
        status = "NOT_RECOMMENDED"
    warning = (
        "This role requires core experience that is not supported by your uploaded resume. "
        "Applying with the generated resume may misrepresent your background."
        if status == "NOT_RECOMMENDED"
        else ""
    )
    recommendation = {
        "EXCELLENT_MATCH": "Strong candidate alignment.",
        "STRONG_MATCH": "Apply after reviewing small gaps.",
        "PARTIAL_MATCH": "Significant review required.",
        "WEAK_MATCH": "Apply only when gaps are realistically transferable.",
        "NOT_RECOMMENDED": "Do not apply with the current resume.",
    }[status]
    matched = list(dict.fromkeys(matched_core + weak_core + transferable))
    suggestions = [
        f"Missing mandatory skills: {', '.join(missing)}" if missing else "No mandatory core skills are completely absent.",
        f"Role alignment: {', '.join(resume_families)} versus {job_family} ({alignment}).",
    ]
    if cap_reasons:
        suggestions.append(f"Score cap: {score_cap}% — {'; '.join(cap_reasons)}.")
    if confirmed_skills_used:
        suggestions.append(
            "Score includes user-confirmed experience not present in the uploaded resume: "
            + ", ".join(confirmed_skills_used) + "."
        )

    result = {
        "ats_score": final_score,
        "match_percentage": final_score,
        "final_score": final_score,
        "total_score": final_score,
        "score_status": status,
        "confidence": "HIGH" if required and job_family != "Other" else "MEDIUM",
        "role_alignment": alignment,
        "resume_role_families": resume_families,
        "job_role_family": job_family,
        "component_scores": component_scores,
        "score_breakdown": component_scores,
        "score_cap_applied": score_cap if score_cap < 100 else None,
        "score_cap_reason": "; ".join(cap_reasons) if cap_reasons else None,
        "matched_core_skills": matched_core,
        "matched_transferable_skills": transferable,
        "missing_mandatory_skills": missing,
        "weak_core_skills": weak_core,
        "matched_keywords": matched,
        "missing_keywords": missing,
        "unsupported_keywords": missing,
        "partially_supported_keywords": weak_core,
        "required_skills": required,
        "preferred_skills": preferred,
        "recommendation": recommendation,
        "warning_message": warning,
        "safe_to_apply": status not in {"NOT_RECOMMENDED"},
        "optimization_allowed": status not in {"NOT_RECOMMENDED"},
        "suggestions": suggestions,
        "requirements": requirements,
        "resume_evidence": evidence,
        "section_scores": {},
        "responsibilities_alignment": experience_points,
        "qualifications_alignment": education_points,
        "role_title_alignment": role_points,
        "experience_alignment": experience_points,
        "education_alignment": education_points,
        "certification_alignment": education_points,
        "formatting_warnings": [],
        "technology_warnings": [warning] if warning else [],
        "timeline_warnings": [],
        "client_domain_warnings": [],
        "contradiction_warnings": [],
        "analysis_version": ANALYSIS_VERSION,
        "keyword_analysis": requirements,
        "confirmed_skills_used": confirmed_skills_used,
        "mandatory_credit_ratio": round(mandatory_credit_ratio, 4),
    }
    result["why_not_95"] = explain_score_gap(result)
    return result
