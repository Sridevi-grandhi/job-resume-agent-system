"""Updated Job Search baseline adapter/cache, using the existing ATS scorer.

Role-search relevance stays in job_relevance_filter. Numeric resume/JD scores
come from the same original-resume calculation as Selected Jobs/ATS, never
from preferences, optimized drafts, confirmations or LLM explanations.
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.models.job_match_model import JobMatch
from app.services.llm_client import LLMClient
from app.services.technology_catalog import TECHNOLOGY_SYNONYMS
from app.services import ats_scoring
from app.services.job_eligibility import job_is_analyzable

logger = logging.getLogger(__name__)

MATCHER_VERSION = "ats-baseline-v2"

# Longest names first so e.g. "Spring Boot" matches before "Spring".
_CANONICAL_SKILLS = sorted({*TECHNOLOGY_SYNONYMS.values()}, key=len, reverse=True)


def _tokenize_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9+#.]+", (text or "").lower())


def _skill_present(skill: str, haystack_lower: str) -> bool:
    pattern = re.escape(skill.lower()).replace(r"\ ", r"\s+")
    return re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", haystack_lower) is not None


def extract_job_skills(job_skills_csv: str, description: str) -> list[str]:
    """Canonical technology names this job asks for — explicit skills field
    first, then technology names evidenced in the description."""
    explicit = [part.strip() for part in re.split(r"[,;/|\n]+", job_skills_csv or "") if part.strip()]
    canonical, seen = [], set()
    for skill in explicit:
        canon = TECHNOLOGY_SYNONYMS.get(skill.lower(), skill)
        key = canon.lower()
        if key not in seen:
            canonical.append(canon)
            seen.add(key)

    desc_lower = f" {description or ''} ".lower()
    for canon in _CANONICAL_SKILLS:
        key = canon.lower()
        if key in seen:
            continue
        if _skill_present(canon, desc_lower):
            canonical.append(canon)
            seen.add(key)
    return canonical[:30]


def extract_resume_skills(resume_text: str) -> set[str]:
    """Canonical technology names actually evidenced in the resume text.
    Never invents a skill that is not textually present."""
    text_lower = f" {resume_text or ''} ".lower()
    return {canon for canon in _CANONICAL_SKILLS if _skill_present(canon, text_lower)}


def _title_overlap_pct(job_title: str, resume_text: str) -> float:
    title_tokens = {t for t in _tokenize_words(job_title) if len(t) > 2}
    if not title_tokens:
        return 0.0
    resume_tokens = set(_tokenize_words(resume_text))
    return 100.0 * len(title_tokens & resume_tokens) / len(title_tokens)


def _role_alignment_from_score(score: int) -> str:
    if score >= 60:
        return "STRONG"
    if score >= 30:
        return "PARTIAL"
    return "MISMATCH"


def deterministic_match(*, job_title: str, job_description: str, job_skills_csv: str, resume_text: str) -> dict[str, Any]:
    # These are exactly the scoring inputs Save copies into the Selected Job.
    # Do not append provider-only metadata or pass optimization evidence.
    result = ats_scoring.calculate_weighted_ats_score(resume_text, SimpleNamespace(
        title=job_title, description=job_description, skills_required=job_skills_csv,
    ))
    alignment = result.get("role_alignment")
    return {
        "match_score": result["match_percentage"],
        "fitting_skills": result.get("matched_keywords", [])[:12],
        "missing_skills": result.get("missing_keywords", [])[:8],
        "role_alignment": "STRONG" if alignment in {"EXACT", "STRONG_RELATED"} else "MISMATCH" if alignment == "MISMATCH" else "PARTIAL",
        "job_skill_count": len(set(result.get("required_skills", []) + result.get("preferred_skills", []))),
        "confidence": result.get("confidence", "LOW"),
        "mandatory_coverage": result.get("mandatory_requirements", {}),
        "preferred_coverage": result.get("preferred_requirements", {}),
    }


def _fallback_verdict(fitting: list[str], missing: list[str], score: int) -> str:
    if not fitting and not missing:
        return "Not enough information was available to compare skills for this role."
    if score >= 80:
        tail = f" Minor gaps in {', '.join(missing[:2])}." if missing else ""
        return f"Strong alignment with {', '.join(fitting[:3]) or 'this role'}.{tail}"
    if score >= 60:
        return (
            f"Good alignment with {', '.join(fitting[:3]) or 'several required skills'}, "
            f"with some gaps in {', '.join(missing[:3]) or 'a few areas'}."
        )
    if score >= 40:
        return (
            f"Moderate alignment. Matches {', '.join(fitting[:2]) or 'a few skills'}; "
            f"missing {', '.join(missing[:3]) or 'several key skills'}."
        )
    tail = f" Missing {', '.join(missing[:3])}." if missing else ""
    return f"Limited alignment with this role's requirements.{tail}"


def job_match_llm_config() -> dict[str, Any]:
    """Independent LLM configuration for job matching. Falls back to the
    ATS Optimizer's shared LLM_* settings when JOB_MATCH_LLM_* are unset, so
    the feature works out of the box in an environment already configured
    for the ATS Optimizer, while still allowing an independent provider."""
    provider = (os.getenv("JOB_MATCH_LLM_PROVIDER") or os.getenv("LLM_PROVIDER") or "none").strip().lower()
    model = (os.getenv("JOB_MATCH_LLM_MODEL") or os.getenv("LLM_MODEL") or "").strip()

    if provider == "openai":
        api_key = (os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY") or "").strip()
    elif provider == "anthropic":
        api_key = (os.getenv("ANTHROPIC_API_KEY") or os.getenv("LLM_API_KEY") or "").strip()
    else:
        api_key = (os.getenv("LLM_API_KEY") or "").strip()

    base_url = (os.getenv("JOB_MATCH_LLM_BASE_URL") or os.getenv("LLM_BASE_URL") or "").strip().rstrip("/")
    if not base_url:
        if provider in ("openai", "openai_compatible"):
            base_url = "https://api.openai.com/v1"
        elif provider == "anthropic":
            base_url = "https://api.anthropic.com"

    if not model:
        if provider == "anthropic":
            model = "claude-sonnet-4-20250514"
        elif provider in ("openai", "openai_compatible"):
            model = "gpt-4o"
        else:
            model = "deterministic-fallback"

    return {
        "provider": provider,
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
        "timeout": int(os.getenv("JOB_MATCH_LLM_TIMEOUT_SECONDS") or os.getenv("LLM_TIMEOUT_SECONDS") or "30"),
        "max_retries": int(os.getenv("JOB_MATCH_LLM_MAX_RETRIES") or "1"),
        "temperature": 0.1,
        "enabled": provider not in ("", "none", "off", "disabled") and bool(api_key),
        "prompt_version": "job-match-v1",
    }


def _build_prompt(resume_text: str, job_title: str, job_description: str, requirements: str) -> tuple[str, str]:
    system_prompt = (
        "You are an evidence-based resume-to-job matching assistant. Compare the "
        "candidate's actual resume with the selected job description. Do not invent "
        "skills, experience, companies, certifications, projects, metrics, or "
        "responsibilities that are not present in the resume text. Return JSON only."
    )
    user_prompt = (
        f"Candidate Resume:\n{(resume_text or '')[:6000]}\n\n"
        f"Job:\n{job_title}\n\n"
        f"Job Description:\n{(job_description or '')[:4000]}\n\n"
        f"Extracted Requirements:\n{(requirements or 'Not separately extracted.')[:1500]}\n\n"
        "Return a single JSON object with exactly these keys: "
        '"fitting_skills" (array of strings — skills evidenced in the resume that '
        'match this job), "missing_skills" (array of strings — job skills not '
        'evidenced in the resume), "verdict" (a concise 1-2 sentence explanation), '
        '"role_alignment" (one of "STRONG", "PARTIAL", "MISMATCH"). Do not include a '
        "numeric score field — scoring is handled separately and any score you "
        "return will be ignored."
    )
    return system_prompt, user_prompt


def llm_explain(*, resume_text: str, job_title: str, job_description: str, requirements: str, deterministic: dict, use_llm: bool = True) -> dict[str, Any]:
    config = job_match_llm_config()
    client = LLMClient(config=config)
    fallback = {
        "fitting_skills": deterministic["fitting_skills"],
        "missing_skills": deterministic["missing_skills"],
        "verdict": _fallback_verdict(deterministic["fitting_skills"], deterministic["missing_skills"], deterministic["match_score"]),
        "role_alignment": deterministic["role_alignment"],
        "llm_used": False,
        "llm_provider": config["provider"],
    }
    if not use_llm or not client.enabled:
        return fallback

    system_prompt, user_prompt = _build_prompt(resume_text, job_title, job_description, requirements)
    try:
        raw = client.generate_json(system_prompt=system_prompt, user_prompt=user_prompt, temperature=config["temperature"])
    except Exception as error:
        logger.warning("job_match_llm_failed provider=%s error=%s", config["provider"], type(error).__name__)
        return fallback

    fitting = raw.get("fitting_skills")
    missing = raw.get("missing_skills")
    verdict = raw.get("verdict")
    role_alignment = raw.get("role_alignment")
    valid = (
        isinstance(fitting, list) and isinstance(missing, list)
        and isinstance(verdict, str) and verdict.strip()
    )
    if not valid:
        logger.warning("job_match_llm_malformed_response provider=%s", config["provider"])
        return fallback

    fitting = [str(item).strip() for item in fitting if str(item).strip()][:12]
    missing = [str(item).strip() for item in missing if str(item).strip()][:8]
    if role_alignment not in {"STRONG", "PARTIAL", "MISMATCH"}:
        role_alignment = deterministic["role_alignment"]

    return {
        "fitting_skills": fitting or deterministic["fitting_skills"],
        "missing_skills": missing or deterministic["missing_skills"],
        "verdict": verdict.strip()[:600],
        "role_alignment": role_alignment,
        "llm_used": True,
        "llm_provider": config["provider"],
    }


def _strip_tz(value):
    if value is None:
        return None
    return value.replace(tzinfo=None) if getattr(value, "tzinfo", None) else value


def _cache_version(user_id, resume_id, resume_text, discovered_job) -> str:
    # Fit the existing VARCHAR(30): no migration or persistence-model change.
    # Content identity catches edits under the same resume/job IDs and also
    # retires every legacy 80/20 cache row and future canonical score versions.
    payload = [MATCHER_VERSION, ats_scoring.ANALYSIS_VERSION, user_id, resume_id,
               resume_text, discovered_job.id, discovered_job.title,
               discovered_job.description, discovered_job.skills or "",
               discovered_job.requirements or ""]
    return "atsb2:" + hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()[:24]


def _is_stale(existing: JobMatch, resume_id: int, discovered_job, version: str) -> bool:
    if existing.resume_id != resume_id:
        return True
    if existing.matcher_version != version:
        return True
    reference = _strip_tz(discovered_job.updated_detected_at) or _strip_tz(discovered_job.first_seen_at)
    calculated = _strip_tz(existing.calculated_at)
    if reference and calculated and reference > calculated:
        return True
    return False


def get_or_compute_match(
    db: Session, *, user_id: int, resume_id: int | None, resume_text: str | None, discovered_job,
    use_llm: bool = True, force: bool = False,
) -> JobMatch | None:
    """Return the cached JobMatch, recomputing only when the resume, the job
    content, or the matcher version has changed since it was last cached
    (or `force=True`, used to upgrade a deterministic-only cached row to an
    LLM-explained one for a small top-ranked subset — see job_discovery_routes).
    use_llm=False skips Stage 3 entirely so bulk deterministic scoring for
    every Stage-1-relevant job never fans out into dozens of LLM calls."""
    if not resume_id or not (resume_text or "").strip():
        return None
    # Reuse Selected Jobs' existing incomplete-JD rule without invoking Save
    # or an optimization workflow. Unknown stays unscored, not title-only 100.
    eligible, _ = job_is_analyzable(SimpleNamespace(
        id=discovered_job.id, user_id=user_id, title=discovered_job.title,
        company=discovered_job.company, description=discovered_job.description,
        source="User Selected Job",
    ), require_url=False)
    if not eligible:
        return None

    existing = (
        db.query(JobMatch)
        .filter(JobMatch.user_id == user_id, JobMatch.discovered_job_id == discovered_job.id)
        .order_by(JobMatch.id.desc())
        .first()
    )
    version = _cache_version(user_id, resume_id, resume_text, discovered_job)
    cache_valid = existing is not None and not _is_stale(existing, resume_id, discovered_job, version)
    if cache_valid and not force:
        return existing

    logger.info("USER_JOB_MATCH_STARTED user_id=%s discovered_job_id=%s", user_id, discovered_job.id)
    try:
        # Stage 3 may force an explanation upgrade; do not run the expensive
        # canonical score a second time for identical inputs on the same page.
        deterministic = {
            "match_score": existing.match_score,
            "fitting_skills": json.loads(existing.fitting_skills or "[]"),
            "missing_skills": json.loads(existing.missing_skills or "[]"),
            "role_alignment": existing.role_alignment,
            "confidence": existing.confidence,
            "mandatory_coverage": json.loads(existing.mandatory_coverage or "{}"),
            "preferred_coverage": json.loads(existing.preferred_coverage or "{}"),
        } if cache_valid else deterministic_match(
            job_title=discovered_job.title, job_description=discovered_job.description,
            job_skills_csv=discovered_job.skills or "", resume_text=resume_text,
        )
        explanation = llm_explain(
            resume_text=resume_text,
            job_title=discovered_job.title,
            job_description=discovered_job.description,
            requirements=discovered_job.requirements or "",
            deterministic=deterministic,
            use_llm=use_llm,
        )
    except Exception:
        logger.exception("USER_JOB_MATCH_FAILED user_id=%s discovered_job_id=%s", user_id, discovered_job.id)
        # Never leak a prior resume/JD/version's cached score after a failure.
        return existing if cache_valid else None

    row = existing or JobMatch(user_id=user_id, discovered_job_id=discovered_job.id)
    if existing is None:
        db.add(row)
    row.resume_id = resume_id
    row.match_score = deterministic["match_score"]
    row.fitting_skills = json.dumps(explanation["fitting_skills"])
    row.missing_skills = json.dumps(explanation["missing_skills"])
    row.verdict = explanation["verdict"]
    row.role_alignment = explanation["role_alignment"]
    row.confidence = deterministic["confidence"]
    row.mandatory_coverage = json.dumps(deterministic["mandatory_coverage"])
    row.preferred_coverage = json.dumps(deterministic["preferred_coverage"])
    row.matcher_version = version
    row.llm_used = bool(explanation["llm_used"])
    row.llm_provider = explanation["llm_provider"]
    row.calculated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    logger.info(
        "USER_JOB_MATCH_COMPLETED user_id=%s discovered_job_id=%s score=%s llm_used=%s",
        user_id, discovered_job.id, row.match_score, row.llm_used,
    )
    return row


def match_to_dict(row: JobMatch | None) -> dict[str, Any] | None:
    if row is None:
        return None
    try:
        fitting = json.loads(row.fitting_skills or "[]")
    except (TypeError, ValueError):
        fitting = []
    try:
        missing = json.loads(row.missing_skills or "[]")
    except (TypeError, ValueError):
        missing = []
    return {
        "match_score": row.match_score,
        "fitting_skills": fitting,
        "missing_skills": missing,
        "verdict": row.verdict,
        "role_alignment": row.role_alignment,
        "confidence": row.confidence,
        "calculated_at": row.calculated_at.isoformat() if row.calculated_at else None,
        "matcher_version": row.matcher_version,
        "llm_used": row.llm_used,
    }
