"""Final validation gate before persisting an optimized resume."""

from __future__ import annotations

import logging
import re

from app.services.keyword_utils import contains_keyword
from app.services.skill_analysis_service import (
    get_skill_evidence,
    normalize_skill_name,
    skill_category,
)
from app.services.resume_assembly_service import (
    canonical_section_kind,
    headings_match,
    normalize_section_heading,
)
from app.services.resume_generator import FAKE_METRIC_PATTERN
from app.services.technology_catalog import PROFESSIONAL_ROLE_CLAIMS


logger = logging.getLogger(__name__)


def supported_by_authoritative_evidence(
    skill: str,
    *,
    original_text: str,
    allowed_confirmed_skills: list[str] | None = None,
) -> bool:
    """Use the shared evidence engine for every claim category.

    An explicit/normalized resume match is evidence. An indirect semantic
    signal still requires an accepted user confirmation, preserving the
    distinction between participation and an invented claim.
    """
    evidence = get_skill_evidence(skill, original_text or "")
    if evidence.get("resume_status") == "VERIFIED":
        return True
    target = normalize_skill_name(skill)
    return any(
        normalize_skill_name(confirmed) == target
        for confirmed in (allowed_confirmed_skills or [])
        if str(confirmed or "").strip()
    )


def unsupported_claim_message(skill: str) -> str:
    return f"Unsupported {skill_category(skill)} introduced: {skill}"


def validate_final_resume(
    original_text: str,
    generated_text: str,
    original_structure: dict,
    generated_sections: list[dict],
    unsupported_keywords: list[str],
    selected_technologies: list[str],
    allowed_confirmed_skills: list[str] | None = None,
    confirmed_evidence_text: str | None = None,
    workflow_id: int | None = None,
) -> dict:
    warnings = []
    errors = []

    # Contact / identity
    for field in ("email", "phone", "name"):
        value = (original_structure or {}).get(field) or ""
        if value and value not in (generated_text or ""):
            errors.append(f"Protected field changed or missing: {field}")
            logger.warning(
                "TRUTHFULNESS_VALIDATION_FAILED workflow_id=%s rule=protected_field "
                "section=contact field=%s reason=missing_or_changed source=resume",
                workflow_id or "n/a",
                field,
            )

    for link in (original_structure or {}).get("links") or []:
        if link and link not in (generated_text or ""):
            warnings.append(f"Contact link missing from generated resume: {link}")

    # Section order / headings — original sections must keep their relative order and
    # none may be removed, but truthful NEW sections (e.g. a "Projects" or "Currently
    # Learning" section added for confirmed evidence) may be appended without failing.
    # Harmless heading formatting (case, trailing colon, whitespace) is normalized, and
    # known wording aliases ("Work Experience" / "Professional Experience", "Summary" /
    # "Professional Summary", etc.) are canonicalized to the same semantic key via
    # canonical_section_kind — the same alias table already used to render/assemble
    # resumes elsewhere, reused here rather than duplicated. Headings that don't match a
    # known section type (custom/"other" sections) fall back to normalized raw-text
    # comparison so genuine reordering of a custom section heading is still caught.
    def _canonical_key(heading: str | None) -> str:
        kind = canonical_section_kind(heading)
        if kind == "other":
            return f"other:{normalize_section_heading(heading)}"
        return kind

    original_order = (original_structure or {}).get("section_order") or [
        s.get("heading") for s in (original_structure or {}).get("sections") or []
    ]
    generated_order = [s.get("heading") for s in generated_sections or []]
    original_keys = [_canonical_key(item) for item in original_order if item]
    generated_keys = [_canonical_key(item) for item in generated_order if item]

    def _find_subsequence_mismatch(needle: list, haystack: list) -> int | None:
        """Two-pointer subsequence check. Returns the index of the first
        `needle` entry that could not be located (in order) in `haystack`,
        or None if `needle` is fully contained as a subsequence."""
        hpos = 0
        for i, item in enumerate(needle):
            while hpos < len(haystack) and haystack[hpos] != item:
                hpos += 1
            if hpos >= len(haystack):
                return i
            hpos += 1
        return None

    mismatch_index = _find_subsequence_mismatch(original_keys, generated_keys) if original_keys else None
    structure_preserved = mismatch_index is None
    if original_keys and not structure_preserved:
        errors.append("Section order or headings were not preserved.")
        logger.warning(
            "TRUTHFULNESS_VALIDATION_FAILED workflow_id=%s rule=section_order "
            "section=structure field=section_order reason=order_or_heading_changed "
            "source=resume expected_sections=%s actual_sections=%s first_mismatch_index=%s",
            workflow_id or "n/a",
            original_keys,
            generated_keys,
            mismatch_index,
        )

    # Employers / dates
    for emp in (original_structure or {}).get("employments") or []:
        company = emp.get("company") or ""
        dates = emp.get("dates") or ""
        if company and company not in (generated_text or ""):
            errors.append(f"Company name missing or changed: {company}")
            logger.warning(
                "TRUTHFULNESS_VALIDATION_FAILED workflow_id=%s rule=employer_preserved "
                "section=experience field=company reason=missing_or_changed source=resume",
                workflow_id or "n/a",
            )
        if dates and dates not in (generated_text or ""):
            errors.append(f"Employment dates missing or changed: {dates}")
            logger.warning(
                "TRUTHFULNESS_VALIDATION_FAILED workflow_id=%s rule=dates_preserved "
                "section=experience field=dates reason=missing_or_changed source=resume",
                workflow_id or "n/a",
            )
        client = emp.get("client") or ""
        if client and client not in (generated_text or ""):
            warnings.append(f"Client name missing from generated resume: {client}")

    # Education / certifications sections exact-ish preservation
    for section in (original_structure or {}).get("sections") or []:
        heading = (section.get("heading") or "").lower()
        if re.search(r"education|certification|certificate", heading):
            original_content = (section.get("content") or "").strip()
            generated_content = ""
            for gen in generated_sections or []:
                if headings_match(gen.get("heading"), section.get("heading")):
                    generated_content = (gen.get("content") or "").strip()
                    break
            if original_content and generated_content != original_content:
                errors.append(f"Protected section modified: {section.get('heading')}")
                logger.warning(
                    "TRUTHFULNESS_VALIDATION_FAILED workflow_id=%s rule=protected_section "
                    "section=%s field=content reason=modified source=resume",
                    workflow_id or "n/a",
                    section.get("heading") or "education_or_certification",
                )

    allowed_confirmed = [
        str(skill).strip() for skill in (allowed_confirmed_skills or []) if str(skill).strip()
    ]
    evidence_baseline = "\n".join(
        part for part in [original_text or "", confirmed_evidence_text or ""] if part
    )

    # Every unresolved JD claim (technology, methodology, practice, etc.) must
    # be evidence-backed before it can appear in a generated resume. The
    # category changes diagnostics only; evidence remains authoritative.
    for skill in unsupported_keywords or []:
        if not contains_keyword(generated_text or "", skill):
            continue
        if supported_by_authoritative_evidence(
            skill,
            original_text=original_text,
            allowed_confirmed_skills=allowed_confirmed,
        ):
            continue
        category = skill_category(skill)
        errors.append(unsupported_claim_message(skill))
        logger.warning(
            "TRUTHFULNESS_VALIDATION_FAILED workflow_id=%s rule=unsupported_%s "
            "section=skills_or_experience field=%s reason=introduced_unsupported "
            "unsupported_terms=%s source=job_description",
            workflow_id or "n/a",
            category.replace(" ", "_"),
            category,
            [skill],
        )

    # A methodology/tool mention does not establish a more senior or distinct
    # professional role. Keep this separate from requirement categories: a
    # user-confirmed Scrum skill is not proof that the candidate was a Scrum
    # Master, just as a confirmed framework is not proof of an architect title.
    for role in PROFESSIONAL_ROLE_CLAIMS:
        if contains_keyword(generated_text or "", role) and not contains_keyword(original_text or "", role):
            errors.append(f"Unsupported professional role introduced: {role}")
            logger.warning(
                "TRUTHFULNESS_VALIDATION_FAILED workflow_id=%s rule=unsupported_professional_role "
                "section=skills_or_experience field=role reason=introduced_unsupported "
                "unsupported_terms=%s source=job_description",
                workflow_id or "n/a",
                [role],
            )

    # Fake metrics — allow only if present in original resume OR confirmed evidence text.
    if FAKE_METRIC_PATTERN.search(generated_text or "") and not FAKE_METRIC_PATTERN.search(evidence_baseline):
        errors.append("Generated resume introduced metrics not present in the original resume.")
        logger.warning(
            "TRUTHFULNESS_VALIDATION_FAILED workflow_id=%s rule=invented_metrics "
            "section=generated_resume field=metrics reason=invented_metric source=resume",
            workflow_id or "n/a",
        )

    if not (generated_text or "").strip():
        errors.append("Generated resume is empty.")

    if len((generated_text or "").split()) < 40:
        warnings.append("Generated resume is unusually short.")

    # Selected technologies should appear at least once
    missing_selected = [
        tech for tech in (selected_technologies or [])[:10]
        if not contains_keyword(generated_text or "", tech)
    ]
    if missing_selected:
        warnings.append(
            "Some selected technologies were not visible in the generated text: "
            + ", ".join(missing_selected)
        )

    status = "passed"
    if errors:
        status = "failed"
    elif warnings:
        status = "passed_with_warnings"

    return {
        "status": status,
        "warnings": warnings,
        "errors": errors,
        "protectedFieldsVerified": not any("Protected field" in e for e in errors),
        "structurePreserved": structure_preserved and not any("Section order" in e for e in errors),
    }
