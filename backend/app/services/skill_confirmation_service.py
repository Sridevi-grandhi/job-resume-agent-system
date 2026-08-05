"""User-confirmed skill evidence: validation, credit scoring, persistence.

Source-of-truth boundary: a confirmation is never evidence by itself. Credit is
only awarded when the user supplies the minimum evidence required for the
evidence type they claim. Checking a skill with no evidence, or explicitly
saying "I do not have this skill," always earns 0% credit and is never
inserted into a generated resume.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.skill_confirmation_model import UserConfirmedSkill
from app.services.keyword_utils import normalize_keyword


EVIDENCE_TYPES = [
    "Current employment",
    "Previous employment",
    "Professional project",
    "Personal project",
    "Academic project",
    "Certification",
    "Training/coursework",
    "Learning only",
]

EXPERIENCE_LEVELS = ["Beginner", "Intermediate", "Advanced"]
EXPERIENCE_RANGES = ["Less than 6 months", "6-12 months", "1-2 years", "2-4 years", "4+ years"]

EMPLOYMENT_EVIDENCE_TYPES = {"Current employment", "Previous employment"}
PROJECT_EVIDENCE_TYPES = {"Professional project", "Personal project", "Academic project"}
PERSONAL_PROJECT_EVIDENCE_TYPES = {"Personal project", "Academic project"}

EVIDENCE_STATUSES = {
    "VERIFIED_FROM_RESUME",
    "USER_CONFIRMED_PROFESSIONAL",
    "USER_CONFIRMED_PROJECT",
    "USER_CONFIRMED_CERTIFICATION",
    "USER_CONFIRMED_TRAINING",
    "LEARNING_ONLY",
    "INCOMPLETE",
    "REJECTED",
}

# answer: "has_it" (evidence form), "learning_only", "not_possessed" ("I do not have this skill")
ANSWERS = {"has_it", "learning_only", "not_possessed"}

_CREDIT_BY_EVIDENCE_TYPE = {
    "Current employment": ("USER_CONFIRMED_PROFESSIONAL", 0.85),
    "Previous employment": ("USER_CONFIRMED_PROFESSIONAL", 0.85),
    "Professional project": ("USER_CONFIRMED_PROJECT", 0.75),
    "Personal project": ("USER_CONFIRMED_PROJECT", 0.55),
    "Academic project": ("USER_CONFIRMED_PROJECT", 0.55),
    "Certification": ("USER_CONFIRMED_CERTIFICATION", 0.35),
    "Training/coursework": ("USER_CONFIRMED_TRAINING", 0.25),
}

VERIFIED_FROM_RESUME_CREDIT = 1.0
LEARNING_ONLY_CREDIT = 0.10


def _now():
    return datetime.now(timezone.utc)


def _has_text(value: Any) -> bool:
    return bool((value or "").strip()) if isinstance(value, str) else bool(value)


def required_fields_for(evidence_type: str, *, is_new_employment: bool = False) -> list[str]:
    """Type-specific fields required on top of the shared baseline (experience_level,
    experience_range, usage_description, confirmed_accurate). Employer/role/dates are
    only required when the user explicitly flags a new employment record."""
    if evidence_type in EMPLOYMENT_EVIDENCE_TYPES:
        return ["employer_name", "role_title", "start_date"] if is_new_employment else []
    if evidence_type in PROJECT_EVIDENCE_TYPES:
        return ["project_name", "work_completed"]
    if evidence_type == "Certification":
        return ["certification_name", "provider_name"]
    if evidence_type == "Training/coursework":
        return ["project_name"]
    return []


def validate_evidence_item(
    item: dict[str, Any], *, found_in_original: bool = False, known_employers: list[str] | None = None
) -> list[str]:
    """Return validation error strings; empty list means the item is acceptable to save."""
    errors: list[str] = []
    skill = item.get("skill") or item.get("skill_name") or "This skill"
    answer = item.get("answer")

    if answer in (None, "not_possessed", "learning_only"):
        return errors  # "not possessed" and "learning only" never require evidence fields

    evidence_type = (item.get("evidence_type") or "").strip()
    if not evidence_type:
        return [f"{skill}: choose how you gained this skill."]
    if evidence_type not in EVIDENCE_TYPES:
        return [f"{skill}: invalid evidence_type '{evidence_type}'."]

    # Shared baseline required for every "has_it" answer, regardless of evidence type.
    missing = []
    experience_level = item.get("experience_level")
    if not experience_level:
        missing.append("experience_level")
    elif experience_level not in EXPERIENCE_LEVELS:
        errors.append(f"{skill}: invalid experience_level '{experience_level}'.")
    experience_range = item.get("experience_range")
    if not experience_range:
        missing.append("experience_range")
    elif experience_range not in EXPERIENCE_RANGES:
        errors.append(f"{skill}: invalid experience_range '{experience_range}'.")
    if not _has_text(item.get("usage_description")):
        missing.append("usage_description")
    if not item.get("confirmed_accurate"):
        missing.append("accuracy confirmation")

    is_new_employment = bool(item.get("is_new_employment"))
    missing.extend(
        field
        for field in required_fields_for(evidence_type, is_new_employment=is_new_employment)
        if not _has_text(item.get(field))
    )

    if evidence_type in EMPLOYMENT_EVIDENCE_TYPES:
        if is_new_employment:
            if not item.get("currently_using") and not _has_text(item.get("end_date")):
                missing.append("end_date (or mark currently employed)")
        else:
            # Existing-position path: employer_name is optional (blank = most recent
            # role), but if the user supplies one it must be a real resume employer —
            # never silently overwritten with a fabricated one.
            employer = (item.get("employer_name") or "").strip()
            known = [e for e in (known_employers or []) if e]
            if employer and known and employer.lower() not in {e.lower() for e in known}:
                errors.append(
                    f"{skill}: employer '{employer}' was not found in your resume. "
                    f"Select an existing position or choose \"New employment not present in resume.\""
                )

    if missing:
        errors.append(f"{skill}: missing required evidence — {', '.join(missing)}.")
    return errors


def classify_evidence_status(
    item: dict[str, Any], *, found_in_original: bool = False, known_employers: list[str] | None = None
) -> tuple[str, float]:
    """Return (evidence_status, credit 0.0-1.0) for a submitted evidence item."""
    answer = item.get("answer")
    if answer == "not_possessed":
        return "REJECTED", 0.0
    if answer == "learning_only":
        return "LEARNING_ONLY", LEARNING_ONLY_CREDIT
    if answer != "has_it":
        return "INCOMPLETE", 0.0

    if validate_evidence_item(item, found_in_original=found_in_original, known_employers=known_employers):
        return "INCOMPLETE", 0.0

    evidence_type = (item.get("evidence_type") or "").strip()
    return _CREDIT_BY_EVIDENCE_TYPE.get(evidence_type, ("INCOMPLETE", 0.0))


def save_evidence(
    db: Session,
    *,
    user_id: int,
    job_id: int,
    resume_id: int | None,
    items: list[dict[str, Any]],
    resume_evidence: dict[str, dict] | None = None,
    known_employers: list[str] | None = None,
) -> list[UserConfirmedSkill]:
    """Validate, classify, and upsert evidence rows. Raises ValueError with all errors on failure."""
    resume_evidence = resume_evidence or {}
    all_errors: list[str] = []
    for item in items:
        normalized = normalize_keyword(item.get("skill") or item.get("skill_name") or "")
        found_in_original = bool(resume_evidence.get(normalized, {}).get("present"))
        all_errors.extend(
            validate_evidence_item(item, found_in_original=found_in_original, known_employers=known_employers)
        )
    if all_errors:
        raise ValueError("; ".join(all_errors))

    saved: list[UserConfirmedSkill] = []
    for item in items:
        skill_name = (item.get("skill") or item.get("skill_name") or "").strip()
        if not skill_name:
            continue
        normalized = normalize_keyword(skill_name)
        found_in_original = bool(resume_evidence.get(normalized, {}).get("present"))
        status, _credit = classify_evidence_status(
            item, found_in_original=found_in_original, known_employers=known_employers
        )
        answer = item.get("answer")
        confirmed = answer in ("has_it", "learning_only")
        evidence_type = "Learning only" if answer == "learning_only" else (item.get("evidence_type") if answer == "has_it" else None)

        fields = dict(
            resume_id=resume_id,
            requirement_type=item.get("requirement_type"),
            confirmed=confirmed,
            evidence_type=evidence_type,
            evidence_status=status,
            experience_level=item.get("experience_level") if answer == "has_it" else None,
            experience_range=item.get("experience_range") if answer == "has_it" else None,
            supporting_context=item.get("supporting_context") if answer == "has_it" else None,
            confirmed_accurate=bool(item.get("confirmed_accurate")) if answer == "has_it" else None,
            employer_name=item.get("employer_name") if answer == "has_it" else None,
            role_title=item.get("role_title") if answer == "has_it" else None,
            project_name=item.get("project_name") if answer == "has_it" else None,
            certification_name=item.get("certification_name") if answer == "has_it" else None,
            provider_name=item.get("provider_name") if answer == "has_it" else None,
            start_date=item.get("start_date") if answer == "has_it" else None,
            end_date=item.get("end_date") if answer == "has_it" else None,
            currently_using=item.get("currently_using") if answer == "has_it" else None,
            years_experience=item.get("years_experience"),
            last_used=item.get("last_used"),
            usage_description=item.get("usage_description") if answer == "has_it" else None,
            work_completed=item.get("work_completed") if answer == "has_it" else None,
            related_technologies=item.get("related_technologies") if answer == "has_it" else None,
            impact_description=item.get("impact_description") if answer == "has_it" else None,
            skill_modules=item.get("skill_modules") if answer == "has_it" else None,
        )

        existing = (
            db.query(UserConfirmedSkill)
            .filter(
                UserConfirmedSkill.user_id == user_id,
                UserConfirmedSkill.job_id == job_id,
                UserConfirmedSkill.normalized_skill_name == normalized,
            )
            .first()
        )
        if existing:
            for key, value in fields.items():
                setattr(existing, key, value)
            existing.skill_name = skill_name
            existing.updated_at = _now()
            db.add(existing)
            db.flush()
            saved.append(existing)
        else:
            record = UserConfirmedSkill(
                user_id=user_id, job_id=job_id, skill_name=skill_name, normalized_skill_name=normalized, **fields
            )
            db.add(record)
            db.flush()
            saved.append(record)

    db.commit()
    for record in saved:
        db.refresh(record)
    return saved


def load_confirmed_skills(db: Session, *, user_id: int, job_id: int) -> list[UserConfirmedSkill]:
    return (
        db.query(UserConfirmedSkill)
        .filter(UserConfirmedSkill.user_id == user_id, UserConfirmedSkill.job_id == job_id)
        .order_by(UserConfirmedSkill.id.asc())
        .all()
    )


def delete_confirmed_skill(db: Session, *, user_id: int, job_id: int, evidence_id: int) -> bool:
    row = (
        db.query(UserConfirmedSkill)
        .filter(
            UserConfirmedSkill.id == evidence_id,
            UserConfirmedSkill.user_id == user_id,
            UserConfirmedSkill.job_id == job_id,
        )
        .first()
    )
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


def confirmed_skill_to_dict(row: UserConfirmedSkill) -> dict[str, Any]:
    credit = credit_for_row(row)
    return {
        "id": row.id,
        "skill": row.skill_name,
        "normalized_skill_name": row.normalized_skill_name,
        "requirement_type": row.requirement_type,
        "confirmed": row.confirmed,
        "evidence_type": row.evidence_type,
        "evidence_status": row.evidence_status,
        "experience_level": row.experience_level,
        "experience_range": row.experience_range,
        "supporting_context": row.supporting_context,
        "confirmed_accurate": row.confirmed_accurate,
        "employer_name": row.employer_name,
        "role_title": row.role_title,
        "project_name": row.project_name,
        "certification_name": row.certification_name,
        "provider_name": row.provider_name,
        "start_date": row.start_date,
        "end_date": row.end_date,
        "currently_using": row.currently_using,
        "years_experience": row.years_experience,
        "last_used": row.last_used,
        "usage_description": row.usage_description,
        "work_completed": row.work_completed,
        "related_technologies": row.related_technologies,
        "impact_description": row.impact_description,
        "skill_modules": row.skill_modules,
        "credit": credit,
        "updated_at": str(row.updated_at) if row.updated_at else None,
    }


def credit_for_row(row: UserConfirmedSkill) -> float:
    status = row.evidence_status
    if status == "VERIFIED_FROM_RESUME":
        return VERIFIED_FROM_RESUME_CREDIT
    if status == "LEARNING_ONLY":
        return LEARNING_ONLY_CREDIT
    if status in {"INCOMPLETE", "REJECTED"}:
        return 0.0
    _status, credit = _CREDIT_BY_EVIDENCE_TYPE.get(row.evidence_type or "", (status, 0.0))
    return credit


def to_scoring_evidence(rows: list[UserConfirmedSkill]) -> dict[str, dict[str, Any]]:
    """Map confirmations into scoring-ready evidence. INCOMPLETE/REJECTED never award credit."""
    evidence: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.evidence_status in {"INCOMPLETE", "REJECTED"}:
            continue
        evidence[row.normalized_skill_name] = {
            "credit": credit_for_row(row),
            "evidence_status": row.evidence_status,
            "evidence_type": row.evidence_type,
            "usage_description": row.usage_description or "",
        }
    return evidence


def to_generation_evidence(rows: list[UserConfirmedSkill]) -> dict[str, dict[str, Any]]:
    """Skill -> evidence used by the resume generator. Excludes INCOMPLETE/REJECTED —
    truthful inclusion (certifications/training) is independent of scoring weight."""
    evidence: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.evidence_status in {"INCOMPLETE", "REJECTED"}:
            continue
        evidence[row.normalized_skill_name] = confirmed_skill_to_dict(row)
    return evidence
