"""Deterministic, evidence-based ATS scoring.

Source-of-truth boundaries:
- ORIGINAL_RESUME is the only evidence of candidate experience.
- JOB_DESCRIPTION and skills_required describe employer requirements only.
- USER_PREFERENCES are intentionally not accepted by this module.
- An optimized resume may surface original evidence, but cannot create evidence.
"""

from __future__ import annotations

import re
import logging
from typing import Any

from app.services.keyword_utils import contains_keyword, extract_job_keywords, normalize_keyword
from app.services.resume_structure_parser import (
    EMAIL_PATTERN,
    PHONE_PATTERN,
    URL_PATTERN,
    parse_resume_structure,
)
from app.services.skill_analysis_service import (
    extract_resume_skill_evidence,
    get_skill_evidence,
)

logger = logging.getLogger(__name__)

ANALYSIS_VERSION = "role-signal-weighted-v10-title-extraction-fix"



# Generic/transferable skills carry low scoring influence and can never
# substitute for missing mandatory technologies (spec Part 7). Specific
# vendor technologies (AWS, PostgreSQL, Docker, Kubernetes, etc.) are NOT
# generic — they score at full credit like any other required skill.
GENERIC_SKILLS = {
    "Agile", "Scrum", "Git", "Jira", "API", "REST APIs",
    "teamwork", "communication", "cloud", "database",
}
GENERIC_SKILL_CREDIT_CAP = 0.4

ROLE_FAMILIES = {
    "DevOps": {
        "core": ["CI/CD"],
        "frameworks": ["Docker", "Kubernetes", "Terraform", "Jenkins"],
        "frontend": [],
        "indicators": ["DevOps", "CI/CD", "Docker", "Kubernetes", "Terraform", "Jenkins", "Ansible"],
        "defining": [
            "DevOps", "infrastructure as code", "infrastructure", "Terraform", "Kubernetes",
            "Helm", "Ansible", "CloudFormation", "platform engineering", "cluster",
            "observability", "deployment automation", "Linux administration", "cloud operations",
        ],
        "shared": ["CI/CD", "Jenkins", "Docker", "Git", "GitHub Actions", "AWS", "Azure", "Python", "Bash", "Agile"],
    },
    "Cloud Engineering": {
        "core": ["AWS", "Azure", "GCP"],
        "frameworks": ["Terraform", "CloudFormation", "Kubernetes"],
        "frontend": [],
        "indicators": ["cloud engineer", "AWS", "Azure", "GCP", "Terraform", "CloudFormation"],
        "defining": ["cloud engineer", "AWS", "Azure", "GCP", "Terraform", "CloudFormation"],
        "shared": ["Docker", "Kubernetes", "CI/CD", "Jenkins", "Python"],
    },
    "Site Reliability Engineering": {
        "core": ["CI/CD"],
        "frameworks": ["Kubernetes", "Terraform", "Prometheus", "Grafana"],
        "frontend": [],
        "indicators": ["site reliability", "SRE", "Kubernetes", "Terraform", "Prometheus", "Grafana"],
        "defining": ["site reliability", "SRE", "Prometheus", "Grafana", "observability", "reliability"],
        "shared": ["Kubernetes", "Terraform", "CI/CD", "Docker", "AWS"],
    },
    "Platform Engineering": {
        "core": ["CI/CD"],
        "frameworks": ["Kubernetes", "Terraform", "Helm", "Argo CD"],
        "frontend": [],
        "indicators": ["platform engineer", "platform engineering", "Kubernetes", "Terraform", "Helm", "Argo CD"],
        "defining": ["platform engineer", "platform engineering", "Helm", "Argo CD", "developer platform"],
        "shared": ["Kubernetes", "Terraform", "CI/CD", "Docker"],
    },
    "SDET / QA Automation": {
        "core": ["Selenium", "Cypress", "Playwright", "Appium"],
        "frameworks": ["Cucumber", "TestNG", "JUnit", "XCTest", "XCUITest"],
        "frontend": [],
        "indicators": [
            "SDET", "QA Automation", "Selenium", "Cypress", "Playwright", "Appium",
            "Cucumber", "TestNG", "XCTest", "XCUITest",
        ],
        "defining": [
            "SDET", "QA Automation", "test automation", "automation test", "quality engineering",
            "Selenium", "Playwright", "Cypress", "Appium", "TestNG", "JUnit", "Cucumber", "BDD",
            "API testing", "Postman", "Rest Assured", "regression testing", "functional testing",
            "test framework", "test cases", "defect", "XCTest", "XCUITest",
        ],
        "shared": ["Jenkins", "CI/CD", "Git", "GitHub Actions", "SQL", "Agile", "Java", "Python", "Maven", "Docker", "AWS"],
    },
    "Frontend": {
        "core": ["JavaScript", "TypeScript"],
        "frameworks": ["React", "Angular", "Vue"],
        "frontend": ["React", "Angular", "Vue"],
        "indicators": ["frontend", "JavaScript", "TypeScript", "React", "Angular", "Vue", "HTML", "CSS"],
        "defining": ["frontend", "React", "Angular", "Vue", "TypeScript", "HTML", "CSS", "UI", "UX"],
        "shared": ["JavaScript", "Git", "CI/CD", "Agile", "Docker"],
    },
    "Java Full Stack": {
        "core": ["Java", "Spring Boot"],
        "frameworks": ["Spring", "Spring Boot", "Hibernate", "JPA", "Maven", "Gradle", "J2EE"],
        "frontend": ["React", "Angular", "JavaScript", "TypeScript"],
        "indicators": ["Java", "Spring", "Spring Boot", "Hibernate", "JPA", "Maven", "Gradle", "Microservices", "React", "Angular", "JVM", "J2EE"],
        "defining": ["Java", "Spring Boot", "Spring", "Hibernate", "JPA", "microservices", "full stack"],
        "shared": ["Maven", "Git", "CI/CD", "Jenkins", "SQL", "Docker", "AWS", "Agile"],
    },
    "Java Backend": {
        "core": ["Java", "Spring Boot"],
        "frameworks": ["Spring", "Spring Boot", "Hibernate", "JPA", "Maven", "Gradle", "J2EE"],
        "frontend": [],
        "indicators": ["Java", "Spring", "Spring Boot", "Hibernate", "JPA", "Maven", "Gradle", "Microservices", "JVM", "J2EE"],
        "defining": ["Java", "Spring Boot", "Spring", "Hibernate", "JPA", "microservices", "backend"],
        "shared": ["Maven", "Git", "CI/CD", "Jenkins", "SQL", "Docker", "AWS", "Agile", "REST APIs"],
    },
    "Python Full Stack": {
        "core": ["Python"],
        "frameworks": ["Django", "Flask", "FastAPI"],
        "frontend": ["React", "Angular", "JavaScript", "TypeScript"],
        "indicators": ["Python", "Django", "Flask", "FastAPI", "React", "Angular", "Celery", "SQLAlchemy"],
        "defining": ["Python", "Django", "Flask", "FastAPI", "full stack"],
        "shared": ["Git", "CI/CD", "SQL", "Docker", "AWS", "Agile"],
    },
    "Python Backend": {
        "core": ["Python"],
        "frameworks": ["Django", "Flask", "FastAPI", "Celery", "SQLAlchemy"],
        "frontend": [],
        "indicators": ["Python", "Django", "Flask", "FastAPI", "Celery", "SQLAlchemy"],
        "defining": ["Python", "Django", "Flask", "FastAPI", "Celery", "backend"],
        "shared": ["Git", "CI/CD", "SQL", "Docker", "AWS", "Agile", "PostgreSQL", "Redis"],
    },
    "Data Engineering": {
        "core": ["Spark", "PySpark", "ETL"],
        "frameworks": ["Kafka", "Airflow", "Databricks", "Snowflake"],
        "frontend": [],
        "indicators": ["ETL", "Spark", "PySpark", "Kafka", "Airflow", "Databricks", "Snowflake", "data pipeline", "data warehouse"],
        "defining": ["ETL", "Spark", "PySpark", "Airflow", "Databricks", "Snowflake", "data pipeline", "data warehouse", "data engineer"],
        "shared": ["Python", "SQL", "AWS", "Kafka", "Git", "CI/CD", "Docker"],
    },
    "Data Science": {
        "core": ["Python"],
        "frameworks": ["Pandas", "NumPy"],
        "frontend": [],
        "indicators": ["Python", "Pandas", "NumPy", "statistics", "data science"],
        "defining": ["data science", "data scientist", "Pandas", "NumPy", "statistics", "modeling"],
        "shared": ["Python", "SQL", "AWS", "Git"],
    },
    "Machine Learning": {
        "core": ["Python", "machine learning"],
        "frameworks": ["TensorFlow", "PyTorch", "scikit-learn"],
        "frontend": [],
        "indicators": ["machine learning", "TensorFlow", "PyTorch", "scikit-learn", "model training"],
        "defining": ["machine learning", "TensorFlow", "PyTorch", "scikit-learn", "model training", "ML engineer"],
        "shared": ["Python", "AWS", "Docker", "Git"],
    },
    "Mobile iOS": {"core": ["Swift"], "frameworks": ["Xcode", "XCTest", "XCUITest"], "frontend": [], "indicators": ["iOS", "Swift", "Xcode", "XCTest", "XCUITest"], "defining": ["iOS", "Swift", "Xcode", "XCUITest"], "shared": ["Git", "CI/CD", "Agile"]},
    "Mobile Android": {"core": ["Kotlin", "Java"], "frameworks": ["Android SDK"], "frontend": [], "indicators": ["Android", "Kotlin", "Android SDK"], "defining": ["Android", "Kotlin", "Android SDK"], "shared": ["Java", "Git", "CI/CD", "Agile"]},
    "Database / SQL": {"core": ["SQL"], "frameworks": ["PostgreSQL", "MySQL", "Oracle", "SQL Server"], "frontend": [], "indicators": ["database", "SQL", "PostgreSQL", "MySQL", "Oracle", "SQL Server"], "defining": ["database", "DBA", "SQL Server", "PostgreSQL", "Oracle"], "shared": ["SQL", "Git", "Agile"]},
    "Business Analysis": {"core": ["business analysis"], "frameworks": ["Jira"], "frontend": [], "indicators": ["business analyst", "requirements gathering", "business analysis", "stakeholder"], "defining": ["business analyst", "requirements gathering", "stakeholder", "business analysis"], "shared": ["Jira", "Agile", "SQL"]},
}

# Shared/supporting tools — useful evidence for ATS matching, but weak alone for role family.
SHARED_ROLE_TOOLS = {
    "Jenkins", "Git", "GitHub Actions", "GitLab CI", "CI/CD", "Docker", "AWS", "Azure", "GCP",
    "SQL", "Agile", "Scrum", "Jira", "Maven", "Gradle", "Bash", "Linux", "Python", "Java",
}


def _has(text: str, skill: str) -> bool:
    evidence = get_skill_evidence(skill, text or "")
    return evidence["match_type"] in {"EXACT", "ALIAS", "CONTEXTUAL"}


SENIORITY_WORDS = re.compile(r"\b(?:senior|sr\.?|lead|principal|staff|junior|jr\.?)\b", re.I)
# Title rules match ROLE TITLES only — never shared tools like CI/CD.
CANONICAL_TITLE_RULES = (
    ("Site Reliability Engineering", re.compile(r"\b(?:site reliability engineer(?:ing)?|sre)\b", re.I)),
    ("Platform Engineering", re.compile(r"\b(?:data platform engineer|platform engineer(?:ing)?|infrastructure automation engineer)\b", re.I)),
    ("Cloud Engineering", re.compile(r"\bcloud engineer(?:ing)?\b", re.I)),
    ("DevOps", re.compile(r"\b(?:dev\s*sec\s*ops|dev\s*ops)\b", re.I)),
    ("SDET / QA Automation", re.compile(
        r"\b(?:sdet|software development engineer in test|qa automation(?:\s+engineer)?|"
        r"automation test engineer|test automation engineer|quality engineer|"
        r"qa engineer|software qa|quality assurance(?:\s+engineer)?)\b",
        re.I,
    )),
    ("Data Engineering", re.compile(r"\b(?:data engineer(?:ing)?|etl developer|big data engineer)\b", re.I)),
    ("Java Full Stack", re.compile(r"\b(?:java\s+full[\s-]*stack|full[\s-]*stack\s+java|java application developer)\b", re.I)),
    ("Java Backend", re.compile(r"\b(?:java\s+(?:backend|developer|engineer|architect)|backend java engineer|spring boot developer)\b", re.I)),
    ("Python Full Stack", re.compile(r"\bpython\s+full[\s-]*stack\b", re.I)),
    ("Python Backend", re.compile(r"\bpython\s+(?:backend|developer|engineer)\b", re.I)),
    ("Data Science", re.compile(r"\bdata scient(?:ist|ce)\b", re.I)),
    ("Machine Learning", re.compile(r"\b(?:machine learning|ml) engineer(?:ing)?\b", re.I)),
    ("Frontend", re.compile(r"\bfront[\s-]*end\b", re.I)),
    ("Mobile iOS", re.compile(r"\bios (?:developer|engineer)\b", re.I)),
    ("Mobile Android", re.compile(r"\bandroid (?:developer|engineer)\b", re.I)),
    ("Database / SQL", re.compile(r"\b(?:database|sql) (?:developer|engineer|administrator|dba)\b", re.I)),
    ("Business Analysis", re.compile(r"\bbusiness analyst\b", re.I)),
)

ROLE_FAMILY_IDS = {
    "DevOps": "DEVOPS", "Cloud Engineering": "CLOUD_ENGINEERING",
    "Site Reliability Engineering": "SRE", "Platform Engineering": "PLATFORM_ENGINEERING",
    "Java Backend": "JAVA_BACKEND", "Java Full Stack": "JAVA_FULL_STACK",
    "Python Backend": "PYTHON_BACKEND", "Python Full Stack": "PYTHON_FULL_STACK",
    "Frontend": "FRONTEND", "Data Engineering": "DATA_ENGINEERING",
    "Data Science": "DATA_SCIENCE", "Machine Learning": "MACHINE_LEARNING",
    "SDET / QA Automation": "SDET", "Mobile iOS": "IOS", "Mobile Android": "ANDROID",
    "Database / SQL": "DATABASE", "Business Analysis": "BUSINESS_ANALYST",
    "Other": "OTHER", "Unknown": "UNKNOWN",
}


def normalize_role_title(title: str) -> str:
    """Normalize presentation variants without turning preferences into evidence."""
    value = SENIORITY_WORDS.sub(" ", str(title or "").lower())
    value = re.sub(r"[/|:\-]+", " ", value)
    value = re.sub(r"\b(?:salt lake city|new york city|san francisco|chicago|remote|hybrid)\b", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _title_family(title: str) -> str | None:
    normalized = normalize_role_title(title)
    if not normalized:
        return None
    for family, pattern in CANONICAL_TITLE_RULES:
        if pattern.search(normalized):
            return family
    return None


def _extract_resume_title_signals(text: str) -> list[str]:
    """Collect high-signal title lines: headline + recent employment titles."""
    structure = parse_resume_structure(text or "")
    titles: list[str] = []
    name = (structure.get("name") or "").strip()
    # Short resumes often parse a role title as "name" (e.g. "DevOps Engineer").
    # Keep it when it matches a known role title — never discard role evidence.
    if name and _title_family(name):
        titles.extend([name, name, name])
    # First non-contact lines often include professional title under the name.
    contact = structure.get("contact") or ""
    for line in (contact or text or "").splitlines()[:8]:
        cleaned = line.strip()
        if not cleaned:
            continue
        if cleaned == name and not _title_family(cleaned):
            continue
        if EMAIL_PATTERN.search(cleaned) or PHONE_PATTERN.search(cleaned) or URL_PATTERN.search(cleaned):
            continue
        if len(cleaned) < 80:
            titles.append(cleaned)
    # Also scan raw leading lines so title-only blobs are never missed.
    for line in (text or "").splitlines()[:4]:
        cleaned = line.strip()
        if cleaned and _title_family(cleaned):
            titles.append(cleaned)
    for index, employment in enumerate(structure.get("employments") or []):
        title = (employment.get("title") or "").strip()
        if not title:
            # Often "Title | Company | Dates" is stored as company/raw.
            raw = " ".join(employment.get("raw_lines") or [])
            title = raw.split("|")[0].strip() if raw else ""
        if title:
            # Recent roles weigh more by appearing first; duplicates ok for scoring.
            weight_copies = 3 if index == 0 else 2 if index == 1 else 1
            titles.extend([title] * weight_copies)
    return titles


def _signal_present(blob: str, signal: str) -> bool:
    if not signal or not blob:
        return False
    # Prefer cheap boundary search for multi-word role phrases; fall back to skill evidence.
    lowered = blob.lower()
    needle = signal.lower()
    if re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", lowered):
        return True
    if " " not in needle and "/" not in needle:
        return False
    return _has(blob, signal)


def _score_family_signals(
    *,
    family: str,
    definition: dict[str, Any],
    title: str,
    title_signals: list[str],
    body: str,
) -> tuple[float, list[str], list[str]]:
    """Weighted role score: titles + defining signals >> shared tools."""
    strong: list[str] = []
    supporting: list[str] = []
    score = 0.0
    title_blob = "\n".join([title or "", *title_signals])
    body_blob = body or ""
    full_blob = f"{title_blob}\n{body_blob}"

    # Explicit title match is decisive.
    if _title_family(title) == family:
        score += 12.0
        strong.append(f"title:{title}")
    for signal_title in title_signals:
        matched = _title_family(signal_title)
        if matched == family:
            score += 8.0
            strong.append(f"experience_title:{signal_title}")
        elif matched:
            score -= 1.5  # competing recent title

    defining = definition.get("defining") or definition.get("indicators") or []
    shared = set(definition.get("shared") or []) | SHARED_ROLE_TOOLS
    for signal in defining:
        in_title = _signal_present(title_blob, signal)
        in_body = _signal_present(body_blob, signal)
        if not in_title and not in_body:
            continue
        if signal in shared:
            points = 1.2 if in_title else 0.6
            supporting.append(signal)
        else:
            points = 3.5 if in_title else 2.2
            strong.append(signal)
        score += points

    already = {item.lower() for item in strong + supporting}
    for signal in list(definition.get("indicators") or []) + list(definition.get("frameworks") or []):
        if signal.lower() in already:
            continue
        if not _signal_present(full_blob, signal):
            continue
        if signal in shared:
            score += 0.35
            supporting.append(signal)
        else:
            score += 1.0
            strong.append(signal)
        already.add(signal.lower())

    # Cheap shared-tool explainability (no expensive evidence engine per token).
    for signal in definition.get("shared") or []:
        if signal.lower() in already:
            continue
        if _signal_present(full_blob, signal):
            score += 0.25
            supporting.append(signal)
            already.add(signal.lower())

    if "Full Stack" in family:
        has_fs_title = bool(re.search(r"\bfull[\s-]*stack\b", title_blob, re.I))
        has_frontend = any(
            _signal_present(full_blob, skill) for skill in definition.get("frontend", [])
        )
        has_core = any(_signal_present(full_blob, skill) for skill in definition.get("core", []))
        if has_fs_title or (has_frontend and has_core):
            score += 2.0
        else:
            # Backend-only evidence must not default to Full Stack via shared language signals.
            score -= 2.0

    def _uniq(items: list[str]) -> list[str]:
        seen = set()
        out = []
        for item in items:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out[:12]

    return score, _uniq(strong), _uniq(supporting)


def classify_role_families(text: str, title: str = "") -> list[str]:
    """Return ranked role families using multi-signal weighted scoring."""
    detail = classify_role(text, title)
    families = detail.get("families") or [detail.get("family") or "Other"]
    return [family for family in families if family]


def classify_role(text: str, title: str = "") -> dict[str, Any]:
    """Authoritative role result used by scoring, eligibility, and API responses.

    Shared tools (Jenkins, CI/CD, Git, Docker, cloud keywords) are supporting signals.
    Explicit titles and role-defining responsibilities dominate.
    """
    normalized_title = normalize_role_title(title)
    title_signals = _extract_resume_title_signals(text) if text else []
    # If caller passed no title, use first resume title signal as headline.
    effective_title = title or (title_signals[0] if title_signals else "")
    body = text or ""

    scored: list[tuple[str, float, list[str], list[str]]] = []
    for family, definition in ROLE_FAMILIES.items():
        points, strong, supporting = _score_family_signals(
            family=family,
            definition=definition,
            title=effective_title,
            title_signals=title_signals,
            body=body,
        )
        if points > 0:
            scored.append((family, points, strong, supporting))

    if not scored:
        return {
            "family": "Unknown",
            "family_id": "UNKNOWN",
            "confidence": 0.35,
            "families": ["Unknown"],
            "strong_signals": [],
            "supporting_signals": [],
            "explanation": "Insufficient role signals.",
        }

    scored.sort(key=lambda item: -item[1])
    best_family, best_score, strong, supporting = scored[0]
    second = scored[1][1] if len(scored) > 1 else 0.0
    # Require meaningful separation from shared-tool-only noise.
    if best_score < 2.5:
        return {
            "family": "Unknown",
            "family_id": "UNKNOWN",
            "confidence": 0.4,
            "families": ["Unknown"],
            "strong_signals": strong,
            "supporting_signals": supporting,
            "explanation": "Role signals were too weak for a confident family.",
        }

    families = [family for family, points, *_ in scored if points >= max(2.0, best_score * 0.55)][:3]
    explicit = _title_family(effective_title) == best_family or any(
        _title_family(signal) == best_family for signal in title_signals[:3]
    )
    if explicit and best_score >= second + 1.5:
        confidence = 0.96
    elif best_score >= second + 3:
        confidence = 0.9
    elif best_score >= second + 1:
        confidence = 0.78
    else:
        confidence = 0.62

    if confidence < 0.55:
        best_family = "Unknown"

    explanation = (
        f"Selected {best_family} from titles/responsibilities; "
        f"shared tools ({', '.join(supporting[:5]) or 'none'}) were supporting only."
    )
    return {
        "family": best_family,
        "family_id": ROLE_FAMILY_IDS.get(best_family, "OTHER"),
        "confidence": round(confidence, 2),
        "families": families if best_family != "Unknown" else ["Unknown"],
        "strong_signals": strong,
        "supporting_signals": supporting,
        "explanation": explanation,
        "score": round(best_score, 2),
    }


def compare_role_families(resume_family: str, job_family: str) -> str:
    if resume_family == "Unknown" or job_family == "Unknown":
        return "UNKNOWN"
    if resume_family == job_family:
        return "EXACT"
    related_pairs = {
        frozenset(("Java Backend", "Java Full Stack")),
        frozenset(("Python Backend", "Python Full Stack")),
        frozenset(("Data Engineering", "Data Science")),
        frozenset(("DevOps", "Cloud Engineering")),
        frozenset(("DevOps", "Site Reliability Engineering")),
        frozenset(("DevOps", "Platform Engineering")),
        frozenset(("Data Engineering", "Platform Engineering")),
    }
    if frozenset((resume_family, job_family)) in related_pairs:
        return "STRONG_RELATED"
    if resume_family == "Other" or job_family == "Other":
        return "UNKNOWN"
    overlap = set(ROLE_FAMILIES.get(resume_family, {}).get("core", [])) & set(
        ROLE_FAMILIES.get(job_family, {}).get("core", [])
    )
    if overlap:
        # Shared generic cores like CI/CD must not create false partial matches.
        if overlap.issubset({"CI/CD"}):
            return "MISMATCH"
        return "PARTIAL_TRANSFERABLE"
    return "MISMATCH"


def _split_requirement_phrases(text: str) -> tuple[str, str]:
    """Split JD into mandatory vs preferred blocks.

    Handles headings like ``Preferred:``, ``Preferred Skills:``, ``Nice to Have``,
    ``Desired Skills & Experience`` — including no space before ``:``.
    """
    source = text or ""
    desired_block = re.search(
        r"(?is)(?:^|\n)\s*(?:desired|preferred|nice to have|bonus)"
        r"(?:\s+(?:skills|qualifications|experience|requirements))*"
        r"(?:\s*&\s*experience)?"
        r"\s*[:\-]?\s*(.*)\Z",
        source,
    )
    if desired_block:
        preferred = desired_block.group(1)
        # Guard: ignore accidental mid-sentence "preferred" matches with almost no content.
        if len((preferred or "").strip()) >= 2:
            mandatory = source[:desired_block.start()]
            return mandatory, preferred
    preferred_parts = re.findall(
        r"(?is)(?:preferred|nice to have|bonus|plus)(?:\s+(?:skills|qualifications|experience|requirements))?\s*[:\-]?\s*([^.\n]+)",
        source,
    )
    preferred = " ".join(preferred_parts)
    mandatory = re.sub(
        r"(?is)(?:preferred|nice to have|bonus|plus)(?:\s+(?:skills|qualifications|experience|requirements))?\s*[:\-]?\s*[^.\n]+",
        " ",
        source,
    )
    return mandatory, preferred


def _relevant_experience(resume_text: str, job_family: str) -> dict[str, Any]:
    structure = parse_resume_structure(resume_text or "")
    relevant = []
    for employment in structure.get("employments") or []:
        role_text = "\n".join([
            employment.get("title") or "", employment.get("company") or "",
            " ".join(employment.get("bullets") or []), " ".join(employment.get("raw_lines") or []),
        ])
        families = classify_role_families(role_text, employment.get("title") or "")
        alignment = max(
            (compare_role_families(family, job_family) for family in (families or ["Other"])),
            key=lambda value: {"EXACT": 5, "STRONG_RELATED": 4, "PARTIAL_TRANSFERABLE": 3, "UNKNOWN": 2, "MISMATCH": 1}[value],
        )
        if alignment in {"EXACT", "STRONG_RELATED"}:
            start, end = employment.get("start_year"), employment.get("end_year")
            relevant.append({**employment, "role_alignment": alignment, "years": max(0, end - start) if start and end else None})
    parsed_years = sum(item["years"] for item in relevant if item["years"] is not None)
    return {
        "roles": relevant,
        "years": parsed_years if any(item["years"] is not None for item in relevant) else None,
        "confidence": "HIGH" if relevant and any(item["years"] is not None for item in relevant) else "MEDIUM" if relevant else "LOW",
    }


def _split_skills_field(skills_field: str) -> list[str]:
    """Split a skills_required field without destroying slash-joined technologies.

    Commas/semicolons/pipes/newlines are delimiters. Bare '/' is NOT a delimiter
    (CI/CD, HTML/CSS stay intact). Parenthetical OR lists are left for the OR parser.
    """
    parts = []
    for part in re.split(r"[,;|\n]+", skills_field or ""):
        cleaned = re.sub(r"\s+", " ", part.strip().strip("."))
        if len(cleaned) > 1:
            parts.append(cleaned)
    return parts


def _dedupe_slash_fragments(skills: list[str]) -> list[str]:
    """Drop bare fragments like CI / CD when CI/CD is already required."""
    normalized = list(dict.fromkeys(skills))
    joined = {skill for skill in normalized if "/" in skill}
    if not joined:
        return normalized
    fragments = set()
    for skill in joined:
        for piece in skill.split("/"):
            piece = piece.strip()
            if piece:
                fragments.add(piece)
                fragments.add(normalize_keyword(piece))
    return [skill for skill in normalized if skill not in fragments]


def _extract_or_groups(mandatory_text: str, required: list[str]) -> list[dict[str, Any]]:
    """Parse AND vs OR semantics from JD wording without role-specific hardcoding.

    Supports:
    - Compact pairs: ``Python or Bash``
    - Parenthetical lists: ``CI/CD (GitHub Actions, GitLab CI, or Jenkins)``
    - Short bullet alternatives: ``Selenium WebDriver, Playwright, OR Cypress``
    - Slash alternatives: ``MySQL/PostgreSQL`` (not CI/CD)
    """
    or_groups = []
    seen = set()

    def _skills_in_token(token: str) -> list[str]:
        cleaned = re.sub(r"[\(\)\[\]:]", " ", token or "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -,/")
        if not cleaned or len(cleaned) > 64:
            return []
        hits = []
        # Longest-first prevents Azure matching inside Azure DevOps, etc.
        for skill in sorted(required, key=lambda value: len(value), reverse=True):
            if not _has(cleaned, skill):
                continue
            if any(_has(existing, skill) or skill.lower() in existing.lower() for existing in hits):
                continue
            hits.append(skill)
        if not hits:
            canonical = normalize_keyword(cleaned)
            if canonical in required:
                hits.append(canonical)
        # When a token still carries trailing prose ("Cypress. Also Java"), keep the
        # earliest skill mention so OR lists are not abandoned.
        if len(hits) > 1:
            positions = []
            lowered = cleaned.lower()
            for skill in hits:
                match = re.search(rf"(?<![a-z0-9]){re.escape(skill.lower())}(?![a-z0-9])", lowered)
                if match:
                    positions.append((match.start(), skill))
            if positions:
                positions.sort()
                return [positions[0][1]]
        return hits

    text = mandatory_text or ""

    def _add_group(tokens: list[str]) -> None:
        alternatives = []
        for token in tokens:
            hits = _skills_in_token(token)
            # Each alternative token must resolve to exactly one required skill —
            # prevents "AWS, Kubernetes, ..., Python or Bash" from becoming one OR unit.
            if len(hits) != 1:
                return
            if hits[0] not in alternatives:
                alternatives.append(hits[0])
        if len(alternatives) >= 2:
            ordered = sorted(alternatives)
            key = tuple(ordered)
            if key not in seen:
                seen.add(key)
                or_groups.append({"type": "OR_GROUP", "skills": ordered})

    # Parenthetical tool lists only: "CI/CD (GitHub Actions, GitLab CI, or Jenkins)"
    for match in re.finditer(r"\(([^)]+)\)", text):
        inner = match.group(1)
        if not re.search(r"\bor\b", inner, re.I):
            continue
        parts = [part.strip() for part in re.split(r",|\bor\b", inner, flags=re.I) if part.strip()]
        if 2 <= len(parts) <= 6:
            _add_group(parts)

    # Short line/bullet alternatives: "A, B, OR C" (2–4 items, requires explicit OR).
    for line in text.splitlines():
        if not re.search(r"\bor\b", line, re.I):
            continue
        cleaned_line = re.sub(r"^[\s•\-\*\d.)]+", "", line).strip()
        if not cleaned_line or len(cleaned_line) > 180:
            continue
        # Keep the sentence fragment that contains OR; ignore trailing sentences.
        sentence = next(
            (part.strip() for part in re.split(r"(?<=\.)\s+", cleaned_line) if re.search(r"\bor\b", part, re.I)),
            cleaned_line,
        )
        sentence = re.sub(
            r"(?i)^(?:required|must\s+have|minimum(?:\s+qualifications)?|skills?|qualifications?)\s*:\s*",
            "",
            sentence,
        ).strip()
        # Normalize "API testing with Rest Assured, Postman, OR similar"
        sentence = re.sub(r"(?i)\bor\s+(?:similar|equivalent|comparable)\b.*$", "", sentence)
        sentence = re.sub(r"(?i)\b(?:with|using|via)\b", ",", sentence)
        parts = [
            part.strip(" -:;.")
            for part in re.split(r",|\bor\b", sentence, flags=re.I)
            if part.strip() and not re.fullmatch(r"(?i)similar|equivalent|etc\.?|and", part.strip())
        ]
        if 2 <= len(parts) <= 5:
            before = len(or_groups)
            _add_group(parts)
            if len(or_groups) > before:
                continue
        # Fallback: if the line mentions multiple already-required skills and OR,
        # treat those mentioned skills as alternatives (skip broad capability labels
        # when tool alternatives are also present).
        mentioned = [skill for skill in required if _has(sentence, skill)]
        capability_labels = {
            "API Testing", "CI/CD", "Database Validation", "REST APIs",
            "Test Automation", "Infrastructure as Code", "ETL",
        }
        tools = [skill for skill in mentioned if skill not in capability_labels]
        candidates = tools if len(tools) >= 2 else mentioned
        if 2 <= len(candidates) <= 5:
            ordered = sorted(candidates)
            key = tuple(ordered)
            if key not in seen:
                seen.add(key)
                or_groups.append({"type": "OR_GROUP", "skills": ordered})

    # Compact pairs: "Python or Bash", "GitHub Actions or Jenkins"
    for match in re.finditer(
        r"([A-Za-z0-9.+#/\-][A-Za-z0-9.+#/\- ]{0,40}?)\s+or\s+([A-Za-z0-9.+#/\-][A-Za-z0-9.+#/\- ]{0,40})",
        text,
        re.I,
    ):
        left = match.group(1).split(",")[-1].strip()
        right = re.split(
            r"(?i)\s+for\b|\s+with\b|\s+to\b|\s+in\b|\s+on\b|[,;.)\]]",
            match.group(2),
            maxsplit=1,
        )[0].strip()
        _add_group([left, right])

    # Short slash alternatives: MySQL/PostgreSQL, React/Angular — not CI/CD (single skill).
    for match in re.finditer(r"\b([A-Za-z][A-Za-z0-9.+#\-]{1,20})\s*/\s*([A-Za-z][A-Za-z0-9.+#\-]{1,20})\b", text):
        left, right = match.group(1), match.group(2)
        composite = normalize_keyword(f"{left}/{right}")
        if composite in required and "/" in composite:
            continue
        alternatives = list(dict.fromkeys(_skills_in_token(left) + _skills_in_token(right)))
        if len(alternatives) >= 2:
            ordered = sorted(alternatives)
            key = tuple(ordered)
            if key not in seen:
                seen.add(key)
                or_groups.append({"type": "OR_GROUP", "skills": ordered})
    return or_groups


def extract_structured_requirements(job) -> dict[str, Any]:
    """Extract requirements from the JD itself.

    Role-family catalogs are used only as recognition aids for terms that actually
    appear in the JD — they must NEVER invent mandatory skills that the JD did not
    mention (that caused false Appium/Cypress penalties for Selenium-only SDET JDs).
    """
    title = str(getattr(job, "title", "") or "")
    description = str(getattr(job, "description", "") or "")
    skills_field = str(getattr(job, "skills_required", "") or "")
    mandatory_text, preferred_text = _split_requirement_phrases(description)
    extracted = extract_job_keywords(title, description, skills_field)
    job_family = classify_role_families(f"{skills_field}\n{description}", title)[0]
    family = ROLE_FAMILIES.get(job_family, {"core": [], "frameworks": [], "frontend": []})

    explicit_required = [
        normalize_keyword(part)
        for part in _split_skills_field(skills_field)
        if len(part) > 1
    ]
    # Catalog hits that appear in the mandatory/required section or skills field.
    # A skill mentioned in BOTH sections stays mandatory (e.g. AWS required + "AWS certification" preferred).
    mandatory_detected = [
        skill for skill in extracted.get("allKeywords", [])
        if _has(mandatory_text, skill) or _has(skills_field, skill)
    ]
    # Family vocab is a hint list only: include ONLY skills actually present in JD text.
    family_hints = list(dict.fromkeys(
        list(family.get("core") or [])
        + list(family.get("frameworks") or [])
        + list(family.get("frontend") or [])
    ))
    family_mentioned = [
        skill for skill in family_hints
        if _has(f"{title}\n{skills_field}\n{mandatory_text}", skill)
    ]
    required = list(dict.fromkeys(
        explicit_required
        + mandatory_detected
        + family_mentioned
        + [skill for skill in extracted.get("requiredKeywords", []) if _has(f"{skills_field}\n{mandatory_text}", skill) or _has(title, skill)]
    ))
    required = _dedupe_slash_fragments([skill for skill in required if skill])
    if "Spring Boot" in required and "Spring" in required:
        required.remove("Spring")
    preferred = list(dict.fromkeys(
        [skill for skill in extracted.get("allKeywords", []) if _has(preferred_text, skill) and skill not in required]
        + [skill for skill in extracted.get("preferredKeywords", []) if _has(preferred_text, skill) and skill not in required]
    ))
    preferred = _dedupe_slash_fragments(preferred)
    or_groups = _extract_or_groups(f"{mandatory_text}\n{skills_field}", required)
    # Preferred OR groups (e.g. AWS OR Azure) — informative, not mandatory units.
    preferred_or_groups = _extract_or_groups(preferred_text, preferred)
    years_match = re.search(r"\b(\d+)\+?\s+years?\b", description, re.I)

    categories = {
        "languages": ["Java", "Python", "JavaScript", "TypeScript", "Kotlin", "Swift", "C#", "Go"],
        "cloud_skills": ["AWS", "Azure", "GCP"],
        "database_skills": ["SQL", "PostgreSQL", "MySQL", "Oracle", "MongoDB", "SQL Server", "Database Validation"],
        "frontend_skills": ["React", "Angular", "Vue", "HTML", "CSS", "JavaScript", "TypeScript"],
        "testing_skills": [
            "Selenium", "Cypress", "Playwright", "Appium", "JUnit", "TestNG", "PyTest",
            "XCTest", "XCUITest", "Cucumber", "Postman", "Rest Assured", "JMeter", "API Testing",
        ],
        "devops_skills": ["Docker", "Kubernetes", "Jenkins", "Terraform", "Ansible", "CI/CD", "Azure DevOps", "Git"],
    }
    all_terms = list(dict.fromkeys(required + preferred))
    result: dict[str, Any] = {
        "target_role": title,
        "job_role_family": job_family,
        "required_core_skills": required,
        "preferred_skills": preferred,
        "or_groups": or_groups,
        "preferred_or_groups": preferred_or_groups,
        "frameworks": [term for term in all_terms if term in (family.get("frameworks") or [])],
        "domain_requirements": [term for term in all_terms if term in {"Healthcare"}],
        "education_requirements": re.findall(r"(?i)(?:bachelor|master|phd|degree)[^.\n]*", mandatory_text),
        "certification_requirements": re.findall(r"(?i)(?:certification|certified)[^.\n]*", mandatory_text),
        "experience_years_required": int(years_match.group(1)) if years_match else None,
        "location_requirement": getattr(job, "location", None),
        "work_mode": getattr(job, "work_mode", None),
        "employment_type": getattr(job, "job_type", None),
        "jd_text_length": len(description.strip()),
        "skills_field_length": len(skills_field.strip()),
    }
    result.update({name: [term for term in all_terms if term in values] for name, values in categories.items()})
    return result


def extract_resume_evidence(resume_text: str, skills: list[str]) -> dict[str, dict[str, Any]]:
    authoritative = extract_resume_skill_evidence(resume_text or "", skills)
    evidence: dict[str, dict[str, Any]] = {}
    for skill in skills:
        item = authoritative.get(get_skill_evidence(skill, "")["normalized_skill"], {})
        strength = item.get("evidence_strength", "none")
        sections = item.get("evidence_sections", [])
        evidence[skill] = {
            **item,
            "skill": skill,
            "evidence_strength": "absent" if strength == "none" else strength,
            "evidence_category": (
                "professional"
                if any(
                    re.search(
                        r"experience|employment|work\s+history|career\s+history|work\s+experience",
                        heading,
                        re.I,
                    )
                    for heading in sections
                )
                else "project"
                if any(re.search(r"project", heading, re.I) for heading in sections)
                else "professional"
                if item.get("evidence_strength") == "strong"
                else None
            ),
            "recent_usage": strength == "strong",
            "years_claimed": None,
        }
    return evidence


def _resume_credit(evidence: dict) -> float:
    strength = evidence["evidence_strength"]
    if strength == "strong":
        # Strong professional (experience/employment) evidence: 100%.
        # Strong project-only evidence in the original resume: 80%.
        return 1.0 if evidence.get("evidence_category") == "professional" else 0.80
    return {"medium": 0.7, "weak": 0.3, "absent": 0.0}[strength]


def _skill_present_in_document(skill: str, document_text: str) -> bool:
    """True when the scored document visibly mentions the skill (including OR alternatives)."""
    if not document_text or not skill:
        return False
    parts = [part.strip() for part in re.split(r"\s+or\s+", str(skill), flags=re.I) if part.strip()]
    candidates = parts if len(parts) > 1 else [skill]
    for part in candidates:
        evidence = get_skill_evidence(part, document_text)
        if evidence.get("match_type") in {"EXACT", "ALIAS", "CONTEXTUAL"}:
            return True
        if contains_keyword(document_text, part):
            return True
    return False


def _skill_in_denied_list(skill: str, denied_skills: list[str] | set[str] | None) -> bool:
    """True when skill matches a REJECTED / NOT_POSSESSED / blocked label."""
    if not denied_skills or not skill:
        return False
    from app.services.skill_analysis_service import normalize_skill_name

    skill_forms = {
        skill,
        skill.lower(),
        normalize_skill_name(skill),
        normalize_keyword(skill),
        str(normalize_keyword(skill)).lower(),
    }
    for denied in denied_skills:
        label = str(denied or "").strip()
        if not label:
            continue
        denied_forms = {
            label,
            label.lower(),
            normalize_skill_name(label),
            normalize_keyword(label),
            str(normalize_keyword(label)).lower(),
        }
        if skill_forms & denied_forms:
            return True
    return False


def _skills_to_ignore_for_match_scoring(
    combined: dict[str, tuple[float, str]],
    *,
    document_text: str,
    all_requirements: list[str],
) -> set[str]:
    """Skills typed into the editor that must not affect JD match dimensions."""
    ignored: set[str] = set()
    for skill in all_requirements:
        _, source = combined.get(skill, (0.0, "none"))
        if source in {"rejected", "unverified"} and _skill_present_in_document(skill, document_text):
            ignored.add(skill)
            parts = [part.strip() for part in re.split(r"\s+or\s+", str(skill), flags=re.I) if part.strip()]
            ignored.update(parts if len(parts) > 1 else [skill])
    return ignored


def _strip_skill_mentions(text: str, skills: set[str]) -> str:
    """Remove rejected/unverified skill tokens before responsibility/role scoring."""
    if not text or not skills:
        return text or ""
    result = text
    for skill in sorted(skills, key=len, reverse=True):
        if not skill:
            continue
        pattern = re.compile(rf"(?i)\b{re.escape(str(skill))}\b")
        result = pattern.sub("", result)
    result = re.sub(r",\s*,+", ", ", result)
    result = re.sub(r"[ \t]{2,}", " ", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _combined_credit(
    skill: str,
    evidence_entry: dict,
    confirmed_skills: dict[str, dict] | None,
    *,
    document_text: str,
    original_resume_text: str | None = None,
    denied_skills: list[str] | set[str] | None = None,
    evidence_gated: bool = False,
) -> tuple[float, str]:
    """Best truthful credit for a skill in the document being scored.

    Confirmed evidence may raise credit only when the scored document reflects the skill.
    When ``evidence_gated`` is True (optimized / recalculate path):
    - REJECTED / denied skills always receive zero credit (even if typed into the editor)
    - Unverified skills that appear only because the user typed them (not on the original
      resume and not accepted confirmed evidence) receive zero credit
    - Text must not self-authenticate
    """
    if _skill_in_denied_list(skill, denied_skills):
        return 0.0, "rejected"

    resume_credit = _resume_credit(evidence_entry)
    confirmed_entry = _lookup_confirmed_skill(confirmed_skills, skill)
    confirmed_credit = 0.0
    confirmed_status = str((confirmed_entry or {}).get("evidence_status") or "").upper()
    if confirmed_entry and confirmed_skills and _skill_present_in_document(skill, document_text):
        # LEARNING_ONLY must not count as professional requirement credit.
        if confirmed_status == "LEARNING_ONLY":
            confirmed_credit = 0.0
        else:
            confirmed_credit = float(confirmed_entry.get("credit") or 0.0)

    if skill in GENERIC_SKILLS:
        resume_credit = min(resume_credit, GENERIC_SKILL_CREDIT_CAP)
        confirmed_credit = min(confirmed_credit, GENERIC_SKILL_CREDIT_CAP)

    if evidence_gated:
        present = _skill_present_in_document(skill, document_text)
        if not present:
            return 0.0, "none"
        if confirmed_credit > 0:
            return confirmed_credit, "confirmed"
        was_on_original = _skill_present_in_document(skill, original_resume_text or "")
        if was_on_original and resume_credit > 0:
            return resume_credit, "resume"
        # Present in current editor but neither original-verified nor accepted-confirmed.
        return 0.0, "unverified"

    if confirmed_credit > resume_credit:
        return confirmed_credit, "confirmed"
    if resume_credit > 0:
        return resume_credit, "resume"
    return 0.0, "none"


def _lookup_confirmed_skill(
    confirmed_skills: dict[str, dict] | None, skill: str
) -> dict | None:
    """Resolve confirmed evidence across normalize_skill_name and normalize_keyword key forms.

    to_scoring_evidence historically keyed by normalize_skill_name ("kafka") while
    requirements use normalize_keyword display forms ("Kafka"). Match either.
    """
    if not confirmed_skills or not skill:
        return None
    if skill in confirmed_skills:
        return confirmed_skills[skill]
    from app.services.skill_analysis_service import normalize_skill_name

    skill_keys = {
        skill,
        skill.lower(),
        normalize_skill_name(skill),
        normalize_keyword(skill),
        str(normalize_keyword(skill)).lower(),
    }
    for key, entry in confirmed_skills.items():
        key_forms = {
            key,
            str(key).lower(),
            normalize_skill_name(key),
            normalize_keyword(key),
            str(normalize_keyword(key)).lower(),
        }
        if skill_keys & key_forms:
            return entry
    return None


def _status(score: int) -> str:
    if score >= 90:
        return "EXCELLENT_MATCH"
    if score >= 75:
        return "STRONG_MATCH"
    if score >= 60:
        return "PARTIAL_MATCH"
    if score >= 40:
        return "WEAK_MATCH"
    return "NOT_RECOMMENDED"


def meets_high_score_requirements(
    *, alignment: str, mandatory_credit_ratio: float, missing_count: int, threshold: int
) -> bool:
    """Defensive gate for Part 8's >=90 / >=95 checklists. Never used to raise a score —
    only to confirm a score the deterministic math already produced is truthfully earned."""
    if threshold >= 95:
        return alignment in {"EXACT", "STRONG_RELATED"} and mandatory_credit_ratio >= 0.95 and missing_count == 0
    if threshold >= 90:
        return alignment in {"EXACT", "STRONG_RELATED"} and mandatory_credit_ratio >= 0.90 and missing_count == 0
    return True


def explain_score_gap(result: dict[str, Any]) -> str:
    """Part 20's 'Why not 95%?' explanation. Empty string once score >= 95."""
    score = result.get("match_percentage") or 0
    if score >= 95:
        return ""
    reasons = []
    missing = result.get("missing_mandatory_skills") or []
    weak = result.get("weak_core_skills") or []
    confirmed_used = result.get("confirmed_skills_used") or []
    if missing:
        reasons.append(f"no evidenced experience with {', '.join(missing)}")
    if weak:
        reasons.append(f"only weak evidence for {', '.join(weak)}")
    cap_reason = result.get("score_cap_reason")
    if cap_reason and not missing and not weak:
        reasons.append(cap_reason.lower())
    requirements = result.get("requirements") or {}
    min_years = requirements.get("experience_years_required")
    if min_years and result.get("role_alignment") not in {"EXACT", "STRONG_RELATED"}:
        reasons.append(f"role alignment does not strongly match the required {min_years}+ years profile")
    if not reasons and confirmed_used:
        # Every mandatory skill is evidenced, but some rely on your confirmed answers
        # rather than pre-existing resume documentation — those score slightly below
        # the maximum resume-native credit, which is why 95%+ isn't reached yet.
        reasons.append(
            "some of that evidence comes from your confirmed answers rather than the "
            f"original resume ({', '.join(confirmed_used)}) — add these to a future resume update "
            "or provide stronger supporting detail to close the remaining gap"
        )
    if not reasons:
        reasons.append("some mandatory requirements are not yet strongly evidenced")
    return f"Your optimized resume reached {score}%. It did not reach 95% because " + "; ".join(reasons) + "."


def calculate_weighted_ats_score(
    resume_text: str,
    job,
    *,
    evidence_source_text: str | None = None,
    confirmed_skills: dict[str, dict] | None = None,
    original_resume_text: str | None = None,
    denied_skills: list[str] | set[str] | None = None,
    evidence_gated: bool = False,
) -> dict[str, Any]:
    """Score the document in ``resume_text`` against the job.

    Original scoring: pass the uploaded resume only; do not pass ``confirmed_skills``;
    leave ``evidence_gated=False``.

    Optimized/edited scoring: pass the generated/edited document with
    ``evidence_gated=True``, ``original_resume_text`` (immutable uploaded resume),
    ``confirmed_skills``, and ``denied_skills`` (REJECTED / NOT_POSSESSED).

    ``evidence_source_text`` is ignored — retained for backward-compatible call signatures.
    """
    del evidence_source_text  # score the document passed as resume_text
    scored_document = resume_text or ""
    requirements = extract_structured_requirements(job)
    required = requirements["required_core_skills"]
    preferred = requirements["preferred_skills"]
    all_requirements = list(dict.fromkeys(required + preferred))
    evidence = extract_resume_evidence(scored_document, all_requirements)

    combined = {
        skill: _combined_credit(
            skill,
            evidence[skill],
            confirmed_skills,
            document_text=scored_document,
            original_resume_text=original_resume_text,
            denied_skills=denied_skills,
            evidence_gated=evidence_gated,
        )
        for skill in all_requirements
    }
    confirmed_skills_used = [
        skill for skill, (_, source) in combined.items() if source == "confirmed"
    ]
    rejected_present_requirements = [
        skill for skill in all_requirements
        if _skill_in_denied_list(skill, denied_skills)
        and _skill_present_in_document(skill, scored_document)
    ]
    unverified_present_requirements = [
        skill for skill, (_, source) in combined.items() if source == "unverified"
    ]
    verified_present_requirements = [
        skill for skill, (_, source) in combined.items() if source == "resume"
    ]

    match_document = scored_document
    if evidence_gated:
        ignored_for_match = _skills_to_ignore_for_match_scoring(
            combined,
            document_text=scored_document,
            all_requirements=all_requirements,
        )
        if ignored_for_match:
            match_document = _strip_skill_mentions(scored_document, ignored_for_match)

    resume_role = classify_role(match_document)
    job_role = classify_role(
        str(getattr(job, "description", "") or "") + "\n" + str(getattr(job, "skills_required", "") or ""),
        str(getattr(job, "title", "") or ""),
    )
    resume_families = resume_role.get("families") or classify_role_families(scored_document) or ["Other"]
    job_family = requirements["job_role_family"] or job_role.get("family") or "Other"
    alignment_rank = {"EXACT": 5, "STRONG_RELATED": 4, "PARTIAL_TRANSFERABLE": 3, "UNKNOWN": 2, "MISMATCH": 1}
    alignment = max(
        (compare_role_families(family, job_family) for family in resume_families),
        key=lambda value: alignment_rank[value],
    )
    if alignment == "EXACT":
        role_points = 20.0
    elif alignment == "STRONG_RELATED":
        role_points = 16.0
    elif alignment == "UNKNOWN":
        role_points = 8.0
    elif alignment == "PARTIAL_TRANSFERABLE":
        role_points = 10.0
    else:
        role_points = 2.0

    grouped_skills = {skill for group in requirements.get("or_groups", []) for skill in group["skills"]}
    # Literal "AWS or Azure" phrases must not become separate zero-credit units alongside
    # the real OR group unit — one alternative can satisfy the group.
    def _is_or_phrase_label(skill: str) -> bool:
        return bool(re.search(r"\s+or\s+", str(skill or ""), re.I))

    seen_or_groups: set[tuple[str, ...]] = set()
    or_units = []
    for group in requirements.get("or_groups", []) or []:
        skills = [skill for skill in (group.get("skills") or []) if skill in combined]
        if len(skills) < 2:
            continue
        key = tuple(sorted(skills))
        if key in seen_or_groups:
            continue
        seen_or_groups.add(key)
        or_units.append({
            "label": " or ".join(skills),
            "skills": skills,
            "credit": max(combined[skill][0] for skill in skills),
        })
    mandatory_units = [
        {"label": skill, "skills": [skill], "credit": combined[skill][0]}
        for skill in required
        if skill in combined and skill not in grouped_skills and not _is_or_phrase_label(skill)
    ] + or_units
    mandatory_credits = [unit["credit"] for unit in mandatory_units]
    # Denominator is OR-aware units, not raw skill count (Jenkins|GitHub Actions = 1 unit).
    mandatory_credit_ratio = (sum(mandatory_credits) / len(mandatory_units)) if mandatory_units else 1.0
    mandatory_points = 35 * mandatory_credit_ratio
    strong_context = sum(unit["credit"] >= 0.8 for unit in mandatory_units)
    relevant_experience = _relevant_experience(match_document, job_family)
    technology_context_ratio = strong_context / len(mandatory_units) if mandatory_units else 1.0
    required_years = requirements.get("experience_years_required")
    if relevant_experience["years"] is not None and required_years:
        duration_ratio = min(1.0, relevant_experience["years"] / required_years)
        experience_ratio = 0.55 * duration_ratio + 0.45 * technology_context_ratio
    elif relevant_experience["roles"]:
        experience_ratio = max(0.55, technology_context_ratio)
    else:
        # Parsing uncertainty must not erase explicit professional technology usage.
        experience_ratio = technology_context_ratio
    experience_points = 20 * min(1.0, experience_ratio)
    preferred_points = 8 * (
        sum(combined[skill][0] for skill in preferred) / len(preferred) if preferred else 1
    )

    jd_keywords = extract_job_keywords(
        str(getattr(job, "title", "") or ""),
        str(getattr(job, "description", "") or ""),
        str(getattr(job, "skills_required", "") or ""),
    )
    responsibility_terms = list(dict.fromkeys(
        (jd_keywords.get("responsibilityKeywords") or []) + (jd_keywords.get("qualificationKeywords") or [])
    ))
    if responsibility_terms:
        responsibility_matches = sum(1 for term in responsibility_terms if _has(match_document, term))
        responsibilities_points = 7 * (responsibility_matches / len(responsibility_terms))
    else:
        responsibilities_points = 7.0

    structure = parse_resume_structure(scored_document)
    sections = structure.get("sections") or []
    has_education = any(re.search(r"education|academic", section.get("heading", ""), re.I) for section in sections)
    has_certification = any(re.search(r"certif|license", section.get("heading", ""), re.I) for section in sections)
    education_applicable = bool(requirements.get("education_requirements") or requirements.get("certification_requirements"))
    education_points = (
        2.5 * int(has_education) + 2.5 * int(has_certification)
        if education_applicable else 5.0
    )
    location_points = 5.0  # No candidate location source exists; keep this neutral and explicit.

    component_scores = {
        "role_alignment": round(role_points, 2),
        "mandatory_core_skills": round(mandatory_points, 2),
        "experience_evidence": round(experience_points, 2),
        "preferred_skills": round(preferred_points, 2),
        "responsibilities_domain": round(responsibilities_points, 2),
        "education_certifications": round(education_points, 2),
        "location_employment": round(location_points, 2),
    }
    raw_score_unrounded = sum(component_scores.values())
    raw_score = round(raw_score_unrounded)

    missing = [unit["label"] for unit in mandatory_units if unit["credit"] == 0.0]
    matched_core = [
        skill for skill in required
        if skill not in GENERIC_SKILLS and combined[skill][0] >= 0.5
    ]
    weak_core = [skill for skill in required if 0.0 < combined[skill][0] < 0.5]
    transferable = [
        skill for skill in all_requirements if skill in GENERIC_SKILLS and combined[skill][0] > 0.0
    ]
    missing_ratio = len(missing) / len(mandatory_units) if mandatory_units else 0
    score_cap = 100
    cap_reasons = []

    family_definition = ROLE_FAMILIES.get(job_family, {})
    primary = family_definition.get("core", [])
    frameworks = family_definition.get("frameworks", [])
    primary_supported = any(combined.get(skill, (0.0, "none"))[0] >= 0.5 for skill in primary)
    framework_supported = any(combined.get(skill, (0.0, "none"))[0] >= 0.5 for skill in frameworks)
    if alignment == "MISMATCH":
        score_cap = min(score_cap, 35)
        cap_reasons.append("Resume and target job belong to different role families")
    primary_evidence_cap_families = {"Java Full Stack", "Java Backend", "Python Full Stack", "Python Backend"}
    if primary and not primary_supported and job_family in primary_evidence_cap_families:
        score_cap = min(score_cap, 35)
        cap_reasons.append("Primary language or technology lacks professional evidence")
    elif frameworks and not framework_supported and job_family in {"Java Full Stack", "Java Backend", "Python Full Stack", "Python Backend"}:
        score_cap = min(score_cap, 45)
        cap_reasons.append("Required primary framework lacks sufficient evidence")
    if missing_ratio > 0.5:
        score_cap = min(score_cap, 45)
        cap_reasons.append("More than half of mandatory core skills are absent")

    # A mandatory skill whose ONLY evidence is a learning-only/training-only confirmation
    # (i.e. absent from the resume, bridged solely by low-tier confirmed evidence) caps the
    # score — that gap is real, even if other mandatory skills are strongly evidenced.
    learning_only_statuses = {"LEARNING_ONLY", "USER_CONFIRMED_TRAINING"}
    mandatory_only_learning = any(
        _lookup_confirmed_skill(confirmed_skills, skill)
        and (_lookup_confirmed_skill(confirmed_skills, skill) or {}).get("evidence_status") in learning_only_statuses
        for skill in required
    )
    if mandatory_only_learning:
        score_cap = min(score_cap, 55)
        cap_reasons.append("Only learning/coursework evidence exists for mandatory skills")

    if not meets_high_score_requirements(
        alignment=alignment, mandatory_credit_ratio=mandatory_credit_ratio,
        missing_count=len(missing), threshold=90,
    ):
        score_cap = min(score_cap, 89)
    elif not meets_high_score_requirements(
        alignment=alignment, mandatory_credit_ratio=mandatory_credit_ratio,
        missing_count=len(missing), threshold=95,
    ):
        score_cap = min(score_cap, 94)

    final_score = max(0, min(raw_score, score_cap))
    status = _status(final_score)
    if missing_ratio > 0.5 or alignment == "MISMATCH":
        status = "NOT_RECOMMENDED"
    warning = (
        "This role requires core experience that is not supported by your uploaded resume. "
        "Applying with the generated resume may misrepresent your background."
        if status == "NOT_RECOMMENDED"
        else ""
    )
    recommendation = {
        "EXCELLENT_MATCH": "Strong candidate alignment.",
        "STRONG_MATCH": "Apply after reviewing small gaps.",
        "PARTIAL_MATCH": "Significant review required.",
        "WEAK_MATCH": "Apply only when gaps are realistically transferable.",
        "NOT_RECOMMENDED": "Do not apply with the current resume.",
    }[status]
    matched = list(dict.fromkeys(matched_core + weak_core + transferable))
    suggestions = [
        f"Missing mandatory skills: {', '.join(missing)}" if missing else "No mandatory core skills are completely absent.",
        f"Role alignment: {', '.join(resume_families)} versus {job_family} ({alignment}).",
    ]
    if cap_reasons:
        suggestions.append(f"Score cap: {score_cap}% — {'; '.join(cap_reasons)}.")
    if confirmed_skills_used:
        suggestions.append(
            "Score includes user-confirmed experience not present in the uploaded resume: "
            + ", ".join(confirmed_skills_used) + "."
        )

    requirement_evidence = []
    for skill in all_requirements:
        entry = evidence[skill]
        credit, source = combined[skill]
        requirement_evidence.append({
            "skill": skill,
            "normalized_skill": entry.get("normalized_skill"),
            "requirement_type": "MANDATORY" if skill in required else "PREFERRED",
            "resume_status": entry.get("resume_status", "MISSING"),
            "evidence_status": entry.get("evidence_status"),
            "evidence_strength": entry.get("evidence_strength", "absent"),
            "match_type": entry.get("match_type", "ABSENT"),
            "classification": entry.get("classification") or (
                "NOT_FOUND" if entry.get("resume_status") == "MISSING"
                else "VERIFIED_CONTEXTUAL" if entry.get("match_type") == "CONTEXTUAL"
                else "VERIFIED_EXACT" if entry.get("match_type") in {"EXACT", "ALIAS"}
                else "POSSIBLE_EVIDENCE" if entry.get("resume_status") == "INDIRECT"
                else "NOT_FOUND"
            ),
            "matched_terms": entry.get("matched_terms") or [],
            "related_evidence": entry.get("related_evidence") or [],
            "evidence_sections": entry.get("evidence_sections", []),
            "evidence_sources": entry.get("evidence_sources") or entry.get("evidence_sections", []),
            "evidence_snippets": entry.get("evidence_snippets", []),
            "reason": entry.get("reason") or "",
            "confidence": entry.get("confidence"),
            "needs_user_confirmation": entry.get("needs_user_confirmation", entry.get("resume_status") != "VERIFIED"),
            "credit": credit,
            "credit_source": source,
        })
    missing_preferred = [skill for skill in preferred if combined[skill][0] == 0.0]
    mandatory_total = len(mandatory_units)
    mandatory_covered = mandatory_total - len(missing)
    keyword_matched = sum(1 for skill in all_requirements if combined[skill][0] > 0.0)
    component_details = {
        key: {"earned": component_scores[source], "max": maximum}
        for key, source, maximum in (
            ("role_alignment", "role_alignment", 20),
            ("mandatory_skills", "mandatory_core_skills", 35),
            ("experience_evidence", "experience_evidence", 20),
            ("preferred_skills", "preferred_skills", 8),
            ("responsibilities_domain", "responsibilities_domain", 7),
            ("education_certifications", "education_certifications", 5),
            ("location_employment", "location_employment", 5),
        )
    }
    diagnostic_warnings = []
    if alignment == "EXACT" and not missing and component_scores["mandatory_core_skills"] < 10.5:
        message = "Exact role alignment with zero missing mandatory skills produced unexpectedly low mandatory-skill credit."
        diagnostic_warnings.append(message)
        logger.warning("ATS_ANALYSIS_INVARIANT %s", message)

    result = {
        "ats_score": final_score,
        "match_percentage": final_score,
        "final_score": final_score,
        "total_score": final_score,
        "score_status": status,
        "confidence": "HIGH" if required and job_family != "Other" else "MEDIUM",
        "role_alignment": alignment,
        "resume_role_families": resume_families,
        "job_role_family": job_family,
        "resume_role_family_id": ROLE_FAMILY_IDS.get(resume_role["family"], "UNKNOWN"),
        "resume_role_confidence": resume_role.get("confidence"),
        "job_role_family_id": ROLE_FAMILY_IDS.get(job_role["family"], "UNKNOWN"),
        "job_role_confidence": job_role.get("confidence"),
        "suggested_role_family": resume_families[0] if resume_families else resume_role.get("family"),
        "role_classification": {
            "resume": {
                "family": resume_role.get("family"),
                "family_id": resume_role.get("family_id"),
                "confidence": resume_role.get("confidence"),
                "strong_signals": resume_role.get("strong_signals") or [],
                "supporting_signals": resume_role.get("supporting_signals") or [],
                "explanation": resume_role.get("explanation"),
            },
            "job": {
                "family": job_role.get("family"),
                "family_id": job_role.get("family_id"),
                "confidence": job_role.get("confidence"),
                "strong_signals": job_role.get("strong_signals") or [],
                "supporting_signals": job_role.get("supporting_signals") or [],
                "explanation": job_role.get("explanation"),
            },
            "alignment": alignment,
        },
        "component_scores": component_scores,
        "score_breakdown": component_scores,
        "component_details": component_details,
        "score_cap_applied": score_cap if score_cap < 100 else None,
        "score_cap_reason": "; ".join(cap_reasons) if cap_reasons else None,
        "matched_core_skills": matched_core,
        "matched_transferable_skills": transferable,
        "missing_mandatory_skills": missing,
        "missing_preferred_skills": missing_preferred,
        "weak_core_skills": weak_core,
        "matched_keywords": matched,
        "missing_keywords": missing,
        "unsupported_keywords": missing,
        "partially_supported_keywords": weak_core,
        "required_skills": required,
        "preferred_skills": preferred,
        "recommendation": recommendation,
        "warning_message": warning,
        "safe_to_apply": status not in {"NOT_RECOMMENDED"},
        "optimization_allowed": status not in {"NOT_RECOMMENDED"},
        "suggestions": suggestions,
        "requirements": requirements,
        "resume_evidence": evidence,
        "requirement_evidence": requirement_evidence,
        "diagnostic_warnings": diagnostic_warnings,
        "relevant_experience": relevant_experience,
        "education_category_applicable": education_applicable,
        "section_scores": {},
        "responsibilities_alignment": experience_points,
        "qualifications_alignment": education_points,
        "role_title_alignment": role_points,
        "experience_alignment": experience_points,
        "education_alignment": education_points,
        "certification_alignment": education_points,
        "formatting_warnings": [],
        "technology_warnings": [warning] if warning else [],
        "timeline_warnings": [],
        "client_domain_warnings": [],
        "contradiction_warnings": [],
        "analysis_version": ANALYSIS_VERSION,
        "keyword_analysis": requirements,
        "confirmed_skills_used": confirmed_skills_used,
        "verified_present_requirements": verified_present_requirements,
        "confirmed_present_requirements": confirmed_skills_used,
        "rejected_present_requirements": rejected_present_requirements,
        "unverified_present_requirements": unverified_present_requirements,
        "evidence_gated": bool(evidence_gated),
        "mandatory_credit_ratio": round(mandatory_credit_ratio, 4),
        "mandatory_unit_count": mandatory_total,
        "mandatory_satisfied_count": mandatory_covered,
        "mandatory_requirements": {
            "covered": mandatory_covered,
            "total": mandatory_total,
            "missing": missing,
        },
        "preferred_requirements": {
            "covered": max(0, len(preferred) - len(missing_preferred)),
            "total": len(preferred),
            "missing": missing_preferred,
        },
        "keyword_coverage": {
            "matched": keyword_matched,
            "required": len(all_requirements),
        },
        "raw_score_unrounded": round(raw_score_unrounded, 2),
        "remaining_gaps": {
            "unsupported": missing,
            "preferred_gaps": missing_preferred,
        },
    }
    result["why_not_95"] = explain_score_gap(result)
    return result


def build_score_view(score: dict) -> dict:
    """UI/API view derived from one authoritative scoring result."""
    components = score.get("component_scores") or score.get("score_breakdown") or {}
    component_details = score.get("component_details") or {}
    mandatory = score.get("mandatory_requirements") or {}
    preferred = score.get("preferred_requirements") or {}
    keyword = score.get("keyword_coverage") or {}
    remaining = score.get("remaining_gaps") or {}
    if not mandatory:
        missing_mandatory = score.get("missing_mandatory_skills") or []
        total = score.get("mandatory_unit_count")
        if total is None:
            total = len(score.get("required_skills") or [])
        mandatory = {
            "covered": max(0, int(total) - len(missing_mandatory)),
            "total": total,
            "missing": missing_mandatory,
        }
    if not preferred:
        missing_preferred = score.get("missing_preferred_skills") or []
        preferred = {
            "covered": max(0, len(score.get("preferred_skills") or []) - len(missing_preferred)),
            "total": len(score.get("preferred_skills") or []),
            "missing": missing_preferred,
        }
    if not keyword:
        keyword = {
            "matched": len(score.get("matched_keywords") or []),
            "required": len(score.get("required_skills") or []) + len(score.get("preferred_skills") or []),
        }
    if not remaining:
        remaining = {
            "unsupported": mandatory.get("missing") or score.get("missing_mandatory_skills") or [],
            "preferred_gaps": preferred.get("missing") or score.get("missing_preferred_skills") or [],
        }
    return {
        "match_percentage": score.get("match_percentage"),
        "raw_score_unrounded": score.get("raw_score_unrounded"),
        "jd_match": score.get("match_percentage"),
        "score_status": score.get("score_status"),
        "role_alignment": score.get("role_alignment"),
        "matched_keywords": score.get("matched_keywords") or [],
        "missing_keywords": score.get("missing_keywords") or [],
        "missing_mandatory_skills": mandatory.get("missing") or [],
        "missing_preferred_skills": preferred.get("missing") or [],
        "matched_core_skills": score.get("matched_core_skills") or [],
        "preferred_skills": score.get("preferred_skills") or [],
        "required_skills": score.get("required_skills") or [],
        "component_scores": component_details or components,
        "component_details": component_details,
        "formatting_warnings": score.get("formatting_warnings") or [],
        "why_not_95": score.get("why_not_95"),
        "confirmed_skills_used": score.get("confirmed_skills_used") or [],
        "verified_present_requirements": score.get("verified_present_requirements") or [],
        "confirmed_present_requirements": score.get("confirmed_present_requirements") or score.get("confirmed_skills_used") or [],
        "rejected_present_requirements": score.get("rejected_present_requirements") or [],
        "unverified_present_requirements": score.get("unverified_present_requirements") or [],
        "evidence_gated": bool(score.get("evidence_gated")),
        "keyword_coverage": keyword,
        "resume_quality": (
            component_details.get("experience_evidence", {}).get("earned")
            if isinstance(component_details.get("experience_evidence"), dict)
            else components.get("experience_evidence")
        ),
        "ats_formatting": 100 if not (score.get("formatting_warnings") or []) else 85,
        "mandatory_requirements": mandatory,
        "preferred_requirements": preferred,
        "remaining_gaps": remaining,
        "mandatory_credit_ratio": score.get("mandatory_credit_ratio"),
        "mandatory_satisfied_count": score.get("mandatory_satisfied_count"),
        "mandatory_unit_count": score.get("mandatory_unit_count"),
    }
