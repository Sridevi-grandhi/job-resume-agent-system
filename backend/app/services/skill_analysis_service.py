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
from app.services.technology_catalog import (
    CAPABILITY_EVIDENCE,
    RELATED_NOT_EQUIVALENT,
    SKILL_CATEGORIES,
    TECHNOLOGY_SYNONYMS,
)

# Bump when matching rules change so requirement caches invalidate.
SKILL_ANALYSIS_VERSION = "8-capability-contextual-evidence"
ANALYSIS_VERSION = SKILL_ANALYSIS_VERSION

# Headings that indicate professional work history (domain-independent).
PROFESSIONAL_HEADING_RE = re.compile(
    r"experience|employment|work\s+history|career\s+history|career\s+path|"
    r"work\s+experience|professional\s+background|positions?\s+held",
    re.I,
)
PROJECT_HEADING_RE = re.compile(r"project", re.I)
SKILLS_HEADING_RE = re.compile(r"skill|technolog|competenc|build|tool|stack|expertise", re.I)
SUMMARY_HEADING_RE = re.compile(r"summary|profile|objective", re.I)
CERT_HEADING_RE = re.compile(r"certificat|licen", re.I)

logger = logging.getLogger(__name__)

# Action verbs that indicate a resume line describes real professional/project usage
# rather than a bare keyword list. Deliberately broad and domain-independent — it must
# recognize DevOps ("provisioned", "administered"), data ("ingested", "modeled"), QA
# ("automated", "validated"), and general engineering phrasing equally, without ever
# referencing a specific role or technology.
USAGE_CONTEXT_VERBS = re.compile(
    r"\b(?:"
    r"used|using|utiliz(?:ed|ing)|built|building|develop(?:ed|ing)|manag(?:ed|ing)|"
    r"configur(?:ed|ing)|implement(?:ed|ing)|packag(?:ed|ing)|deploy(?:ed|ing)|"
    r"creat(?:ed|ing)|administ(?:ered|ering)|provision(?:ed|ing)|automat(?:ed|ing)|"
    r"migrat(?:ed|ing)|architect(?:ed|ing)|engineer(?:ed|ing)|orchestrat(?:ed|ing)|"
    r"maintain(?:ed|ing)|monitor(?:ed|ing)|integrat(?:ed|ing)|optimiz(?:ed|ing)|"
    r"scal(?:ed|ing)|led|lead(?:ing)?|establish(?:ed|ing)|streamlin(?:ed|ing)|"
    r"wrote|writing|script(?:ed|ing)|containeriz(?:ed|ing)|operat(?:ed|ing)|"
    r"support(?:ed|ing)|resolv(?:ed|ing)|troubleshot|troubleshoot(?:ing)?|"
    r"debugg?(?:ed|ing)|test(?:ed|ing)|deliver(?:ed|ing)|design(?:ed|ing)|"
    r"analyz(?:ed|ing)|ingest(?:ed|ing)|model(?:ed|ing|led|ling)|"
    r"spearhead(?:ed|ing)|drove|driving|own(?:ed|ing)|launch(?:ed|ing)|"
    r"upgrad(?:ed|ing)|refactor(?:ed|ing)|authored|instrument(?:ed|ing)|"
    r"execut(?:ed|ing)|ran|run(?:ning)?|perform(?:ed|ing)|handled|handling|"
    r"leverag(?:ed|ing)|applied|applying|contribut(?:ed|ing)|collaborat(?:ed|ing)|"
    r"work(?:ed|ing)\s+with|responsible\s+for"
    r")\b",
    re.I,
)

MAVEN_ALIASES = {
    "maven",
    "apache maven",
    "maven build tool",
    "maven build",
    "maven dependency management",
    "mvn",
}

CANONICAL_ALIASES = {
    "jpa": ["java persistence api", "javax.persistence", "jakarta.persistence"],
    "spring boot": ["springboot", "spring-boot"],
    "react": ["reactjs", "react.js", "react js"],
    "postgresql": ["postgres", "postgre sql", "postgre-sql"],
    "sql server": ["sqlserver", "sql-server", "ms sql", "mssql", "microsoft sql server"],
    "maven": sorted(MAVEN_ALIASES - {"maven"}),
    "scrum": ["scrum methodology", "agile scrum"],
    "aws": ["amazon web services"],
    "kubernetes": ["k8s", "eks"],
    "github actions": ["github action"],
    "gitlab ci": ["gitlab ci/cd", "gitlab cicd"],
    "argo cd": ["argocd"],
    "prometheus": ["prometheus monitoring"],
    "linux": ["linux administration"],
    "terraform": ["hashicorp terraform"],
    "rest apis": ["restful api", "restful apis", "rest api"],
    "ci/cd": ["ci cd", "cicd", "continuous integration continuous delivery"],
    "javascript": ["js"],
    "typescript": ["ts"],
    "node.js": ["nodejs", "node js"],
    ".net": ["dotnet", "dot net"],
}

DISPLAY_NAMES = {
    "aws": "AWS", "ci/cd": "CI/CD", "rest apis": "REST APIs",
    "sql server": "SQL Server", "postgresql": "PostgreSQL", "node.js": "Node.js",
    ".net": ".NET", "c++": "C++", "c#": "C#", "maven": "Maven",
    "jpa": "JPA", "javascript": "JavaScript", "typescript": "TypeScript",
    "kubernetes": "Kubernetes",
    "test automation": "Test Automation",
    "database validation": "Database Validation",
    "api testing": "API Testing",
    "infrastructure as code": "Infrastructure as Code",
    "etl": "ETL",
    "rest assured": "Rest Assured",
    "azure devops": "Azure DevOps",
}

# Keep legacy INDIRECT_PATTERNS for CI/CD/Scrum; capability catalog is preferred.
INDIRECT_PATTERNS = {
    "ci/cd": [r"\b(?:github actions|gitlab ci|jenkins)\b"],
    "scrum": [
        r"\bagile\b.{0,80}\b(?:sprint planning|retrospectives?|sprint ceremonies|daily standups?)\b",
        r"\b(?:sprint planning|retrospectives?|daily standups?)\b",
    ],
}

CATEGORY_MAP = {
    "maven": "Build Tool", "gradle": "Build Tool", "scrum": "Methodology", "agile": "Methodology",
    "oracle": "Database", "sql server": "Database", "postgresql": "Database", "sql": "Database",
    "jpa": "Framework", "kubernetes": "Cloud / DevOps",
}


def skill_category(skill: str) -> str:
    """Return the claim category for user-facing validation diagnostics.

    This uses the same normalization path as requirement evidence; unknown
    terms remain conservatively classified as technologies so an unsupported
    vendor/tool claim cannot evade the truthfulness gate.
    """
    normalized = normalize_skill_name(skill)
    display = display_skill_name(normalized)
    category = SKILL_CATEGORIES.get(display)
    if category:
        return category
    if normalized in CATEGORY_MAP:
        return CATEGORY_MAP[normalized].lower()
    lowered = str(skill or "").lower()
    if any(token in lowered for token in ("certified", "certification", "certificate")):
        return "certification"
    if any(token in lowered for token in ("architecture", "architectural")):
        return "architecture pattern"
    if any(token in lowered for token in ("protocol", "oauth", "http", "grpc")):
        return "protocol"
    return "technology"


def _basic_normalize(value: str) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"(?<=\w)[_-](?=\w)", " ", text)
    text = re.sub(r"[^a-z0-9+#./\s-]", " ", text)
    return re.sub(r"\s+", " ", text).strip(" -")


def _synonym_canonical_map() -> dict[str, str]:
    """Map synonym/alias phrases → normalized canonical skill keys."""
    mapping: dict[str, str] = {}
    for synonym, canonical in TECHNOLOGY_SYNONYMS.items():
        mapping[_basic_normalize(synonym)] = _basic_normalize(canonical)
        mapping[_basic_normalize(canonical)] = _basic_normalize(canonical)
    for canonical, definition in CAPABILITY_EVIDENCE.items():
        mapping[_basic_normalize(canonical)] = _basic_normalize(canonical)
        for alias in definition.get("aliases") or []:
            mapping[_basic_normalize(alias)] = _basic_normalize(canonical)
    return mapping


_SYNONYM_TO_CANONICAL = None


def _synonym_lookup() -> dict[str, str]:
    global _SYNONYM_TO_CANONICAL
    if _SYNONYM_TO_CANONICAL is None:
        _SYNONYM_TO_CANONICAL = _synonym_canonical_map()
    return _SYNONYM_TO_CANONICAL


def normalize_skill_name(skill: str) -> str:
    normalized = _basic_normalize(skill)
    if normalized in MAVEN_ALIASES or normalized == "maven":
        return "maven"
    synonym_hit = _synonym_lookup().get(normalized)
    if synonym_hit:
        return synonym_hit
    for canonical, aliases in CANONICAL_ALIASES.items():
        alias_norms = {_basic_normalize(alias) for alias in aliases}
        if normalized == canonical or normalized in alias_norms:
            return canonical
    return normalized


def display_skill_name(skill: str) -> str:
    normalized = normalize_skill_name(skill)
    if normalized in DISPLAY_NAMES:
        return DISPLAY_NAMES[normalized]
    # Prefer catalog display casing when available.
    for synonym, canonical in TECHNOLOGY_SYNONYMS.items():
        if _basic_normalize(canonical) == normalized:
            return canonical
    return " ".join(part.capitalize() for part in normalized.split())


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
    # Reverse TECHNOLOGY_SYNONYMS + capability aliases so resume search uses genuine equivalents.
    for synonym, canonical in TECHNOLOGY_SYNONYMS.items():
        if _basic_normalize(canonical) == normalized:
            aliases.append(synonym)
            aliases.append(canonical)
    capability = CAPABILITY_EVIDENCE.get(normalized) or {}
    aliases.extend(capability.get("aliases") or [])
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


def _normalize_line(line: str) -> str:
    """Normalize resume lines for set membership — strip bullets/numbering so a hit
    on '- Built X' intersects the employment bullet 'Built X'."""
    text = re.sub(r"\s+", " ", str(line or "")).strip().lower()
    text = re.sub(r"^[-•*\u2022\u25cf\u25e6]+\s*", "", text)
    text = re.sub(r"^\d+[.)]\s*", "", text)
    return text


def _line_has_usage_context(line: str) -> bool:
    """True when a line describes real usage — not a skills-category label.

    "Monitoring: Prometheus, Datadog" and "Build Tools: Maven" must not count as
    usage just because the category word matches a verb stem.
    """
    text = str(line or "").strip()
    if not text or not USAGE_CONTEXT_VERBS.search(text):
        return False
    if re.match(r"^[A-Za-z][A-Za-z0-9 /&+]{0,40}:\s*\S", text):
        after_colon = text.split(":", 1)[1]
        return bool(USAGE_CONTEXT_VERBS.search(after_colon))
    return True


def _resume_structure_context(resume_text: str) -> dict[str, Any]:
    """Sections plus a heading-independent index of lines already recognized as real
    employment/project content. The index lets evidence strength recognize professional
    usage even when a section's heading uses non-standard wording our pattern list
    doesn't cover, or when heading detection fails entirely."""
    structure = parse_resume_structure(resume_text or "")
    sections = [
        (section.get("heading") or "Content", section.get("content") or "")
        for section in structure.get("sections") or []
    ]
    # Full parsed text is always searched, even when headings are absent or parsing is imperfect.
    sections.append(("Full Resume", resume_text or ""))
    employment_lines = {
        _normalize_line(line)
        for employment in structure.get("employments") or []
        for line in [*(employment.get("bullets") or []), *(employment.get("raw_lines") or [])]
        if _normalize_line(line)
    }
    project_lines = {
        _normalize_line(line)
        for project in structure.get("projects") or []
        for line in [*(project.get("bullets") or []), project.get("description") or ""]
        if _normalize_line(line)
    }
    return {"sections": sections, "employment_lines": employment_lines, "project_lines": project_lines}


def _classify_evidence_strength(
    *,
    headings: list[str],
    hit_lines: set[str],
    employment_lines: set[str],
    project_lines: set[str],
    usage_context: bool,
    matched_rows: list[tuple[str, str, str]],
) -> str:
    """Determine strong/medium/weak from section context — never from role names.

    STRONG: demonstrated in professional/project experience (heading, employment index,
            or usage-verb line outside a pure skills list).
    MEDIUM: explicit multi-section or summary/cert support, or usage without clear job block.
    WEAK:   isolated skills-list keyword only.
    """
    professional = any(PROFESSIONAL_HEADING_RE.search(heading) for heading in headings) or bool(
        hit_lines & employment_lines
    )
    project = any(PROJECT_HEADING_RE.search(heading) for heading in headings) or bool(
        hit_lines & project_lines
    )
    skills_list = any(SKILLS_HEADING_RE.search(heading) for heading in headings)
    summary = any(SUMMARY_HEADING_RE.search(heading) for heading in headings)
    certification = any(CERT_HEADING_RE.search(heading) for heading in headings)

    non_skills_usage = any(
        _line_has_usage_context(line) and not SKILLS_HEADING_RE.search(heading)
        for heading, line, _ in matched_rows
        if heading != "Full Resume"
    )
    # PDF extractors often fold experience bullets into a Skills block. A long
    # action-verb line is still professional usage, regardless of heading label.
    experience_like_usage = any(
        _line_has_usage_context(line)
        and (
            bool(re.match(r"^\s*[-•*]", line))
            or len(line.strip()) >= 40
            or bool(hit_lines & employment_lines)
        )
        for _, line, _ in matched_rows
    )
    # Usage verbs on Full Resume / Content lines still count when structure parsing failed.
    fallback_usage = usage_context and (
        any(heading in {"Full Resume", "Content"} for heading, _, _ in matched_rows)
        or bool(hit_lines & employment_lines)
    )

    if professional or project or non_skills_usage or experience_like_usage:
        return "strong"
    if fallback_usage and not skills_list:
        return "strong"
    if skills_list and (summary or certification):
        return "medium"
    if usage_context and (len(headings) > 1 or fallback_usage):
        return "medium"
    if skills_list and not usage_context:
        return "weak"
    if skills_list and usage_context:
        return "medium"
    if len(headings) > 1 or usage_context:
        return "medium"
    return "weak"


def _evidence_reason(status: str, strength: str, match_type: str, sections: list[str]) -> str:
    if status == "MISSING":
        return "Requirement exists in the job description but no equivalent resume evidence was found."
    if status == "INDIRECT":
        return (
            "Related tooling or phrasing found; this is possible evidence only and does not "
            "establish the exact required technology."
        )
    if match_type == "CONTEXTUAL" and strength in {"strong", "medium"}:
        return (
            f"Professional contextual evidence for this requirement found in: "
            f"{', '.join(sections) or 'resume'}."
        )
    if strength == "strong":
        return f"Explicit {match_type.lower()} professional/project usage found in: {', '.join(sections) or 'resume'}."
    if strength == "medium":
        return f"Explicit mention found ({match_type}); supporting context is limited."
    return "Explicit keyword found, but detailed professional usage was not located."


def _classification_for(match_type: str, resume_status: str, strength: str) -> str:
    if resume_status == "MISSING":
        return "NOT_FOUND"
    if resume_status == "INDIRECT" or strength == "weak":
        return "POSSIBLE_EVIDENCE"
    if match_type in {"CONTEXTUAL"}:
        return "VERIFIED_CONTEXTUAL"
    if match_type in {"EXACT", "ALIAS"}:
        return "VERIFIED_EXACT"
    return "VERIFIED_CONTEXTUAL" if resume_status == "VERIFIED" else "POSSIBLE_EVIDENCE"


def _section_for_snippet(resume_text: str, snippet: str, context: dict[str, Any]) -> str:
    needle = _normalize_line(snippet)
    if not needle:
        return "Full Resume"
    if needle in context.get("employment_lines") or any(
        needle in line or line in needle for line in context.get("employment_lines") or []
    ):
        return "Professional Experience"
    if needle in context.get("project_lines") or any(
        needle in line or line in needle for line in context.get("project_lines") or []
    ):
        return "Projects"
    for heading, content in context.get("sections") or []:
        if heading == "Full Resume":
            continue
        if snippet and snippet.lower() in (content or "").lower():
            if SKILLS_HEADING_RE.search(heading):
                return "Technical Skills"
            if SUMMARY_HEADING_RE.search(heading):
                return "Professional Summary"
            if PROFESSIONAL_HEADING_RE.search(heading):
                return "Professional Experience"
            if PROJECT_HEADING_RE.search(heading):
                return "Projects"
            if CERT_HEADING_RE.search(heading):
                return "Certifications"
            return heading
    return "Full Resume"


def _find_capability_evidence(skill: str, resume_text: str, context: dict[str, Any]) -> dict[str, Any] | None:
    """Catalog-driven contextual verification for capability/concept requirements."""
    normalized = normalize_skill_name(skill)
    definition = CAPABILITY_EVIDENCE.get(normalized)
    if not definition:
        return None
    text = resume_text or ""
    matched_terms: list[str] = []
    snippets: list[str] = []

    for pattern in definition.get("verified_patterns") or []:
        match = re.search(pattern, text, re.I | re.S)
        if not match:
            continue
        snippet = re.sub(r"\s+", " ", match.group(0)).strip()
        snippets.append(snippet)
        matched_terms.append(match.group(0)[:80])

    for tool in definition.get("defining_tools") or []:
        match_type, match = _find_explicit(text, tool)
        if not match:
            continue
        # Prefer lines with usage verbs; skills-list-only defining tools still count as medium.
        line_hits = [
            line.strip()
            for line in text.splitlines()
            if _find_explicit(line, tool)[1]
        ]
        usage_lines = [line for line in line_hits if _line_has_usage_context(line)]
        chosen = (usage_lines or line_hits or [match.group(0)])[0]
        snippets.append(re.sub(r"\s+", " ", chosen).strip())
        matched_terms.append(tool)
        if usage_lines:
            # Strong professional defining-tool usage verifies the capability.
            pass

    if not snippets:
        return None

    # Determine strength from snippet context.
    usage = any(_line_has_usage_context(snippet) for snippet in snippets) or any(
        _normalize_line(snippet) in context["employment_lines"]
        or any(_normalize_line(snippet) in line or line in _normalize_line(snippet)
               for line in context["employment_lines"])
        for snippet in snippets
    )
    headings = [_section_for_snippet(text, snippet, context) for snippet in snippets]
    headings = list(dict.fromkeys(headings))
    strength = "strong" if usage else "medium"
    # Skills-list-only defining tool without usage → still verified for capability concepts
    # that are explicitly demonstrated by those tools, but medium confidence.
    if not usage and all(SKILLS_HEADING_RE.search(h or "") or h == "Technical Skills" for h in headings):
        strength = "medium"
    section = headings[0] if headings else "Full Resume"
    return {
        "match_type": "CONTEXTUAL",
        "classification": "VERIFIED_CONTEXTUAL",
        "resume_status": "VERIFIED",
        "evidence_strength": strength,
        "evidence_sections": headings or [section],
        "evidence_snippets": list(dict.fromkeys(snippets))[:3],
        "matched_terms": list(dict.fromkeys(_basic_normalize(term) for term in matched_terms))[:8],
        "confidence": 0.92 if strength == "strong" else 0.78,
        "needs_user_confirmation": strength == "weak",
        "reason": (
            f"Resume demonstrates {display_skill_name(skill)} through professional "
            f"contextual evidence in {section}."
        ),
    }


def _find_related_evidence(skill: str, resume_text: str) -> dict[str, Any] | None:
    """Related-but-not-equivalent signals.

    Capability concepts may surface as POSSIBLE_EVIDENCE (INDIRECT).
    Specific vendor/technologies stay NOT_FOUND — related hits are diagnostic only
    and never verify a different product (Postman≠Rest Assured, AWS≠Azure, etc.).
    """
    normalized = normalize_skill_name(skill)
    text = resume_text or ""
    related_def = dict(RELATED_NOT_EQUIVALENT.get(normalized) or {})
    capability = CAPABILITY_EVIDENCE.get(normalized) or {}
    is_capability = normalized in CAPABILITY_EVIDENCE
    related_tools = list(related_def.get("related_tools") or []) + list(capability.get("related_tools") or [])
    related_patterns = list(related_def.get("related_patterns") or []) + list(
        capability.get("related_patterns") or []
    )

    snippets: list[str] = []
    matched_terms: list[str] = []
    for tool in related_tools:
        match_type, match = _find_explicit(text, tool)
        if match:
            snippets.append(re.sub(r"\s+", " ", match.group(0)).strip())
            matched_terms.append(tool)
    for pattern in related_patterns:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            snippets.append(re.sub(r"\s+", " ", match.group(0)).strip())
            matched_terms.append(match.group(0)[:60])
    if not snippets:
        return None

    reason = (
        f"Related evidence found ({', '.join(matched_terms[:4])}); this does not establish "
        f"{display_skill_name(skill)} itself."
    )
    if is_capability:
        # e.g. Database Validation with SQL listed but no validation verbs.
        return {
            "match_type": "INDIRECT",
            "classification": "POSSIBLE_EVIDENCE",
            "resume_status": "INDIRECT",
            "evidence_strength": "weak",
            "evidence_sections": ["Full Resume"],
            "evidence_snippets": list(dict.fromkeys(snippets))[:3],
            "matched_terms": list(dict.fromkeys(_basic_normalize(term) for term in matched_terms))[:8],
            "confidence": 0.4,
            "needs_user_confirmation": True,
            "reason": reason,
        }

    # Vendor/technology requirements: related ≠ equivalent → still NOT_FOUND.
    return {
        "match_type": "ABSENT",
        "classification": "NOT_FOUND",
        "resume_status": "MISSING",
        "evidence_strength": "none",
        "evidence_sections": [],
        "evidence_snippets": [],
        "matched_terms": list(dict.fromkeys(_basic_normalize(term) for term in matched_terms))[:8],
        "related_evidence": list(dict.fromkeys(snippets))[:3],
        "confidence": 0.0,
        "needs_user_confirmation": True,
        "reason": reason,
    }


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
    context = _resume_structure_context(resume_text)

    for heading, content in context["sections"]:
        match_type, match = _find_explicit(content, normalized)
        if not match:
            continue
        # Every matching line matters, not just the first — a skill's earliest mention
        # (often a Skills-list or summary line) must never hide a later bullet that
        # proves real professional/project usage of the same skill.
        matched_lines = [line.strip() for line in content.splitlines() if _find_explicit(line, normalized)[1]]
        if not matched_lines and match:
            # Content matched as a whole (e.g. single wrapped PDF paragraph).
            start = max(0, match.start() - 80)
            end = min(len(content), match.end() + 80)
            matched_lines = [re.sub(r"\s+", " ", content[start:end]).strip()]
        for line in matched_lines:
            hits.append((heading, line, match_type))
        if match_type == "ALIAS":
            best_match_type = "ALIAS"
        elif best_match_type == "ABSENT":
            best_match_type = "EXACT"
        if any(_line_has_usage_context(line) for line in matched_lines):
            usage_context = True

    # Avoid counting the Full Resume duplicate when section-aware hits exist.
    section_hits = [hit for hit in hits if hit[0] != "Full Resume"]
    effective_hits = section_hits or hits
    headings = list(dict.fromkeys(heading for heading, _, _ in effective_hits))
    snippets = list(dict.fromkeys(line for _, line, _ in effective_hits if line))[:3]
    hit_lines = {_normalize_line(line) for _, line, _ in effective_hits if line}

    if effective_hits:
        if usage_context:
            match_type = "CONTEXTUAL"
        else:
            match_type = best_match_type
        strength = _classify_evidence_strength(
            headings=headings,
            hit_lines=hit_lines,
            employment_lines=context["employment_lines"],
            project_lines=context["project_lines"],
            usage_context=usage_context,
            matched_rows=effective_hits,
        )
        # Explicit resume hits are never MISSING. Strong/medium ⇒ auto-verified.
        # Weak skills-list-only hits still report VERIFIED+weak; the UI remapper may
        # ask for confirmation without claiming the skill was "not found".
        resume_status = "VERIFIED"
        needs_confirmation = strength == "weak"
        classification = _classification_for(match_type, resume_status, strength)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "skill_evidence skill=%s normalized=%s match=%s strength=%s classification=%s sections=%s",
                display, normalized, match_type, strength, classification, headings,
            )
        return {
            "skill": display,
            "normalized_skill": normalized,
            "canonical_requirement": display,
            "status": resume_status,
            "resume_status": resume_status,
            "evidence_status": "RESUME_VERIFIED" if not needs_confirmation else "NEEDS_CONFIRMATION",
            "classification": classification,
            "found": True,
            "present": True,
            "found_in_resume": True,
            "evidence_strength": strength,
            "evidence_sections": headings,
            "evidence_sources": headings,
            "evidence_snippets": snippets,
            "matched_terms": aliases_for_skill(normalized)[:1],
            "match_type": match_type,
            "confidence": {"strong": 0.95, "medium": 0.8, "weak": 0.45}.get(strength, 0.5),
            "reason": _evidence_reason(resume_status, strength, match_type, headings),
            "needs_user_confirmation": needs_confirmation,
            "needs_evidence": needs_confirmation,
        }

    capability = _find_capability_evidence(normalized, resume_text, context)
    if capability:
        return {
            "skill": display,
            "normalized_skill": normalized,
            "canonical_requirement": display,
            "status": capability["resume_status"],
            "resume_status": capability["resume_status"],
            "evidence_status": "RESUME_VERIFIED",
            "classification": capability["classification"],
            "found": True,
            "present": True,
            "found_in_resume": True,
            "evidence_strength": capability["evidence_strength"],
            "evidence_sections": capability["evidence_sections"],
            "evidence_sources": capability["evidence_sections"],
            "evidence_snippets": capability["evidence_snippets"],
            "matched_terms": capability.get("matched_terms") or [],
            "match_type": capability["match_type"],
            "confidence": capability["confidence"],
            "reason": capability["reason"],
            "needs_user_confirmation": capability["needs_user_confirmation"],
            "needs_evidence": capability["needs_user_confirmation"],
        }

    related = _find_related_evidence(normalized, resume_text)
    if related and related["resume_status"] == "INDIRECT":
        return {
            "skill": display,
            "normalized_skill": normalized,
            "canonical_requirement": display,
            "status": related["resume_status"],
            "resume_status": related["resume_status"],
            "evidence_status": "NEEDS_CONFIRMATION",
            "classification": related["classification"],
            "found": True,
            "present": True,
            "found_in_resume": True,
            "evidence_strength": related["evidence_strength"],
            "evidence_sections": related["evidence_sections"],
            "evidence_sources": related["evidence_sections"],
            "evidence_snippets": related["evidence_snippets"],
            "matched_terms": related.get("matched_terms") or [],
            "related_evidence": related.get("related_evidence") or [],
            "match_type": related["match_type"],
            "confidence": related["confidence"],
            "reason": related["reason"],
            "needs_user_confirmation": True,
            "needs_evidence": True,
        }

    for pattern in INDIRECT_PATTERNS.get(normalized, []):
        match = re.search(pattern, resume_text or "", re.I | re.S)
        if match:
            snippet = re.sub(r"\s+", " ", match.group(0)).strip()
            return {
                "skill": display,
                "normalized_skill": normalized,
                "canonical_requirement": display,
                "status": "INDIRECT",
                "resume_status": "INDIRECT",
                "evidence_status": "NEEDS_CONFIRMATION",
                "classification": "POSSIBLE_EVIDENCE",
                "found": True,
                "present": True,
                "found_in_resume": True,
                "evidence_strength": "weak",
                "evidence_sections": ["Full Resume"],
                "evidence_sources": ["Full Resume"],
                "evidence_snippets": [snippet],
                "matched_terms": [],
                "match_type": "INDIRECT",
                "confidence": 0.4,
                "reason": _evidence_reason("INDIRECT", "weak", "INDIRECT", ["Full Resume"]),
                "needs_user_confirmation": True,
                "needs_evidence": True,
            }

    related_diag = related if related else {}
    return {
        "skill": display,
        "normalized_skill": normalized,
        "canonical_requirement": display,
        "status": "MISSING",
        "resume_status": "MISSING",
        "evidence_status": "MISSING",
        "classification": "NOT_FOUND",
        "found": False,
        "present": False,
        "found_in_resume": False,
        "evidence_strength": "none",
        "evidence_sections": [],
        "evidence_sources": [],
        "evidence_snippets": [],
        "matched_terms": related_diag.get("matched_terms") or [],
        "related_evidence": related_diag.get("related_evidence") or related_diag.get("evidence_snippets") or [],
        "match_type": "ABSENT",
        "confidence": 0.0,
        "reason": related_diag.get("reason") or _evidence_reason("MISSING", "none", "ABSENT", []),
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
