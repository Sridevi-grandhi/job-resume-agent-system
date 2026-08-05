"""AI Resume Improvement Report: deterministic before/after meters, a diff summary,
and a plain-English explanation of why the optimized score changed.

Everything here is derived from data the scoring/generation/validation pipeline
already produced — no new evidence or claims are introduced.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.resume_structure_parser import parse_resume_structure


_WEAK_BULLET_START = re.compile(
    r"^(worked on|worked with|helped|responsible for|involved in|assisted with|was part of|participated in)\b",
    re.I,
)


def _all_bullets(structure: dict) -> list[str]:
    bullets: list[str] = []
    for emp in structure.get("employments") or []:
        bullets.extend(emp.get("bullets") or [])
    for proj in structure.get("projects") or []:
        bullets.extend(proj.get("bullets") or [])
    return bullets


def _resume_quality_score(resume_text: str) -> float:
    structure = parse_resume_structure(resume_text or "")
    bullets = _all_bullets(structure)
    if not bullets:
        return 50.0
    strong = sum(1 for b in bullets if not _WEAK_BULLET_START.match(b.strip()))
    normalized = [re.sub(r"\s+", " ", b.strip().lower()) for b in bullets]
    unique_ratio = len(set(normalized)) / len(normalized)
    reasonable_length = sum(1 for b in bullets if 6 <= len(b.split()) <= 40) / len(bullets)
    score = 100 * (0.5 * (strong / len(bullets)) + 0.3 * unique_ratio + 0.2 * reasonable_length)
    return round(max(0.0, min(100.0, score)), 1)


def _keyword_coverage_score(score_result: dict) -> float:
    required = score_result.get("required_skills") or []
    preferred = score_result.get("preferred_skills") or []
    all_keywords = list(dict.fromkeys(required + preferred))
    if not all_keywords:
        return 100.0
    matched = set(score_result.get("matched_keywords") or [])
    hits = sum(1 for keyword in all_keywords if keyword in matched)
    return round(100 * hits / len(all_keywords), 1)


def _formatting_score(validation_report: dict | None, resume_text: str) -> float:
    final = (validation_report or {}).get("final_validation") or {}
    score = 100.0
    if final.get("protectedFieldsVerified") is False:
        score -= 30
    if final.get("structurePreserved") is False:
        score -= 20
    score -= 5 * len(final.get("warnings") or [])
    score -= 15 * len(final.get("errors") or [])
    word_count = len((resume_text or "").split())
    if word_count < 150:
        score -= 20
    return round(max(0.0, min(100.0, score)), 1)


def build_meters(
    *,
    original_score_result: dict,
    optimized_score_result: dict,
    original_text: str,
    optimized_text: str,
    validation_report: dict | None,
) -> dict[str, dict[str, float]]:
    return {
        "resume_quality": {
            "before": _resume_quality_score(original_text),
            "after": _resume_quality_score(optimized_text),
        },
        "ats_match": {
            "before": float(original_score_result.get("match_percentage") or 0),
            "after": float(optimized_score_result.get("match_percentage") or 0),
        },
        "keyword_coverage": {
            "before": _keyword_coverage_score(original_score_result),
            "after": _keyword_coverage_score(optimized_score_result),
        },
        "formatting": {
            "before": _formatting_score(None, original_text),
            "after": _formatting_score(validation_report, optimized_text),
        },
    }


def diff_summary(original_text: str, optimized_text: str) -> dict[str, Any]:
    original_structure = parse_resume_structure(original_text or "")
    optimized_structure = parse_resume_structure(optimized_text or "")

    original_sections = {
        section.get("heading"): (section.get("content") or "").strip()
        for section in original_structure.get("sections") or []
    }
    sections_improved = [
        section.get("heading")
        for section in optimized_structure.get("sections") or []
        if (section.get("content") or "").strip() != original_sections.get(section.get("heading"), "")
    ]

    original_bullets = {b.strip() for b in _all_bullets(original_structure)}
    optimized_bullets = [b.strip() for b in _all_bullets(optimized_structure)]
    bullets_rewritten = sum(1 for b in optimized_bullets if b not in original_bullets)

    return {"sections_improved": sections_improved, "bullets_rewritten": bullets_rewritten}


def build_explanation(
    *,
    optimized_score_result: dict,
    diff: dict,
    content_changed: bool,
    keywords_added: list[str],
) -> list[str]:
    reasons = []
    confirmed = optimized_score_result.get("confirmed_skills_used") or []
    if confirmed:
        reasons.append(f"Verified experience was added for: {', '.join(confirmed)}")
    if diff["bullets_rewritten"]:
        reasons.append(f"{diff['bullets_rewritten']} experience/project bullet(s) were rewritten for clarity and impact")
    if diff["sections_improved"]:
        reasons.append(f"These sections were improved: {', '.join(diff['sections_improved'])}")
    if keywords_added:
        reasons.append(f"ATS keyword coverage increased: {', '.join(keywords_added[:10])}")
    if not content_changed:
        reasons.append(
            "No truthful, unsupported-claim-free changes were available, so the resume text stayed the same"
        )
    if not reasons:
        reasons.append("The resume already closely matched this job's requirements")
    return reasons


def build_improvement_report(
    *,
    original_score_result: dict,
    optimized_score_result: dict,
    original_text: str,
    optimized_text: str,
    validation_report: dict | None,
    content_changed: bool,
) -> dict[str, Any]:
    meters = build_meters(
        original_score_result=original_score_result,
        optimized_score_result=optimized_score_result,
        original_text=original_text,
        optimized_text=optimized_text,
        validation_report=validation_report,
    )
    diff = diff_summary(original_text, optimized_text)

    original_matched = set(original_score_result.get("matched_keywords") or [])
    optimized_matched = set(optimized_score_result.get("matched_keywords") or [])
    keywords_added = sorted(optimized_matched - original_matched)

    original_score = int(original_score_result.get("match_percentage") or 0)
    optimized_score = int(optimized_score_result.get("match_percentage") or 0)

    explanation = build_explanation(
        optimized_score_result=optimized_score_result,
        diff=diff,
        content_changed=content_changed,
        keywords_added=keywords_added,
    )

    return {
        "original_score": original_score,
        "optimized_score": optimized_score,
        "improvement": optimized_score - original_score,
        "new_skills_added": optimized_score_result.get("confirmed_skills_used") or [],
        "keywords_added": keywords_added,
        "sections_improved": diff["sections_improved"],
        "bullets_rewritten": diff["bullets_rewritten"],
        "missing_skills_remaining": optimized_score_result.get("missing_mandatory_skills") or [],
        "ats_recommendations": optimized_score_result.get("suggestions") or [],
        "meters": meters,
        "explanation": explanation,
    }
