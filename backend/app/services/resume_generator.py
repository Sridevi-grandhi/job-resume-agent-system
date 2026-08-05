"""Truthful ATS resume generator — preserves protected fields and original structure."""

from __future__ import annotations

import re

from app.services.client_domain_validator import (
    detect_client_domain,
    scrub_foreign_domain_language,
)
from app.services.keyword_utils import contains_keyword, find_keywords_in_text
from app.services.resume_structure_parser import parse_resume_structure
from app.services.technology_timeline_validator import filter_technologies_for_period


ACTION_VERBS = [
    "Designed", "Developed", "Implemented", "Integrated", "Optimized",
    "Automated", "Containerized", "Deployed", "Configured", "Migrated",
    "Enhanced", "Refactored", "Troubleshot", "Collaborated", "Supported", "Monitored",
]

FAKE_METRIC_PATTERN = re.compile(
    r"(?i)(\b\d{1,3}%\b|\b\d+\s*(million|billion|k)\b|\b\d{2,}\+?\s*(users|customers|transactions)\b|"
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


def _reorder_skills(original_skills: str, priority_keywords: list[str], resume_text: str) -> str:
    existing = find_keywords_in_text(original_skills or resume_text)
    # Preserve unknowns from original comma/line lists
    extras = []
    for part in re.split(r"[,;\n|/]+", original_skills or ""):
        part = part.strip()
        if part and part not in existing and len(part) < 40:
            extras.append(part)

    ordered = []
    for keyword in priority_keywords:
        if contains_keyword(resume_text, keyword) and keyword not in ordered:
            ordered.append(keyword)
    for keyword in existing:
        if keyword not in ordered:
            ordered.append(keyword)

    # Group lightly if original used category labels
    if re.search(r"(?i)(backend|frontend|database|cloud|devops)\s*:", original_skills or ""):
        backend = [k for k in ordered if k in {
            "Java", "Spring", "Spring Boot", "Spring MVC", "Spring Security", "Hibernate",
            "Microservices", "REST APIs", "Kafka", "RabbitMQ", "Node.js", "Python",
            "Django", "Flask", "FastAPI",
        }]
        frontend = [k for k in ordered if k in {
            "React", "Angular", "AngularJS", "Vue", "HTML", "CSS", "JavaScript", "TypeScript", "Next.js",
        }]
        database = [k for k in ordered if k in {
            "PostgreSQL", "MySQL", "Oracle", "MongoDB", "Redis", "SQL", "SQL Server",
        }]
        cloud = [k for k in ordered if k in {
            "AWS", "Azure", "GCP", "Docker", "Kubernetes", "Jenkins", "GitHub Actions",
            "Terraform", "CI/CD", "Git",
        }]
        testing = [k for k in ordered if k in {
            "JUnit", "PyTest", "Selenium", "Splunk", "ELK", "Dynatrace", "AppDynamics",
        }]
        methods = [k for k in ordered if k in {"Agile", "Scrum"}]
        lines = []
        if backend:
            lines.append("Backend: " + ", ".join(backend))
        if frontend:
            lines.append("Frontend: " + ", ".join(frontend))
        if database:
            lines.append("Database: " + ", ".join(database))
        if cloud:
            lines.append("Cloud/DevOps: " + ", ".join(cloud))
        if testing:
            lines.append("Testing/Monitoring: " + ", ".join(testing))
        if methods:
            lines.append("Methodologies: " + ", ".join(methods))
        leftover = [k for k in ordered if k not in backend + frontend + database + cloud + testing + methods]
        if leftover or extras:
            lines.append("Additional: " + ", ".join(leftover + extras[:8]))
        return "\n".join(lines)

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
        # Only add if bullet already discusses related work (apis, services, deploy, etc.)
        related = False
        if keyword in {"REST APIs", "Spring Boot", "Microservices", "Java"} and re.search(r"api|service|backend|java|spring", lower):
            related = True
        if keyword in {"React", "JavaScript", "TypeScript", "HTML", "CSS"} and re.search(r"front|ui|react|javascript|component", lower):
            related = True
        if keyword in {"Docker", "Kubernetes", "Jenkins", "AWS", "CI/CD"} and re.search(r"deploy|ci|cd|container|cloud|pipeline|docker", lower):
            related = True
        if keyword in {"PostgreSQL", "MySQL", "MongoDB", "SQL", "Redis"} and re.search(r"database|sql|data|query", lower):
            related = True
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


def _confirmed_bullet(description: str) -> str:
    text = re.sub(r"\s+", " ", (description or "").strip())
    if not text:
        return text
    text = text[0].upper() + text[1:]
    if not text.endswith((".", "!", "?")):
        text += "."
    return text


def _confirmed_evidence_buckets(confirmed_evidence: dict[str, dict] | None) -> dict[str, list[dict]]:
    buckets = {"professional": [], "project": [], "certification": [], "training": [], "learning": []}
    for skill, item in (confirmed_evidence or {}).items():
        entry = {"skill": skill, **item}
        evidence_type = (item.get("evidence_type") or "").strip()
        if evidence_type == "Learning only" or item.get("evidence_status") == "LEARNING_ONLY":
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
    pieces = [item.get("usage_description") or ""]
    modules = item.get("skill_modules")
    if modules and modules.lower() not in pieces[0].lower():
        pieces.append(f"Covering {modules}.")
    tech = item.get("related_technologies")
    if tech and tech.lower() not in pieces[0].lower():
        pieces.append(f"Related tools: {tech}.")
    context = item.get("supporting_context")
    if context:
        pieces.append(context if context.strip().endswith((".", "!", "?")) else context.strip() + ".")
    impact = item.get("impact_description")
    if impact:
        pieces.append(impact if impact.strip().endswith((".", "!", "?")) else impact.strip() + ".")
    return _confirmed_bullet(" ".join(p for p in pieces if p).strip())


def _new_employment_block(item: dict) -> str:
    """A user-confirmed employer/role that isn't already on the resume — shown with its
    own truthful header (never merged into an existing, unrelated employer's block)."""
    dates = " - ".join(filter(None, [item.get("start_date"), "Present" if item.get("currently_using") else item.get("end_date")]))
    header = " | ".join(p for p in [item.get("employer_name") or "", item.get("role_title") or "", dates.strip(" -")] if p)
    lines = [header] if header else []
    lines.append(f"- {_employment_bullet(item)}")
    return "\n".join(lines)


def _project_block(item: dict) -> str:
    name = item.get("project_name") or item.get("supporting_context") or f"{item['skill']} Project"
    lines = [name]
    if item.get("role_title"):
        lines.append(item["role_title"])
    dates = " - ".join(filter(None, [item.get("start_date"), "Present" if item.get("currently_using") else item.get("end_date")]))
    if dates.strip(" -"):
        lines.append(dates)
    body = item.get("work_completed") or item.get("usage_description") or ""
    if body:
        lines.append(_confirmed_bullet(body))
    if item.get("skill_modules"):
        lines.append(f"Covering {item['skill_modules']}.")
    if item.get("related_technologies"):
        lines.append(f"Technologies used: {item['related_technologies']}.")
    if item.get("supporting_context") and item.get("supporting_context") != name:
        lines.append(_confirmed_bullet(item["supporting_context"]))
    if item.get("impact_description"):
        lines.append(_confirmed_bullet(item["impact_description"]))
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
                by_index.setdefault(index, []).append(_employment_bullet(item))
        for index, bullets in by_index.items():
            structure["employments"][index]["bullets"] = list(
                structure["employments"][index].get("bullets") or []
            ) + bullets
    else:
        unplaced_professional = list(buckets["professional"])

    placement = injection_plan.get("placement") or {}
    summary_kw = placement.get("summary") or injection_plan.get("supportedKeywords", [])[:5]
    skills_kw = placement.get("skills") or injection_plan.get("supportedKeywords", [])[:12]
    experience_kw = placement.get("experience") or injection_plan.get("supportedKeywords", [])[:8]
    project_kw = placement.get("projects") or injection_plan.get("supportedKeywords", [])[:6]
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

    # If parser found no sections, preserve original text with a summary prepended carefully
    if not output_sections:
        plain = resume_text.strip()
        return {
            "plain_text": plain,
            "structure": structure,
            "sections": [],
        }

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
