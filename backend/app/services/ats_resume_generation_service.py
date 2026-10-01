"""
ATS resume generation via configurable LLM, with deterministic fallback.

Resume and JD content are treated as untrusted source documents, not instructions.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.services.llm_client import LLMClient, LLMGenerationError, PROMPT_VERSION, llm_config, safe_llm_failure_reason
from app.services.llm_response_normalizer import (
    build_schema_repair_prompt,
    log_schema_validation_failure,
    normalize_generation_response,
)
from app.services.resume_assembly_service import assemble_optimized_resume
from app.services.resume_generator import generate_optimized_resume


logger = logging.getLogger(__name__)


class GenerationValidationError(RuntimeError):
    pass


def _fallback_reason(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return "schema_validation_failed"
    if isinstance(error, GenerationValidationError):
        # All GenerationValidationError raises come from truthfulness gates
        # (_validate_llm_result), not from Pydantic schema validation.
        return "truthfulness_validation_failed"
    return safe_llm_failure_reason(error)


def _generation_metadata(
    llm: LLMClient,
    *,
    llm_used: bool,
    fallback_reason: str | None = None,
    generation_duration_ms: int | None = None,
    llm_attempt_count: int = 0,
    llm_response_received: bool = False,
    schema_validation_passed: bool = False,
    json_repair_used: bool = False,
    schema_repair_retry_used: bool = False,
) -> dict[str, Any]:
    fallback_used = not llm_used
    return {
        "generation_method": "llm" if llm_used else "deterministic_fallback",
        "generation_provider": getattr(llm, "provider", "none"),
        "generation_model": getattr(llm, "model", "deterministic-fallback"),
        "llm_used": llm_used,
        "llm_provider": getattr(llm, "provider", "none"),
        "llm_model": getattr(llm, "model", "deterministic-fallback"),
        "llm_service": getattr(llm, "service_name", None),
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "generation_duration_ms": generation_duration_ms,
        "llm_attempt_count": llm_attempt_count,
        "llm_response_received": llm_response_received,
        "schema_validation_passed": schema_validation_passed,
        "json_repair_used": json_repair_used,
        "schema_repair_retry_used": schema_repair_retry_used,
    }


class RewrittenBullet(BaseModel):
    text: str
    supported_by: list[str] = Field(default_factory=list)


class StructuredOptimizerResponse(BaseModel):
    generated_resume_plain_text: str = Field(min_length=1)
    rewritten_summary: str = ""
    rewritten_bullets: list[RewrittenBullet] = Field(default_factory=list)
    keyword_evidence: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    factual_preservation_confirmed: bool = True

    @field_validator("keyword_evidence", mode="before")
    @classmethod
    def _coerce_keyword_evidence(cls, value: Any) -> list:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return []


SCHEMA_REPAIR_SYSTEM_PROMPT = """
You correct JSON structure only. Never invent resume facts.
Return one JSON object matching the required schema exactly.
No markdown. No explanation. JSON only.
""".strip()

SYSTEM_PROMPT = """
You optimize resumes using ONLY original_resume_facts and accepted_user_evidence.
Job requirements are targeting hints, never candidate facts.

Rules:
- Preserve identity, employers, dates, titles, education, certifications, project names.
- You are producing a JOB-TARGETED VERSION OF THE CANDIDATE'S EXISTING RESUME, not a new resume.
  The candidate is the same person; employers, projects, and career history stay truthful. Only
  wording, emphasis, keyword placement, and structure are optimized for this specific job.
- Professional Summary: improve what is weak, preserve what is strong. Target about 120-180 words
  (roughly 6-8 recruiter-readable lines) for an experienced candidate — this is a target, not a rigid
  count; do not pad with filler to hit it, and do not cut it short either. Where supported, weave in:
  target role, actual years of experience, core technical specialization, 6-10 of the most important
  SUPPORTED job-relevant technologies (from supported_keywords), major architecture/platform
  experience, relevant cloud/database/DevOps/testing experience, relevant domain experience, and a
  concise value statement — as natural professional sentences a recruiter reads in 10-20 seconds, NOT
  a comma-separated technology list. Keep every factual detail already present in the ORIGINAL
  summary — seniority, target role/family, and every technology/skill already named there — even
  while rewriting the wording and structure; never drop a fact just because the new phrasing is
  shorter. If the original summary states a specific number of years of experience (e.g. "8 years"),
  the new summary MUST explicitly restate that same number of years — do not replace it with only an
  employer name, a start date, or "since <year>" phrasing.
- Professional Experience bullets: each should read as ACTION + TECHNOLOGY/IMPLEMENTATION +
  PURPOSE/RESPONSIBILITY (+ IMPACT only when the original resume or confirmed evidence actually
  supports a real outcome). Prefer strong verbs (Developed, Designed, Implemented, Integrated,
  Automated, Built, Configured, Deployed, Enhanced, Optimized, Validated, Tested, Troubleshot,
  Collaborated) over weak lead-ins (Worked on, Involved in, Responsible for) when the evidence
  supports a stronger verb. A bullet must describe what the candidate actually did — it must NEVER
  define what a technology is (e.g. never "Selenium is a testing tool", "Kafka is a messaging
  platform", "Docker is a containerization platform" — these are forbidden in any form). Only weave a
  supported_keywords technology into an EXISTING relevant bullet or a bullet backed by real evidence —
  never add a standalone bullet whose only purpose is to mention one keyword.
- Prioritize roughly 8-12 of the most important supported_keywords when that many are genuinely
  supported — fewer is fine and expected when fewer are truthfully supported; never fabricate a
  technology just to reach a count. Spread them naturally across Summary, Technical Skills, and
  Experience/Projects — do not repeat the same keyword list verbatim in every section.
- Never invent employers, dates, skills, metrics, or experience.
- Never add unsupported_keywords or user_denied_skills.
- Keep section order. Separate professional vs project/learning evidence.

RETURN JSON ONLY.
NO markdown.
NO ```json fences.
NO explanation.
NO prose before or after JSON.

Required JSON object (exact field names):
{
  "generated_resume_plain_text": "<complete resume string>",
  "rewritten_summary": "<string, may be empty>",
  "rewritten_bullets": [{"text": "<string>", "supported_by": ["E1","UC1"]}],
  "keyword_evidence": [],
  "warnings": [],
  "factual_preservation_confirmed": true
}

Output must differ from the original resume while staying truthful.
""".strip()


_JD_DESCRIPTION_CHAR_LIMIT = 1600
_EVIDENCE_CLAIM_LIMIT = 24
_RESUME_FACTS_CHAR_LIMIT = 4500


def _truncate(text: str, limit: int) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n…[truncated]"


def _compact_resume_facts(resume_text: str, structure: dict) -> str:
    """Prefer structured essentials over dumping the full resume twice."""
    lines: list[str] = []
    header = structure.get("header") or {}
    contact_bits = [
        header.get("name") or "",
        header.get("email") or "",
        header.get("phone") or "",
        header.get("location") or "",
    ]
    contact = " | ".join(bit for bit in contact_bits if bit)
    if contact:
        lines.append(contact)
    links = header.get("links") or []
    if links:
        lines.append("Links: " + ", ".join(str(link) for link in links[:4] if link))

    summary = (structure.get("summary") or "").strip()
    if summary:
        lines.append("Summary:")
        lines.append(_truncate(summary, 700))

    skills = structure.get("skills") or structure.get("skill_lines") or []
    if isinstance(skills, list) and skills:
        lines.append("Skills: " + ", ".join(str(skill) for skill in skills[:40] if skill))
    elif isinstance(skills, str) and skills.strip():
        lines.append("Skills: " + _truncate(skills, 500))

    for employment in (structure.get("employments") or [])[:6]:
        title_line = " | ".join(
            str(part)
            for part in [
                employment.get("employer") or employment.get("company"),
                employment.get("title") or employment.get("role"),
                employment.get("dates") or employment.get("date_range"),
            ]
            if part
        )
        if title_line:
            lines.append(title_line)
        for bullet in (employment.get("bullets") or [])[:6]:
            lines.append(f"- {_truncate(str(bullet), 220)}")

    for project in (structure.get("projects") or [])[:4]:
        name = project.get("name") or project.get("title")
        if name:
            lines.append(f"Project: {name}")
        for bullet in (project.get("bullets") or [])[:4]:
            lines.append(f"- {_truncate(str(bullet), 200)}")

    education = structure.get("education") or []
    if education:
        lines.append("Education:")
        if isinstance(education, list):
            for item in education[:4]:
                lines.append(f"- {_truncate(str(item if not isinstance(item, dict) else item.get('raw') or item), 180)}")
        else:
            lines.append(_truncate(str(education), 400))

    certifications = structure.get("certifications") or []
    if certifications:
        lines.append("Certifications:")
        if isinstance(certifications, list):
            for item in certifications[:6]:
                lines.append(f"- {_truncate(str(item if not isinstance(item, dict) else item.get('name') or item), 120)}")
        else:
            lines.append(_truncate(str(certifications), 300))

    compact = "\n".join(line for line in lines if line).strip()
    if len(compact) >= 200:
        return _truncate(compact, _RESUME_FACTS_CHAR_LIMIT)
    return _truncate(resume_text or "", _RESUME_FACTS_CHAR_LIMIT)


def _evidence_claims(structure: dict) -> dict[str, str]:
    claims = {}
    index = 1
    for employment in structure.get("employments") or []:
        for bullet in employment.get("bullets") or []:
            if index > _EVIDENCE_CLAIM_LIMIT:
                return claims
            claims[f"E{index}"] = bullet
            index += 1
    for project in structure.get("projects") or []:
        for bullet in project.get("bullets") or []:
            if index > _EVIDENCE_CLAIM_LIMIT:
                return claims
            claims[f"E{index}"] = bullet
            index += 1
    return claims


def _user_evidence_claims(confirmed_evidence: dict[str, dict] | None) -> dict[str, dict]:
    """Build UC# claims from accepted user evidence only (deduped by skill)."""
    claims = {}
    seen_skills: set[str] = set()
    index = 1
    blocked = {"REJECTED", "INCOMPLETE", "OBSOLETE", "NOT_POSSESSED"}
    accepted = {
        "USER_CONFIRMED_PROFESSIONAL",
        "USER_CONFIRMED_PROJECT",
        "USER_CONFIRMED_CERTIFICATION",
        "USER_CONFIRMED_TRAINING",
        "LEARNING_ONLY",
        "VERIFIED_FROM_RESUME",
        "",
    }
    for skill, item in (confirmed_evidence or {}).items():
        status = str((item or {}).get("evidence_status") or "").upper()
        if status in blocked:
            continue
        if status not in accepted:
            continue
        skill_key = str(skill or "").strip().lower()
        if not skill_key or skill_key in seen_skills:
            continue
        seen_skills.add(skill_key)
        claims[f"UC{index}"] = {
            "skill": skill,
            "evidence_type": item.get("evidence_type"),
            "evidence_status": item.get("evidence_status"),
            "employer_name": item.get("employer_name"),
            "role_title": item.get("role_title"),
            "project_name": item.get("project_name"),
            "start_date": item.get("start_date"),
            "end_date": item.get("end_date"),
            "currently_using": item.get("currently_using"),
            "description": item.get("usage_description") or item.get("work_completed"),
        }
        index += 1
    return claims


def _compact_job_requirements(job) -> dict[str, str]:
    description = str(getattr(job, "description", "") or "").strip()
    if len(description) > _JD_DESCRIPTION_CHAR_LIMIT:
        description = description[:_JD_DESCRIPTION_CHAR_LIMIT].rstrip() + "\n…[truncated]"
    skills = str(getattr(job, "skills_required", "") or "").strip()
    if len(skills) > 800:
        skills = skills[:800].rstrip() + "…"
    return {
        "title": str(getattr(job, "title", "") or ""),
        "company": str(getattr(job, "company", "") or ""),
        "description": description,
        "skills_required": skills,
    }


def _build_generation_payload(
    *,
    resume_text: str,
    job,
    injection_plan: dict,
    structure: dict,
    confirmed_evidence: dict[str, dict] | None,
    denied_skills: list[str] | None,
    verified_resume_skills: list[str] | None,
    correction_instructions: str | None,
) -> dict[str, Any]:
    user_evidence_claims = _user_evidence_claims(confirmed_evidence)
    supported = list(dict.fromkeys(injection_plan.get("supportedKeywords") or []))[:18]
    unsupported = list(dict.fromkeys(injection_plan.get("unsupportedKeywords") or []))[:18]
    placement = injection_plan.get("placement") or {}
    compact_placement = {
        key: (value[:6] if isinstance(value, list) else value)
        for key, value in placement.items()
        if key in {"summary", "skills", "experience", "projects"}
    }
    # Prefer structured resume facts; keep claim IDs and claim bodies aligned.
    evidence_claims = _evidence_claims(structure)
    claim_ids = list(evidence_claims.keys())[:_EVIDENCE_CLAIM_LIMIT]
    aligned_claims = {
        key: _truncate(evidence_claims[key], 160)
        for key in claim_ids
    }
    payload = {
        "original_resume_facts": _compact_resume_facts(resume_text, structure or {}),
        "accepted_user_evidence": user_evidence_claims,
        "verified_resume_skills": list(verified_resume_skills or [])[:30],
        "user_denied_skills": list(denied_skills or [])[:18],
        "job_requirements": _compact_job_requirements(job),
        "supported_keywords": supported,
        "unsupported_keywords": unsupported,
        "placement_hints": compact_placement,
        "evidence_claim_ids": claim_ids,
        "evidence_claims": aligned_claims,
        "instructions": (
            "Rewrite truthfully for this job using supported_keywords and "
            "accepted_user_evidence only. Change summary, skill order, and several bullets. "
            "Never invent employers, dates, metrics, or technologies. "
            "Keep unsupported_keywords as gaps. Return JSON only. "
            "In rewritten_bullets.supported_by use only evidence_claim_ids (E#) "
            "or accepted_user_evidence keys (UC#)."
        ),
    }
    if correction_instructions:
        payload["correction_instructions"] = str(correction_instructions)[:800]
    return payload


def _normalize_evidence_id(value: Any) -> str:
    text = str(value or "").strip().upper()
    match = re.fullmatch(r"(E|UC)0*(\d+)", text)
    if match:
        return f"{match.group(1)}{int(match.group(2))}"
    return text


def _log_truthfulness_failure(
    *,
    workflow_id: int | None,
    rule: str,
    section: str,
    field: str,
    reason: str,
    unsupported_terms: list[str] | None = None,
    source: str = "resume",
) -> None:
    logger.warning(
        "TRUTHFULNESS_VALIDATION_FAILED workflow_id=%s rule=%s section=%s field=%s "
        "reason=%s unsupported_terms=%s source=%s",
        workflow_id or "n/a",
        rule,
        section,
        field,
        reason,
        unsupported_terms or [],
        source,
    )


def _validate_llm_result(
    result: dict[str, Any],
    *,
    resume_text: str,
    valid_evidence_ids: set[str],
    denied_skills: list[str] | None = None,
    unsupported_keywords: list[str] | None = None,
    allowed_confirmed_skills: list[str] | None = None,
    confirmed_evidence_text: str | None = None,
    workflow_id: int | None = None,
) -> dict[str, Any]:
    from app.services.keyword_utils import contains_keyword
    from app.services.final_resume_validator import (
        supported_by_authoritative_evidence,
        unsupported_claim_message,
    )
    from app.services.resume_generator import sanitize_invented_metrics, unsupported_invented_metrics, apply_truthfulness_corrections

    normalized_valid_ids = {
        _normalize_evidence_id(item) for item in valid_evidence_ids if item
    }
    valid_bullets = []
    for bullet in (result.get("rewritten_bullets") or []):
        text = str(bullet.get("text") or "").strip()
        supported_raw = bullet.get("supported_by") or []
        if not text or not supported_raw:
            continue
        supported_norm = [_normalize_evidence_id(item) for item in supported_raw if str(item).strip()]
        invalid = [item for item in supported_norm if item not in normalized_valid_ids]
        if invalid:
            _log_truthfulness_failure(
                workflow_id=workflow_id,
                rule="invalid_evidence_ids",
                section="rewritten_bullets",
                field="supported_by",
                reason=f"bullet cites unknown evidence ids: {invalid}",
                unsupported_terms=invalid,
                source="confirmed_evidence" if any(i.startswith("UC") for i in invalid) else "resume",
            )
            raise GenerationValidationError(
                f"LLM bullet references invalid evidence IDs: {', '.join(invalid)}"
            )
        valid_bullets.append({"text": text, "supported_by": supported_norm})
    result["rewritten_bullets"] = valid_bullets

    plain = (result.get("generated_resume_plain_text") or "").strip()
    if not plain:
        _log_truthfulness_failure(
            workflow_id=workflow_id,
            rule="empty_resume",
            section="generated_resume_plain_text",
            field="generated_resume_plain_text",
            reason="LLM returned empty generated_resume_plain_text",
            source="resume",
        )
        raise GenerationValidationError("LLM returned empty generated_resume_plain_text")

    evidence_baseline = "\n".join(
        part for part in [resume_text or "", confirmed_evidence_text or ""] if part
    )
    # Denied / unsupported JD skills must not appear as newly introduced claims,
    # unless covered by accepted USER_CONFIRMED_* evidence.
    denied_blocked = []
    unsupported_blocked = []
    for term in list(denied_skills or []):
        label = str(term or "").strip()
        if not label or not contains_keyword(plain, label):
            continue
        if supported_by_authoritative_evidence(
            label,
            original_text=resume_text,
            allowed_confirmed_skills=allowed_confirmed_skills,
        ):
            continue
        denied_blocked.append(label)
    for term in list(unsupported_keywords or []):
        label = str(term or "").strip()
        if not label or not contains_keyword(plain, label):
            continue
        if supported_by_authoritative_evidence(
            label,
            original_text=resume_text,
            allowed_confirmed_skills=allowed_confirmed_skills,
        ):
            continue
        unsupported_blocked.append(label)
    if denied_blocked:
        unique_denied = list(dict.fromkeys(denied_blocked))
        _log_truthfulness_failure(
            workflow_id=workflow_id,
            rule="unsupported_or_rejected_skill_introduced",
            section="generated_resume_plain_text",
            field="skills_or_experience",
            reason="generated resume introduced rejected/unsupported skills",
            unsupported_terms=unique_denied,
            source="job_description",
        )
        raise GenerationValidationError(
            "; ".join(unsupported_claim_message(term) for term in unique_denied)
        )
    if unsupported_blocked:
        unique_blocked = list(dict.fromkeys(unsupported_blocked))
        _log_truthfulness_failure(
            workflow_id=workflow_id,
            rule="unsupported_or_rejected_skill_introduced",
            section="generated_resume_plain_text",
            field="skills_or_experience",
            reason="generated resume introduced rejected/unsupported skills",
            unsupported_terms=unique_blocked,
            source="job_description",
        )
        corrected, removed = apply_truthfulness_corrections(
            plain,
            original_text=resume_text,
            unsupported_terms=unique_blocked,
            confirmed_evidence_text=confirmed_evidence_text or "",
        )
        if removed:
            logger.warning(
                "TRUTHFULNESS_CORRECTION_APPLIED workflow_id=%s removed_or_rewritten=%s",
                workflow_id or "n/a",
                removed,
            )
            plain = corrected
            result["generated_resume_plain_text"] = plain
            result["truthfulness_correction_applied"] = True
        leftover_blocked = [
            term for term in unique_blocked
            if contains_keyword(plain, term)
            and not supported_by_authoritative_evidence(
                term,
                original_text=resume_text,
                allowed_confirmed_skills=allowed_confirmed_skills,
            )
        ]
        if leftover_blocked:
            raise GenerationValidationError(
                "; ".join(unsupported_claim_message(term) for term in leftover_blocked)
            )
        logger.info(
            "TRUTHFULNESS_REVALIDATION_PASSED workflow_id=%s rule=unsupported_claim",
            workflow_id or "n/a",
        )

    # Invented metrics: rewrite/remove only the unsupported number, then re-check.
    corrected, removed_metrics = sanitize_invented_metrics(plain, evidence_baseline)
    if removed_metrics:
        _log_truthfulness_failure(
            workflow_id=workflow_id,
            rule="invented_metrics",
            section="generated_resume_plain_text",
            field="metrics",
            reason="generated resume introduced metrics absent from original resume/confirmed evidence",
            unsupported_terms=removed_metrics,
            source="resume",
        )
        logger.warning(
            "TRUTHFULNESS_CORRECTION_APPLIED workflow_id=%s removed_or_rewritten=%s",
            workflow_id or "n/a",
            removed_metrics,
        )
        plain = corrected
        result["generated_resume_plain_text"] = plain
        leftover = unsupported_invented_metrics(plain, evidence_baseline)
        if leftover:
            raise GenerationValidationError(
                "LLM introduced metrics not present in the original resume"
            )
        logger.info("TRUTHFULNESS_REVALIDATION_PASSED workflow_id=%s rule=invented_metrics", workflow_id or "n/a")

    original_norm = re.sub(r"\s+", " ", (resume_text or "").strip().lower())
    generated_norm = re.sub(r"\s+", " ", plain.lower())
    if generated_norm == original_norm:
        _log_truthfulness_failure(
            workflow_id=workflow_id,
            rule="identical_resume",
            section="generated_resume_plain_text",
            field="generated_resume_plain_text",
            reason="LLM returned a resume identical to the original",
            source="resume",
        )
        raise GenerationValidationError(
            "LLM returned a resume identical to the original; requiring a meaningful rewrite"
        )
    return result


_SKILLS_HEADING_PATTERN = re.compile(
    r"(?im)^(technical\s+skills|core\s+skills|skills|technical\s+proficiencies|technologies)\s*:?\s*$"
)
_SUMMARY_HEADING_PATTERN = re.compile(
    r"(?im)^(professional\s+summary|career\s+summary|executive\s+summary|technical\s+summary|"
    r"summary(?:\s+of\s+qualifications)?|profile|objective)\s*:?\s*$"
)


def _insert_after_heading(text: str, heading_pattern: re.Pattern, addition: str) -> str | None:
    """Inserts `addition` as its own line directly after the first heading
    matching `heading_pattern`, before the section's next blank line. Returns
    None if no such heading exists so callers can fall back."""
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if heading_pattern.match(line.strip()):
            insert_at = index + 1
            while insert_at < len(lines) and lines[insert_at].strip():
                insert_at += 1
            lines.insert(insert_at, addition)
            return "\n".join(lines)
    return None


def _append_missing_keywords_to_skills_section(text: str, missing_terms: list[str]) -> str:
    """Appends already-supported terms verbatim into the Technical Skills
    section (or a new one, if none exists). Never invents anything — every
    term passed in was already truthfully evidenced in the ORIGINAL resume
    (see _preserve_previously_supported_keywords)."""
    if not missing_terms or not (text or "").strip():
        return text
    addition = ", ".join(dict.fromkeys(missing_terms))
    return (
        _insert_after_heading(text, _SKILLS_HEADING_PATTERN, addition)
        or text.rstrip() + "\n\nTechnical Skills\n" + addition
    )


def _append_missing_keywords_to_summary(text: str, missing_terms: list[str]) -> str:
    """Appends already-supported terms as one self-contained sentence at the
    end of the Professional Summary section (or a new one, if none exists).
    Never invents anything — every term passed in already had "strong"
    professional/project usage evidence in the ORIGINAL resume (see
    _preserve_previously_supported_keywords), so restating that same real
    usage in the summary is not a new claim.

    Placement matters here, not just presence: the same section-aware
    evidence scorer that scores the rest of the resume only credits a skill
    as strongly-demonstrated when the sentence containing it also describes
    real usage (an action verb) outside a bare skills list — a plain
    comma-separated addition to a skills list only ever earns partial
    credit. Phrasing it as genuine usage restores the SAME evidence strength
    the original resume already had, rather than inventing a stronger claim.
    """
    if not missing_terms or not (text or "").strip():
        return text
    terms = list(dict.fromkeys(missing_terms))
    sentence = "Additional hands-on experience using " + ", ".join(terms) + "."
    return (
        _insert_after_heading(text, _SUMMARY_HEADING_PATTERN, sentence)
        or text.rstrip() + "\n\nProfessional Summary\n" + sentence
    )


_YEARS_PATTERN = re.compile(r"\b(\d{1,2})\+?\s*years?\b", re.I)


def _section_content(text: str, heading_pattern: re.Pattern) -> str:
    lines = (text or "").split("\n")
    for index, line in enumerate(lines):
        if heading_pattern.match(line.strip()):
            end = index + 1
            while end < len(lines) and lines[end].strip():
                end += 1
            return "\n".join(lines[index + 1:end]).strip()
    return ""


def _preserve_years_of_experience(plain_text: str, *, original_resume_text: str) -> tuple[str, bool]:
    """Deterministic safety net, independent of prompt compliance: if the
    ORIGINAL resume's Professional Summary states a specific number of years
    of experience but the generated summary drops it (e.g. paraphrasing it
    into "since <year>" or an employer name instead), append it back as a
    short factual clause. Confirmed live: an explicit prompt instruction to
    preserve this fact was not enough on its own — the LLM still dropped it
    in every trial — so this restores it truthfully rather than relying on
    prompt compliance alone. Never invents a number: only restates the exact
    figure the candidate's own original summary already stated.
    """
    original_summary = _section_content(original_resume_text, _SUMMARY_HEADING_PATTERN)
    match = _YEARS_PATTERN.search(original_summary)
    if not match:
        return plain_text, False

    generated_summary = _section_content(plain_text, _SUMMARY_HEADING_PATTERN)
    if not generated_summary:
        return plain_text, False
    if _YEARS_PATTERN.search(generated_summary):
        # Some years-of-experience figure is already stated. If it doesn't
        # match the original, that's a truthfulness concern for the
        # generation prompt/validator to catch — this safety net must not
        # make it worse by inserting a second, conflicting figure.
        return plain_text, False

    sentence = f"Brings {match.group(0)} of hands-on experience to this role."
    updated = _insert_after_heading(plain_text, _SUMMARY_HEADING_PATTERN, sentence)
    if updated is None:
        return plain_text, False
    return updated, True


def _summary_emphasized_technologies(original_resume_text: str) -> list[str]:
    """Canonical technologies (role-independent catalog — Java, Python,
    Microservices, AWS, Selenium, Spark, SAP-adjacent terms, etc.) that the
    candidate's OWN original Professional Summary already names. These are
    "strong facts" the summary itself chose to emphasize — Part 2's step A
    ("extract strong facts from original summary") — independent of
    whether the current JD happens to require them."""
    from app.services.technology_catalog import CANONICAL_TECHNOLOGIES

    original_summary = _section_content(original_resume_text, _SUMMARY_HEADING_PATTERN)
    if not original_summary:
        return []
    lowered = f" {original_summary.lower()} "
    return [tech for tech in CANONICAL_TECHNOLOGIES if f" {tech.lower()} " in lowered or f" {tech.lower()}," in lowered or f" {tech.lower()}." in lowered]


def _preserve_previously_supported_keywords(plain_text: str, *, original_resume_text: str, job) -> tuple[str, list[str]]:
    """Deterministic safety net, independent of which generation path ran:
    any JD-relevant skill that was already truthfully evidenced in the
    ORIGINAL resume, but is missing from the freshly generated text, gets
    added back — at the SAME evidence strength the original resume had, no
    stronger.

    Rewording is expected and desirable (e.g. leaning on a more specific
    "PostgreSQL" instead of a generic "SQL" already mentioned) — but the
    deterministic ATS scorer credits exact terms, not synonyms, so a
    reasonable wording choice can silently cost mandatory/preferred-skill
    credit the original resume already earned. This never invents or
    inflates a claim: a term that only had "strong" evidence (demonstrated
    professional/project usage) in the original goes back into the summary
    as real usage; a term that only had "weak"/"moderate" evidence (a bare
    skills-list mention) goes back into the skills list, not the summary —
    both evaluated by the SAME evidence extractor the scorer itself uses.
    Also covers Part 2's broader complaint (not just strict JD-required
    skills): a technology the candidate's OWN original summary already
    named as a strength — even one the current JD doesn't explicitly
    require — must not silently vanish just because the rewrite emphasized
    JD-matching over everything else. _summary_emphasized_technologies
    finds those (reusing the same role-independent technology catalog used
    elsewhere in this codebase, never a role-specific list) and folds them
    into the same evidence-strength comparison below.
    """
    from app.services.ats_scoring import extract_resume_evidence, extract_structured_requirements

    jd_skills: list[str] = []
    try:
        requirements = extract_structured_requirements(job)
        jd_skills = list(dict.fromkeys(
            (requirements.get("required_core_skills") or []) + (requirements.get("preferred_skills") or [])
        ))
    except Exception:
        pass
    summary_skills = _summary_emphasized_technologies(original_resume_text)
    skills = list(dict.fromkeys(jd_skills + summary_skills))
    if not skills:
        return plain_text, []

    try:
        original_evidence = extract_resume_evidence(original_resume_text or "", skills)
        generated_evidence = extract_resume_evidence(plain_text or "", skills)
    except Exception:
        return plain_text, []

    def _strength(evidence_map: dict, skill: str) -> str | None:
        strength = (evidence_map.get(skill) or {}).get("evidence_strength")
        return strength if strength not in (None, "absent", "none") else None

    # Ordinal so a downgrade (e.g. strong narrative usage -> a bare mention
    # in a skills list) is caught, not just complete disappearance. A term
    # that's STILL present somewhere, just with weaker evidence than the
    # original had, silently costs the same scorer credit as if it had
    # vanished entirely — this is what actually happened to e.g. "Spring
    # Boot" moving from a strong summary sentence to a Technical Skills-only
    # mention, which a presence-only check would miss.
    _STRENGTH_RANK = {None: 0, "weak": 1, "medium": 2, "moderate": 2, "strong": 3}

    def _rank(evidence_map: dict, skill: str) -> int:
        return _STRENGTH_RANK.get(_strength(evidence_map, skill), 0)

    # Skill counts only — never skill names or resume/JD text — to keep this
    # useful for diagnosing Bug-3-class regressions (generated wording
    # silently losing credit the original resume already had) without
    # logging resume content.
    original_credit_count = sum(1 for skill in skills if _strength(original_evidence, skill))
    generated_credit_count_before_fix = sum(1 for skill in skills if _strength(generated_evidence, skill))
    logger.info("SUMMARY_ORIGINAL_SCORE jd_skill_count=%s credited=%s", len(skills), original_credit_count)
    logger.info(
        "SUMMARY_GENERATED_SCORE jd_skill_count=%s credited=%s", len(skills), generated_credit_count_before_fix
    )

    dropped = []
    for skill in skills:
        original_item = original_evidence.get(skill) or {}
        # A bare, exact Skills-list mention remains weak and may need user
        # confirmation, but preserving that same mention does not add a claim.
        # Indirect evidence (e.g. sprint planning ↛ Scrum) cannot justify it.
        explicit_weak_skill = (
            original_item.get("resume_status") == "VERIFIED"
            and original_item.get("match_type") == "EXACT"
            and original_item.get("found_in_resume") is True
            and original_item.get("evidence_strength") == "weak"
            and any(
                _SKILLS_HEADING_PATTERN.match(str(section or ""))
                for section in original_item.get("evidence_sections") or []
            )
        )
        if original_item.get("resume_status") == "INDIRECT" or (
            original_item.get("needs_user_confirmation") and not explicit_weak_skill
        ):
            continue
        if _rank(generated_evidence, skill) < _rank(original_evidence, skill):
            dropped.append(skill)
    if not dropped:
        return plain_text, []

    # Route by the ORIGINAL strength: a term that was strongly demonstrated
    # goes back into the summary as real usage (restoring "strong" credit);
    # a term that was only ever weak/moderate goes back into the skills
    # list only — never claim MORE than the original resume itself did.
    strong_dropped = [skill for skill in dropped if _strength(original_evidence, skill) == "strong"]
    other_dropped = [skill for skill in dropped if skill not in strong_dropped]

    logger.info("GENERATION_KEYWORD_PRESERVATION_APPLIED count=%s", len(dropped))
    result_text = plain_text
    if strong_dropped:
        result_text = _append_missing_keywords_to_summary(result_text, strong_dropped)
    if other_dropped:
        result_text = _append_missing_keywords_to_skills_section(result_text, other_dropped)
    return result_text, dropped


def _replace_section_content(text: str, heading_pattern: re.Pattern, new_content: str) -> str:
    """Replaces the paragraph body of the first section matching
    heading_pattern with new_content, preserving the heading, every other
    section, and overall document order. Returns text unchanged if no such
    heading is found (caller decides how to handle that)."""
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if heading_pattern.match(line.strip()):
            start = index + 1
            end = start
            while end < len(lines) and lines[end].strip():
                end += 1
            return "\n".join(lines[:start] + [new_content.strip()] + lines[end:])
    return text


SUMMARY_SYSTEM_PROMPT = """
You are rewriting one candidate's Professional Summary for a specific job application.
For an experienced candidate, target about 120-180 words (roughly 6-8 recruiter-readable lines) of
natural, professional sentences — never a bare technology list, and never mechanically padded to hit
a word count. Preserve every strong, factual detail already present in the CURRENT summary (years of
experience, seniority, target role/family, technologies already named): only rewrite what is weak,
generic, or missing. Where supported by the current summary or the verified evidence provided,
include: target role, actual years of experience, core technical specialization, 6-10 of the
strongest job-description-relevant technologies, major architecture/platform experience, relevant
cloud/database/DevOps/testing experience, relevant domain experience, key technical responsibilities,
and a concise value proposition — readable by a recruiter in 10-20 seconds.
Use only technologies, responsibilities, and experience already present in the current summary or in
the verified evidence provided. Do not invent technologies, metrics, percentages, achievements,
employers, dates, projects, responsibilities, or years of experience.
Return JSON only: {"summary": "..."}. No markdown. No explanation.
""".strip()


def _build_summary_prompt(
    *, current_summary: str, job_title: str, job_description: str,
    allowed_terms: list[str], previous_suggestion: str, feedback: str | None,
) -> str:
    parts = [
        f"Target job title: {job_title or 'Not specified'}",
        f"Relevant job description context: {(job_description or '')[:800]}",
        f"Current Professional Summary: {current_summary}",
    ]
    if allowed_terms:
        parts.append("Verified/confirmed technologies you may reference if relevant: " + ", ".join(allowed_terms[:20]))
    if previous_suggestion:
        parts.append(
            "Previous rewrite (the new version must use meaningfully different wording than this, "
            f"while staying truthful): {previous_suggestion}"
        )
    if feedback:
        parts.append(f"Feedback on your previous attempt — address this: {feedback}")
    return "\n".join(parts)


def rewrite_summary_with_llm(
    *,
    current_summary: str,
    full_resume_text: str,
    original_resume_text: str,
    job,
    confirmed_skills: dict | None = None,
    allowed_terms: list[str] | None = None,
    denied_skills: list[str] | None = None,
    previous_suggestion: str = "",
) -> tuple[str | None, str | None, bool]:
    """OpenAI-backed Professional Summary rewrite — reuses the SAME
    full-generation LLM client/config as whole-resume optimization (a
    multi-sentence rewrite is a bigger task than a single bullet, so unlike
    Try Again on a bullet this intentionally does NOT use the Ollama
    bullet-rewrite config). Never raises.

    Returns (new_summary, provider_label, no_change_recommended):
      - (None, None, False) on any technical failure/empty/malformed
        result — caller keeps the current summary unchanged.
      - (None, None, True) when the LLM produced nothing meaningfully
        different from the current summary (or, on a "Try Again" request,
        from the previous suggestion) even after one retry.
      - (summary, provider, False) only when the candidate scores at least
        as well as the current summary on the SAME weighted ATS scorer used
        everywhere else — a genuinely weaker rewrite is retried once with
        feedback and, if still weaker, discarded in favor of the current
        summary rather than replacing strong content with weak content.
    """
    from app.services.ats_scoring import calculate_weighted_ats_score
    from app.services.bullet_rewrite_service import is_near_identical

    llm = LLMClient(config=llm_config())
    if not getattr(llm, "enabled", False):
        return None, None, False

    current_summary = str(current_summary or "").strip()
    if not current_summary:
        return None, None, False

    job_title = getattr(job, "title", "") or ""
    job_description = getattr(job, "description", "") or ""
    allowed_terms = allowed_terms or []
    baseline_suggestion = previous_suggestion or current_summary

    def _attempt(feedback: str | None) -> str | None:
        prompt = _build_summary_prompt(
            current_summary=current_summary, job_title=job_title, job_description=job_description,
            allowed_terms=allowed_terms, previous_suggestion=previous_suggestion, feedback=feedback,
        )
        try:
            result = llm.generate_json(system_prompt=SUMMARY_SYSTEM_PROMPT, user_prompt=prompt, temperature=0.3)
        except Exception as error:
            logger.info("SUMMARY_TRY_AGAIN_FAILED provider=%s model=%s reason=%s",
                        llm.provider, llm.model, safe_llm_failure_reason(error))
            return None
        candidate = re.sub(r"\s+", " ", str((result or {}).get("summary") or "")).strip()
        # A loose floor only — catches a degenerate one-liner, not a hard
        # word-count requirement (target length is guided by the prompt).
        if not candidate or len(candidate.split()) < 40:
            return None
        return candidate

    logger.info("SUMMARY_TRY_AGAIN_STARTED provider=%s model=%s retry=%s", llm.provider, llm.model, bool(previous_suggestion))
    candidate = _attempt(None)
    if candidate is None:
        return None, None, False

    if is_near_identical(candidate, baseline_suggestion):
        retry = _attempt(f"Your rewrite was nearly identical to: {baseline_suggestion}. Produce a meaningfully different version.")
        if retry is None or is_near_identical(retry, baseline_suggestion):
            return None, None, True
        candidate = retry

    if is_near_identical(candidate, current_summary):
        return None, None, True

    baseline_score = calculate_weighted_ats_score(
        full_resume_text, job, confirmed_skills=confirmed_skills,
        original_resume_text=original_resume_text, denied_skills=denied_skills, evidence_gated=True,
    ).get("match_percentage") or 0
    logger.info("SUMMARY_ORIGINAL_SCORE match_percentage=%s", baseline_score)

    candidate_text = _replace_section_content(full_resume_text, _SUMMARY_HEADING_PATTERN, candidate)
    candidate_score = calculate_weighted_ats_score(
        candidate_text, job, confirmed_skills=confirmed_skills,
        original_resume_text=original_resume_text, denied_skills=denied_skills, evidence_gated=True,
    ).get("match_percentage") or 0
    logger.info("SUMMARY_GENERATED_SCORE match_percentage=%s", candidate_score)

    if candidate_score < baseline_score:
        logger.info("SUMMARY_GENERATION_RETRY reason=scored_below_current")
        retry = _attempt(
            "Your previous rewrite scored lower against this job than the CURRENT summary. Keep every "
            "strong, job-relevant fact from the current summary (years of experience, target role, "
            "JD-matching technologies already named) and only improve weak or generic wording."
        )
        if retry and not is_near_identical(retry, current_summary):
            retry_text = _replace_section_content(full_resume_text, _SUMMARY_HEADING_PATTERN, retry)
            retry_score = calculate_weighted_ats_score(
                retry_text, job, confirmed_skills=confirmed_skills,
                original_resume_text=original_resume_text, denied_skills=denied_skills, evidence_gated=True,
            ).get("match_percentage") or 0
            logger.info("SUMMARY_GENERATED_SCORE match_percentage=%s retry=true", retry_score)
            if retry_score >= baseline_score:
                return retry, (llm.service_name or llm.provider), False
        # Neither attempt matched or beat the current summary's score — preserve the
        # stronger current content rather than downgrade it.
        return None, None, False

    return candidate, (llm.service_name or llm.provider), False


def _accept_llm_payload(
    raw_result: dict[str, Any],
    *,
    resume_text: str,
    structure: dict[str, Any],
    valid_evidence_ids: set[str],
    llm: LLMClient,
    workflow_id: int | None,
    json_repair_used: bool,
    denied_skills: list[str] | None = None,
    unsupported_keywords: list[str] | None = None,
    allowed_confirmed_skills: list[str] | None = None,
    confirmed_evidence_text: str | None = None,
    confirmed_evidence: dict[str, dict] | None = None,
) -> tuple[dict[str, Any], bool, bool]:
    normalized, norm_repair = normalize_generation_response(raw_result)
    json_repair_used = json_repair_used or norm_repair
    try:
        validated = StructuredOptimizerResponse.model_validate(normalized).model_dump()
    except ValidationError as error:
        summary = log_schema_validation_failure(
            workflow_id=workflow_id,
            model=llm.model,
            response_length=len(json.dumps(raw_result, ensure_ascii=False)),
            error=error,
            raw_preview=json.dumps(normalized, ensure_ascii=False)[:500],
        )
        logger.info("LLM_SCHEMA_REPAIR_STARTED workflow_id=%s model=%s", workflow_id or "n/a", llm.model)
        repair_prompt = build_schema_repair_prompt(normalized, summary)
        repaired_raw = llm.generate_json(
            system_prompt=SCHEMA_REPAIR_SYSTEM_PROMPT,
            user_prompt=repair_prompt,
            temperature=0.0,
        )
        repaired_norm, repair_norm_used = normalize_generation_response(repaired_raw)
        json_repair_used = json_repair_used or repair_norm_used
        try:
            validated = StructuredOptimizerResponse.model_validate(repaired_norm).model_dump()
        except ValidationError as repair_error:
            log_schema_validation_failure(
                workflow_id=workflow_id,
                model=llm.model,
                response_length=len(json.dumps(repaired_raw, ensure_ascii=False)),
                error=repair_error,
            )
            logger.info(
                "LLM_SCHEMA_REPAIR_FAILED workflow_id=%s model=%s",
                workflow_id or "n/a",
                llm.model,
            )
            raise repair_error from error
        logger.info(
            "LLM_SCHEMA_REPAIR_SUCCESS workflow_id=%s model=%s",
            workflow_id or "n/a",
            llm.model,
        )
        schema_repair_retry_used = True
    else:
        schema_repair_retry_used = False
        logger.info(
            "LLM_SCHEMA_VALIDATION_SUCCESS workflow_id=%s model=%s json_repair_used=%s",
            workflow_id or "n/a",
            llm.model,
            json_repair_used,
        )

    result = _validate_llm_result(
        validated,
        resume_text=resume_text,
        valid_evidence_ids=valid_evidence_ids,
        denied_skills=denied_skills,
        unsupported_keywords=unsupported_keywords,
        allowed_confirmed_skills=allowed_confirmed_skills,
        confirmed_evidence_text=confirmed_evidence_text,
        workflow_id=workflow_id,
    )
    # Deterministic assembly restores protected identity/history + section structure.
    assembled = assemble_optimized_resume(
        resume_text=resume_text,
        structure=structure,
        llm_result=result,
        confirmed_evidence=confirmed_evidence,
        denied_skills=denied_skills,
        unsupported_keywords=unsupported_keywords,
    )
    result = {
        **result,
        "generated_resume_plain_text": assembled["plain_text"],
        "assembled_sections": assembled.get("sections") or [],
        "assembly_method": assembled.get("assembly_method"),
    }
    return result, json_repair_used, schema_repair_retry_used


def generate_with_llm_or_fallback(
    *,
    resume_text: str,
    job,
    injection_plan: dict,
    structure: dict,
    confirmed_evidence: dict[str, dict] | None = None,
    denied_skills: list[str] | None = None,
    verified_resume_skills: list[str] | None = None,
    correction_instructions: str | None = None,
    client: LLMClient | None = None,
    skip_llm: bool = False,
    workflow_id: int | None = None,
) -> dict[str, Any]:
    """
    Prefer LLM generation when configured; otherwise use deterministic generator.
    Always returns plain_text resume content for scoring/validation.
    """
    llm = client or LLMClient()
    started_at = time.perf_counter()
    llm_config_dict = getattr(llm, "config", None) or {}
    logger.info(
        "GENERATION_STARTED enabled=%s provider=%s model=%s service=%s base_url=%s "
        "timeout_s=%s skip_llm=%s",
        getattr(llm, "enabled", False),
        getattr(llm, "provider", "none"),
        getattr(llm, "model", "deterministic-fallback"),
        getattr(llm, "service_name", None) or "n/a",
        llm_config_dict.get("base_url") if isinstance(llm_config_dict, dict) else None,
        llm_config_dict.get("timeout") if isinstance(llm_config_dict, dict) else None,
        skip_llm,
    )
    logger.info(
        "LLM_CONFIGURATION enabled=%s provider=%s model=%s",
        getattr(llm, "enabled", False),
        getattr(llm, "provider", "none"),
        getattr(llm, "model", "deterministic-fallback"),
    )
    metadata = {
        "llm_provider": getattr(llm, "provider", "none"),
        "llm_model": getattr(llm, "model", "deterministic-fallback"),
        "prompt_version": PROMPT_VERSION,
        "generation_mode": "deterministic",
    }

    if skip_llm or not getattr(llm, "enabled", False):
        reason = "llm_skipped" if skip_llm and getattr(llm, "enabled", False) else "llm_not_configured"
        logger.info(
            "DETERMINISTIC_FALLBACK_STARTED provider=%s model=%s reason=%s",
            getattr(llm, "provider", "none"),
            getattr(llm, "model", "deterministic-fallback"),
            reason,
        )
        try:
            generated = generate_optimized_resume(resume_text, job, injection_plan, confirmed_evidence)
        except Exception as fallback_error:
            logger.error(
                "DETERMINISTIC_FALLBACK_FAILED provider=%s model=%s reason=%s",
                getattr(llm, "provider", "none"),
                getattr(llm, "model", "deterministic-fallback"),
                safe_llm_failure_reason(fallback_error),
            )
            raise
        preserved_text, dropped_keywords = _preserve_previously_supported_keywords(
            generated.get("plain_text") or "", original_resume_text=resume_text, job=job,
        )
        preserved_text, _ = _preserve_years_of_experience(preserved_text, original_resume_text=resume_text)
        generated = {**generated, "plain_text": preserved_text}
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        generation_metadata = _generation_metadata(
            llm, llm_used=False, fallback_reason=reason, generation_duration_ms=duration_ms,
        )
        logger.info(
            "DETERMINISTIC_FALLBACK_COMPLETED provider=%s model=%s reason=%s generation_duration_ms=%s",
            getattr(llm, "provider", "none"),
            getattr(llm, "model", "deterministic-fallback"),
            reason,
            duration_ms,
        )
        logger.info(
            "FALLBACK_COMPLETED provider=%s model=%s reason=%s generation_duration_ms=%s",
            getattr(llm, "provider", "none"),
            getattr(llm, "model", "deterministic-fallback"),
            reason,
            duration_ms,
        )
        return {
            **generated,
            **metadata,
            **generation_metadata,
            "generation_metadata": generation_metadata,
            "keyword_evidence": [],
            "generation_warnings": [
                "LLM not configured; used deterministic truthful generator."
                if reason == "llm_not_configured"
                else "LLM skipped after a prior timeout/connection failure; used deterministic truthful generator."
            ],
        }

    payload = _build_generation_payload(
        resume_text=resume_text,
        job=job,
        injection_plan=injection_plan,
        structure=structure,
        confirmed_evidence=confirmed_evidence,
        denied_skills=denied_skills,
        verified_resume_skills=verified_resume_skills,
        correction_instructions=correction_instructions,
    )
    user_evidence_claims = payload["accepted_user_evidence"]
    user_prompt = json.dumps(payload, ensure_ascii=False)
    logger.info(
        "LLM_PROMPT_READY provider=%s model=%s prompt_chars=%s resume_chars=%s "
        "jd_chars=%s evidence_ids=%s",
        llm.provider,
        llm.model,
        len(SYSTEM_PROMPT) + len(user_prompt),
        len(resume_text or ""),
        len((payload.get("job_requirements") or {}).get("description") or ""),
        len(payload.get("evidence_claims") or {}) + len(user_evidence_claims),
    )

    llm_attempt_count = 0
    json_repair_used = False
    schema_repair_retry_used = False
    llm_response_received = False

    try:
        raw_result = llm.generate_json(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=float(
                (llm_config_dict.get("temperature", 0.1) if isinstance(llm_config_dict, dict) else 0.1)
            ),
        )
        llm_attempt_count += 1
        llm_response_received = True
        logger.info(
            "ATS_LLM_RESPONSE_RECEIVED workflow_id=%s provider=%s model=%s",
            workflow_id or "n/a",
            llm.provider,
            llm.model,
        )

        accepted_statuses = {
            "USER_CONFIRMED_PROFESSIONAL",
            "USER_CONFIRMED_PROJECT",
            "USER_CONFIRMED_CERTIFICATION",
            "USER_CONFIRMED_TRAINING",
            "LEARNING_ONLY",
            "VERIFIED_FROM_RESUME",
            "",
        }
        confirmed_evidence_text = "\n".join(
            str(
                (item or {}).get("usage_description")
                or (item or {}).get("work_completed")
                or (item or {}).get("description")
                or ""
            )
            for item in (confirmed_evidence or {}).values()
            if str((item or {}).get("evidence_status") or "").upper() in accepted_statuses
        )
        allowed_confirmed_skills = sorted({
            str((item or {}).get("skill") or key)
            for key, item in (confirmed_evidence or {}).items()
            if str((item or {}).get("evidence_status") or "").upper().startswith("USER_CONFIRMED")
            or not (item or {}).get("evidence_status")
        })
        result, json_repair_used, schema_repair_retry_used = _accept_llm_payload(
            raw_result,
            resume_text=resume_text,
            structure=structure or {},
            valid_evidence_ids=(
                set(payload.get("evidence_claims") or {})
                | set(payload.get("evidence_claim_ids") or [])
                | set(user_evidence_claims)
            ),
            llm=llm,
            workflow_id=workflow_id,
            json_repair_used=json_repair_used,
            denied_skills=denied_skills,
            unsupported_keywords=list(injection_plan.get("unsupportedKeywords") or []),
            allowed_confirmed_skills=allowed_confirmed_skills,
            confirmed_evidence_text=confirmed_evidence_text,
            confirmed_evidence=confirmed_evidence,
        )
        plain = (result.get("generated_resume_plain_text") or "").strip()
        plain, dropped_keywords = _preserve_previously_supported_keywords(
            plain, original_resume_text=resume_text, job=job,
        )
        plain, _ = _preserve_years_of_experience(plain, original_resume_text=resume_text)
        assembled_sections = result.get("assembled_sections") or []
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        generation_metadata = _generation_metadata(
            llm,
            llm_used=True,
            generation_duration_ms=duration_ms,
            llm_attempt_count=llm_attempt_count + (1 if schema_repair_retry_used else 0),
            llm_response_received=llm_response_received,
            schema_validation_passed=True,
            json_repair_used=json_repair_used,
            schema_repair_retry_used=schema_repair_retry_used,
        )
        logger.info(
            "ATS_LLM_GENERATION_ACCEPTED workflow_id=%s provider=%s model=%s "
            "json_repair_used=%s schema_repair_retry_used=%s duration_ms=%s",
            workflow_id or "n/a",
            llm.provider,
            llm.model,
            json_repair_used,
            schema_repair_retry_used,
            duration_ms,
        )
        logger.info("LLM_VALIDATION_SUCCESS provider=%s model=%s", llm.provider, llm.model)
        logger.info(
            "LLM_GENERATION_SUCCESS provider=%s model=%s fallback_used=false "
            "generation_duration_ms=%s response_chars=%s assembly=%s",
            llm.provider, llm.model, duration_ms, len(plain),
            result.get("assembly_method") or "none",
        )
        return {
            "plain_text": plain,
            "structure": structure,
            "sections": assembled_sections,
            "keyword_evidence": result.get("keyword_evidence") or [],
            "generation_warnings": result.get("warnings") or [],
            "factual_preservation_confirmed": bool(
                result.get("factual_preservation_confirmed")
            ),
            "llm_provider": llm.provider,
            "llm_model": llm.model,
            "prompt_version": PROMPT_VERSION,
            "generation_mode": "llm",
            "assembly_method": result.get("assembly_method"),
            **generation_metadata,
            "generation_metadata": generation_metadata,
        }
    except Exception as error:
        reason = _fallback_reason(error)
        if isinstance(error, (ValidationError, GenerationValidationError)):
            logger.info(
                "LLM_VALIDATION_FAILED workflow_id=%s provider=%s model=%s reason=%s",
                workflow_id or "n/a",
                llm.provider,
                llm.model,
                reason,
            )
        logger.info(
            "LLM_GENERATION_FAILED workflow_id=%s provider=%s model=%s reason=%s",
            workflow_id or "n/a",
            llm.provider,
            llm.model,
            reason,
        )
        logger.info(
            "DETERMINISTIC_FALLBACK_STARTED workflow_id=%s provider=%s model=%s reason=%s",
            workflow_id or "n/a",
            llm.provider,
            llm.model,
            reason,
        )
        try:
            generated = generate_optimized_resume(resume_text, job, injection_plan, confirmed_evidence)
        except Exception as fallback_error:
            logger.error(
                "DETERMINISTIC_FALLBACK_FAILED provider=%s model=%s reason=%s",
                llm.provider, llm.model, safe_llm_failure_reason(fallback_error),
            )
            raise
        preserved_text, dropped_keywords = _preserve_previously_supported_keywords(
            generated.get("plain_text") or "", original_resume_text=resume_text, job=job,
        )
        preserved_text, _ = _preserve_years_of_experience(preserved_text, original_resume_text=resume_text)
        generated = {**generated, "plain_text": preserved_text}
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        generation_metadata = _generation_metadata(
            llm,
            llm_used=False,
            fallback_reason=reason,
            generation_duration_ms=duration_ms,
            llm_attempt_count=llm_attempt_count,
            llm_response_received=llm_response_received,
            schema_validation_passed=False,
            json_repair_used=json_repair_used,
            schema_repair_retry_used=schema_repair_retry_used,
        )
        logger.info(
            "DETERMINISTIC_FALLBACK_COMPLETED workflow_id=%s provider=%s model=%s reason=%s generation_duration_ms=%s",
            workflow_id or "n/a",
            llm.provider,
            llm.model,
            reason,
            duration_ms,
        )
        logger.info(
            "FALLBACK_COMPLETED provider=%s model=%s reason=%s generation_duration_ms=%s",
            llm.provider, llm.model, reason, duration_ms,
        )
        return {
            **generated,
            "llm_provider": llm.provider,
            "llm_model": llm.model,
            "prompt_version": PROMPT_VERSION,
            "generation_mode": "deterministic_fallback",
            **generation_metadata,
            "generation_metadata": generation_metadata,
            "keyword_evidence": [],
            "generation_warnings": [
                f"LLM generation failed ({reason}); used deterministic fallback."
            ],
        }
