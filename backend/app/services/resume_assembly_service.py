"""Deterministic post-LLM resume assembly.

Protected identity/history fields and section structure always come from the
original uploaded resume. The LLM may only supply mutable optimization content
(summary wording, skills presentation, experience/project bullet rewrites).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.services.resume_generator import (
    _confirmed_evidence_buckets,
    _employment_bullet,
    _has_usage_context,
    _is_evidence_noise,
    _looks_like_definition,
    _merge_section,
    _new_employment_block,
    _certification_line,
    _project_block,
    _training_line,
    _target_employment_index,
)
from app.services.resume_structure_parser import parse_resume_structure


logger = logging.getLogger(__name__)


def normalize_section_heading(heading: str | None) -> str:
    """Normalize harmless heading formatting for comparisons."""
    text = re.sub(r"\s+", " ", (heading or "").strip())
    text = text.rstrip(":").strip()
    return text.lower()


def headings_match(left: str | None, right: str | None) -> bool:
    return normalize_section_heading(left) == normalize_section_heading(right)


def _section_kind(heading: str | None) -> str:
    heading_l = normalize_section_heading(heading)
    if re.search(r"summary|profile|objective", heading_l):
        return "summary"
    if re.search(r"skill|technolog|competenc", heading_l):
        return "skills"
    if re.search(r"experience|employment|work\s+history", heading_l):
        return "experience"
    if re.search(r"project", heading_l):
        return "projects"
    if re.search(r"education|academic", heading_l):
        return "education"
    if re.search(r"certif|license", heading_l):
        return "certifications"
    return "other"


# Public alias — the one canonical heading->semantic-kind mapping this
# project already has (this exact function, just previously private to this
# module). Reused by final_resume_validator's section-order check instead
# of duplicating a second heading-alias table: "Work Experience"/"Experience"
# and "Professional Experience" must canonicalize identically everywhere,
# not just here.
canonical_section_kind = _section_kind


def _evidence_id_targets(structure: dict) -> dict[str, tuple[str, int, int]]:
    """Map E# claim ids to (employment|project, entry_index, bullet_index)."""
    targets: dict[str, tuple[str, int, int]] = {}
    index = 1
    for emp_i, employment in enumerate(structure.get("employments") or []):
        for bullet_i, _bullet in enumerate(employment.get("bullets") or []):
            targets[f"E{index}"] = ("employment", emp_i, bullet_i)
            index += 1
    for proj_i, project in enumerate(structure.get("projects") or []):
        for bullet_i, _bullet in enumerate(project.get("bullets") or []):
            targets[f"E{index}"] = ("project", proj_i, bullet_i)
            index += 1
    return targets


def _normalize_evidence_id(value: Any) -> str:
    text = str(value or "").strip().upper()
    match = re.fullmatch(r"(E|UC)0*(\d+)", text)
    if match:
        return f"{match.group(1)}{int(match.group(2))}"
    return text


def _llm_section_map(llm_plain: str) -> dict[str, str]:
    if not (llm_plain or "").strip():
        return {}
    parsed = parse_resume_structure(llm_plain)
    mapping: dict[str, str] = {}
    for section in parsed.get("sections") or []:
        kind = _section_kind(section.get("heading"))
        content = (section.get("content") or "").strip()
        if content and kind not in mapping:
            mapping[kind] = content
    return mapping


def _render_employment_block(emp: dict[str, Any]) -> str:
    header_parts = [emp.get("company") or ""]
    if emp.get("title"):
        header_parts.append(emp["title"])
    if emp.get("dates"):
        header_parts.append(emp["dates"])
    lines = [" | ".join(part for part in header_parts if part)]
    if emp.get("client"):
        lines.append(f"Client: {emp['client']}")
    for bullet in emp.get("bullets") or []:
        text = str(bullet or "").strip()
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines)


def _render_project_block(project: dict[str, Any]) -> str:
    lines = [project.get("name") or "Project"]
    if project.get("client"):
        lines.append(f"Client: {project['client']}")
    if project.get("dates"):
        lines.append(project["dates"])
    description = (project.get("description") or "").strip()
    if description:
        lines.append(description)
    for bullet in project.get("bullets") or []:
        text = str(bullet or "").strip()
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines)


def _apply_rewritten_bullets(
    structure: dict[str, Any],
    rewritten_bullets: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Copy employments/projects and overlay LLM bullet rewrites via E# ids."""
    employments = [dict(emp) for emp in (structure.get("employments") or [])]
    for emp in employments:
        emp["bullets"] = list(emp.get("bullets") or [])
    projects = [dict(proj) for proj in (structure.get("projects") or [])]
    for proj in projects:
        proj["bullets"] = list(proj.get("bullets") or [])

    targets = _evidence_id_targets(structure)
    extras: list[str] = []
    for item in rewritten_bullets or []:
        text = str((item or {}).get("text") or "").strip()
        if not text:
            continue
        # A Professional Experience/Project bullet must describe candidate
        # experience/contribution/responsibility/implementation/result —
        # never define what a technology is. Reject a proposed bullet/rewrite
        # that reads as a textbook definition or has no real usage-context
        # verb; the existing bullet at that position (if any) is left
        # untouched rather than being replaced with a worse one.
        if _looks_like_definition(text) or not _has_usage_context(text):
            logger.info("GENERATION_BULLET_REJECTED_DEFINITIONAL")
            continue
        supported = [
            _normalize_evidence_id(value)
            for value in ((item or {}).get("supported_by") or [])
            if str(value).strip()
        ]
        placed = False
        for claim_id in supported:
            target = targets.get(claim_id)
            if not target:
                continue
            kind, entry_i, bullet_i = target
            if kind == "employment" and entry_i < len(employments):
                bullets = employments[entry_i]["bullets"]
                if bullet_i < len(bullets):
                    bullets[bullet_i] = text
                else:
                    bullets.append(text)
                placed = True
                break
            if kind == "project" and entry_i < len(projects):
                bullets = projects[entry_i]["bullets"]
                if bullet_i < len(bullets):
                    bullets[bullet_i] = text
                else:
                    bullets.append(text)
                placed = True
                break
        if not placed and any(claim_id.startswith("UC") for claim_id in supported):
            extras.append(text)
        elif not placed and supported:
            # Unknown E# already rejected upstream; keep as extra only for UC leftovers.
            continue
        elif not placed:
            extras.append(text)
    return employments, projects, extras


def _append_confirmed_professional(
    employments: list[dict[str, Any]],
    confirmed_evidence: dict[str, dict] | None,
) -> tuple[list[dict[str, Any]], list[dict]]:
    from app.services.keyword_utils import contains_keyword

    buckets = _confirmed_evidence_buckets(confirmed_evidence)
    unplaced: list[dict] = []
    if buckets["professional"] and employments:
        by_index: dict[int, list[str]] = {}
        for item in buckets["professional"]:
            index = _target_employment_index(employments, item.get("employer_name"))
            skill = str(item.get("skill") or "").strip()
            bullet = _employment_bullet(item)
            if not bullet or _is_evidence_noise(bullet):
                continue
            # Skip when a polished UC rewrite or existing bullet already covers this skill.
            already = " ".join(
                str(b) for emp in employments for b in (emp.get("bullets") or [])
            )
            if skill and contains_keyword(already, skill):
                continue
            if index is None:
                unplaced.append(item)
            else:
                by_index.setdefault(index, []).append(bullet)
        for index, bullets in by_index.items():
            existing = list(employments[index].get("bullets") or [])
            existing_norm = {re.sub(r"\s+", " ", b.strip().lower()) for b in existing}
            for bullet in bullets:
                key = re.sub(r"\s+", " ", bullet.strip().lower())
                if key and key not in existing_norm:
                    existing.append(bullet)
                    existing_norm.add(key)
            employments[index]["bullets"] = existing
    else:
        unplaced = list(buckets["professional"])
    return employments, unplaced


def _pick_summary(
    *,
    original_content: str,
    rewritten_summary: str,
    llm_sections: dict[str, str],
) -> str:
    for candidate in (rewritten_summary, llm_sections.get("summary") or ""):
        text = (candidate or "").strip()
        if text:
            # Replace the entire summary — one concise block (3–5 sentences max).
            sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
            return " ".join(sentences[:5]).strip()
    return (original_content or "").strip()


def _pick_skills(
    *,
    original_content: str,
    llm_sections: dict[str, str],
    denied_skills: list[str] | None,
    unsupported_keywords: list[str] | None,
    confirmed_evidence: dict[str, dict] | None = None,
) -> str:
    """Preserve original categorized skills layout; inject missing confirmed skill names."""
    from app.services.keyword_utils import contains_keyword

    base = (original_content or "").strip()
    # Never replace a structured/table-like original skills block with a flat LLM line.
    llm_skills = (llm_sections.get("skills") or "").strip()
    original_looks_structured = bool(
        base and (
            "\n" in base
            or ":" in base
            or base.count(",") >= 3
            or base.count(";") >= 2
        )
    )
    if original_looks_structured or not llm_skills:
        content = base
    else:
        content = llm_skills or base

    confirmed_names = []
    for skill, item in (confirmed_evidence or {}).items():
        status = str((item or {}).get("evidence_status") or "").upper()
        if not status.startswith("USER_CONFIRMED"):
            continue
        label = str((item or {}).get("skill") or skill or "").strip()
        if label and not contains_keyword(content, label):
            confirmed_names.append(label)
    if confirmed_names and content:
        # Append only missing confirmed labels; keep original layout intact.
        content = content.rstrip() + "\n" + ", ".join(dict.fromkeys(confirmed_names))
    elif confirmed_names and not content:
        content = ", ".join(dict.fromkeys(confirmed_names))

    blocked = [
        str(term).strip()
        for term in list(denied_skills or []) + list(unsupported_keywords or [])
        if str(term).strip()
    ]
    if blocked and content and base:
        # Do not keep newly introduced blocked skills.
        if any(
            contains_keyword(content, term) and not contains_keyword(base, term)
            for term in blocked
        ):
            return base
    return content


def assemble_optimized_resume(
    *,
    resume_text: str,
    structure: dict[str, Any] | None,
    llm_result: dict[str, Any] | None,
    confirmed_evidence: dict[str, dict] | None = None,
    denied_skills: list[str] | None = None,
    unsupported_keywords: list[str] | None = None,
) -> dict[str, Any]:
    """Merge LLM mutable content into the original protected resume structure."""
    original = structure or parse_resume_structure(resume_text or "")
    llm_result = llm_result or {}
    llm_plain = str(llm_result.get("generated_resume_plain_text") or "").strip()
    llm_sections = _llm_section_map(llm_plain)
    rewritten_summary = str(llm_result.get("rewritten_summary") or "").strip()
    rewritten_bullets = llm_result.get("rewritten_bullets") or []

    employments, projects, extra_bullets = _apply_rewritten_bullets(original, rewritten_bullets)
    from app.services.resume_document_service import dedupe_experience_employers, sanitize_resume_public_text

    employments = dedupe_experience_employers(employments)
    employments, unplaced_professional = _append_confirmed_professional(
        employments, confirmed_evidence
    )
    if extra_bullets and employments:
        employments[0]["bullets"] = list(employments[0].get("bullets") or []) + extra_bullets

    experience_text = "\n\n".join(
        _render_employment_block(emp) for emp in employments if (emp.get("company") or emp.get("bullets"))
    )
    projects_text = "\n\n".join(
        _render_project_block(proj) for proj in projects if (proj.get("name") or proj.get("bullets"))
    )

    output_sections: list[dict[str, str]] = []
    for section in original.get("sections") or []:
        heading = section.get("heading") or "Section"
        content = section.get("content") or ""
        kind = _section_kind(heading)
        if kind == "summary":
            new_content = _pick_summary(
                original_content=content,
                rewritten_summary=rewritten_summary,
                llm_sections=llm_sections,
            )
        elif kind == "skills":
            new_content = _pick_skills(
                original_content=content,
                llm_sections=llm_sections,
                denied_skills=denied_skills,
                unsupported_keywords=unsupported_keywords,
                confirmed_evidence=confirmed_evidence,
            )
        elif kind == "experience":
            new_content = experience_text if experience_text else content
        elif kind == "projects":
            new_content = projects_text or content
        elif kind in {"education", "certifications"}:
            new_content = content
        else:
            new_content = content
        output_sections.append({"heading": heading, "content": new_content})

    if not output_sections:
        # Parser found no headings — still emit contact + mutable blocks safely.
        if rewritten_summary or llm_sections.get("summary"):
            output_sections.append({
                "heading": "Professional Summary",
                "content": _pick_summary(
                    original_content="",
                    rewritten_summary=rewritten_summary,
                    llm_sections=llm_sections,
                ),
            })
        if experience_text:
            output_sections.append({
                "heading": "Professional Experience",
                "content": experience_text,
            })
        elif (resume_text or "").strip():
            output_sections.append({
                "heading": "Professional Experience",
                "content": (resume_text or "").strip(),
            })

    buckets = _confirmed_evidence_buckets(confirmed_evidence)
    if unplaced_professional:
        block = "\n\n".join(_new_employment_block(item) for item in unplaced_professional)
        if block.strip() and block.strip() not in experience_text:
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

    contact = (original.get("contact") or "").strip()
    # Always restore protected identity fields into the contact block if missing.
    protected_bits = [
        original.get("name") or "",
        original.get("email") or "",
        original.get("phone") or "",
    ]
    for bit in protected_bits:
        if bit and bit not in contact:
            contact = f"{bit}\n{contact}".strip() if contact else bit
    for link in original.get("links") or []:
        if link and link not in contact:
            contact = f"{contact}\n{link}".strip() if contact else str(link)

    parts = [contact] if contact else []
    for section in output_sections:
        parts.append(section["heading"])
        parts.append(section["content"])
    plain = "\n\n".join(part.strip() for part in parts if part and str(part).strip())
    plain = sanitize_resume_public_text(plain)

    # Final safety: if a protected field somehow still missing from plain text, prepend it.
    for field in ("name", "email", "phone"):
        value = (original.get(field) or "").strip()
        if value and value not in plain:
            plain = f"{value}\n{plain}".strip()
            logger.info(
                "RESUME_ASSEMBLY_RESTORED_PROTECTED_FIELD field=%s",
                field,
            )

    for emp in original.get("employments") or []:
        company = (emp.get("company") or "").strip()
        if company and company not in plain:
            logger.info("RESUME_ASSEMBLY_RESTORED_EMPLOYER company_present=true")
            plain = f"{plain}\n{company}".strip()

    plain = sanitize_resume_public_text(plain)

    logger.info(
        "RESUME_ASSEMBLY_COMPLETED sections=%s employments=%s llm_bullets=%s",
        len(output_sections),
        len(employments),
        len(rewritten_bullets or []),
    )
    return {
        "plain_text": plain,
        "structure": original,
        "sections": output_sections,
        "assembly_method": "deterministic_protected_merge",
    }
