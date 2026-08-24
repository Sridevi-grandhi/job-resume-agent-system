"""Keyword normalization, boundary-safe matching, JD extraction, and injection planning."""

from __future__ import annotations

import re
from typing import Iterable

from app.services.technology_catalog import (
    CANONICAL_TECHNOLOGIES,
    TECHNOLOGY_SYNONYMS,
)


def normalize_keyword(term: str) -> str:
    raw = re.sub(r"\s+", " ", str(term or "").strip().lower())
    raw = raw.replace("_", " ").replace("-", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    if raw in TECHNOLOGY_SYNONYMS:
        return TECHNOLOGY_SYNONYMS[raw]
    # Try without punctuation
    compact = re.sub(r"[^\w\s+#./]", "", raw)
    if compact in TECHNOLOGY_SYNONYMS:
        return TECHNOLOGY_SYNONYMS[compact]
    for canonical in CANONICAL_TECHNOLOGIES:
        if compact == canonical.lower():
            return canonical
    # Title-case unknown multi-word carefully
    for synonym, canonical in TECHNOLOGY_SYNONYMS.items():
        if compact == synonym:
            return canonical
    return canonical_display(term)


def canonical_display(term: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(term or "").strip())
    if not cleaned:
        return ""
    known = {c.lower(): c for c in CANONICAL_TECHNOLOGIES}
    if cleaned.lower() in known:
        return known[cleaned.lower()]
    return cleaned


def _boundary_pattern(keyword: str) -> re.Pattern:
    escaped = re.escape(keyword.lower())
    # Allow flexible whitespace/hyphen inside multi-word terms
    escaped = escaped.replace(r"\ ", r"[\s\-/]+")
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.I)


def contains_keyword(text: str, keyword: str) -> bool:
    """Boundary-safe keyword check. Avoids Java∈JavaScript, SQL∈NoSQL, etc."""
    if not text or not keyword:
        return False
    canonical = normalize_keyword(keyword)
    # Special false-positive guards
    lowered = text.lower()
    key = canonical.lower()
    if key == "java":
        # Remove javascript mentions before matching java
        scrubbed = re.sub(r"javascript", " ", lowered, flags=re.I)
        return bool(_boundary_pattern("java").search(scrubbed))
    if key == "sql":
        scrubbed = re.sub(r"nosql|mysql|postgresql|sqlite|mssql|pl\s*sql", " ", lowered, flags=re.I)
        return bool(_boundary_pattern("sql").search(scrubbed))
    if key == "react":
        scrubbed = re.sub(r"reactive|reactivity", " ", lowered, flags=re.I)
        return bool(_boundary_pattern("react").search(scrubbed))
    if key == "spring":
        # Spring alone should not auto-match Spring Boot (caller can check Boot separately)
        if _boundary_pattern("spring boot").search(lowered):
            # still counts as spring family presence for "Spring" queries? Prefer exact.
            pass
        return bool(_boundary_pattern("spring").search(lowered))
    if key == "angular":
        scrubbed = re.sub(r"angularjs", " ", lowered, flags=re.I)
        return bool(_boundary_pattern("angular").search(scrubbed))
    if key == "azure":
        # Do not treat Azure DevOps as Azure cloud platform evidence/requirement.
        scrubbed = re.sub(r"azure\s*devops", " ", lowered, flags=re.I)
        return bool(_boundary_pattern("azure").search(scrubbed))
    return bool(_boundary_pattern(canonical).search(text))


def find_keywords_in_text(text: str, catalog: Iterable[str] | None = None) -> list[str]:
    source = list(catalog) if catalog is not None else CANONICAL_TECHNOLOGIES
    found = []
    for term in source:
        if contains_keyword(text, term):
            canonical = normalize_keyword(term)
            if canonical not in found:
                found.append(canonical)
    return found


def extract_job_keywords(job_title: str, job_description: str, skills_required: str = "") -> dict:
    """Extract and classify JD keywords using catalog + skills field."""
    skills_field = skills_required or ""
    description = job_description or ""
    title = job_title or ""

    required = []
    # Skills field terms are treated as required/preferred signal
    # Do not split on '/' — that would turn CI/CD into CI and CD.
    for part in re.split(r"[,;\n|]+", skills_field):
        part = part.strip()
        if len(part) < 2:
            continue
        canonical = normalize_keyword(part)
        if canonical and canonical not in required:
            # Only keep catalog-known or reasonably tech-looking terms
            if canonical in CANONICAL_TECHNOLOGIES or contains_keyword(" ".join(CANONICAL_TECHNOLOGIES), canonical):
                required.append(canonical)
            elif len(canonical.split()) <= 3 and re.search(r"[A-Za-z]", canonical):
                # Keep if it matches a known synonym path
                if normalize_keyword(part) in CANONICAL_TECHNOLOGIES:
                    required.append(canonical)

    preferred = find_keywords_in_text(f"{title}\n{description}")
    # Move overlapping into required if also in skills
    for term in preferred:
        if term not in required and any(contains_keyword(skills_field, term) for _ in [0]):
            required.append(term)

    # Responsibility / qualification cues
    responsibility_hits = []
    qualification_hits = []
    for term in preferred:
        # simple section heuristics
        if re.search(rf"(?is)responsibilit\w*.{{0,200}}{re.escape(term)}", description):
            responsibility_hits.append(term)
        if re.search(rf"(?is)qualification\w*.{{0,200}}{re.escape(term)}", description):
            qualification_hits.append(term)

    # Preferred = in JD but not required
    preferred_only = [t for t in preferred if t not in required]

    # Ensure title role words that are tech
    title_tech = find_keywords_in_text(title)
    for term in title_tech:
        if term not in required:
            required.insert(0, term)

    return {
        "requiredKeywords": required,
        "preferredKeywords": preferred_only,
        "responsibilityKeywords": responsibility_hits,
        "qualificationKeywords": qualification_hits,
        "allKeywords": list(dict.fromkeys(required + preferred_only)),
    }


def select_supported_keywords(
    jd_keywords: list[str],
    resume_text: str,
    verified_skills: list[str] | None = None,
) -> dict:
    """JD keywords present in resume text or accepted verified/confirmed skill aliases."""
    from app.services.skill_analysis_service import normalize_skill_name

    verified_forms: set[str] = set()
    for skill in verified_skills or []:
        if not skill:
            continue
        display = normalize_keyword(skill)
        verified_forms.update({
            skill,
            str(skill).lower(),
            display,
            str(display).lower(),
            normalize_skill_name(skill),
            normalize_skill_name(display),
        })
    supported = []
    unsupported = []
    for keyword in jd_keywords:
        canonical = normalize_keyword(keyword)
        forms = {
            keyword,
            str(keyword).lower(),
            canonical,
            str(canonical).lower(),
            normalize_skill_name(keyword),
            normalize_skill_name(canonical),
        }
        if contains_keyword(resume_text, canonical) or (forms & verified_forms):
            if canonical not in supported:
                supported.append(canonical)
        else:
            if canonical not in unsupported:
                unsupported.append(canonical)
    return {
        "supportedKeywords": supported,
        "unsupportedKeywords": unsupported,
    }
