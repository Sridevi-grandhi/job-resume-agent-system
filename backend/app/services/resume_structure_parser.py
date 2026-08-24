"""Resume structure parser for ATS Analyzer — preserves headings and protected fields."""

from __future__ import annotations

import re
from typing import Any


SECTION_HEADING_PATTERNS = [
    r"professional\s+summary",
    r"career\s+summary",
    r"executive\s+summary",
    r"summary(?:\s+of\s+qualifications)?",
    r"profile",
    r"objective",
    r"technical\s+summary",
    r"technical\s+skills",
    r"technical\s+proficienc(?:y|ies)",
    r"key\s+skills",
    r"skills(?:\s+(?:&|and)\s+(?:abilities|expertise))?",
    r"core\s+competenc(?:y|ies)",
    r"areas?\s+of\s+expertise",
    r"technologies",
    r"tools?\s+(?:&|and)\s+technologies",
    r"tech(?:nology)?\s+stack",
    r"professional\s+experience",
    r"work\s+experience",
    r"work\s+history",
    r"career\s+history",
    r"relevant\s+experience",
    r"experience(?:\s+summary)?",
    r"employment(?:\s+history)?",
    r"projects?",
    r"personal\s+projects?",
    r"academic\s+projects?",
    r"key\s+projects?",
    r"project\s+experience",
    r"education",
    r"academic\s+background",
    r"certifications?",
    r"certificates?",
    r"licenses?",
    r"achievements?",
    r"key\s+achievements?",
    r"awards?",
]

DATE_PATTERN = re.compile(
    r"(?i)((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4}|\d{4})"
    r"\s*[-–—to]+\s*"
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4}|\d{4}|present|current|now)"
)

NON_EXPERIENCE_HEADING = re.compile(
    r"skill|technolog|competenc|expertise|tool|stack|summary|profile|objective"
    r"|education|academic|certificat|licen|achievement|award",
    re.I,
)

CLIENT_PATTERN = re.compile(r"(?i)\bclient\s*[:\-]\s*(.+)$")
EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE_PATTERN = re.compile(r"(?:\+?\d{1,3}[\s\-.]?)?(?:\(?\d{3}\)?[\s\-.]?)\d{3}[\s\-.]?\d{4}")
URL_PATTERN = re.compile(r"https?://\S+|www\.\S+|linkedin\.com/\S+|github\.com/\S+", re.I)


def _is_heading(line: str) -> bool | str:
    cleaned = re.sub(r"^[\W_]+|[\W_]+$", "", line.strip())
    if not cleaned or len(cleaned) > 60:
        return False
    # All-caps short lines often are headings.
    if cleaned.isupper() and len(cleaned.split()) <= 6:
        return cleaned.title() if cleaned.isupper() else cleaned
    lowered = cleaned.lower()
    for pattern in SECTION_HEADING_PATTERNS:
        if re.fullmatch(pattern, lowered):
            return cleaned
    # Title Case headings matching known patterns partially — e.g. "IV. Professional
    # Experience" or "Professional Experience (5+ yrs)". Requires the heading phrase to
    # account for nearly all of the line; otherwise an ordinary sentence that merely
    # contains a heading-ish word ("...engineer with 7 years of experience.") would be
    # misread as a new section and silently fragment real content out of its section.
    if re.match(r"^[A-Z][A-Za-z0-9 /&+\-]{2,50}$", cleaned):
        for pattern in SECTION_HEADING_PATTERNS:
            match = re.search(pattern, lowered)
            if match and (len(lowered) - (match.end() - match.start())) <= 15:
                return cleaned
    return False


def _split_contact_and_body(text: str) -> tuple[str, list[str]]:
    lines = [line.rstrip() for line in (text or "").replace("\r\n", "\n").split("\n")]
    # Drop leading empty lines
    while lines and not lines[0].strip():
        lines.pop(0)

    contact_lines = []
    body_start = 0
    for index, line in enumerate(lines[:12]):
        heading = _is_heading(line)
        if heading and index > 0:
            body_start = index
            break
        contact_lines.append(line)
        body_start = index + 1
    else:
        # No clear heading in first lines — take first non-empty block as contact
        for index, line in enumerate(lines):
            if not line.strip() and contact_lines:
                body_start = index + 1
                break
            if line.strip():
                contact_lines.append(line)
                body_start = index + 1

    return "\n".join(contact_lines).strip(), lines[body_start:]


def _parse_sections(body_lines: list[str]) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    current_heading = None
    current_lines: list[str] = []

    def flush():
        nonlocal current_heading, current_lines
        if current_heading is None and not any(line.strip() for line in current_lines):
            current_lines = []
            return
        heading = current_heading or "Content"
        content = "\n".join(current_lines).strip()
        sections.append({"heading": heading, "content": content})
        current_heading = None
        current_lines = []

    for line in body_lines:
        heading = _is_heading(line)
        if heading:
            flush()
            current_heading = heading
            continue
        current_lines.append(line)
    flush()
    return sections


def _extract_years_from_dates(dates: str) -> tuple[int | None, int | None]:
    if not dates:
        return None, None
    years = [int(y) for y in re.findall(r"(?:19|20)\d{2}", dates)]
    if not years:
        return None, None
    start = years[0]
    end = years[-1] if len(years) > 1 else years[0]
    if re.search(r"(?i)present|current|now", dates):
        from datetime import datetime, timezone
        end = datetime.now(timezone.utc).year
    return start, end


def _parse_employment_blocks(content: str) -> list[dict[str, Any]]:
    lines = [line.rstrip() for line in content.split("\n")]
    employments: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def start_new(header_line: str):
        nonlocal current
        if current:
            employments.append(current)
        date_match = DATE_PATTERN.search(header_line)
        dates = date_match.group(0) if date_match else ""
        header_without_dates = DATE_PATTERN.sub("", header_line).strip(" |-–—,")
        parts = [p.strip() for p in re.split(r"[|•·]", header_without_dates) if p.strip()]
        company = parts[0] if parts else header_without_dates
        title = parts[1] if len(parts) > 1 else ""
        start_year, end_year = _extract_years_from_dates(dates)
        current = {
            "company": company,
            "client": "",
            "title": title,
            "dates": dates,
            "start_year": start_year,
            "end_year": end_year,
            "bullets": [],
            "raw_lines": [header_line],
        }

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        client_match = CLIENT_PATTERN.search(stripped)
        if client_match and current:
            current["client"] = client_match.group(1).strip()
            current["raw_lines"].append(line)
            continue
        if DATE_PATTERN.search(stripped) and not stripped.startswith(("-", "•", "*")):
            start_new(stripped)
            continue
        if current is None:
            start_new(stripped)
            continue
        if stripped.startswith(("-", "•", "*")) or re.match(r"^\d+\.", stripped):
            bullet = re.sub(r"^[-•*\d.]+\s*", "", stripped).strip()
            if bullet:
                current["bullets"].append(bullet)
            current["raw_lines"].append(line)
        else:
            # PDF extractors often drop bullet markers. Long action-oriented lines under
            # an employment header are still professional evidence, not titles.
            looks_like_bullet = (
                len(stripped) >= 40
                and not DATE_PATTERN.search(stripped)
                and re.match(
                    r"(?i)^(built|developed|designed|deployed|implemented|managed|led|created|"
                    r"provisioned|administered|automated|migrated|configured|maintained|"
                    r"orchestrated|supported|wrote|used|using|delivered|optimized|"
                    r"integrated|monitored|containerized|established|streamlined)",
                    stripped,
                )
            )
            if looks_like_bullet:
                current["bullets"].append(stripped)
                current["raw_lines"].append(line)
            else:
                # Possible title line under company
                if not current.get("title") and len(stripped) < 80:
                    current["title"] = stripped
                current["raw_lines"].append(line)

    if current:
        employments.append(current)
    return employments


def _parse_project_blocks(content: str) -> list[dict[str, Any]]:
    projects = []
    blocks = re.split(r"\n\s*\n", content.strip()) if content.strip() else []
    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        name = lines[0]
        bullets = []
        description_lines = []
        client = ""
        dates = ""
        for line in lines[1:]:
            client_match = CLIENT_PATTERN.search(line)
            if client_match:
                client = client_match.group(1).strip()
                continue
            date_match = DATE_PATTERN.search(line)
            if date_match and len(line) < 80:
                dates = date_match.group(0)
                continue
            if line.startswith(("-", "•", "*")):
                bullets.append(re.sub(r"^[-•*]+\s*", "", line).strip())
            else:
                description_lines.append(line)
        start_year, end_year = _extract_years_from_dates(dates or block)
        projects.append({
            "name": name,
            "client": client,
            "description": " ".join(description_lines).strip(),
            "dates": dates,
            "start_year": start_year,
            "end_year": end_year,
            "bullets": bullets,
            "raw": block,
        })
    return projects


def parse_resume_structure(resume_text: str) -> dict[str, Any]:
    contact, body_lines = _split_contact_and_body(resume_text or "")
    sections = _parse_sections(body_lines)

    email_match = EMAIL_PATTERN.search(contact)
    phone_match = PHONE_PATTERN.search(contact)
    urls = URL_PATTERN.findall(contact)
    contact_lines = [line for line in contact.split("\n") if line.strip()]
    name = contact_lines[0] if contact_lines else ""

    employments: list[dict[str, Any]] = []
    projects: list[dict[str, Any]] = []
    for section in sections:
        heading_l = section["heading"].lower()
        if re.search(r"experience|employment", heading_l):
            employments.extend(_parse_employment_blocks(section["content"]))
        elif re.search(r"project", heading_l):
            projects.extend(_parse_project_blocks(section["content"]))
        elif not NON_EXPERIENCE_HEADING.search(heading_l):
            # Heading text didn't match a known category — either non-standard wording
            # (e.g. "Career History", a custom title) or heading detection failed
            # entirely, collapsing the whole resume into one "Content" section. Date +
            # bullet structure is still a reliable, role-independent signal of real job
            # content, so recover it here instead of silently losing professional
            # evidence for every skill mentioned in that block.
            fallback_blocks = [block for block in _parse_employment_blocks(section["content"]) if block.get("bullets")]
            employments.extend(fallback_blocks)

    return {
        "contact": contact,
        "name": name,
        "email": email_match.group(0) if email_match else "",
        "phone": phone_match.group(0) if phone_match else "",
        "links": urls,
        "sections": sections,
        "employments": employments,
        "projects": projects,
        "section_order": [section["heading"] for section in sections],
    }
