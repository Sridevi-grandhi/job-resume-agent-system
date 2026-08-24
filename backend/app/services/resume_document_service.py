"""Structured resume document model for the Step 5 WYSIWYG editor.

Converts between plain-text resumes and an editable document tree.
Role-agnostic: section types are inferred from headings, not hardcoded careers.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from app.services.resume_structure_parser import parse_resume_structure


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
    employments = list(parsed.get("employments") or [])
    projects = list(parsed.get("projects") or [])
    emp_index = 0
    proj_index = 0

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
            items = []
            # Prefer structured employments when available for this section.
            while emp_index < len(employments):
                emp = employments[emp_index]
                emp_index += 1
                items.append({
                    "id": _id("e"),
                    "company": emp.get("company") or "",
                    "title": emp.get("title") or "",
                    "location": "",
                    "dates": emp.get("dates") or "",
                    "bullets": [{"id": _id("b"), "text": b} for b in (emp.get("bullets") or []) if b],
                })
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
            items = []
            while proj_index < len(projects):
                proj = projects[proj_index]
                proj_index += 1
                items.append({
                    "id": _id("p"),
                    "name": proj.get("name") or "",
                    "dates": proj.get("dates") or "",
                    "description": proj.get("description") or "",
                    "bullets": [{"id": _id("b"), "text": b} for b in (proj.get("bullets") or []) if b],
                })
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
                        (item.get("title") or "").strip(),
                        (item.get("company") or "").strip(),
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
