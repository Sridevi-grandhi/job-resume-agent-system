"""Ollama-backed bullet-level "Try Again" rewrite for the resume editor.

Deliberately separate from full-resume generation
(ats_resume_generation_service.py): this handles ONE bullet at a time via
llm_client.bullet_rewrite_llm_config() (Ollama by default, independent of
whatever LLM_PROVIDER is configured for full generation). Full-resume
optimization and one-bullet rewriting are different-sized tasks and are
kept on separate providers by design.
"""

from __future__ import annotations

import difflib
import logging
import re

from app.services.llm_client import LLMClient, bullet_rewrite_llm_config, safe_llm_failure_reason
from app.services.technology_catalog import TECHNOLOGY_SYNONYMS

logger = logging.getLogger(__name__)

# Longest names first so e.g. "Spring Boot" matches before "Spring".
_CANONICAL_TECH_TERMS = sorted({*TECHNOLOGY_SYNONYMS.values()}, key=len, reverse=True)

_NEAR_IDENTICAL_THRESHOLD = 0.92


def is_near_identical(a: str, b: str) -> bool:
    """True when two pieces of wording are the same in substance — exact
    match, or close enough (whitespace/case-insensitive similarity ratio)
    that presenting one as an "AI improvement" over the other would be
    misleading. Domain-independent: pure text comparison, no role/tech
    awareness needed here."""
    norm_a = re.sub(r"\s+", " ", (a or "").strip().lower())
    norm_b = re.sub(r"\s+", " ", (b or "").strip().lower())
    if not norm_a or not norm_b:
        return norm_a == norm_b
    if norm_a == norm_b:
        return True
    return difflib.SequenceMatcher(None, norm_a, norm_b).ratio() >= _NEAR_IDENTICAL_THRESHOLD

SYSTEM_PROMPT = """
You are rewriting one professional resume bullet.
Create a stronger, concise, ATS-friendly version.
Preserve factual meaning.
Do not invent technologies, responsibilities, metrics, achievements, employers, dates, projects, or experience.
Use only information supported by the original bullet and the verified evidence provided.
Return JSON only: {"rewritten_bullet": "..."}.
One bullet only. No markdown. No explanation.
""".strip()

def _truncate(text: str, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _build_prompt(
    *, original: str, context: str, job_title: str, job_description: str,
    allowed_terms: list[str], previous_suggestion: str,
) -> str:
    parts = [
        f"Bullet type: {context}",
        f"Target job title: {job_title or 'Not specified'}",
        f"Relevant job description context: {_truncate(job_description, 600)}",
        f"Original bullet: {original}",
    ]
    if allowed_terms:
        parts.append(
            "Verified/confirmed technologies you may reference if relevant: "
            + ", ".join(allowed_terms[:20])
        )
    if previous_suggestion:
        parts.append(
            "Previous suggestion (the new rewrite must use meaningfully "
            f"different wording than this, while staying truthful): {previous_suggestion}"
        )
    return "\n".join(parts)


def _request_rewrite(client: LLMClient, config: dict, *, original: str, context: str,
                      job_title: str, job_description: str, allowed_terms: list[str],
                      previous_suggestion: str) -> str | None:
    user_prompt = _build_prompt(
        original=original, context=context, job_title=job_title, job_description=job_description,
        allowed_terms=allowed_terms, previous_suggestion=previous_suggestion,
    )
    logger.info("OLLAMA_BULLET_REQUEST provider=%s model=%s base_url=%s", client.provider, client.model, config.get("base_url"))
    try:
        result = client.generate_json(
            system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt, temperature=config["temperature"],
        )
    except Exception as error:
        reason = safe_llm_failure_reason(error)
        if reason == "timeout":
            logger.warning("OLLAMA_BULLET_TIMEOUT provider=%s model=%s", client.provider, client.model)
        else:
            logger.warning("BULLET_FALLBACK_USED provider=%s model=%s reason=%s", client.provider, client.model, reason)
        return None

    logger.info("OLLAMA_RESPONSE_RECEIVED provider=%s model=%s", client.provider, client.model)
    rewritten = re.sub(r"\s+", " ", str((result or {}).get("rewritten_bullet") or "")).strip()
    if not rewritten or len(rewritten) < 3:
        logger.warning("OLLAMA_BULLET_INVALID provider=%s model=%s reason=empty_or_too_short", client.provider, client.model)
        return None
    return rewritten


def rewrite_bullet_with_ollama(
    *,
    original: str,
    context: str = "bullet",
    job_title: str = "",
    job_description: str = "",
    allowed_terms: list[str] | None = None,
    previous_suggestion: str = "",
) -> tuple[str | None, str | None, bool]:
    """Returns (rewritten_text, provider_label, no_change_recommended).

    (None, None, False) on any failure/empty/malformed result — the caller
    falls back to a deterministic rewrite. (None, None, True) when Ollama is
    reachable but, even after one retry, produces nothing meaningfully
    different from the original bullet (or, for a "Try Again" request,
    nothing meaningfully different from the previous suggestion) — a real
    "no change recommended" outcome, distinct from a technical failure.
    Never raises."""
    config = bullet_rewrite_llm_config()
    client = LLMClient(config=config)
    if not client.enabled:
        logger.info("BULLET_FALLBACK_USED reason=llm_not_configured provider=%s", config["provider"])
        return None, None, False

    original = str(original or "").strip()
    if not original:
        return None, None, False

    logger.info("BULLET_TRY_AGAIN_STARTED provider=%s model=%s context=%s retry=%s",
                client.provider, client.model, context, bool(previous_suggestion))
    allowed_terms = allowed_terms or []
    # A candidate must be meaningfully different from BOTH the original
    # bullet and (on a "Try Again") the previous suggestion — a rewrite that
    # just circles back to the original wording is exactly as meaningless as
    # one that repeats the previous suggestion.
    baseline = previous_suggestion or original

    def _too_similar(candidate: str) -> bool:
        return is_near_identical(candidate, original) or (
            bool(previous_suggestion) and is_near_identical(candidate, previous_suggestion)
        )

    rewritten = _request_rewrite(
        client, config, original=original, context=context, job_title=job_title,
        job_description=job_description, allowed_terms=allowed_terms, previous_suggestion=previous_suggestion,
    )
    if rewritten is None:
        return None, None, False

    if _too_similar(rewritten):
        # Ask for a genuinely different alternative before giving up.
        retry = _request_rewrite(
            client, config, original=original, context=context, job_title=job_title,
            job_description=job_description, allowed_terms=allowed_terms, previous_suggestion=baseline,
        )
        if retry is None or _too_similar(retry):
            logger.info("OLLAMA_BULLET_NO_CHANGE_RECOMMENDED provider=%s model=%s", client.provider, client.model)
            return None, None, True
        rewritten = retry

    logger.info("OLLAMA_BULLET_SUCCESS provider=%s model=%s", client.provider, client.model)
    return rewritten, (client.service_name or client.provider), False


def _tech_terms_present(text_lower: str) -> set[str]:
    found = set()
    for term in _CANONICAL_TECH_TERMS:
        pattern = re.escape(term.lower()).replace(r"\ ", r"\s+")
        if re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", text_lower):
            found.add(term.lower())
    return found


def guard_invented_tokens(suggested: str, *, original: str, resume_text: str, job_description: str) -> str:
    """Truthfulness guard: reject a suggestion that introduces a
    TECHNOLOGY/TOOL not present anywhere in the original bullet, resume, or
    JD, or a metric (percentage, dollar figure, user/transaction count) not
    already present. Returns the original bullet unchanged in that case.

    Deliberately narrower than a blanket "every word must already exist"
    check — a wording polish naturally introduces new connective/action
    words (e.g. "aligned with", "delivered"), and rejecting those defeats
    the whole point of a rewrite. Only fabricated FACTS (tech, metrics) are
    guarded against, matching the same truthfulness concern the full-resume
    generation path enforces.
    """
    allowed_context = f"{original}\n{resume_text}\n{job_description}".lower()
    allowed_tech = _tech_terms_present(allowed_context)
    suggested_tech = _tech_terms_present(suggested.lower())
    if suggested_tech - allowed_tech:
        return original

    from app.services.resume_generator import FAKE_METRIC_PATTERN

    if FAKE_METRIC_PATTERN.search(suggested) and not FAKE_METRIC_PATTERN.search(allowed_context):
        return original

    return suggested
