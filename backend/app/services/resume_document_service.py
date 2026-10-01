"""Structured resume document model for the Step 5 WYSIWYG editor.

Converts between plain-text resumes and an editable document tree.
Role-agnostic: section types are inferred from headings, not hardcoded careers.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from app.services.resume_structure_parser import (
    _parse_employment_blocks,
    _parse_project_blocks,
    parse_resume_structure,
)


def _id(prefix: str = "n") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _section_type(heading: str) -> str:
    h = (heading or "").lower()
    if re.search(r"summary|profile|objective", h):
        return "summary"
    if re.search(r"skill|technolog|competenc|expertise|tools?", h):
        return "skills"
    if re.search(r"experience|employment|work\s+history|career", h):
        return "experience"
    if re.search(r"project", h):
        return "projects"
    if re.search(r"education|academic", h):
        return "education"
    if re.search(r"certif|license", h):
        return "certifications"
    return "custom"


def _split_skills(content: str) -> list[str]:
    skills: list[str] = []
    for line in (content or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" in line and len(line.split(":")[0]) < 40:
            _, rest = line.split(":", 1)
            parts = re.split(r"[,|/•]+", rest)
        else:
            parts = re.split(r"[,|/•]+", line)
        for part in parts:
            token = part.strip(" -•\t")
            if token and len(token) < 80:
                skills.append(token)
    # de-dupe preserving order
    seen = set()
    out = []
    for skill in skills:
        key = skill.lower()
        if key not in seen:
            seen.add(key)
            out.append(skill)
    return out


def _lines_to_bullets(content: str) -> list[dict[str, str]]:
    bullets = []
    for line in (content or "").splitlines():
        text = re.sub(r"^[\s•\-\*\u2022]+", "", line).strip()
        if text:
            bullets.append({"id": _id("b"), "text": text})
    return bullets


def _parse_header(contact: str, name_hint: str = "") -> dict[str, Any]:
    lines = [line.strip() for line in (contact or "").splitlines() if line.strip()]
    name = name_hint or (lines[0] if lines else "")
    email = ""
    phone = ""
    location = ""
    title = ""
    links: list[str] = []
    email_re = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
    phone_re = re.compile(r"(?:\+?\d{1,3}[\s\-.]?)?(?:\(?\d{3}\)?[\s\-.]?)\d{3}[\s\-.]?\d{4}")
    url_re = re.compile(r"https?://\S+|www\.\S+|linkedin\.com/\S+|github\.com/\S+", re.I)

    for index, line in enumerate(lines):
        if email_re.search(line):
            email = email_re.search(line).group(0)
        if phone_re.search(line):
            phone = phone_re.search(line).group(0)
        for match in url_re.findall(line):
            links.append(match.rstrip(".,);"))
        if index == 1 and line != name and not email_re.search(line) and not phone_re.search(line) and not url_re.search(line):
            # Likely professional title or location
            if re.search(r"engineer|developer|analyst|manager|architect|scientist|lead|consultant|specialist", line, re.I):
                title = line
            elif not location:
                location = line
        elif index > 1 and not location and not email_re.search(line) and not phone_re.search(line) and not url_re.search(line):
            if len(line) < 60:
                location = location or line

    return {
        "name": name,
        "title": title,
        "email": email,
        "phone": phone,
        "location": location,
        "links": links,
    }


def plain_text_to_document(
    plain_text: str,
    *,
    template_id: str = "professional-ats",
    page_size: str = "letter",
) -> dict[str, Any]:
    """Build an editable document tree from resume plain text."""
    parsed = parse_resume_structure(plain_text or "")
    header = _parse_header(parsed.get("contact") or "", parsed.get("name") or "")
    if not header.get("email") and parsed.get("email"):
        header["email"] = parsed["email"]
    if not header.get("phone") and parsed.get("phone"):
        header["phone"] = parsed["phone"]
    if parsed.get("links") and not header.get("links"):
        header["links"] = list(parsed.get("links") or [])

    sections: list[dict[str, Any]] = []

    for section in parsed.get("sections") or []:
        heading = section.get("heading") or "Section"
        content = section.get("content") or ""
        stype = _section_type(heading)
        node: dict[str, Any] = {
            "id": _id("s"),
            "type": stype,
            "heading": heading,
        }
        if stype == "summary":
            node["content"] = content.strip()
        elif stype == "skills":
            node["skills"] = _split_skills(content)
            node["content"] = content.strip()
        elif stype == "experience":
            # Parsed directly from THIS section's own content — never a shared
            # cross-section counter. A resume with more than one experience-shaped
            # heading (e.g. "Professional Experience" and a separate "Freelance
            # Work" block) used to drain one flat, pre-aggregated employments list
            # via a single running index, so an earlier section could silently
            # steal entries that belonged to a later one, starving it into the
            # empty-items fallback below even though its own text had real
            # employer content.
            items = [
                {
                    "id": _id("e"),
                    "company": emp.get("company") or "",
                    "title": emp.get("title") or "",
                    "location": "",
                    "dates": emp.get("dates") or "",
                    "bullets": [{"id": _id("b"), "text": b} for b in (emp.get("bullets") or []) if b],
                }
                for emp in _parse_employment_blocks(content)
            ]
            if not items:
                # Fallback: treat whole content as one role block
                items.append({
                    "id": _id("e"),
                    "company": "",
                    "title": "",
                    "location": "",
                    "dates": "",
                    "bullets": _lines_to_bullets(content),
                })
            node["items"] = items
        elif stype == "projects":
            items = [
                {
                    "id": _id("p"),
                    "name": proj.get("name") or "",
                    "dates": proj.get("dates") or "",
                    "description": proj.get("description") or "",
                    "bullets": [{"id": _id("b"), "text": b} for b in (proj.get("bullets") or []) if b],
                }
                for proj in _parse_project_blocks(content)
            ]
            if not items:
                items.append({
                    "id": _id("p"),
                    "name": "",
                    "dates": "",
                    "description": "",
                    "bullets": _lines_to_bullets(content),
                })
            node["items"] = items
        else:
            node["content"] = content.strip()
            node["bullets"] = _lines_to_bullets(content)
        sections.append(node)

    if not sections and (plain_text or "").strip():
        sections.append({
            "id": _id("s"),
            "type": "custom",
            "heading": "Resume",
            "content": (plain_text or "").strip(),
            "bullets": [],
        })

    return {
        "templateId": template_id,
        "pageSize": page_size,
        "fontFamily": "Calibri",
        "fontSize": 11,
        "spacing": "normal",
        "align": "left",
        "header": header,
        "sections": sections,
    }


def document_to_plain_text(document: dict[str, Any] | None) -> str:
    """Serialize an editable document tree back to ATS-friendly plain text."""
    if not document:
        return ""
    header = document.get("header") or {}
    lines: list[str] = []
    name = (header.get("name") or "").strip()
    title = (header.get("title") or "").strip()
    contact_bits = [
        bit for bit in [
            (header.get("email") or "").strip(),
            (header.get("phone") or "").strip(),
            (header.get("location") or "").strip(),
            *[(link or "").strip() for link in (header.get("links") or [])],
        ] if bit
    ]
    if name:
        lines.append(name)
    if title:
        lines.append(title)
    if contact_bits:
        lines.append(" | ".join(contact_bits))
    if lines:
        lines.append("")

    for section in document.get("sections") or []:
        heading = (section.get("heading") or "Section").strip()
        lines.append(heading)
        stype = section.get("type") or _section_type(heading)
        if stype == "summary":
            content = (section.get("content") or "").strip()
            if content:
                lines.append(content)
        elif stype == "skills":
            skills = section.get("skills") or []
            if skills:
                lines.append(", ".join(skills))
            elif section.get("content"):
                lines.append(str(section.get("content")).strip())
        elif stype == "experience":
            for item in section.get("items") or []:
                meta = " | ".join(
                    part for part in [
                        (item.get("company") or "").strip(),
                        (item.get("title") or "").strip(),
                        (item.get("location") or "").strip(),
                        (item.get("dates") or "").strip(),
                    ] if part
                )
                if meta:
                    lines.append(meta)
                for bullet in item.get("bullets") or []:
                    text = (bullet.get("text") if isinstance(bullet, dict) else str(bullet)).strip()
                    if text:
                        lines.append(f"- {text}")
                lines.append("")
        elif stype == "projects":
            for item in section.get("items") or []:
                meta = " | ".join(
                    part for part in [
                        (item.get("name") or "").strip(),
                        (item.get("dates") or "").strip(),
                    ] if part
                )
                if meta:
                    lines.append(meta)
                desc = (item.get("description") or "").strip()
                if desc:
                    lines.append(desc)
                for bullet in item.get("bullets") or []:
                    text = (bullet.get("text") if isinstance(bullet, dict) else str(bullet)).strip()
                    if text:
                        lines.append(f"- {text}")
                lines.append("")
        else:
            content = (section.get("content") or "").strip()
            if content:
                lines.append(content)
            for bullet in section.get("bullets") or []:
                text = (bullet.get("text") if isinstance(bullet, dict) else str(bullet)).strip()
                if text:
                    lines.append(f"- {text}")
        lines.append("")

    # Collapse excessive blank lines
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def merge_presentation(document: dict[str, Any], presentation: dict[str, Any] | None) -> dict[str, Any]:
    """Apply template/presentation settings without mutating content fields."""
    if not presentation:
        return document
    updated = dict(document)
    for key in ("templateId", "pageSize", "fontFamily", "fontSize", "spacing", "align"):
        if key in presentation and presentation[key] is not None:
            updated[key] = presentation[key]
    return updated


def document_sections_for_validation(document: dict[str, Any] | None) -> list[dict[str, str]]:
    """Build heading/content pairs from the editor document for final validation.

    Prefer the document's own section list over re-parsing plain text so section
    order/heading checks are not false-failed by parser heuristics.
    """
    if not document:
        return []
    sections: list[dict[str, str]] = []
    for section in document.get("sections") or []:
        heading = (section.get("heading") or "Section").strip()
        stype = section.get("type") or _section_type(heading)
        parts: list[str] = []
        if stype == "summary":
            content = (section.get("content") or "").strip()
            if content:
                parts.append(content)
        elif stype == "skills":
            skills = section.get("skills") or []
            if skills:
                parts.append(", ".join(str(skill) for skill in skills if skill))
            elif section.get("content"):
                parts.append(str(section.get("content")).strip())
        elif stype in {"experience", "projects"}:
            for item in section.get("items") or []:
                if stype == "experience":
                    meta = " | ".join(
                        part for part in [
                            (item.get("company") or "").strip(),
                            (item.get("title") or "").strip(),
                            (item.get("dates") or "").strip(),
                        ] if part
                    )
                else:
                    meta = " | ".join(
                        part for part in [
                            (item.get("name") or "").strip(),
                            (item.get("dates") or "").strip(),
                        ] if part
                    )
                if meta:
                    parts.append(meta)
                desc = (item.get("description") or "").strip()
                if desc:
                    parts.append(desc)
                for bullet in item.get("bullets") or []:
                    text = (bullet.get("text") if isinstance(bullet, dict) else str(bullet)).strip()
                    if text:
                        parts.append(f"- {text}")
                parts.append("")
        else:
            # `content` (from plain_text_to_document) already carries the full
            # raw section text; `bullets` is only a derived per-line view of
            # that same text. Appending both duplicated every line (e.g. an
            # Education entry rendered as itself AND a "- " bulleted repeat),
            # which false-failed the protected_section truthfulness check
            # against the original's un-duplicated content. Use content when
            # present; fall back to bullets only when there is no raw content.
            content = (section.get("content") or "").strip()
            if content:
                parts.append(content)
            else:
                for bullet in section.get("bullets") or []:
                    text = (bullet.get("text") if isinstance(bullet, dict) else str(bullet)).strip()
                    if text:
                        parts.append(f"- {text}")
        sections.append({
            "heading": heading,
            "content": "\n".join(parts).strip(),
        })
    return sections


def download_basename(candidate_name: str | None) -> str:
    """Build a safe FirstName_LastName_Optimized_Resume file stem."""
    raw = re.sub(r"[^A-Za-z0-9]+", " ", (candidate_name or "").strip())
    if not raw:
        return "Optimized_Resume"
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_")
    if not cleaned:
        return "Optimized_Resume"
    return f"{cleaned}_Optimized_Resume"


_INTERNAL_STATUS_PATTERNS = [
    re.compile(r"(?i)\(\s*user\s+confirmed\s*\)"),
    re.compile(r"(?i)\bUSER_CONFIRMED(?:_PROFESSIONAL|_PROJECT|_CERTIFICATION|_TRAINING)?\b"),
    re.compile(r"(?i)\bREJECTED\b"),
    re.compile(r"(?i)\bNOT_POSSESSED\b"),
    re.compile(r"(?i)\bINCOMPLETE\b"),
    re.compile(r"(?i)\bevidence[_\s-]?id\b"),
    re.compile(r"(?i)^\s*client:\s*status\s+meetings\.?\s*$"),
    # Resume-workspace editor UI labels/badges/buttons (AI Improve modal,
    # inline change badges) — never editor chrome, only ever whole-line
    # matches so ordinary resume prose containing these common words
    # ("Conducted code review...", "Managed vendor rejects...") is untouched.
    re.compile(r"(?i)^[\s\-•*]*✨?\s*AI\s+Improved\s*$"),
    re.compile(r"(?i)^[\s\-•*]*✨?\s*AI\s+Generated\s*$"),
    re.compile(r"(?i)^[\s\-•*]*AI\s+Improve\s*$"),
    re.compile(r"(?i)^[\s\-•*]*✓?\s*Accepted\s*$"),
    re.compile(r"(?i)^[\s\-•*]*Edited\s+manually\s*$"),
    re.compile(r"(?i)^[\s\-•*]*Review\s*$"),
    re.compile(r"(?i)^[\s\-•*]*Accept\s*$"),
    re.compile(r"(?i)^[\s\-•*]*Reject\s*$"),
    re.compile(r"(?i)^[\s\-•*]*Try\s+Again\s*$"),
    re.compile(r"(?i)^[\s\-•*]*Delete\s*$"),
    re.compile(r"(?i)^[\s\-•*]*No\s+change\s+recommended\.?\s*$"),
]


def sanitize_resume_public_text(text: str) -> str:
    """Remove internal workflow/evidence labels that must never appear on a resume."""
    lines = []
    seen_bullets: set[str] = set()
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            lines.append("")
            continue
        if any(pattern.search(line) for pattern in _INTERNAL_STATUS_PATTERNS):
            continue
        normalized = re.sub(r"^[-•*]\s+", "", line).strip().lower()
        normalized = re.sub(r"\s+", " ", normalized)
        if normalized and normalized in seen_bullets:
            continue
        if normalized:
            seen_bullets.add(normalized)
        lines.append(raw_line.rstrip())
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def dedupe_experience_employers(employments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one block per employer; merge bullets when duplicates appear."""
    merged: list[dict[str, Any]] = []
    index_by_company: dict[str, int] = {}
    for emp in employments or []:
        company = re.sub(r"\s+", " ", (emp.get("company") or "").strip()).lower()
        if company and company in index_by_company:
            target = merged[index_by_company[company]]
            existing = {re.sub(r"\s+", " ", str(b).strip().lower()) for b in (target.get("bullets") or [])}
            for bullet in emp.get("bullets") or []:
                key = re.sub(r"\s+", " ", str(bullet).strip().lower())
                if key and key not in existing:
                    target.setdefault("bullets", []).append(bullet)
                    existing.add(key)
            continue
        merged.append(dict(emp))
        if company:
            index_by_company[company] = len(merged) - 1
    return merged