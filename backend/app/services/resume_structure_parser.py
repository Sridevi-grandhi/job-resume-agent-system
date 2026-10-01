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

# Anchored to the START of the line and requires a capitalized name after the
# separator — a genuine "Client: Acme Corp" / "Client - Globex Inc" label line,
# never a mid-sentence technical term. Unanchored, this used to match ANY
# occurrence of "client" + hyphen anywhere in a bullet — "client-side logic",
# "client-facing APIs", "client-server architecture" — silently swallowing
# the ENTIRE bullet as a bogus client-name declaration and dropping it from
# parsing (which cascades into lost ATS evidence credit and misaligned
# original-vs-generated bullet diffs for every bullet after it).
CLIENT_PATTERN = re.compile(r"(?i)^\s*client\s*[:\-]\s*([A-Z][\w&.,'-]*(?:\s+[\w&.,'-]+)*)\s*$")

# A line beginning with a resume action verb is treated as bullet-worthy
# professional evidence even without a leading marker — PDF extraction (and
# some DOCX templates) routinely drop bullet glyphs. Shared with
# template_preservation_service's DOCX overlay so both "is this line a
# bullet" decisions stay in sync — a paragraph classification mismatch
# between the two is exactly what causes metadata/header lines to be
# mistaken for bullets (or vice versa) during export.
ACTION_VERB_BULLET_RE = re.compile(
    r"(?i)^(built|developed|designed|deployed|implemented|managed|led|created|"
    r"provisioned|administered|automated|migrated|configured|maintained|"
    r"orchestrated|supported|wrote|used|using|delivered|optimized|"
    r"integrated|monitored|containerized|established|streamlined)",
)

# Metadata label lines that are never resume body/bullet prose — structural
# identity fields (Part 1: employer/client/project/location/address/role/
# environment) that must never be overwritten by generated bullet text.
METADATA_LABEL_RE = re.compile(
    r"(?i)^\s*(client|client\s*location|location|address|role|title|company|"
    r"employer|project|environment(?:s)?)\s*[:\-]\s*\S",
)

EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE_PATTERN = re.compile(r"(?:\+?\d{1,3}[\s\-.]?)?(?:\(?\d{3}\)?[\s\-.]?)\d{3}[\s\-.]?\d{4}")
URL_PATTERN = re.compile(r"https?://\S+|www\.\S+|linkedin\.com/\S+|github\.com/\S+", re.I)


def _is_heading(line: str) -> bool | str:
    cleaned = re.sub(r"^[\W_]+|[\W_]+$", "", line.strip())
    if not cleaned or len(cleaned) > 60:
        return False
    # Root cause of a live false-positive section_order validation failure:
    # this used to treat ANY short all-caps line as a heading, unconditionally
    # — which misreads a short all-caps EMPLOYER NAME ("CISCO SYSTEMS",
    # "DELOITTE") or an experience sub-label ("RESPONSIBILITIES", "CLIENT")
    # as a brand-new top-level section, fragmenting "Professional Experience"
    # into many bogus sections. All-caps text is now heading-worthy ONLY
    # when it also matches a KNOWN section-heading pattern (same check as
    # the lowercase/title-case path below) — never on casing alone.
    lowered = cleaned.lower()
    for pattern in SECTION_HEADING_PATTERNS:
        if re.fullmatch(pattern, lowered):
            return cleaned.title() if cleaned.isupper() else cleaned
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

    # Primary signal: the first REAL section heading found anywhere marks
    # where contact/header info ends and the resume body begins — including
    # at index 0. A previous `index > 0` guard here assumed a heading could
    # never legitimately be the very first line, but a real production
    # resume can have NO name/contact preamble captured in parsed_text at
    # all (e.g. the candidate's name/contact lives in a DOCX header or
    # text-box that plain-paragraph text extraction never captures) — the
    # body then starts with "PROFESSIONAL SUMMARY" as line 1. With the old
    # `index > 0` guard, that heading (and every line after it, for as long
    # as no blank line ever appears — some extractors strip blank lines
    # entirely) got silently swallowed into "contact", leaving _parse_sections
    # with an EMPTY body and therefore ZERO detected sections: every
    # truthfulness check that depends on original_structure (section_order,
    # employer/dates, protected education/certification sections) was
    # silently skipped for this resume shape. Scanning the whole text (not
    # just the first 12 lines) also recovers documents whose header block is
    # longer than 12 lines before the first real heading.
    for index, line in enumerate(lines):
        if _is_heading(line):
            return "\n".join(lines[:index]).strip(), lines[index:]

    # No heading found anywhere — fall back to "the first blank-line-delimited
    # block is the contact block" (a genuinely headingless resume; recovered
    # via the employment-block fallback elsewhere).
    contact_lines: list[str] = []
    body_start = 0
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


# A short "City, ST" / "City, Country"-shaped line — used only to keep a
# location line from being mistaken for the role title when a split header
# spreads company/location/role/dates across separate lines (it must never
# claim the "title" slot ahead of the real role line that follows it).
_LOCATION_LIKE_RE = re.compile(r"^[A-Za-z][A-Za-z .'-]+,\s*[A-Za-z]{2,}$")


def _reads_like_a_name(stripped: str) -> bool:
    """Company names (and job titles) are conventionally Title-Cased or
    ALL-CAPS on every significant word ("Acme Corp", "CISCO SYSTEMS",
    "Northlight Analytics") — a prose fragment almost never is ("Cross-
    functional stakeholder alignment" capitalizes only its first word).
    This is what keeps a short, un-punctuated responsibility fragment from
    being mistaken for a header line just because it's brief and a date
    happens to follow it soon after — it generalizes past any fixed verb
    allowlist, since it doesn't matter which verb (if any) the prose uses.
    """
    words = [w for w in re.split(r"\s+", stripped) if w and w[0].isalpha()]
    if not words:
        return False
    if stripped == stripped.upper():
        return True
    lowercase_leads = sum(1 for w in words if w[0].islower())
    return lowercase_leads <= 1


def _looks_like_company_line(stripped: str) -> bool:
    """A plausible employer/header line when a split header has no date on
    it: short, not a bullet/date/client/metadata-label line, and not
    prose. Prose almost always either opens with a resume action verb
    (ACTION_VERB_BULLET_RE), reads as a full, period-terminated sentence,
    or — unlike a real header — capitalizes only its first word. Excluding
    all three is what keeps a marker-less accomplishment line ("Delivered
    consistent results across teams.", "Cross-functional stakeholder
    alignment") from ever being mistaken for a company name just because a
    date happens to follow it a few lines later.
    """
    if not stripped or len(stripped) > 100:
        return False
    if stripped.startswith(("-", "•", "*")) or re.match(r"^\d+\.", stripped):
        return False
    if DATE_PATTERN.search(stripped):
        return False
    if METADATA_LABEL_RE.match(stripped) or CLIENT_PATTERN.search(stripped):
        return False
    if ACTION_VERB_BULLET_RE.match(stripped):
        return False
    if stripped.endswith(".") and len(stripped) >= 40:
        return False
    if not _reads_like_a_name(stripped):
        return False
    return True


def _date_within_lookahead(stripped_lines: list[str], start_index: int, span: int = 3) -> bool:
    """True when a date-range appears within the next few structural lines.
    Stops at a bullet line — a header's date must appear before its own
    accomplishment content begins, never be searched for past it.
    """
    for offset in range(span):
        index = start_index + offset
        if index >= len(stripped_lines):
            break
        candidate = stripped_lines[index]
        if not candidate:
            continue
        if candidate.startswith(("-", "•", "*")) or re.match(r"^\d+\.", candidate):
            break
        if DATE_PATTERN.search(candidate):
            return True
    return False


def _parse_employment_blocks(content: str) -> list[dict[str, Any]]:
    lines = [line.rstrip() for line in content.split("\n")]
    stripped_lines = [line.strip() for line in lines]
    employments: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    awaiting_date = False

    def start_new(header_line: str, *, defer_date: bool):
        nonlocal current, awaiting_date
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
        awaiting_date = defer_date and not dates

    for index, line in enumerate(lines):
        stripped = stripped_lines[index]
        if not stripped:
            continue
        client_match = CLIENT_PATTERN.search(stripped)
        if client_match and current:
            current["client"] = client_match.group(1).strip()
            current["raw_lines"].append(line)
            continue

        line_has_date = bool(DATE_PATTERN.search(stripped)) and not stripped.startswith(("-", "•", "*"))

        # A split header's date line arrives on its own, completing a block
        # that was opened earlier from a bare company/location line. Fill
        # the block's own dates instead of starting a brand-new (bogus,
        # company-less) block from this date-only line.
        if line_has_date and current is not None and awaiting_date and not current.get("dates"):
            date_match = DATE_PATTERN.search(stripped)
            current["dates"] = date_match.group(0)
            current["start_year"], current["end_year"] = _extract_years_from_dates(current["dates"])
            awaiting_date = False
            residual = DATE_PATTERN.sub("", stripped).strip(" |-–—,")
            if residual and not current.get("title") and len(residual) < 80:
                current["title"] = residual
            current["raw_lines"].append(line)
            continue

        if line_has_date:
            start_new(stripped, defer_date=False)
            continue

        if current is None:
            # Multi-signal look-ahead: a company-shaped line with a date a
            # few lines below it (and nothing bullet-like in between) opens
            # a split-header block that defers its own date; anything else
            # opens a block the same way it always has.
            defer = _looks_like_company_line(stripped) and _date_within_lookahead(stripped_lines, index + 1)
            start_new(stripped, defer_date=defer)
            continue

        if stripped.startswith(("-", "•", "*")) or re.match(r"^\d+\.", stripped):
            bullet = re.sub(r"^[-•*\d.]+\s*", "", stripped).strip()
            if bullet:
                current["bullets"].append(bullet)
            current["raw_lines"].append(line)
            continue

        # A company-shaped line appearing mid-block, once the current block
        # already looks structurally complete (has bullets or a resolved
        # date) and isn't itself still waiting on its own date, is the next
        # split-header employer starting — confirmed only when a date
        # follows within a few lines, never on the shape of this line alone.
        if (
            not awaiting_date
            and (current.get("bullets") or current.get("dates"))
            and _looks_like_company_line(stripped)
            and _date_within_lookahead(stripped_lines, index + 1)
        ):
            start_new(stripped, defer_date=True)
            continue

        # PDF extractors often drop bullet markers. Long action-oriented lines under
        # an employment header are still professional evidence, not titles.
        # Once the role title is already captured, a substantial marker-less
        # line that isn't a recognized metadata label (Client:/Role:/etc.) is
        # still real accomplishment content even when it doesn't start with
        # one of the fixed action verbs below — the alternative was silently
        # discarding it (only the 26-verb allowlist ever counted), which lost
        # real bullets for any resume phrased with a different verb.
        looks_like_bullet = (
            len(stripped) >= 40
            and not DATE_PATTERN.search(stripped)
            and (
                ACTION_VERB_BULLET_RE.match(stripped)
                or (current.get("title") and not METADATA_LABEL_RE.match(stripped))
            )
        )
        if looks_like_bullet:
            current["bullets"].append(stripped)
            current["raw_lines"].append(line)
        else:
            # Possible title line under company — unless this reads as a
            # location line, in which case leave the title slot open for
            # the role line that (in a split header) still follows it.
            if not current.get("title") and len(stripped) < 80 and not _LOCATION_LIKE_RE.match(stripped):
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
            # evidence for every skill mentioned in that block. A block with BOTH a
            # company name AND a matched date range is real employment evidence even
            # if zero of its lines were recognized as bullets — never drop it just
            # because the bullet heuristic came up empty. `dates` is only ever set
            # via the DATE_PATTERN branch of start_new(), so this still excludes a
            # block that _parse_employment_blocks only opened because some
            # unrelated non-bullet, non-date line (e.g. a stray summary sentence in
            # a headingless resume) had nothing else to attach to.
            fallback_blocks = [
                block for block in _parse_employment_blocks(section["content"])
                if block.get("bullets") or (block.get("company") and block.get("dates"))
            ]
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
