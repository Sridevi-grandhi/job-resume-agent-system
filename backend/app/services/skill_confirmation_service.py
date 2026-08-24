"""User-confirmed skill evidence: validation, credit scoring, persistence.

Source-of-truth boundary: a confirmation is never evidence by itself. Credit is
only awarded when the user supplies the minimum evidence required for the
evidence type they claim. Checking a skill with no evidence, or explicitly
saying "I do not have this skill," always earns 0% credit and is never
inserted into a generated resume.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.resume_model import Resume
from app.models.skill_confirmation_model import UserConfirmedSkill
from app.services.skill_analysis_service import content_hash, normalize_skill_name


logger = logging.getLogger(__name__)

# Fake "skills" created by older slash-splitting — never active when CI/CD is the capability.
_CICD_FRAGMENT_NAMES = frozenset({"ci", "cd"})
_USER_CONFIRMED_STATUSES = frozenset({
    "USER_CONFIRMED_PROFESSIONAL",
    "USER_CONFIRMED_PROJECT",
    "USER_CONFIRMED_CERTIFICATION",
    "USER_CONFIRMED_TRAINING",
})

# Higher wins when multiple active rows exist for the same normalized skill.
EVIDENCE_STATUS_PRECEDENCE = {
    "USER_CONFIRMED_PROFESSIONAL": 100,
    "USER_CONFIRMED_PROJECT": 90,
    "USER_CONFIRMED_CERTIFICATION": 80,
    "USER_CONFIRMED_TRAINING": 70,
    "VERIFIED_FROM_RESUME": 60,
    "LEARNING_ONLY": 55,
    "REJECTED": 50,  # NOT_POSSESSED
    "INCOMPLETE": 10,
    "OBSOLETE": 0,
}


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
    "OBSOLETE",
}

RESOLVED_CONFIRMATION_STATUSES = {
    "VERIFIED_FROM_RESUME",
    "USER_CONFIRMED_PROFESSIONAL",
    "USER_CONFIRMED_PROJECT",
    "USER_CONFIRMED_CERTIFICATION",
    "USER_CONFIRMED_TRAINING",
    "LEARNING_ONLY",
    "REJECTED",
}

ACTIVE_EVIDENCE_STATUSES = RESOLVED_CONFIRMATION_STATUSES | {"INCOMPLETE"}

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

    # The ATS confirmation wizard deliberately asks for only the minimum truthful
    # evidence: level, usage context, and an optional short description.  Keep the
    # detailed/legacy evidence path below for callers that need richer records.
    if item.get("simple_confirmation"):
        experience_level = item.get("experience_level")
        if not experience_level:
            errors.append(f"{skill}: choose an experience level.")
        elif experience_level not in EXPERIENCE_LEVELS:
            errors.append(f"{skill}: invalid experience_level '{experience_level}'.")
        description = (item.get("usage_description") or "").strip()
        if not description:
            errors.append(f"{skill}: enter a brief description in your own words.")
        elif len(description) > 300:
            errors.append(f"{skill}: description must be 300 characters or fewer.")
        return errors

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
    workflow_run_id: str | None = None,
) -> list[UserConfirmedSkill]:
    """Validate, classify, and upsert evidence rows. Raises ValueError with all errors on failure.

    Draft stubs with answer=None are rejected — keep drafts frontend-only until the user
    submits a real decision (has_it / learning_only / not_possessed).
    """
    resume_evidence = resume_evidence or {}
    all_errors: list[str] = []
    persistable: list[dict[str, Any]] = []
    for item in items:
        answer = item.get("answer")
        skill = item.get("skill") or item.get("skill_name") or "This skill"
        if answer is None:
            all_errors.append(
                f"{skill}: no confirmation decision was provided. "
                "Select Yes/No (or learning only) and complete required evidence before saving."
            )
            continue
        if answer not in ANSWERS:
            all_errors.append(f"{skill}: invalid answer '{answer}'.")
            continue
        found_in_original = bool(
            resume_evidence.get(normalize_skill_name(item.get("skill") or item.get("skill_name") or ""), {}).get("present")
        )
        errors = validate_evidence_item(item, found_in_original=found_in_original, known_employers=known_employers)
        if errors:
            all_errors.extend(errors)
            continue
        persistable.append(item)
    if all_errors:
        raise ValueError("; ".join(all_errors))
    if not persistable:
        raise ValueError("No valid confirmation decisions were provided.")

    saved: list[UserConfirmedSkill] = []
    for item in persistable:
        skill_name = (item.get("skill") or item.get("skill_name") or "").strip()
        if not skill_name:
            continue
        # Canonical key shared by Step 4, readiness, and generation.
        normalized = normalize_skill_name(skill_name)
        found_in_original = bool(resume_evidence.get(normalized, {}).get("present"))
        status, _credit = classify_evidence_status(
            item, found_in_original=found_in_original, known_employers=known_employers
        )
        answer = item.get("answer")
        confirmed = answer in ("has_it", "learning_only")
        evidence_type = "Learning only" if answer == "learning_only" else (item.get("evidence_type") if answer == "has_it" else None)

        fields = dict(
            resume_id=resume_id,
            workflow_run_id=item.get("workflow_run_id") or workflow_run_id,
            is_active=True,
            superseded_at=None,
            requirement_type=item.get("requirement_type"),
            confirmed=confirmed,
            evidence_type=evidence_type,
            evidence_status=status,
            evidence_source=(
                "user_input" if answer in {"has_it", "learning_only"}
                else "user_denial" if answer == "not_possessed"
                else "user_selection"
            ),
            user_input_description=item.get("usage_description") if answer == "has_it" else None,
            ai_rewritten_description=None,
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

        # Upsert any prior row for this skill on this job (including older resume ids /
        # legacy Title-case normalized keys). Then supersede remaining active duplicates.
        candidates = [
            row for row in db.query(UserConfirmedSkill).filter(
                UserConfirmedSkill.user_id == user_id,
                UserConfirmedSkill.job_id == job_id,
            ).order_by(UserConfirmedSkill.id.desc()).all()
            if normalize_skill_name(row.normalized_skill_name or row.skill_name or "") == normalized
        ]
        existing = next(
            (
                row for row in candidates
                if getattr(row, "is_active", True) is not False
                and (resume_id is None or row.resume_id in (None, resume_id))
            ),
            None,
        ) or (candidates[0] if candidates else None)

        if existing:
            for key, value in fields.items():
                setattr(existing, key, value)
            existing.skill_name = skill_name
            existing.normalized_skill_name = normalized
            existing.updated_at = _now()
            db.add(existing)
            db.flush()
            winner = existing
        else:
            winner = UserConfirmedSkill(
                user_id=user_id,
                job_id=job_id,
                skill_name=skill_name,
                normalized_skill_name=normalized,
                **fields,
            )
            db.add(winner)
            db.flush()

        for row in candidates:
            if row.id == winner.id:
                continue
            if getattr(row, "is_active", True) is not False or row.evidence_status != "OBSOLETE":
                _deactivate_row(row, status="OBSOLETE")
                db.add(row)

        logger.info(
            "EVIDENCE_UPSERT user_id=%s job_id=%s resume_id=%s skill=%s normalized=%s "
            "evidence_id=%s evidence_status=%s is_active=%s workflow_run_id=%s",
            user_id,
            job_id,
            resume_id,
            skill_name,
            normalized,
            winner.id,
            winner.evidence_status,
            getattr(winner, "is_active", True),
            getattr(winner, "workflow_run_id", None),
        )
        saved.append(winner)

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


def _row_normalized(row: UserConfirmedSkill) -> str:
    return normalize_skill_name(row.normalized_skill_name or row.skill_name or "")


def _is_cicd_fragment(normalized: str) -> bool:
    return normalized in _CICD_FRAGMENT_NAMES


def _skill_key_set(items: list[dict[str, Any]] | None) -> set[str]:
    keys: set[str] = set()
    for item in items or []:
        key = normalize_skill_name(item.get("skill") or item.get("normalized_skill") or "")
        if key:
            keys.add(key)
    return keys


def _status_rank(status: str | None) -> int:
    return EVIDENCE_STATUS_PRECEDENCE.get(status or "", -1)


def _equivalent_resume_ids(
    db: Session,
    *,
    user_id: int,
    resume_id: int | None,
    resume_hash: str | None = None,
) -> set[int]:
    """Resume ids with identical parsed content (re-uploads of the same file)."""
    if resume_id is None:
        return set()
    equivalent = {resume_id}
    try:
        current = db.query(Resume).filter(Resume.id == resume_id, Resume.user_id == user_id).first()
    except Exception:
        return equivalent
    text = getattr(current, "parsed_text", None) if current is not None else None
    if not isinstance(text, str):
        # Unit tests / stubs without real Resume rows — scope to the given id only.
        return equivalent
    target_hash = resume_hash or content_hash(text)
    try:
        siblings = db.query(Resume).filter(Resume.user_id == user_id).all()
    except Exception:
        return equivalent
    for row in siblings or []:
        other_id = getattr(row, "id", None)
        other_text = getattr(row, "parsed_text", None)
        if other_id in (None, resume_id) or not isinstance(other_text, str):
            continue
        if content_hash(other_text) == target_hash:
            equivalent.add(int(other_id))
    return equivalent


def current_confirmation_queue(grouped_analysis: dict[str, Any]) -> set[str]:
    return _skill_key_set(
        list(grouped_analysis.get("weak_or_indirect_skills") or [])
        + list(grouped_analysis.get("missing_mandatory_skills") or [])
        + list(grouped_analysis.get("missing_preferred_skills") or [])
    )


def current_verified_skills(grouped_analysis: dict[str, Any]) -> set[str]:
    return _skill_key_set(grouped_analysis.get("verified_resume_skills") or [])


def is_active_confirmation(row: UserConfirmedSkill) -> bool:
    if getattr(row, "is_active", True) is False:
        return False
    return (row.evidence_status or "") not in {"OBSOLETE"}


def active_confirmed_skills(rows: list[UserConfirmedSkill]) -> list[UserConfirmedSkill]:
    return [row for row in rows if is_active_confirmation(row)]


def _deactivate_row(row: UserConfirmedSkill, *, status: str | None = "OBSOLETE") -> None:
    row.is_active = False
    row.superseded_at = _now()
    row.updated_at = _now()
    if status:
        row.evidence_status = status


def get_current_effective_evidence(
    db: Session,
    *,
    user_id: int,
    job_id: int,
    resume_id: int | None,
    workflow_run_id: str | None = None,
    resume_hash: str | None = None,
    persist_rebinds: bool = True,
) -> list[UserConfirmedSkill]:
    """Authoritative current evidence for Step 3/4, readiness, and generation.

    - Scopes to the current resume (plus equivalent-content re-uploads).
    - Applies status precedence so confirmed rows beat older INCOMPLETE duplicates.
    - Ensures at most one active effective row per normalized skill.
    """
    rows = load_confirmed_skills(db, user_id=user_id, job_id=job_id)
    equivalent_ids = _equivalent_resume_ids(
        db, user_id=user_id, resume_id=resume_id, resume_hash=resume_hash,
    )
    changed = False
    in_scope: list[UserConfirmedSkill] = []

    for row in rows:
        if not is_active_confirmation(row):
            continue
        row_resume = row.resume_id
        if resume_id is not None and row_resume not in (None, *equivalent_ids):
            # Different resume content — must not block or resolve the current run.
            if row.evidence_status == "INCOMPLETE":
                _deactivate_row(row, status="OBSOLETE")
                db.add(row)
                changed = True
            continue

        # Rebind equivalent-content confirmations onto the current resume id.
        if (
            resume_id is not None
            and row_resume is not None
            and row_resume != resume_id
            and row_resume in equivalent_ids
            and row.evidence_status in (_USER_CONFIRMED_STATUSES | {"LEARNING_ONLY", "REJECTED", "INCOMPLETE"})
        ):
            logger.info(
                "EVIDENCE_REBIND user_id=%s job_id=%s skill=%s evidence_id=%s "
                "from_resume_id=%s to_resume_id=%s status=%s",
                user_id,
                job_id,
                row.skill_name,
                row.id,
                row_resume,
                resume_id,
                row.evidence_status,
            )
            row.resume_id = resume_id
            if workflow_run_id:
                row.workflow_run_id = workflow_run_id
            row.updated_at = _now()
            db.add(row)
            changed = True

        # Normalize legacy Title-case keys onto the shared canonical form.
        canonical = _row_normalized(row)
        if canonical and row.normalized_skill_name != canonical:
            row.normalized_skill_name = canonical
            row.updated_at = _now()
            db.add(row)
            changed = True

        in_scope.append(row)

    by_skill: dict[str, list[UserConfirmedSkill]] = {}
    for row in in_scope:
        key = _row_normalized(row)
        if not key:
            continue
        by_skill.setdefault(key, []).append(row)

    effective: list[UserConfirmedSkill] = []
    for normalized, group in by_skill.items():
        group_sorted = sorted(
            group,
            key=lambda row: (
                _status_rank(row.evidence_status),
                row.id or 0,
            ),
            reverse=True,
        )
        winner = group_sorted[0]
        effective.append(winner)
        for loser in group_sorted[1:]:
            if getattr(loser, "is_active", True) is not False:
                logger.info(
                    "EVIDENCE_SUPERSEDE user_id=%s job_id=%s skill=%s "
                    "winner_id=%s winner_status=%s loser_id=%s loser_status=%s",
                    user_id,
                    job_id,
                    normalized,
                    winner.id,
                    winner.evidence_status,
                    loser.id,
                    loser.evidence_status,
                )
                _deactivate_row(loser, status="OBSOLETE")
                db.add(loser)
                changed = True

        if workflow_run_id and winner.workflow_run_id != workflow_run_id:
            if winner.evidence_status in (_USER_CONFIRMED_STATUSES | {"LEARNING_ONLY", "REJECTED"}):
                winner.workflow_run_id = workflow_run_id
                winner.updated_at = _now()
                db.add(winner)
                changed = True

        logger.info(
            "EFFECTIVE_EVIDENCE user_id=%s job_id=%s resume_id=%s workflow_run_id=%s "
            "skill=%s evidence_id=%s evidence_status=%s is_active=%s",
            user_id,
            job_id,
            resume_id,
            workflow_run_id,
            winner.skill_name,
            winner.id,
            winner.evidence_status,
            getattr(winner, "is_active", True),
        )

    if changed and persist_rebinds:
        db.commit()
        for row in effective:
            try:
                db.refresh(row)
            except Exception:
                pass

    return effective


def _belongs_in_current_step4(
    row: UserConfirmedSkill,
    *,
    confirmation_queue: set[str],
    verified_names: set[str],
) -> bool:
    """Whether a confirmation row may appear in the current Step 4 active list."""
    if not is_active_confirmation(row):
        return False
    normalized = _row_normalized(row)
    if not normalized:
        return False
    if _is_cicd_fragment(normalized) and ("ci/cd" in verified_names or "ci/cd" not in confirmation_queue):
        return False
    # Resume-verified skills never surface as confirmation rows.
    if normalized in verified_names:
        return False
    # Incomplete workflow stubs are only current when still in the confirmation queue.
    if row.evidence_status == "INCOMPLETE":
        return normalized in confirmation_queue
    # Active user decisions for this job/resume remain visible for Step 4 / API clients
    # even if the analysis queue later drops the skill (e.g. OR-covered or verified).
    if row.evidence_status in (_USER_CONFIRMED_STATUSES | {"LEARNING_ONLY", "REJECTED"}):
        return True
    if row.evidence_status == "VERIFIED_FROM_RESUME":
        return normalized in confirmation_queue
    return False


def reconcile_confirmations_with_analysis(
    db: Session,
    *,
    user_id: int,
    job_id: int,
    resume_id: int | None,
    grouped_analysis: dict[str, Any],
    workflow_run_id: str | None = None,
    resume_hash: str | None = None,
) -> list[UserConfirmedSkill]:
    """Deactivate stale confirmations, then return authoritative current effective rows.

    Historical rows remain in the DB for audit. VERIFIED_FROM_RESUME and OR-covered
    skills supersede old INCOMPLETE workflow state. Incomplete stubs are never reused
    across different resume content / workflow runs.
    """
    rows = load_confirmed_skills(db, user_id=user_id, job_id=job_id)
    verified = current_verified_skills(grouped_analysis)
    confirmation_queue = current_confirmation_queue(grouped_analysis)
    equivalent_ids = _equivalent_resume_ids(
        db, user_id=user_id, resume_id=resume_id, resume_hash=resume_hash,
    )
    changed = False
    inactive_historical = 0

    for row in rows:
        normalized = _row_normalized(row)
        should_deactivate = False

        if resume_id is not None and row.resume_id and row.resume_id not in equivalent_ids:
            # Different resume content: incompletes must not leak; confirmed stays historical.
            if row.evidence_status == "INCOMPLETE":
                should_deactivate = True
        elif workflow_run_id and row.workflow_run_id and row.workflow_run_id != workflow_run_id:
            if row.evidence_status == "INCOMPLETE":
                should_deactivate = True
        elif _is_cicd_fragment(normalized) and (
            "ci/cd" in verified or "ci/cd" not in confirmation_queue
        ):
            should_deactivate = True
        elif row.evidence_status == "INCOMPLETE" and (
            normalized in verified or (normalized and normalized not in confirmation_queue)
        ):
            should_deactivate = True
        elif normalized in verified and row.evidence_status == "INCOMPLETE":
            should_deactivate = True

        if should_deactivate and (
            getattr(row, "is_active", True) is not False or row.evidence_status != "OBSOLETE"
        ):
            _deactivate_row(row, status="OBSOLETE")
            changed = True
            inactive_historical += 1

    if changed:
        db.commit()

    effective = get_current_effective_evidence(
        db,
        user_id=user_id,
        job_id=job_id,
        resume_id=resume_id,
        workflow_run_id=workflow_run_id,
        resume_hash=resume_hash,
        persist_rebinds=True,
    )

    logger.info(
        "CURRENT ANALYSIS confirmations user_id=%s job_id=%s resume_id=%s workflow_run_id=%s "
        "verified=%s needs_confirmation=%s effective=%s inactive_marked=%s",
        user_id,
        job_id,
        resume_id,
        workflow_run_id,
        len(verified),
        len(confirmation_queue),
        len(effective),
        inactive_historical,
    )
    return effective


def _effective_rows_by_normalized(
    confirmation_rows: list[UserConfirmedSkill] | None,
) -> dict[str, UserConfirmedSkill]:
    """One row per normalized skill using evidence precedence (confirmed beats INCOMPLETE)."""
    by_normalized: dict[str, UserConfirmedSkill] = {}
    for row in active_confirmed_skills(confirmation_rows or []):
        if _is_cicd_fragment(_row_normalized(row)):
            continue
        key = _row_normalized(row)
        if not key:
            continue
        current = by_normalized.get(key)
        if current is None or _status_rank(row.evidence_status) > _status_rank(current.evidence_status):
            by_normalized[key] = row
        elif (
            current is not None
            and _status_rank(row.evidence_status) == _status_rank(current.evidence_status)
            and (row.id or 0) > (current.id or 0)
        ):
            by_normalized[key] = row
    return by_normalized


def _unsupported_requirement_warning(
    *,
    skill: str,
    normalized: str,
    row: UserConfirmedSkill | None,
    item: dict[str, Any],
) -> dict[str, Any]:
    """Build a non-blocking warning for a mandatory JD gap."""
    status = (
        (row.evidence_status if row else None)
        or item.get("evidence_status")
        or item.get("resume_status")
        or "MISSING"
    )
    if status == "INCOMPLETE":
        reason = (
            f"{skill} evidence is incomplete. It will be excluded from the optimized resume "
            "and may limit your maximum match score."
        )
        code = "INCOMPLETE_EVIDENCE"
    elif status == "REJECTED":
        reason = (
            f"{skill} was marked as not possessed. It will remain a Remaining Gap "
            "and may limit your maximum match score."
        )
        code = "NOT_POSSESSED"
    else:
        reason = (
            f"{skill} is not supported by your current resume or confirmed evidence. "
            "It will be excluded from the optimized resume and may limit your maximum match score."
        )
        code = "UNSUPPORTED_REQUIREMENT"
    return {
        "skill": skill,
        "normalized_skill": normalized,
        "reason": reason,
        "evidence_status": status,
        "severity": "warning",
        "code": code,
    }


def evaluate_generation_readiness(
    grouped_analysis: dict[str, Any],
    confirmation_rows: list[UserConfirmedSkill] | None = None,
) -> dict[str, Any]:
    """Decide whether resume generation may proceed from CURRENT analysis + active confirmations.

    Product rule: missing / incomplete / not-possessed JD requirements NEVER block generation.
    They become generation_warnings and Remaining Gaps that reduce reachable ATS score.
    Preferred unresolved items also never block.

    can_generate_resume is True whenever this readiness evaluator runs on a valid analysis
    payload. True workflow failures (no resume, no JD, analysis failed) are enforced by
    callers, not by mandatory evidence gaps.
    """
    confirmation_queue = current_confirmation_queue(grouped_analysis)
    verified_names = current_verified_skills(grouped_analysis)
    by_normalized = {
        key: row
        for key, row in _effective_rows_by_normalized(confirmation_rows).items()
        if key not in verified_names
    }
    generation_warnings: list[dict[str, Any]] = []
    current_required_confirmations = []
    mandatory_queue = [
        item for item in (grouped_analysis.get("weak_or_indirect_skills") or [])
        if (item.get("requirement_type") or item.get("importance") or "").upper() == "MANDATORY"
    ] + list(grouped_analysis.get("missing_mandatory_skills") or [])

    for item in mandatory_queue:
        skill = item.get("skill") or item.get("normalized_skill") or "Unknown"
        normalized = normalize_skill_name(skill)
        if not normalized or normalized in verified_names or _is_cicd_fragment(normalized):
            continue
        row = by_normalized.get(normalized)
        # USER_CONFIRMED_* and LEARNING_ONLY / REJECTED are "resolved" for confirmation UX
        # (do not keep asking). REJECTED / LEARNING_ONLY still stay unsupported for generation
        # via the scorer / generator evidence filters — only incomplete/missing warn here.
        if row and row.evidence_status in RESOLVED_CONFIRMATION_STATUSES:
            if row.evidence_status == "REJECTED":
                generation_warnings.append(_unsupported_requirement_warning(
                    skill=skill, normalized=normalized, row=row, item=item,
                ))
            continue
        current_required_confirmations.append(skill)
        generation_warnings.append(_unsupported_requirement_warning(
            skill=skill, normalized=normalized, row=row, item=item,
        ))

    preferred_unresolved = []
    current_optional_confirmations = []
    preferred_queue = [
        item for item in (grouped_analysis.get("weak_or_indirect_skills") or [])
        if (item.get("requirement_type") or item.get("importance") or "").upper() == "PREFERRED"
    ] + list(grouped_analysis.get("missing_preferred_skills") or [])
    for item in preferred_queue:
        skill = item.get("skill") or item.get("normalized_skill") or "Unknown"
        normalized = normalize_skill_name(skill)
        row = by_normalized.get(normalized)
        if row and row.evidence_status in RESOLVED_CONFIRMATION_STATUSES:
            continue
        current_optional_confirmations.append(skill)
        preferred_unresolved.append({
            "skill": skill,
            "normalized_skill": normalized,
            "reason": "Preferred confirmation is optional and does not block generation",
            "evidence_status": (row.evidence_status if row else item.get("resume_status") or "NEEDS_CONFIRMATION"),
            "severity": "warning",
        })

    # Evidence gaps are warnings only — never hard blockers.
    can_generate = True
    # blocking_reasons stays empty for evidence; reserved for true system failures at callers.
    blocking_reasons: list[dict[str, Any]] = []

    logger.info(
        "generation readiness can_generate_resume=%s mandatory_warnings=%s preferred_unresolved=%s queue=%s",
        can_generate,
        [item["skill"] for item in generation_warnings],
        current_optional_confirmations,
        sorted(confirmation_queue),
    )
    return {
        "can_generate_resume": can_generate,
        "blocking_reasons": blocking_reasons,
        "generation_warnings": generation_warnings,
        "preferred_unresolved": preferred_unresolved,
        "current_required_confirmations": current_required_confirmations,
        "current_optional_confirmations": current_optional_confirmations,
        "mandatory_confirmation_count": len(mandatory_queue),
        "mandatory_resolved_count": len(mandatory_queue) - len(current_required_confirmations),
        "current_unresolved_required_count": len(current_required_confirmations),
    }


def summarize_active_evidence(
    grouped_analysis: dict[str, Any],
    confirmation_rows: list[UserConfirmedSkill] | None = None,
) -> dict[str, Any]:
    """Step 4 counts and lists from the SAME current-analysis dataset only."""
    confirmation_queue = current_confirmation_queue(grouped_analysis)
    verified_names = current_verified_skills(grouped_analysis)
    all_rows = list(confirmation_rows or [])

    display_rows = [
        row for row in all_rows
        if _belongs_in_current_step4(
            row,
            confirmation_queue=confirmation_queue,
            verified_names=verified_names,
        )
    ]
    history_rows = [
        row for row in all_rows
        if row not in display_rows and (
            not is_active_confirmation(row)
            or row.evidence_status == "INCOMPLETE"
            or _row_normalized(row) in verified_names
            or _is_cicd_fragment(_row_normalized(row))
        )
    ]

    user_confirmed = [row for row in display_rows if row.evidence_status in _USER_CONFIRMED_STATUSES]
    learning_only = [row for row in display_rows if row.evidence_status == "LEARNING_ONLY"]
    not_possessed = [row for row in display_rows if row.evidence_status == "REJECTED"]
    incomplete = [row for row in display_rows if row.evidence_status == "INCOMPLETE"]

    counts = {
        "verified_from_resume": len(grouped_analysis.get("verified_resume_skills") or []),
        "user_confirmed_with_evidence": len(user_confirmed),
        "learning_only": len(learning_only),
        "not_possessed": len(not_possessed),
        "incomplete": len(incomplete),
    }
    # Consistency guard: displayed incomplete count must match listed incomplete rows.
    assert counts["incomplete"] == len(incomplete)

    return {
        "verified_from_resume_count": counts["verified_from_resume"],
        "verified_from_resume": list(grouped_analysis.get("verified_resume_skills") or []),
        "user_confirmed": [confirmed_skill_to_dict(row) for row in user_confirmed],
        "learning_only": [confirmed_skill_to_dict(row) for row in learning_only],
        "not_possessed": [confirmed_skill_to_dict(row) for row in not_possessed],
        "incomplete": [confirmed_skill_to_dict(row) for row in incomplete],
        "unresolved_required": [confirmed_skill_to_dict(row) for row in incomplete if (row.requirement_type or "").upper() == "MANDATORY"],
        "optional_preferred": [
            confirmed_skill_to_dict(row) for row in display_rows
            if (row.requirement_type or "").upper() == "PREFERRED"
        ],
        "evidence": [confirmed_skill_to_dict(row) for row in display_rows],
        "evidence_history": [confirmed_skill_to_dict(row) for row in history_rows],
        "counts": counts,
    }


def delete_confirmed_skill(db: Session, *, user_id: int, job_id: int, evidence_id: int) -> bool:
    """Deactivate a current user confirmation (do not delete resume-derived evidence)."""
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
    if row.evidence_status == "VERIFIED_FROM_RESUME":
        return False
    _deactivate_row(row, status="OBSOLETE")
    db.add(row)
    db.commit()
    return True


def confirmed_skill_to_dict(row: UserConfirmedSkill) -> dict[str, Any]:
    credit = credit_for_row(row)
    confirmation_status = {
        "VERIFIED_FROM_RESUME": "resume_verified",
        "REJECTED": "user_denied",
        "INCOMPLETE": "unconfirmed",
    }.get(row.evidence_status, "user_confirmed")
    experience_context = {
        "Current employment": "professional",
        "Previous employment": "professional",
        "Professional project": "professional",
        "Personal project": "personal_project",
        "Academic project": "academic",
        "Certification": "certification",
        "Training/coursework": "training",
        "Learning only": "training",
    }.get(row.evidence_type)
    evidence_strength = {
        "VERIFIED_FROM_RESUME": "strongest",
        "USER_CONFIRMED_PROFESSIONAL": "strong",
        "USER_CONFIRMED_PROJECT": "medium",
        "USER_CONFIRMED_CERTIFICATION": "medium",
        "USER_CONFIRMED_TRAINING": "weak",
        "LEARNING_ONLY": "weak",
        "REJECTED": "unavailable",
        "INCOMPLETE": "unavailable",
    }.get(row.evidence_status, "unavailable")
    return {
        "id": row.id,
        "skill": row.skill_name,
        "normalized_skill_name": row.normalized_skill_name,
        "canonical_skill": normalize_skill_name(row.skill_name),
        "confirmation_status": confirmation_status,
        "requirement_type": row.requirement_type,
        "confirmed": row.confirmed,
        "is_active": bool(getattr(row, "is_active", True)),
        "workflow_run_id": getattr(row, "workflow_run_id", None),
        "evidence_type": row.evidence_type,
        "evidence_status": row.evidence_status,
        "evidence_source": row.evidence_source or "user_selection",
        "user_input_description": row.user_input_description,
        "ai_rewritten_description": row.ai_rewritten_description,
        "experience_context": experience_context,
        "description": row.usage_description,
        "source": "resume" if confirmation_status == "resume_verified" else (row.evidence_source or "user_selection"),
        "evidence_strength": evidence_strength,
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


def _is_accepted_evidence_row(row: UserConfirmedSkill) -> bool:
    """True for resume-verified or accepted user-confirmed rows (not incomplete/rejected/denied)."""
    status = row.evidence_status or ""
    if status in {"INCOMPLETE", "REJECTED", "OBSOLETE"}:
        return False
    if getattr(row, "is_active", True) is False:
        return False
    if status == "VERIFIED_FROM_RESUME":
        return True
    if status in _USER_CONFIRMED_STATUSES or status == "LEARNING_ONLY":
        # Only accept explicit user-input evidence paths. Historical/unknown sources stay out.
        source = (row.evidence_source or "").strip().lower()
        return source in {"user_input", "user_selection"}
    return False


def to_scoring_evidence(rows: list[UserConfirmedSkill]) -> dict[str, dict[str, Any]]:
    """Map confirmations into scoring-ready evidence. INCOMPLETE/REJECTED never award credit.

    Keys are dual-indexed under normalize_skill_name AND normalize_keyword display forms so
    calculate_weighted_ats_score requirement lookups always resolve.
    """
    from app.services.keyword_utils import normalize_keyword

    evidence: dict[str, dict[str, Any]] = {}
    for row in _effective_rows_by_normalized(rows).values():
        if not _is_accepted_evidence_row(row):
            continue
        key = _row_normalized(row)
        entry = {
            "credit": credit_for_row(row),
            "evidence_status": row.evidence_status,
            "evidence_type": row.evidence_type,
            "usage_description": row.usage_description or "",
        }
        display = normalize_keyword(row.skill_name or key)
        for alias in {key, display, str(display).lower(), normalize_skill_name(display), row.skill_name}:
            if alias:
                evidence[alias] = entry
    return evidence


def to_generation_evidence(rows: list[UserConfirmedSkill]) -> dict[str, dict[str, Any]]:
    """Skill -> evidence used by the resume generator. Excludes INCOMPLETE/REJECTED —
    truthful inclusion (certifications/training) is independent of scoring weight."""
    from app.services.keyword_utils import normalize_keyword

    evidence: dict[str, dict[str, Any]] = {}
    for row in _effective_rows_by_normalized(rows).values():
        if not _is_accepted_evidence_row(row):
            continue
        payload = confirmed_skill_to_dict(row)
        key = _row_normalized(row)
        display = normalize_keyword(row.skill_name or key)
        for alias in {key, display, row.skill_name}:
            if alias:
                evidence[alias] = payload
    return evidence


def build_canonical_supported_evidence(
    *,
    verified_resume_skills: list[str] | None,
    confirmation_rows: list[UserConfirmedSkill] | None,
) -> dict[str, Any]:
    """One supported-evidence set for scoring, generation, validation, and Remaining Gaps.

    SUPPORTED = VERIFIED_FROM_RESUME ∪ ACCEPTED_USER_CONFIRMED
    (excludes INCOMPLETE / REJECTED / inactive / stale).
    """
    from app.services.keyword_utils import normalize_keyword

    scoring = to_scoring_evidence(confirmation_rows or [])
    generation = to_generation_evidence(confirmation_rows or [])
    verified = []
    for skill in verified_resume_skills or []:
        key = normalize_skill_name(skill)
        if key:
            verified.append(key)
    supported_keys: set[str] = set(verified)
    for key in list(scoring.keys()) + list(generation.keys()):
        supported_keys.add(normalize_skill_name(key))
        supported_keys.add(str(normalize_keyword(key)).lower())
        supported_keys.add(normalize_keyword(key))
    return {
        "scoring_evidence": scoring,
        "generation_evidence": generation,
        "verified_resume_skills": verified,
        "supported_skill_keys": supported_keys,
        "confirmed_skill_names": sorted({
            (row.skill_name or row.normalized_skill_name)
            for row in (confirmation_rows or [])
            if row.evidence_status in _USER_CONFIRMED_STATUSES
            and getattr(row, "is_active", True) is not False
        }),
    }


def skill_supported_by_evidence(skill: str, supported_keys: set[str]) -> bool:
    """True when skill (or any OR alternative) is covered by the supported evidence set."""
    from app.services.keyword_utils import normalize_keyword

    if not skill or not supported_keys:
        return False
    # OR labels such as "AWS or Azure" / "Docker or Kubernetes"
    parts = [part.strip() for part in re.split(r"\s+or\s+", str(skill), flags=re.I) if part.strip()]
    candidates = parts if len(parts) > 1 else [skill]
    for part in candidates:
        forms = {
            part,
            part.lower(),
            normalize_skill_name(part),
            normalize_keyword(part),
            str(normalize_keyword(part)).lower(),
        }
        if forms & supported_keys:
            return True
    return False
