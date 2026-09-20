"""Truthful ATS resume generator — preserves protected fields and original structure."""

from __future__ import annotations

import re

from app.services.client_domain_validator import (
    detect_client_domain,
    scrub_foreign_domain_language,
)
from app.services.keyword_utils import contains_keyword, find_keywords_in_text
from app.services.resume_structure_parser import parse_resume_structure
from app.services.skill_analysis_service import USAGE_CONTEXT_VERBS
from app.services.technology_timeline_validator import filter_technologies_for_period


ACTION_VERBS = [
    "Designed", "Developed", "Implemented", "Integrated", "Optimized",
    "Automated", "Containerized", "Deployed", "Configured", "Migrated",
    "Enhanced", "Refactored", "Troubleshot", "Collaborated", "Supported", "Monitored",
]

FAKE_METRIC_PATTERN = re.compile(
    # Percentages intentionally omit a trailing \b after "%" — "%" is non-word, so
    # "45%." / "45%" at EOS would otherwise miss the invented-metric check.
    r"(?i)(\b\d{1,3}%|\b\d+\s*(million|billion|k)\b|\b\d{2,}\+?\s*(users|customers|transactions)\b|"
    r"\$\d|\b99\.9%|\breduced by\s+\d|\bincreased by\s+\d)"
)


def _years_of_experience_phrase(resume_text: str) -> str:
    match = re.search(r"(?i)(\d+\+?\s*\+?\s*years?(?:\s+of)?\s+experience)", resume_text or "")
    return match.group(1) if match else ""


def _rewrite_summary(original_summary: str, job_title: str, keywords: list[str], resume_text: str) -> str:
    years = _years_of_experience_phrase(original_summary) or _years_of_experience_phrase(resume_text)
    role = (job_title or "Software Engineer").strip()
    # Keep only keywords already supported
    chosen = [k for k in keywords if contains_keyword(resume_text, k)][:5]
    if not chosen:
        chosen = find_keywords_in_text(resume_text)[:5]
    keyword_phrase = ", ".join(chosen) if chosen else "core application technologies"

    if original_summary.strip():
        # Preserve intent: rebuild 3-5 lines using original cues + supported keywords
        base = re.sub(r"\s+", " ", original_summary).strip()
        # Avoid inventing seniority
        experience_bit = f" with {years}" if years else " with experience"
        line1 = f"{role}{experience_bit} designing and developing scalable applications using {keyword_phrase}."
        line2 = (
            "Skilled in building reliable services, collaborating in Agile teams, "
            "and delivering maintainable solutions aligned to business needs."
        )
        # Keep a shortened truthful fragment from original if short enough
        original_snippet = base[:180].rstrip(" .,")
        lines = [line1, line2]
        if original_snippet and not any(FAKE_METRIC_PATTERN.search(x) for x in (original_snippet,)):
            lines.append(f"Background includes: {original_snippet}.")
        return "\n".join(lines[:5])

    experience_bit = f" with {years}" if years else " with experience"
    return (
        f"{role}{experience_bit} designing and developing scalable web applications using {keyword_phrase}. "
        f"Skilled in building API-driven workflows, optimizing data operations, and collaborating in Agile teams "
        f"to deliver reliable enterprise applications."
    )


def _categorize_skills_dynamically(ordered: list[str], extras: list[str]) -> str:
    """Group skills by lightweight keyword families — no role-hardcoded templates."""
    families: list[tuple[str, tuple[str, ...]]] = [
        ("Languages", ("python", "java", "javascript", "typescript", "c#", "c++", "go", "golang", "ruby", "php", "scala", "kotlin", "swift", "r ", "sql")),
        ("Backend", ("spring", "django", "flask", "fastapi", "node", ".net", "express", "hibernate", "microservices", "rest", "graphql", "api")),
        ("Frontend", ("react", "angular", "vue", "html", "css", "next.js", "redux", "ui")),
        ("Data", ("spark", "hadoop", "kafka", "airflow", "dbt", "snowflake", "bigquery", "redshift", "etl", "pandas", "numpy")),
        ("Databases", ("postgres", "mysql", "oracle", "mongo", "redis", "dynamodb", "sql server", "cassandra", "elasticsearch")),
        ("Cloud", ("aws", "azure", "gcp", "cloud")),
        ("DevOps / SRE", ("docker", "kubernetes", "terraform", "ansible", "jenkins", "github actions", "gitlab ci", "ci/cd", "helm", "prometheus", "grafana")),
        ("Testing / QA", ("selenium", "cypress", "junit", "pytest", "testng", "cucumber", "playwright", "postman", "jmeter")),
        ("Security", ("oauth", "saml", "vault", "sso", "owasp", "iam", "security")),
        ("Methodologies", ("agile", "scrum", "kanban", "devops", "sre")),
    ]
    used: set[str] = set()
    lines: list[str] = []
    lower_map = {k: k for k in ordered}

    def _matches(skill: str, needles: tuple[str, ...]) -> bool:
        s = skill.lower()
        return any(n.strip() in s for n in needles)

    for label, needles in families:
        bucket = [k for k in ordered if k not in used and _matches(k, needles)]
        if bucket:
            lines.append(f"{label}: " + ", ".join(bucket))
            used.update(bucket)
    leftover = [k for k in ordered if k not in used] + [e for e in extras if e not in ordered and e not in used]
    if leftover:
        lines.append("Additional: " + ", ".join(leftover[:24]))
    return "\n".join(lines) if lines else ", ".join((ordered + extras)[:40])


def _reorder_skills(original_skills: str, priority_keywords: list[str], resume_text: str) -> str:
    existing = find_keywords_in_text(original_skills or resume_text)
    # Preserve unknowns from original comma/line lists
    extras = []
    for part in re.split(r"[,;\n|/]+", original_skills or ""):
        part = part.strip()
        if part and part not in existing and len(part) < 40 and not re.search(r"(?i)^(backend|frontend|database|cloud|devops|languages|testing|tools)\s*:?\s*$", part):
            extras.append(part)

    ordered = []
    for keyword in priority_keywords:
        # Priority keywords are already limited to resume-verified or accepted confirmed evidence.
        if keyword and keyword not in ordered:
            ordered.append(keyword)
    for keyword in existing:
        if keyword not in ordered:
            ordered.append(keyword)

    # Prefer dynamic categorical layout whenever the original used categories OR many skills.
    if re.search(r"(?i)(backend|frontend|database|cloud|devops|languages|skills)\s*:", original_skills or "") or len(ordered) >= 8:
        return _categorize_skills_dynamically(ordered, extras)

    combined = ordered + [e for e in extras if e not in ordered]
    return ", ".join(combined[:40])


_WEAK_BULLET_LEAD_INS = [
    (re.compile(r"^(worked on|worked with|working on)\s+", re.I), "Developed "),
    (re.compile(r"^helped (?:to |with )?", re.I), "Contributed to "),
    (re.compile(r"^(was responsible for|responsible for)\s+", re.I), "Managed "),
    (re.compile(r"^(was involved in|involved in)\s+", re.I), "Contributed to "),
    (re.compile(r"^assisted (?:with|in) ", re.I), "Supported "),
    (re.compile(r"^(was part of|participated in)\s+", re.I), "Collaborated on "),
]

# Qualitative (non-numeric) closers — never invented metrics, just professional framing.
_IMPACT_CLOSERS = [
    "to support maintainable delivery",
    "to improve system reliability",
    "to streamline development workflows",
    "to enhance overall code quality",
    "to strengthen team delivery speed",
]


def _apply_weak_lead_in(text: str) -> str:
    for pattern, replacement in _WEAK_BULLET_LEAD_INS:
        if pattern.match(text):
            return pattern.sub(replacement, text, count=1)
    return text


def _strengthen_bullet(bullet: str, allowed_keywords: list[str], domain: str) -> str:
    text = bullet.strip()
    if not text:
        return text
    # Strip invented metrics if somehow present in source (keep original numbers only if already there)
    # Do not add new metrics.
    text = scrub_foreign_domain_language(text, domain) if domain else text

    # Replace weak/passive lead-ins with a stronger action verb.
    text = _apply_weak_lead_in(text)
    if text and text[0].islower():
        text = text[0].upper() + text[1:]

        # Naturally weave up to two missing allowed keywords already implied by bullet context.
    lower = text.lower()
    woven = 0
    newly_woven = []
    for keyword in allowed_keywords:
        if woven >= 2:
            break
        if contains_keyword(text, keyword):
            continue
        # Contextual weave: only when the bullet already discusses related work themes.
        related = bool(
            re.search(
                r"api|service|backend|frontend|ui|deploy|ci|cd|container|cloud|pipeline|"
                r"docker|database|sql|data|query|test|automat|monitor|integrat|configur|"
                r"build|develop|implement|architect|platform|infrastruct",
                lower,
            )
        )
        if related and keyword.lower() not in lower:
            newly_woven.append(keyword)
            woven += 1
    if newly_woven:
        text = text.rstrip(".") + f" using {', '.join(newly_woven)}."

    # Qualitative impact if sentence is very short and has no impact clause. Deterministic
    # rotation (not randomness) keeps generation reproducible for the same input.
    if len(text.split()) < 12 and not re.search(r"improv|support|streamlin|enhanc|reliab|maintain", text, re.I):
        closer = _IMPACT_CLOSERS[len(text) % len(_IMPACT_CLOSERS)]
        text = text.rstrip(".") + f" {closer}."
    return text


def _dedupe_bullets(bullets: list[str]) -> list[str]:
    seen = set()
    result = []
    for bullet in bullets:
        key = re.sub(r"\s+", " ", bullet.strip().lower())
        if key and key not in seen:
            seen.add(key)
            result.append(bullet)
    return result


def _prioritize_bullets(bullets: list[str], priority_keywords: list[str]) -> list[str]:
    """Stable-sort bullets so ones matching more JD-relevant keywords come first."""

    def relevance(bullet: str) -> int:
        return sum(1 for keyword in priority_keywords if contains_keyword(bullet, keyword))

    return sorted(bullets, key=lambda b: -relevance(b))


def _rewrite_experience_section(content: str, employments: list[dict], keywords: list[str], resume_text: str) -> str:
    if not employments:
        # Fallback: strengthen bullet-like lines in place
        lines = []
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith(("-", "•", "*")):
                bullet = re.sub(r"^[-•*]+\s*", "", stripped)
                new_bullet = _strengthen_bullet(bullet, keywords, "")
                lines.append(f"- {new_bullet}")
            else:
                lines.append(line)
        return "\n".join(lines)

    blocks = []
    for emp in employments:
        header_parts = [emp.get("company") or ""]
        if emp.get("title"):
            header_parts.append(emp["title"])
        if emp.get("dates"):
            header_parts.append(emp["dates"])
        header = " | ".join([p for p in header_parts if p])
        lines = [header]
        if emp.get("client"):
            lines.append(f"Client: {emp['client']}")

        domain = detect_client_domain(emp.get("client", ""), " ".join(emp.get("bullets") or []), emp.get("company", ""))
        allowed, _blocked = filter_technologies_for_period(
            keywords,
            emp.get("start_year"),
            emp.get("end_year"),
        )
        # Prefer keywords already in this employment text
        local_text = " ".join(emp.get("bullets") or []) + " " + " ".join(emp.get("raw_lines") or [])
        local_allowed = [k for k in allowed if contains_keyword(local_text, k) or contains_keyword(resume_text, k)]

        bullets = _prioritize_bullets(_dedupe_bullets(emp.get("bullets") or []), local_allowed)
        rewritten = []
        for bullet in bullets[:6]:
            rewritten.append(_strengthen_bullet(bullet, local_allowed[:8], domain))
        rewritten = _dedupe_bullets(rewritten)
        if not rewritten and local_text.strip():
            # Do not invent duties; leave raw non-bullet content
            for raw in emp.get("raw_lines") or []:
                if raw != header and not raw.lower().startswith("client:"):
                    lines.append(raw)
        for bullet in rewritten:
            lines.append(f"- {bullet}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _rewrite_projects_section(content: str, projects: list[dict], keywords: list[str], resume_text: str) -> str:
    if not projects:
        return content
    blocks = []
    for project in projects:
        lines = [project.get("name") or "Project"]
        if project.get("client"):
            lines.append(f"Client: {project['client']}")
        if project.get("dates"):
            lines.append(project["dates"])
        domain = detect_client_domain(project.get("client", ""), project.get("description", ""), project.get("name", ""))
        allowed, _ = filter_technologies_for_period(keywords, project.get("start_year"), project.get("end_year"))
        allowed = [k for k in allowed if contains_keyword(resume_text, k)][:4]
        description = project.get("description") or ""
        if description:
            description = scrub_foreign_domain_language(description, domain)
            if allowed and not any(contains_keyword(description, k) for k in allowed):
                description = description.rstrip(".") + f" Technologies used include {', '.join(allowed[:3])}."
            lines.append(description)
        project_bullets = _prioritize_bullets(_dedupe_bullets(project.get("bullets") or []), allowed)
        rewritten_bullets = _dedupe_bullets(
            [_strengthen_bullet(bullet, allowed, domain) for bullet in project_bullets[:4]]
        )
        for bullet in rewritten_bullets:
            lines.append(f"- {bullet}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


_PROFESSIONAL_EVIDENCE_TYPES = {"Current employment", "Previous employment"}
_PROJECT_EVIDENCE_TYPES = {"Professional project", "Personal project", "Academic project"}

_EVIDENCE_NOISE_PATTERNS = [
    re.compile(r"(?i)^\s*client:\s*status\s+meetings\.?\s*$"),
    re.compile(r"(?i)^\s*used\s+in\s+project\.?\s*$"),
    re.compile(r"(?i)^\s*confirm(ed)?\s+(accurate|yes|true)\.?\s*$"),
    re.compile(r"(?i)^\s*n/?a\.?\s*$"),
    re.compile(r"(?i)^\s*see\s+above\.?\s*$"),
    re.compile(r"(?i)^\s*as\s+discussed\.?\s*$"),
]


def _is_evidence_noise(text: str) -> bool:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return True
    if len(cleaned) < 12 and not re.search(r"[A-Za-z]{3,}", cleaned):
        return True
    return any(pattern.match(cleaned) for pattern in _EVIDENCE_NOISE_PATTERNS)


# A short subject (the technology name — possibly multi-word, e.g. "Apache
# JMeter") followed by "is/are/was/were a/an/the/mainly used/..." or
# "helps"/"provides"/"refers to" is a TEXTBOOK DEFINITION of the technology,
# not a description of what the candidate did with it. Domain-independent —
# matches regardless of which specific tool/skill is named.
_DEFINITION_STYLE_PATTERN = re.compile(
    r"(?i)^(?:the\s+)?[\w][\w./+#-]*(?:\s+[\w][\w./+#-]*){0,3}\s+"
    r"(?:is|are|was|were)\s+(?:a|an|the|mainly|primarily|generally|typically|"
    r"often|commonly|basically|essentially|simply|used\b)"
    r"|^(?:the\s+)?[\w][\w./+#-]*(?:\s+[\w][\w./+#-]*){0,3}\s+"
    r"(?:helps?|provides?|refers?\s+to|stands?\s+for)\b"
)


def _looks_like_definition(text: str) -> bool:
    return bool(_DEFINITION_STYLE_PATTERN.match((text or "").strip()))


def _has_usage_context(text: str) -> bool:
    """Same action-verb vocabulary the evidence-strength scorer itself uses
    (skill_analysis_service.USAGE_CONTEXT_VERBS) — domain-independent and
    already proven to recognize real professional/project usage across
    roles. A sentence without one of these verbs describes what a
    technology IS, not what the candidate DID with it."""
    return bool(USAGE_CONTEXT_VERBS.search(text or ""))


def _resume_ready_evidence_sentence(skill: str, description: str) -> str:
    """Turn questionnaire free-text into one professional resume bullet —
    but ONLY when that free text is genuine PROFESSIONAL_USAGE_EVIDENCE
    (describes what the candidate did), never SKILL_KNOWLEDGE (a bare
    acknowledgment, or a textbook definition of the technology echoed back
    from the questionnaire). Returns "" when there's nothing truthful and
    substantive to turn into a bullet, so the caller places the skill in
    Technical Skills only instead of manufacturing a bullet — knowing a
    skill is not the same as proving professional usage of it."""
    skill = (skill or "").strip()
    text = re.sub(r"\s+", " ", (description or "").strip())
    if _is_evidence_noise(text) or not text:
        return ""
    # Checked on the RAW answer, before any presentation cleanup below strips
    # the leading verb — stripping is cosmetic, not a reason to lose the
    # usage-evidence signal it carries.
    if _looks_like_definition(text) or not _has_usage_context(text):
        return ""

    # Presentation cleanup only, below this point — a leading "I "/"We " is
    # just an awkward pronoun; the verb itself is kept, both for grammar and
    # because it's exactly the usage-context signal just confirmed above.
    text = re.sub(
        r"(?i)^(it\s+helps\s+us\s+|this\s+tool\s+is\s+used\s+to\s+|used\s+to\s+)",
        "",
        text,
    )
    text = re.sub(r"(?i)^(i|we)\s+", "", text)
    text = re.sub(r"(?i)^performed\s+", "Performed ", text)
    text = text.strip(" -")
    if not text:
        return ""

    if skill and skill.lower() not in text.lower():
        if re.match(r"(?i)^(performed|developed|implemented|automated|built|led|designed)\b", text):
            text = f"{text[0].upper() + text[1:]} using {skill}."
        else:
            text = f"Applied {skill} to {text[0].lower() + text[1:]}"
    elif re.match(r"(?i)^performed\s+to\s+", text):
        text = re.sub(r"(?i)^performed\s+to\s+", "Performed ", text)

    # Keep a single concise sentence.
    sentence = re.split(r"(?<=[.!?])\s+", text)[0].strip()
    if not sentence or _looks_like_definition(sentence) or not _has_usage_context(sentence):
        return ""
    return _confirmed_bullet(sentence)


def _confirmed_bullet(description: str) -> str:
    text = re.sub(r"\s+", " ", (description or "").strip())
    if not text or _is_evidence_noise(text):
        return ""
    text = text[0].upper() + text[1:]
    if not text.endswith((".", "!", "?")):
        text += "."
    return text


def _confirmed_evidence_buckets(confirmed_evidence: dict[str, dict] | None) -> dict[str, list[dict]]:
    buckets = {"professional": [], "project": [], "certification": [], "training": [], "learning": []}
    seen: set[str] = set()
    for skill, item in (confirmed_evidence or {}).items():
        status = str((item or {}).get("evidence_status") or "").upper()
        if status in {"REJECTED", "INCOMPLETE", "NOT_POSSESSED", "OBSOLETE"}:
            continue
        skill_key = str((item or {}).get("skill") or skill or "").strip().lower()
        if not skill_key or skill_key in seen:
            continue
        seen.add(skill_key)
        entry = {"skill": (item or {}).get("skill") or skill, **(item or {})}
        evidence_type = (entry.get("evidence_type") or "").strip()
        if evidence_type == "Learning only" or entry.get("evidence_status") == "LEARNING_ONLY":
            buckets["learning"].append(entry)
        elif evidence_type in _PROFESSIONAL_EVIDENCE_TYPES:
            buckets["professional"].append(entry)
        elif evidence_type in _PROJECT_EVIDENCE_TYPES:
            buckets["project"].append(entry)
        elif evidence_type == "Certification":
            buckets["certification"].append(entry)
        elif evidence_type == "Training/coursework":
            buckets["training"].append(entry)
        else:
            buckets["project"].append(entry)
    return buckets


def _target_employment_index(employments: list[dict], employer_name: str | None) -> int | None:
    """Resolve a confirmed employment item to the matching parsed employer.
    Falls back to the most recent role only when no employer_name was given at all
    (the "unspecified existing position" case). A given employer_name that does NOT
    match any parsed employment is a deliberately new/unrecognized employer — it must
    never be silently merged into an unrelated existing role, so this returns None."""
    if not employments:
        return None
    if not employer_name:
        return 0
    target = employer_name.strip().lower()
    for index, emp in enumerate(employments):
        if (emp.get("company") or "").strip().lower() == target:
            return index
    return None


def _employment_bullet(item: dict) -> str:
    skill = (item.get("skill") or "").strip()
    primary = (
        item.get("usage_description")
        or item.get("work_completed")
        or item.get("impact_description")
        or ""
    )
    sentence = _resume_ready_evidence_sentence(skill, str(primary))
    modules = str(item.get("skill_modules") or "").strip()
    if sentence and modules and modules.lower() not in sentence.lower() and not _is_evidence_noise(modules):
        sentence = sentence.rstrip(".") + f", covering {modules}."
    return sentence


def _new_employment_block(item: dict) -> str:
    """A user-confirmed employer/role that isn't already on the resume — shown with its
    own truthful header (never merged into an existing, unrelated employer's block)."""
    dates = " - ".join(filter(None, [item.get("start_date"), "Present" if item.get("currently_using") else item.get("end_date")]))
    header = " | ".join(p for p in [item.get("employer_name") or "", item.get("role_title") or "", dates.strip(" -")] if p)
    bullet = _employment_bullet(item)
    if not bullet:
        return ""
    lines = [header] if header else []
    lines.append(f"- {bullet}")
    return "\n".join(lines)


def _project_block(item: dict) -> str:
    skill = (item.get("skill") or "").strip()
    name = item.get("project_name") or f"{skill} Project"
    if _is_evidence_noise(str(name)):
        name = f"{skill} Project" if skill else "Project"
    lines = [name]
    if item.get("role_title") and not _is_evidence_noise(str(item.get("role_title"))):
        lines.append(item["role_title"])
    dates = " - ".join(filter(None, [item.get("start_date"), "Present" if item.get("currently_using") else item.get("end_date")]))
    if dates.strip(" -"):
        lines.append(dates)
    body = item.get("work_completed") or item.get("usage_description") or ""
    sentence = _resume_ready_evidence_sentence(skill, str(body))
    modules = str(item.get("skill_modules") or "").strip()
    if sentence and modules and modules.lower() not in sentence.lower() and not _is_evidence_noise(modules):
        sentence = sentence.rstrip(".") + f", covering {modules}."
    if sentence:
        lines.append(f"- {sentence}")
    return "\n".join(lines)


def _certification_line(item: dict) -> str:
    name = item.get("certification_name") or item["skill"]
    issuer = item.get("provider_name")
    earned = item.get("start_date")
    detail = " — ".join(filter(None, [issuer, f"earned {earned}" if earned else None]))
    return f"- {name}" + (f" ({detail})" if detail else "")


def _training_line(item: dict) -> str:
    name = item.get("project_name") or f"{item['skill']} training"
    provider = item.get("provider_name")
    status = "Completed" if (item.get("end_date") and not item.get("currently_using")) else "In progress"
    detail = " — ".join(filter(None, [provider, status]))
    return f"- {name}" + (f" ({detail})" if detail else "")


def _merge_section(output_sections: list[dict], heading_pattern: str, new_heading: str, block_text: str) -> None:
    if not block_text.strip():
        return
    for section in output_sections:
        if re.search(heading_pattern, section.get("heading") or "", re.I):
            section["content"] = (section["content"].rstrip() + "\n\n" + block_text).strip()
            return
    output_sections.append({"heading": new_heading, "content": block_text.strip()})


def generate_optimized_resume(
    resume_text: str, job, injection_plan: dict, confirmed_evidence: dict[str, dict] | None = None
) -> dict:
    structure = parse_resume_structure(resume_text)
    buckets = _confirmed_evidence_buckets(confirmed_evidence)

    # Truthfully add confirmed employment evidence to the SPECIFIC employer it names —
    # never a fabricated one — BEFORE section rewriting, so it inherits that employer's
    # real company/title/dates header.
    unplaced_professional = []
    if buckets["professional"] and structure.get("employments"):
        by_index: dict[int, list[str]] = {}
        for item in buckets["professional"]:
            index = _target_employment_index(structure["employments"], item.get("employer_name"))
            if index is None:
                unplaced_professional.append(item)
            else:
                # No genuine PROFESSIONAL_USAGE_EVIDENCE (only SKILL_KNOWLEDGE,
                # or a definitional answer) -> no bullet manufactured; the
                # skill still reaches Technical Skills via confirmed_names below.
                bullet = _employment_bullet(item)
                if bullet:
                    by_index.setdefault(index, []).append(bullet)
        for index, bullets in by_index.items():
            structure["employments"][index]["bullets"] = list(
                structure["employments"][index].get("bullets") or []
            ) + bullets
    else:
        unplaced_professional = list(buckets["professional"])

    placement = injection_plan.get("placement") or {}
    summary_kw = list(placement.get("summary") or injection_plan.get("supportedKeywords", [])[:5])
    skills_kw = list(placement.get("skills") or injection_plan.get("supportedKeywords", [])[:12])
    experience_kw = list(placement.get("experience") or injection_plan.get("supportedKeywords", [])[:8])
    project_kw = list(placement.get("projects") or injection_plan.get("supportedKeywords", [])[:6])
    # Confirmed evidence skills must appear in skills/summary placement when JD-relevant.
    from app.services.keyword_utils import normalize_keyword as _nk

    confirmed_names = []
    for skill, item in (confirmed_evidence or {}).items():
        display = _nk(item.get("skill") or skill)
        if display:
            confirmed_names.append(display)
    if confirmed_names:
        skills_kw = list(dict.fromkeys(confirmed_names + skills_kw))
        summary_kw = list(dict.fromkeys(confirmed_names[:5] + summary_kw))
        experience_kw = list(dict.fromkeys(confirmed_names + experience_kw))
    job_title = getattr(job, "title", "") or ""

    output_sections = []
    for section in structure.get("sections") or []:
        heading = section.get("heading") or "Section"
        content = section.get("content") or ""
        heading_l = heading.lower()

        if re.search(r"summary|profile|objective", heading_l):
            new_content = _rewrite_summary(content, job_title, summary_kw, resume_text)
        elif re.search(r"skill|technolog|competenc", heading_l):
            new_content = _reorder_skills(content, skills_kw, resume_text)
        elif re.search(r"experience|employment", heading_l):
            new_content = _rewrite_experience_section(
                content,
                structure.get("employments") or [],
                experience_kw,
                resume_text,
            )
        elif re.search(r"project", heading_l):
            new_content = _rewrite_projects_section(
                content,
                structure.get("projects") or [],
                project_kw,
                resume_text,
            )
        else:
            # Education / certifications / other — preserve exactly
            new_content = content

        output_sections.append({"heading": heading, "content": new_content})

    # If parser found no sections, still optimize from structured parse instead of copying.
    if not output_sections:
        summary = _rewrite_summary("", job_title, summary_kw, resume_text)
        skills = _reorder_skills("", skills_kw, resume_text)
        experience = _rewrite_experience_section(
            "",
            structure.get("employments") or [],
            experience_kw,
            resume_text,
        )
        projects = _rewrite_projects_section(
            "",
            structure.get("projects") or [],
            project_kw,
            resume_text,
        )
        output_sections = [
            {"heading": "Professional Summary", "content": summary},
            {
                "heading": "Technical Skills",
                "content": skills or ", ".join(find_keywords_in_text(resume_text)[:20]),
            },
        ]
        if experience.strip():
            output_sections.append({"heading": "Professional Experience", "content": experience})
        elif (resume_text or "").strip():
            # Keep original body under experience when employment parse is empty,
            # but still prepend optimized summary/skills so content always changes.
            output_sections.append({"heading": "Professional Experience", "content": (resume_text or "").strip()})
        if projects.strip():
            output_sections.append({"heading": "Projects", "content": projects})

    # Professional evidence that names an employer not already on the resume (or has no
    # employments to attach to at all) — never merged into an unrelated existing role,
    # surfaced as its own clearly-labeled, truthfully-headered entry instead.
    if unplaced_professional:
        block = "\n\n".join(_new_employment_block(item) for item in unplaced_professional)
        _merge_section(output_sections, r"experience|employment", "Recent Experience Notes", block)

    if buckets["project"]:
        block = "\n\n".join(_project_block(item) for item in buckets["project"])
        _merge_section(output_sections, r"project", "Projects", block)

    if buckets["certification"]:
        block = "\n".join(_certification_line(item) for item in buckets["certification"])
        _merge_section(output_sections, r"certif|license", "Certifications", block)

    if buckets["training"]:
        block = "\n".join(_training_line(item) for item in buckets["training"])
        _merge_section(output_sections, r"training|coursework", "Training", block)

    if buckets["learning"]:
        block = "\n".join(f"- Currently learning {item['skill']}." for item in buckets["learning"])
        _merge_section(output_sections, r"currently learning", "Currently Learning", block)

    parts = [structure.get("contact") or ""]
    for section in output_sections:
        parts.append(section["heading"])
        parts.append(section["content"])
    plain = "\n\n".join([p.strip() for p in parts if p and p.strip()])

    # Guarantee a meaningful content change when JD-aligned optimization is possible.
    original_norm = re.sub(r"\s+", " ", (resume_text or "").strip().lower())
    plain_norm = re.sub(r"\s+", " ", plain.lower())
    if plain_norm == original_norm and (job_title or summary_kw or skills_kw or confirmed_evidence):
        tailored = _rewrite_summary(
            "",
            job_title,
            summary_kw or skills_kw or list((confirmed_evidence or {}).keys()),
            resume_text,
        )
        plain = f"Professional Summary\n{tailored}\n\n{plain}".strip()

    # Absolute ban on newly introduced fake metrics — but user-supplied confirmed evidence
    # is truthful, not invented, so it's part of the baseline this check is compared against.
    evidence_text_fields = ("usage_description", "work_completed", "impact_description", "related_technologies", "supporting_context", "skill_modules")
    evidence_baseline = "\n".join(
        [resume_text or ""]
        + [
            str(item.get(field) or "")
            for item in (confirmed_evidence or {}).values()
            for field in evidence_text_fields
        ]
    )
    if FAKE_METRIC_PATTERN.search(plain) and not FAKE_METRIC_PATTERN.search(evidence_baseline):
        plain = FAKE_METRIC_PATTERN.sub("", plain)
        plain = re.sub(r"\s{2,}", " ", plain)

    return {
        "plain_text": plain,
        "structure": structure,
        "sections": output_sections,
        "confirmed_skills_applied": list((confirmed_evidence or {}).keys()),
    }
