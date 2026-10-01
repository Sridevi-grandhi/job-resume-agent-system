"""Render optimized text onto the uploaded DOCX template when possible.

Preserves page setup, styles, direct run formatting, tables, headers, and footers
by copying the original package and overlaying section content in place.

Experience/project paragraphs are classified per-paragraph (bullet vs.
structural metadata) rather than treated as one flat, undifferentiated
stream — see _classify_experience_paragraph. This is the fix for three
related production bugs that shared one root cause: the old flat-cursor
overlay would overwrite ANY non-heading paragraph in an experience/projects
section (employer/client headers, role lines, dates, Environment lines —
whatever the original template happened to put there) with "whatever is
next" in a single per-SECTION buffer of optimized bullet lines. That both
destroyed structural identity metadata (employer/client/location/dates) and
explained the reported "random blue/bold/italic" bullets: a generated
bullet's TEXT would land inside a paragraph that kept the ORIGINAL
paragraph's own formatting — which, for a company/client header line, is
very often bold/colored/italic in real resume templates.
"""

from __future__ import annotations

import copy
import logging
import re
import shutil
from collections import Counter
from pathlib import Path
from uuid import uuid4

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from app.services.resume_document_service import sanitize_resume_public_text
from app.services.resume_structure_parser import (
    ACTION_VERB_BULLET_RE,
    CLIENT_PATTERN,
    DATE_PATTERN,
    METADATA_LABEL_RE,
    parse_resume_structure,
)


logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "uploads" / "optimized"

_HEADING_RE = re.compile(
    r"(?i)^(professional\s+summary|summary|profile|objective|technical\s+skills|"
    r"key\s+skills|core\s+competenc(?:y|ies)|skills(?:\s+(?:&|and)\s+(?:abilities|expertise))?|"
    r"skills|technologies|competencies|tools\s*(?:&|and)\s*technologies|"
    r"professional\s+experience|experience|"
    r"employment|work\s+history|projects?|education|certifications?)$"
)
# "environment" deliberately excluded — a standalone "Environment:" label is
# per-project experience metadata (see METADATA_LABEL_RE), never a
# top-level section boundary; treating it as one used to reset the
# renderer's current_kind mid-project and skip everything after it.

_BULLET_MARKER_RE = re.compile(r"^[-•*]\s+|^\d+[.)]\s+")


def _body_paragraphs(document):
    for paragraph in document.paragraphs:
        yield paragraph
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs


def _replace_preserving_runs(paragraph, text: str) -> None:
    runs = paragraph.runs
    if not runs:
        paragraph.add_run(text)
        return
    # Keep first run's formatting for the full replacement text.
    first = runs[0]
    first.text = text
    for run in runs[1:]:
        run.text = ""


def _looks_like_heading(text: str) -> bool:
    cleaned = re.sub(r"\s+", " ", (text or "").strip()).rstrip(":")
    if not cleaned or len(cleaned) > 60:
        return False
    # Underscore tokens / edit markers are never headings.
    if "_" in cleaned and " " not in cleaned:
        return False
    # Same root-cause fix as resume_structure_parser._is_heading: casing
    # alone must never decide this — an all-caps EMPLOYER NAME ("CISCO
    # SYSTEMS", "DELOITTE") or sub-label ("RESPONSIBILITIES", "CLIENT") is
    # exactly as short/all-caps as a real heading. _HEADING_RE (case-
    # insensitive) is the one and only signal now.
    return bool(_HEADING_RE.match(cleaned))


def _section_kind(heading: str) -> str:
    h = (heading or "").strip().lower().rstrip(":")
    if re.search(r"summary|profile|objective", h):
        return "summary"
    if re.search(r"skill|technolog|competenc|tools\s*(?:&|and)\s*technolog", h):
        return "skills"
    if re.search(r"experience|employment|work\s+history|career", h):
        return "experience"
    if re.search(r"project", h):
        return "projects"
    if re.search(r"education|academic", h):
        return "education"
    if re.search(r"certif|license", h):
        return "certifications"
    return "other"


def _classify_experience_paragraph(text: str) -> str:
    """"bullet" is the ONLY classification the renderer is ever allowed to
    overwrite (Part 1/Part 4: immutable experience metadata, Environment
    lines preserved). Everything else — employer/client/project headers,
    role/title lines, location/address lines, date lines, "Environment:"
    lines, or any paragraph that doesn't clearly look like a bullet — is
    "metadata"/"other" and is left completely untouched. This is
    deliberately conservative: misclassifying a genuine bullet as
    "metadata" just means that one bullet doesn't get optimized text
    (the original stays, never corrupted); misclassifying metadata as a
    bullet would be the actively harmful direction, so it never happens
    here."""
    if METADATA_LABEL_RE.match(text):
        return "metadata"
    if _BULLET_MARKER_RE.match(text):
        return "bullet"
    if DATE_PATTERN.search(text):
        # An employer/role header line commonly carries its own date range
        # ("Deloitte, Hyderabad, India | Apr 2020 - Aug 2022") — never a bullet.
        return "metadata"
    if CLIENT_PATTERN.search(text):
        return "metadata"
    if len(text) >= 40 and ACTION_VERB_BULLET_RE.match(text):
        # Marker-less bullet (some templates/PDF-derived DOCX drop bullet
        # glyphs) — same heuristic resume_structure_parser already uses for
        # this exact situation, reused here rather than re-derived.
        return "bullet"
    return "other"


def _remove_paragraph(paragraph: Paragraph) -> None:
    """Fully removes a paragraph element — not just its text — from the
    document tree. Used only for paragraphs that ORIGINALLY held real
    content (a summary sentence, skills prose, an experience bullet) but
    have no corresponding optimized replacement because the new content is
    shorter than the original template's paragraph count for that slot.

    This is the fix for the production "large empty gap between Professional
    Summary and Technical Skills" bug: the overlay used to blank such a
    paragraph's text with _replace_preserving_runs(paragraph, "") while
    leaving the paragraph ELEMENT itself in the tree — which still reserves
    a full blank line's worth of vertical space (its own space-before/
    space-after and line height). A multi-paragraph original summary block
    (common when a template writes each sentence as its own paragraph) got
    its first paragraph replaced with the new summary text and every
    remaining paragraph blanked-but-kept, rendering as a stack of blank
    lines before the next heading.

    Genuinely pre-existing blank paragraphs in the source template (deliberate
    template spacing) are never touched by this — the overlay loop skips
    already-empty paragraphs entirely before any replacement logic runs, so
    intentional spacing survives untouched (Part 13's "artificial vs.
    intentional" distinction)."""
    element = paragraph._p
    parent = element.getparent()
    if parent is not None:
        parent.remove(element)


def _collapse_excess_blank_paragraphs(document, max_consecutive: int = 1) -> None:
    """Collapses RUNS of consecutive already-empty body paragraphs down to
    at most `max_consecutive` — never touches a single isolated blank
    paragraph (which may be a template's deliberate section-spacer) or any
    paragraph that has text. This is the fix for the production "large
    empty gap between Professional Summary and Technical Skills" bug: real
    uploaded templates sometimes carry several consecutive blank paragraphs
    as an authoring/formatting artifact (not a deliberate multi-line
    spacer) — e.g. a summary section padded with 5-7 blank lines before the
    next heading — and each one still reserves a full blank line of
    vertical space, stacking into a half-page gap. Collapsing the run to
    one keeps SOME breathing room (matching the template's own use of
    blank-paragraph spacing, per Part 11/14) without the multi-inch gap.
    Table-cell paragraphs are untouched: document.paragraphs only yields
    top-level body paragraphs."""
    run: list[Paragraph] = []
    for paragraph in list(document.paragraphs):
        if (paragraph.text or "").strip():
            for extra in run[max_consecutive:]:
                _remove_paragraph(extra)
            run = []
            continue
        run.append(paragraph)
    for extra in run[max_consecutive:]:
        _remove_paragraph(extra)


def _clone_paragraph_after(anchor: Paragraph, text: str) -> Paragraph:
    """Clones the anchor paragraph's own XML (its own run formatting
    included) and inserts the copy immediately after it — used only to add
    a genuinely new, verified bullet, and only ever cloning another BULLET
    paragraph FROM THE SAME PROJECT (never a header/role/section-heading
    style) — Part 4's "new bullet clones canonical responsibility style"."""
    new_element = copy.deepcopy(anchor._p)
    anchor._p.addnext(new_element)
    new_paragraph = Paragraph(new_element, anchor._parent)
    _replace_preserving_runs(new_paragraph, text)
    return new_paragraph


def _group_bullets_by_run(lines: list[str]) -> list[list[str]]:
    """Splits one section's optimized lines into per-project bullet lists
    by grouping consecutive bullet lines into runs — mirrors exactly how
    the DOCX side groups paragraphs (_classify_experience_paragraph), so
    the two stay in lock-step without depending on parsing a single-line
    "Company | Role | Dates" employer header, which real templates often
    spread across two or three separate lines (employer line, then a
    separate "Role: ..." line, then a separate dates line — exactly the
    shape called out in this feature's own source ticket) and
    resume_structure_parser's own employer-block parser does not reliably
    handle. Deliberately NOT reusing that parser's employments/projects
    field extraction for this — a run boundary here only needs to detect
    "this line is not a bullet", which _classify_experience_paragraph
    already does robustly."""
    runs: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _classify_experience_paragraph(stripped) == "bullet":
            bullet_text = _BULLET_MARKER_RE.sub("", stripped).strip()
            if current is None:
                current = []
                runs.append(current)
            if bullet_text:
                current.append(bullet_text)
        else:
            current = None  # closes the run — the next bullet line starts a new project
    return runs


def _optimized_section_map(optimized_text: str) -> dict[str, str]:
    parsed = parse_resume_structure(optimized_text or "")
    mapping: dict[str, str] = {}
    for section in parsed.get("sections") or []:
        kind = _section_kind(section.get("heading") or "")
        content = (section.get("content") or "").strip()
        if content and kind not in mapping:
            mapping[kind] = content
    # Contact/header lines from optimized text (for non-heading leading paras).
    contact = (parsed.get("contact") or "").strip()
    if contact:
        mapping["contact"] = contact
    return mapping


def _content_lines(text: str) -> list[str]:
    return [line.rstrip() for line in (text or "").splitlines() if line.strip()]


def _paragraph_in_table(paragraph: Paragraph) -> bool:
    element = paragraph._element
    while element is not None:
        if element.tag == qn("w:tbl"):
            return True
        element = element.getparent()
    return False


# ---------------------------------------------------------------------------
# Technical Skills table — category-aware routing (Part 5)
# ---------------------------------------------------------------------------

# Generic (role-independent) domain keyword groups used only to route a
# supported-but-missing technology to the EXISTING category cell it best
# matches — never to invent a taxonomy. Each entry: (keywords found in the
# technology name) -> (keywords looked for in the category's own label).
_SKILL_DOMAIN_ROUTES: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("java", "python", "c#", ".net", "javascript", "typescript", "golang", " go ", "ruby", "c++", "kotlin", "scala", "abap"),
     ("language",)),
    (("html", "css", "react", "angular", "vue", "rest", "graphql", "web"),
     ("web",)),
    (("spring", "django", "flask", "express", "hibernate", "node", ".net core", "fastapi", "framework"),
     ("framework", "backend")),
    (("sql", "mysql", "postgres", "oracle", "mongodb", "cassandra", "dynamodb", "redis", "database", "snowflake", "hana"),
     ("database",)),
    (("aws", "azure", "gcp", "docker", "kubernetes", "terraform", "ansible", "jenkins", "ci/cd", "helm", "cloud", "devops"),
     ("cloud", "devops")),
    (("splunk", "prometheus", "grafana", "elk", "logging", "monitoring", "jwt", "oauth", "security", "siem"),
     ("monitoring", "logging", "security")),
    (("selenium", "cypress", "playwright", "junit", "testng", "jmeter", "postman", "test", "qa"),
     ("test", "qa")),
    (("git", "svn", "bitbucket", "github", "gitlab", "version control"),
     ("version control",)),
    (("maven", "gradle", "ant", "webpack", "build"),
     ("build",)),
    (("bash", "shell", "powershell", "groovy", "perl", "scripting"),
     ("script",)),
    (("tomcat", "weblogic", "websphere", "iis", "jboss", "application server"),
     ("application server", "web services")),
    (("jira", "confluence", "scrum", "agile", "kanban"),
     ("tool",)),
]


def _best_matching_skill_cell(technology: str, category_cells: list[tuple[str, "object"]]):
    """category_cells: list of (label_text, cell). Returns the best-matching
    cell by generic domain-keyword overlap, or None if nothing matches
    well enough to be trustworthy (caller then creates a new row rather
    than force-fitting into an unrelated category)."""
    tech_l = f" {technology.lower()} "
    label_domains = []
    for label_text, cell in category_cells:
        label_l = label_text.lower()
        label_domains.append((label_text, cell, label_l))

    for tech_keywords, category_keywords in _SKILL_DOMAIN_ROUTES:
        if not any(keyword in tech_l for keyword in tech_keywords):
            continue
        for label_text, cell, label_l in label_domains:
            if any(keyword in label_l for keyword in category_keywords):
                return cell
    return None


def _table_category_cells(table) -> list[tuple[str, "object"]]:
    """Best-effort (label_text, value_cell) pairs for a skills table.
    Supports the common 2-column "Category | Skills" layout and the
    single-column "Category: skills" layout — never assumes a specific
    table shape beyond "each row is one category"."""
    pairs = []
    for row in table.rows:
        cells = row.cells
        if len(cells) >= 2:
            label = cells[0].text.strip()
            value_cell = cells[-1]
            if label:
                pairs.append((label, value_cell))
                continue
        # Single-cell "Category: skills" row.
        text = cells[0].text.strip() if cells else ""
        if ":" in text:
            label, _, _ = text.partition(":")
            if label.strip():
                pairs.append((label.strip(), cells[0]))
    return pairs


def _tokenize_skills_text(skills_text: str) -> list[str]:
    """Line-aware tokenizer: splits on newlines FIRST, and for a "Category:
    value, value" line strips the category-label prefix before
    comma-splitting the values — never comma-splits across a newline, and
    never treats "Category: Value" as one combined token (Part 5's root
    tokenizer bug: a naive whole-blob comma-split let a category label
    fused with its first value slip through as a "technology token")."""
    tokens: list[str] = []
    for raw_line in (skills_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # "Category: value, value" or "Category - value, value" — keep only
        # the values part; the label is informational, never a token.
        label_match = re.match(r"^[A-Za-z][A-Za-z0-9 &/'.-]{1,40}\s*[:\-]\s*(.+)$", line)
        values_part = label_match.group(1) if label_match else line
        for token in re.split(r"[,;/|]+", values_part):
            cleaned = token.strip()
            if not cleaned or len(cleaned) >= 40:
                continue
            # A pure "Word & Word" / "Word and Word" fragment is a category
            # label, never a technology token, even standalone on its own line.
            if re.match(r"^[A-Z][a-z]+(?:\s*(?:&|and)\s*[A-Z][a-z]+)+$", cleaned):
                continue
            tokens.append(cleaned)
    return tokens


def _inject_missing_skills_into_tables(document, skills_text: str) -> None:
    """Adds missing confirmed skill tokens into the CLOSEST matching
    EXISTING category cell — never concatenates raw category-label text
    into an unrelated value cell, and never dumps everything into
    whichever cell happens to be last (Part 5's "Monitoring, Logging &
    Security" corruption bug)."""
    from app.services.keyword_utils import contains_keyword

    tokens = _tokenize_skills_text(skills_text)
    if not tokens or not document.tables:
        return
    full_text = "\n".join(p.text for p in _body_paragraphs(document))
    missing = [token for token in tokens if not contains_keyword(full_text, token)]
    if not missing:
        return

    for table in document.tables:
        table_text = "\n".join(cell.text for row in table.rows for cell in row.cells)
        if len(table_text) < 20:
            continue
        category_cells = _table_category_cells(table)
        if not category_cells:
            continue
        routed = 0
        unrouted: list[str] = []
        for token in missing:
            target_cell = _best_matching_skill_cell(token, category_cells)
            if target_cell is None:
                unrouted.append(token)
                continue
            existing = target_cell.text.strip()
            # Avoid duplicating a token already present verbatim in this cell.
            if contains_keyword(existing, token):
                continue
            target_cell.text = f"{existing}, {token}" if existing else token
            routed += 1
        if unrouted:
            # No confident category match — clone the LAST row's formatting
            # for a new "Additional Skills" row rather than guessing wrong.
            _add_skill_row(table, "Additional Skills", unrouted)
        logger.info("TEMPLATE_SKILLS_TABLE_INJECT routed=%s unrouted=%s", routed, len(unrouted))
        return


def _add_skill_row(table, label: str, tokens: list[str]) -> None:
    if not tokens:
        return
    try:
        last_row = table.rows[-1]
        new_row = table.add_row()
        for source_cell, new_cell in zip(last_row.cells, new_row.cells):
            new_cell_xml = copy.deepcopy(source_cell._tc)
            new_cell._tc.getparent().replace(new_cell._tc, new_cell_xml)
        cells = new_row.cells
        value = ", ".join(dict.fromkeys(tokens))
        if len(cells) >= 2:
            _replace_preserving_runs(cells[0].paragraphs[0], label) if cells[0].paragraphs[0].runs else cells[0].paragraphs[0].add_run(label)
            target = cells[-1]
        else:
            target = cells[0]
            value = f"{label}: {value}"
        if target.paragraphs and target.paragraphs[0].runs:
            _replace_preserving_runs(target.paragraphs[0], value)
        else:
            (target.paragraphs[0] if target.paragraphs else target.add_paragraph()).add_run(value)
    except Exception:
        logger.warning("TEMPLATE_SKILLS_TABLE_NEW_ROW_FAILED", exc_info=True)


def template_mapping(source_path: str, file_type: str) -> dict:
    if file_type != "docx" or not Path(source_path).is_file():
        return {"source_file_type": file_type, "sections": [], "template_preserved": False}
    document = Document(source_path)
    elements = []
    for index, paragraph in enumerate(_body_paragraphs(document)):
        if paragraph.text.strip():
            elements.append({
                "source_element_id": f"p-{index}",
                "text": paragraph.text,
                "style": paragraph.style.name if paragraph.style else None,
            })
    return {
        "source_file_type": "docx",
        "sections": [{"type": "document", "source_element_ids": [e["source_element_id"] for e in elements]}],
        "elements": elements,
        "template_preserved": True,
    }


class _RunState:
    """Tracks which project's bullet buffer is currently being filled
    within one experience/projects section, and where the last emitted
    bullet paragraph is (so leftover generated bullets can be inserted
    right after it, cloning ITS style — never a header's)."""

    def __init__(self):
        self.kind: str | None = None
        self.project_index = -1
        self.bullet_index = 0
        self.last_bullet_paragraph: Paragraph | None = None
        self.in_run = False

    def start_new_project(self):
        self.project_index += 1
        self.bullet_index = 0
        self.last_bullet_paragraph = None
        self.in_run = False


def _flush_leftover_bullets(state: "_RunState", per_kind_bullets: dict[str, list[list[str]]]) -> None:
    if not state.in_run or state.last_bullet_paragraph is None or state.kind not in per_kind_bullets:
        return
    projects = per_kind_bullets[state.kind]
    if not (0 <= state.project_index < len(projects)):
        return
    bullets = projects[state.project_index]
    anchor = state.last_bullet_paragraph
    while state.bullet_index < len(bullets):
        anchor = _clone_paragraph_after(anchor, bullets[state.bullet_index])
        state.bullet_index += 1
        state.last_bullet_paragraph = anchor


def _top_up_missing_sections(document, optimized_text: str, rendered_text: str) -> None:
    """Safety net for a template so different from optimized_text that the
    main overlay loop above couldn't place most of it — e.g. a template
    genuinely missing a whole section. Deliberately NOT a "dump the entire
    optimized_text" fallback: it only ever adds a section whose heading
    does not already exist ANYWHERE in the document (paragraphs or
    tables — see seen_headings), so an already-rendered section (heading,
    and for skills its table) can never be duplicated. The contact block is
    structurally unreachable from here: parse_resume_structure's "contact"
    field is separate from its "sections" list, and only "sections" is
    read below.
    """
    seen_headings = {
        _section_kind(p.text.strip())
        for p in _body_paragraphs(document)
        if _looks_like_heading((p.text or "").strip())
    }
    existing = {
        _BULLET_MARKER_RE.sub("", line).strip()
        for line in rendered_text.splitlines() if line.strip()
    }
    parsed = parse_resume_structure(optimized_text)
    for section in parsed.get("sections") or []:
        heading = (section.get("heading") or "").strip()
        if not heading:
            continue
        kind = _section_kind(heading)
        if kind in seen_headings:
            continue
        content_lines = _content_lines((section.get("content") or "").strip())
        if not content_lines:
            continue
        document.add_paragraph(heading)
        for line in content_lines:
            normalized = _BULLET_MARKER_RE.sub("", line).strip()
            if normalized and normalized not in existing:
                document.add_paragraph(normalized)
                existing.add(normalized)
        seen_headings.add(kind)


def _collapse_duplicate_top_level_headings(document) -> None:
    """Keep the first Professional Summary / Technical Skills / Experience heading.

    Extra body headings of the same kind (including a generated 'Technical Skills'
    line that survived overlay) are dropped. Table category labels are ignored
    because they are not top-level sections.
    """
    seen: set[str] = set()
    for paragraph in list(document.paragraphs):
        if _paragraph_in_table(paragraph):
            continue
        text = (paragraph.text or "").strip()
        if not _looks_like_heading(text):
            continue
        kind = _section_kind(text)
        if kind not in {"summary", "skills", "experience"}:
            continue
        if kind in seen:
            _remove_paragraph(paragraph)
            continue
        seen.add(kind)


def validate_rendered_structure(
    document, optimized_text: str = "", original_employer_names: list[str] | None = None,
) -> dict:
    """Post-render structural check — never raises, never blocks a save
    (called for logging only from render_optimized_document). Counts
    top-level section headings in the document body only (table cells such
    as skill categories are not headings) and contact-line occurrences,
    purely structurally — no hardcoded name/email/keyword — so a regression
    that re-introduces duplicate content is caught by count. Tests that want
    a hard failure should assert directly on the returned counts.

    `original_employer_names`, when given, additionally checks that every
    one of the original resume's employer names still appears somewhere in
    the rendered body — purely additive (default None leaves every
    existing call site/test unaffected)."""
    # Top-level section headings live in the document body only. Table cells
    # such as category labels ("Tools", "Databases") are child content of the
    # existing Technical Skills section — never additional Skills headings.
    body = [p for p in document.paragraphs if not _paragraph_in_table(p)]
    body_texts = [(p.text or "").strip() for p in body]
    heading_counts = Counter(
        _section_kind(text) for text in body_texts if _looks_like_heading(text)
    )
    duplicate_section_kinds = sorted(kind for kind, count in heading_counts.items() if count > 1)

    contact_block_count = 1
    if optimized_text:
        contact_text = (parse_resume_structure(optimized_text).get("contact") or "").strip()
        contact_lines = [line.strip() for line in contact_text.splitlines() if line.strip()]
        if contact_lines:
            contact_block_count = max(body_texts.count(line) for line in contact_lines)

    result = {
        "heading_counts": dict(heading_counts),
        "duplicate_section_kinds": duplicate_section_kinds,
        "contact_block_count": contact_block_count,
    }
    if original_employer_names is not None:
        full_text = "\n".join(body_texts)
        found = [name for name in original_employer_names if name and name in full_text]
        result["employer_count_expected"] = len(original_employer_names)
        result["employer_count_found"] = len(found)
        result["missing_employers"] = [name for name in original_employer_names if name not in found]

    return result


def render_optimized_document(
    source_path: str,
    file_type: str,
    optimized_text: str,
    *,
    output_path: str | None = None,
) -> dict:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    destination = Path(output_path) if output_path else OUTPUT_DIR / f"{uuid4()}.docx"
    destination.parent.mkdir(parents=True, exist_ok=True)

    if file_type == "docx" and Path(source_path).is_file():
        optimized_text = sanitize_resume_public_text(optimized_text or "")
        # Always start from a full package copy so styles/borders/tables survive.
        shutil.copy2(source_path, destination)
        document = Document(str(destination))
        section_map = _optimized_section_map(optimized_text)
        per_kind_bullets = {
            "experience": _group_bullets_by_run(_content_lines(section_map.get("experience", ""))),
            "projects": _group_bullets_by_run(_content_lines(section_map.get("projects", ""))),
        }
        current_kind = "contact"
        # Split, not crammed into one paragraph (Part 29/30 rendering-quality
        # fix): a summary is only genuinely ONE paragraph when it HAS one
        # line. A bullet-style or multi-sentence-per-paragraph summary (as
        # real uploaded templates commonly use) must map one optimized line
        # to one paragraph, mirroring the skills/other flat-cursor sections
        # below — cramming every line into a single run with embedded "\n"
        # characters produced a garbled wall of text, since Word does not
        # render a literal newline inside a text run as a line break.
        summary_lines = [
            _BULLET_MARKER_RE.sub("", line).strip()
            for line in _content_lines(section_map.get("summary") or "")
        ]
        summary_mode = False
        summary_cursor = 0
        summary_last_paragraph: Paragraph | None = None
        # Non-experience/projects sections (skills/education/certifications)
        # keep the original flat-cursor behavior — only experience/projects
        # paragraphs need per-project bullet-vs-metadata classification.
        content_buffers = {
            kind: _content_lines(text)
            for kind, text in section_map.items()
            if kind not in {"contact", "experience", "projects", "summary"}
        }
        cursors = {kind: 0 for kind in content_buffers}
        run_state = _RunState()

        for paragraph in list(document.paragraphs):
            text = (paragraph.text or "").strip()
            if not text:
                continue
            if _looks_like_heading(text):
                _flush_leftover_bullets(run_state, per_kind_bullets)
                current_kind = _section_kind(text)
                summary_mode = current_kind == "summary"
                run_state = _RunState()
                run_state.kind = current_kind
                continue
            if _paragraph_in_table(paragraph):
                continue
            if current_kind in {"education", "certifications", "other"}:
                continue
            if summary_mode:
                if summary_cursor < len(summary_lines):
                    _replace_preserving_runs(paragraph, summary_lines[summary_cursor])
                    summary_cursor += 1
                    summary_last_paragraph = paragraph
                else:
                    # Optimized summary has fewer lines than the template's
                    # paragraph slots — remove the stale extra slot entirely
                    # (Part 10/11's blank-gap fix applies here too).
                    _remove_paragraph(paragraph)
                continue

            if current_kind in {"experience", "projects"}:
                role = _classify_experience_paragraph(text)
                if role != "bullet":
                    # Structural metadata (employer/client/project/location/
                    # address/role/dates/Environment) — Part 1: never
                    # overwritten, never restyled. Closes out any bullet run
                    # in progress (flushing leftover generated bullets first).
                    if run_state.in_run:
                        _flush_leftover_bullets(run_state, per_kind_bullets)
                        run_state.start_new_project()
                    continue
                if not run_state.in_run:
                    run_state.start_new_project()
                    run_state.in_run = True
                projects = per_kind_bullets.get(current_kind) or []
                project_index = min(run_state.project_index, len(projects) - 1) if projects else -1
                bullets = projects[project_index] if project_index >= 0 else []
                if run_state.bullet_index < len(bullets):
                    _replace_preserving_runs(paragraph, bullets[run_state.bullet_index])
                    run_state.bullet_index += 1
                    run_state.last_bullet_paragraph = paragraph
                else:
                    # Optimized content has fewer bullets for this project
                    # than the original template — remove the stale extra
                    # bullet paragraph entirely (not just blank it) so it
                    # doesn't leave a blank-line gap. last_bullet_paragraph
                    # stays pointed at the last REAL bullet so a later
                    # leftover-bullet flush still has a valid, attached anchor.
                    _remove_paragraph(paragraph)
                continue

            # Any other recognized-but-simple section (skills prose outside
            # the table, etc.) keeps the original flat-cursor behavior.
            lines = content_buffers.get(current_kind) or []
            cursor = cursors.get(current_kind, 0)
            if cursor < len(lines):
                replacement = lines[cursor]
                cursors[current_kind] = cursor + 1
                # Generated content may accidentally repeat a section heading
                # as a body line. Never write that into a content slot — it
                # would create a second top-level Skills/Summary heading.
                heading_candidate = _BULLET_MARKER_RE.sub("", replacement).strip()
                if _looks_like_heading(heading_candidate):
                    continue
                _replace_preserving_runs(paragraph, replacement)
            elif current_kind == "skills":
                _remove_paragraph(paragraph)

        _flush_leftover_bullets(run_state, per_kind_bullets)
        # Optimized summary has MORE lines than the template had paragraph
        # slots for — clone the last summary paragraph's own style for each
        # extra line, same pattern as leftover experience bullets.
        while summary_cursor < len(summary_lines) and summary_last_paragraph is not None:
            summary_last_paragraph = _clone_paragraph_after(summary_last_paragraph, summary_lines[summary_cursor])
            summary_cursor += 1

        # Inject any confirmed skills missing from preserved skills tables.
        if section_map.get("skills"):
            _inject_missing_skills_into_tables(document, section_map["skills"])

        # Collapse any run of several consecutive blank paragraphs (from the
        # overlay above, or already present in the uploaded template) down
        # to one — Part 10/11's "large empty gap" fix.
        _collapse_excess_blank_paragraphs(document)

        # Table-aware: a categorized skills table (or any other table-based
        # content) must count as "already rendered" — see _body_paragraphs.
        rendered_text = "\n".join(p.text for p in _body_paragraphs(document) if (p.text or "").strip())
        if len(rendered_text.strip()) < max(80, len(optimized_text.strip()) // 3):
            _top_up_missing_sections(document, optimized_text, rendered_text)

        _collapse_duplicate_top_level_headings(document)

        structure_report = validate_rendered_structure(document, optimized_text)
        if structure_report["duplicate_section_kinds"] or structure_report["contact_block_count"] > 1:
            logger.warning("TEMPLATE_DOCX_STRUCTURE_ISSUE report=%s", structure_report)

        document.save(destination)
        logger.info(
            "TEMPLATE_DOCX_RENDERED preserved=true source=docx destination=%s",
            destination.name,
        )
        return {
            "file_path": str(destination),
            "template_preserved": True,
            "source_file_type": "docx",
            "template_note": (
                "Original DOCX package retained (styles, tables, borders, fonts). "
                "Mutable section text overlaid in place; headings, structural "
                "metadata (employer/client/location/dates/Environment), and "
                "education/certifications preserved."
            ),
        }

    # Non-DOCX sources: approximate clean export.
    optimized_text = sanitize_resume_public_text(optimized_text or "")
    document = Document()
    for line in _content_lines(optimized_text):
        if re.match(r"^[-•*]\s+", line):
            document.add_paragraph(re.sub(r"^[-•*]\s+", "", line), style="List Bullet")
        else:
            document.add_paragraph(line)
    document.save(destination)
    note = (
        "TXT contains no source styling; a clean ATS-friendly DOCX was created."
        if file_type == "txt"
        else "PDF is not an editable template; an approximate DOCX was created and minor layout differences may occur."
    )
    return {
        "file_path": str(destination),
        "template_preserved": False,
        "source_file_type": file_type,
        "template_note": note,
    }
