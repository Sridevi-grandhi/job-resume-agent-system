"""
ATS resume generation via configurable LLM, with deterministic fallback.

Resume and JD content are treated as untrusted source documents, not instructions.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.services.llm_client import LLMClient, LLMGenerationError, PROMPT_VERSION, safe_llm_failure_reason
from app.services.resume_generator import generate_optimized_resume


logger = logging.getLogger(__name__)


class GenerationValidationError(RuntimeError):
    pass


def _fallback_reason(error: Exception) -> str:
    if isinstance(error, (ValidationError, GenerationValidationError)):
        return "validation_failed"
    return safe_llm_failure_reason(error)


def _generation_metadata(llm: LLMClient, *, llm_used: bool, fallback_reason: str | None = None) -> dict[str, Any]:
    fallback_used = not llm_used
    return {
        "generation_method": "llm" if llm_used else "deterministic_fallback",
        "llm_used": llm_used,
        "llm_provider": llm.provider,
        "llm_model": llm.model,
        "llm_service": llm.service_name,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
    }


class RewrittenBullet(BaseModel):
    text: str
    supported_by: list[str] = Field(min_length=1)


class StructuredOptimizerResponse(BaseModel):
    generated_resume_plain_text: str = Field(min_length=1)
    rewritten_summary: str = ""
    rewritten_bullets: list[RewrittenBullet] = Field(default_factory=list)
    keyword_evidence: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    factual_preservation_confirmed: bool


SYSTEM_PROMPT = """
You optimize resumes using ONLY original_resume_facts and accepted_user_evidence.
Job requirements are targeting hints, never candidate facts.

Rules:
- Preserve identity, employers, dates, titles, education, certifications, project names.
- Rewrite summary, reorder skills by JD priority, strengthen bullets with supported tech only.
- Never invent employers, dates, skills, metrics, or experience.
- Never add unsupported_keywords or user_denied_skills.
- Keep section order. Separate professional vs project/learning evidence.
- Return ONE JSON object only with:
  generated_resume_plain_text (required complete resume string),
  rewritten_summary (string),
  rewritten_bullets (array of {text, supported_by} using E# / UC# ids),
  keyword_evidence (array),
  warnings (array),
  factual_preservation_confirmed (boolean).
- Output must differ from the original resume while staying truthful.
""".strip()


_JD_DESCRIPTION_CHAR_LIMIT = 2800
_EVIDENCE_CLAIM_LIMIT = 40


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
    claims = {}
    for index, (skill, item) in enumerate((confirmed_evidence or {}).items(), start=1):
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
    supported = list(dict.fromkeys(injection_plan.get("supportedKeywords") or []))[:24]
    unsupported = list(dict.fromkeys(injection_plan.get("unsupportedKeywords") or []))[:24]
    placement = injection_plan.get("placement") or {}
    compact_placement = {
        key: (value[:8] if isinstance(value, list) else value)
        for key, value in placement.items()
        if key in {"summary", "skills", "experience", "projects"}
    }
    payload = {
        "original_resume_facts": resume_text,
        "accepted_user_evidence": user_evidence_claims,
        "verified_resume_skills": list(verified_resume_skills or [])[:40],
        "user_denied_skills": list(denied_skills or [])[:24],
        "job_requirements": _compact_job_requirements(job),
        "section_order": structure.get("section_order") or [],
        "supported_keywords": supported,
        "unsupported_keywords": unsupported,
        "placement": compact_placement,
        "evidence_claims": _evidence_claims(structure),
        "instructions": (
            "Rewrite truthfully for this job using supported_keywords and "
            "accepted_user_evidence only. Change summary, skill order, and several bullets. "
            "Return JSON only."
        ),
    }
    if correction_instructions:
        payload["correction_instructions"] = str(correction_instructions)[:1200]
    return payload


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
) -> dict[str, Any]:
    """
    Prefer LLM generation when configured; otherwise use deterministic generator.
    Always returns plain_text resume content for scoring/validation.
    """
    llm = client or LLMClient()
    logger.info(
        "GENERATION_STARTED enabled=%s provider=%s model=%s service=%s base_url=%s "
        "timeout_s=%s skip_llm=%s",
        llm.enabled,
        llm.provider,
        llm.model,
        llm.service_name or "n/a",
        llm.config.get("base_url"),
        llm.config.get("timeout"),
        skip_llm,
    )
    logger.info(
        "LLM_CONFIGURATION enabled=%s provider=%s model=%s",
        llm.enabled, llm.provider, llm.model,
    )
    metadata = {
        "llm_provider": llm.provider,
        "llm_model": llm.model,
        "prompt_version": PROMPT_VERSION,
        "generation_mode": "deterministic",
    }

    if skip_llm or not llm.enabled:
        reason = "llm_skipped" if skip_llm and llm.enabled else "llm_not_configured"
        generation_metadata = _generation_metadata(llm, llm_used=False, fallback_reason=reason)
        logger.info("FALLBACK_STARTED provider=%s model=%s reason=%s", llm.provider, llm.model, reason)
        generated = generate_optimized_resume(resume_text, job, injection_plan, confirmed_evidence)
        logger.info("FALLBACK_COMPLETED provider=%s model=%s reason=%s", llm.provider, llm.model, reason)
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

    try:
        raw_result = llm.generate_json(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=float(llm.config.get("temperature", 0.1)),
        )
        result = StructuredOptimizerResponse.model_validate(raw_result).model_dump()
        valid_evidence_ids = set(payload["evidence_claims"]) | set(user_evidence_claims)
        for bullet in result["rewritten_bullets"]:
            if not set(bullet["supported_by"]).issubset(valid_evidence_ids):
                raise GenerationValidationError("LLM bullet references invalid evidence IDs")
        plain = (result.get("generated_resume_plain_text") or "").strip()
        if not plain:
            raise GenerationValidationError("LLM returned empty generated_resume_plain_text")
        original_norm = re.sub(r"\s+", " ", (resume_text or "").strip().lower())
        generated_norm = re.sub(r"\s+", " ", plain.lower())
        if generated_norm == original_norm:
            raise GenerationValidationError(
                "LLM returned a resume identical to the original; requiring a meaningful rewrite"
            )
        generation_metadata = _generation_metadata(llm, llm_used=True)
        logger.info("LLM_VALIDATION_SUCCESS provider=%s model=%s", llm.provider, llm.model)
        logger.info(
            "LLM_GENERATION_SUCCESS provider=%s model=%s fallback_used=false",
            llm.provider, llm.model,
        )
        return {
            "plain_text": plain,
            "structure": structure,
            "sections": [],
            "keyword_evidence": result.get("keyword_evidence") or [],
            "generation_warnings": result.get("warnings") or [],
            "factual_preservation_confirmed": bool(
                result.get("factual_preservation_confirmed")
            ),
            "llm_provider": llm.provider,
            "llm_model": llm.model,
            "prompt_version": PROMPT_VERSION,
            "generation_mode": "llm",
            **generation_metadata,
            "generation_metadata": generation_metadata,
        }
    except Exception as error:
        reason = _fallback_reason(error)
        if isinstance(error, (ValidationError, GenerationValidationError)):
            logger.info("LLM_VALIDATION_FAILED provider=%s model=%s reason=%s", llm.provider, llm.model, reason)
        generation_metadata = _generation_metadata(llm, llm_used=False, fallback_reason=reason)
        logger.info(
            "LLM_GENERATION_FALLBACK provider=%s model=%s reason=%s",
            llm.provider, llm.model, reason,
        )
        logger.info("FALLBACK_STARTED provider=%s model=%s reason=%s", llm.provider, llm.model, reason)
        generated = generate_optimized_resume(resume_text, job, injection_plan, confirmed_evidence)
        logger.info("FALLBACK_COMPLETED provider=%s model=%s reason=%s", llm.provider, llm.model, reason)
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
