"""Authoritative requirement-to-resume skill analysis.

Every ATS consumer uses this module for normalization, alias matching, section-aware
evidence, and missing/weak/verified classification. Matching is boundary-aware;
job requirements are never treated as candidate evidence.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from app.services.resume_structure_parser import parse_resume_structure

# Bump when matching rules change so requirement caches invalidate.
SKILL_ANALYSIS_VERSION = "2"
ANALYSIS_VERSION = SKILL_ANALYSIS_VERSION

logger = logging.getLogger(__name__)

MAVEN_ALIASES = {
    "maven",
    "apache maven",
    "maven build tool",
    "maven build",
    "maven dependency management",
    "mvn",
}

CANONICAL_ALIASES = {
    "spring boot": ["springboot", "spring-boot"],
    "react": ["reactjs", "react.js", "react js"],
    "postgresql": ["postgres", "postgre sql", "postgre-sql"],
    "sql server": ["sqlserver", "sql-server", "ms sql", "mssql", "microsoft sql server"],
    "maven": sorted(MAVEN_ALIASES - {"maven"}),
    "scrum": ["scrum methodology", "agile scrum"],
    "aws": ["amazon web services"],
    "rest apis": ["restful api", "restful apis", "rest api"],
    "ci/cd": ["ci cd", "cicd", "continuous integration continuous delivery"],
    "node.js": ["nodejs", "node js"],
    ".net": ["dotnet", "dot net"],
}

DISPLAY_NAMES = {
    "aws": "AWS", "ci/cd": "CI/CD", "rest apis": "REST APIs",
    "sql server": "SQL Server", "postgresql": "PostgreSQL", "node.js": "Node.js",
    ".net": ".NET", "c++": "C++", "c#": "C#", "maven": "Maven",
}

INDIRECT_PATTERNS = {
    "scrum": [
        r"\bagile\b.{0,80}\b(?:sprint planning|retrospectives?|sprint ceremonies|daily standups?)\b",
        r"\b(?:sprint planning|retrospectives?|daily standups?)\b",
    ],
}

CATEGORY_MAP = {
    "maven": "Build Tool", "gradle": "Build Tool", "scrum": "Methodology", "agile": "Methodology",
    "oracle": "Database", "sql server": "Database", "postgresql": "Database", "sql": "Database",
}


def _basic_normalize(value: str) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"(?<=\w)[_-](?=\w)", " ", text)
    text = re.sub(r"[^a-z0-9+#./\s-]", " ", text)
    return re.sub(r"\s+", " ", text).strip(" -")


def normalize_skill_name(skill: str) -> str:
    normalized = _basic_normalize(skill)
    if normalized in MAVEN_ALIASES or normalized == "maven":
        return "maven"
    for canonical, aliases in CANONICAL_ALIASES.items():
        alias_norms = {_basic_normalize(alias) for alias in aliases}
        if normalized == canonical or normalized in alias_norms:
            return canonical
    return normalized


def display_skill_name(skill: str) -> str:
    normalized = normalize_skill_name(skill)
    return DISPLAY_NAMES.get(normalized, " ".join(part.capitalize() for part in normalized.split()))


def content_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _term_pattern(term: str) -> re.Pattern:
    escaped = re.escape(term)
    escaped = escaped.replace(r"\ ", r"[\s_-]+")
    # Alphanumeric lookarounds protect Java/JavaScript and avoid Maven inside other tokens.
    return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.I)


def phrase_match(alias: str, text: str) -> bool:
    return bool(_term_pattern(_basic_normalize(alias)).search(text or ""))


def aliases_for_skill(skill: str) -> list[str]:
    normalized = normalize_skill_name(skill)
    aliases = [normalized, *CANONICAL_ALIASES.get(normalized, [])]
    if normalized == "maven":
        aliases = sorted(MAVEN_ALIASES)
    # Preserve order, unique
    seen = set()
    ordered = []
    for alias in aliases:
        key = _basic_normalize(alias)
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def _find_explicit(text: str, skill: str) -> tuple[str, re.Match | None]:
    normalized = normalize_skill_name(skill)
    for alias in aliases_for_skill(normalized):
        if alias == normalized:
            continue
        match = _term_pattern(alias).search(text or "")
        if match:
            return "ALIAS", match
    canonical_match = _term_pattern(normalized).search(text or "")
    if canonical_match:
        return "EXACT", canonical_match
    return "ABSENT", None


def _resume_sections(resume_text: str) -> list[tuple[str, str]]:
    structure = parse_resume_structure(resume_text or "")
    sections = [
        (section.get("heading") or "Content", section.get("content") or "")
        for section in structure.get("sections") or []
    ]
    # Full parsed text is always searched, even when headings are absent or parsing is imperfect.
    sections.append(("Full Resume", resume_text or ""))
    return sections


def find_skill_evidence(skill: str, full_resume_text: str, parsed_sections=None) -> dict[str, Any]:
    """Public evidence API used by requirement analysis and diagnostics."""
    del parsed_sections  # sections are derived from full text for consistency
    return get_skill_evidence(skill, full_resume_text)


def get_skill_evidence(skill: str, resume_text: str) -> dict[str, Any]:
    normalized = normalize_skill_name(skill)
    display = display_skill_name(skill)
    hits: list[tuple[str, str, str]] = []
    best_match_type = "ABSENT"
    usage_context = False

    for heading, content in _resume_sections(resume_text):
        match_type, match = _find_explicit(content, normalized)
        if not match:
            continue
        line = next((line.strip() for line in content.splitlines() if _find_explicit(line, normalized)[1]), "")
        hits.append((heading, line, match_type))
        if match_type == "ALIAS":
            best_match_type = "ALIAS"
        elif best_match_type == "ABSENT":
            best_match_type = "EXACT"
        if re.search(
            r"\b(?:used|using|built|developed|managed|configured|implemented|packaged|deployed|created)\b",
            line,
            re.I,
        ):
            usage_context = True

    # Avoid counting the Full Resume duplicate when section-aware hits exist.
    section_hits = [hit for hit in hits if hit[0] != "Full Resume"]
    effective_hits = section_hits or hits
    headings = list(dict.fromkeys(heading for heading, _, _ in effective_hits))
    snippets = list(dict.fromkeys(line for _, line, _ in effective_hits if line))[:3]
    professional = any(re.search(r"experience|employment", heading, re.I) for heading in headings)
    project = any(re.search(r"project", heading, re.I) for heading in headings)
    skills_list = any(re.search(r"skill|technolog|competenc|build|tool", heading, re.I) for heading in headings)
    summary = any(re.search(r"summary|profile|objective", heading, re.I) for heading in headings)
    certification = any(re.search(r"certificat|licen", heading, re.I) for heading in headings)

    if effective_hits:
        if usage_context:
            match_type = "CONTEXTUAL"
        else:
            match_type = best_match_type
        if professional or project:
            strength = "strong"
        elif skills_list and (summary or certification):
            strength = "medium"
        elif skills_list:
            strength = "weak"
        elif len(headings) > 1 or usage_context:
            strength = "medium"
        else:
            strength = "weak"
        # Any explicit resume hit is VERIFIED — never treat EXACT/ALIAS/CONTEXTUAL as MISSING.
        resume_status = "VERIFIED"
        return {
            "skill": display,
            "normalized_skill": normalized,
            "status": resume_status,
            "resume_status": resume_status,
            "found": True,
            "present": True,
            "found_in_resume": True,
            "evidence_strength": strength,
            "evidence_sections": headings,
            "evidence_snippets": snippets,
            "match_type": match_type,
            "needs_user_confirmation": False,
            "needs_evidence": False,
        }

    for pattern in INDIRECT_PATTERNS.get(normalized, []):
        match = re.search(pattern, resume_text or "", re.I | re.S)
        if match:
            snippet = re.sub(r"\s+", " ", match.group(0)).strip()
            return {
                "skill": display,
                "normalized_skill": normalized,
                "status": "INDIRECT",
                "resume_status": "INDIRECT",
                "found": True,
                "present": True,
                "found_in_resume": True,
                "evidence_strength": "weak",
                "evidence_sections": ["Full Resume"],
                "evidence_snippets": [snippet],
                "match_type": "INDIRECT",
                "needs_user_confirmation": True,
                "needs_evidence": True,
            }

    return {
        "skill": display,
        "normalized_skill": normalized,
        "status": "MISSING",
        "resume_status": "MISSING",
        "found": False,
        "present": False,
        "found_in_resume": False,
        "evidence_strength": "none",
        "evidence_sections": [],
        "evidence_snippets": [],
        "match_type": "ABSENT",
        "needs_user_confirmation": True,
        "needs_evidence": True,
    }


def extract_resume_skill_evidence(resume_text: str, skills: list[str]) -> dict[str, dict[str, Any]]:
    result = {}
    for skill in skills:
        normalized = normalize_skill_name(skill)
        if normalized not in result:
            result[normalized] = get_skill_evidence(skill, resume_text)
    return result


def compare_job_requirements_to_resume(requirements: list[dict[str, Any]], resume_text: str) -> dict[str, Any]:
    skills = []
    seen = set()
    for requirement in requirements:
        normalized = normalize_skill_name(requirement.get("skill", ""))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        evidence = get_skill_evidence(requirement.get("skill", ""), resume_text)
        requirement_type = str(requirement.get("requirement_type") or requirement.get("importance") or "MANDATORY").upper()
        skills.append({
            **evidence,
            "skill": display_skill_name(requirement.get("skill", "")),
            "requirement_type": requirement_type,
            "category": requirement.get("category") or CATEGORY_MAP.get(normalized, "Other"),
            "needs_evidence": evidence["resume_status"] != "VERIFIED",
            "found_in_resume": bool(evidence.get("found")),
        })

    summary = {
        "total_requirements": len(skills),
        "verified": sum(item["resume_status"] == "VERIFIED" for item in skills),
        "weak_or_indirect": sum(item["resume_status"] in {"WEAK", "INDIRECT"} for item in skills),
        "missing_mandatory": sum(item["resume_status"] == "MISSING" and item["requirement_type"] == "MANDATORY" for item in skills),
        "missing_preferred": sum(item["resume_status"] == "MISSING" and item["requirement_type"] == "PREFERRED" for item in skills),
    }
    return {"skills": skills, "summary": summary, "analysis_version": SKILL_ANALYSIS_VERSION}
