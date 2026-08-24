"""Render optimized text without overwriting the uploaded resume.

DOCX files are copied as the base document and only body text is replaced. Existing
sections, page setup, styles, runs, numbering, tables, headers, and footers remain in
the package. PDF/TXT inputs receive an explicitly approximate clean DOCX export.
"""

from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from docx import Document


OUTPUT_DIR = Path(__file__).resolve().parents[1] / "uploads" / "optimized"


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
    old_lengths = [max(1, len(run.text)) for run in runs]
    total = sum(old_lengths)
    cursor = 0
    for index, run in enumerate(runs):
        if index == len(runs) - 1:
            run.text = text[cursor:]
        else:
            take = round(len(text) * old_lengths[index] / total)
            run.text = text[cursor:cursor + take]
            cursor += take


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
    return {"source_file_type": "docx", "sections": [{"type": "document", "source_element_ids": [e["source_element_id"] for e in elements]}], "elements": elements, "template_preserved": True}


def render_optimized_document(source_path: str, file_type: str, optimized_text: str, *, output_path: str | None = None) -> dict:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    destination = Path(output_path) if output_path else OUTPUT_DIR / f"{uuid4()}.docx"
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = [line.strip() for line in (optimized_text or "").replace("\r\n", "\n").split("\n") if line.strip()]

    if file_type == "docx" and Path(source_path).is_file():
        document = Document(source_path)
        paragraphs = [p for p in _body_paragraphs(document) if p.text.strip()]
        for paragraph, line in zip(paragraphs, lines):
            _replace_preserving_runs(paragraph, re.sub(r"^[-•*]\s*", "", line) if paragraph.style and "list" in paragraph.style.name.lower() else line)
        for line in lines[len(paragraphs):]:
            document.add_paragraph(line)
        document.save(destination)
        return {"file_path": str(destination), "template_preserved": True, "source_file_type": "docx", "template_note": "Original DOCX package and styling retained; text replaced in existing elements."}

    document = Document()
    for line in lines:
        if re.match(r"^[-•*]\s+", line):
            document.add_paragraph(re.sub(r"^[-•*]\s+", "", line), style="List Bullet")
        else:
            document.add_paragraph(line)
    document.save(destination)
    note = "TXT contains no source styling; a clean ATS-friendly DOCX was created." if file_type == "txt" else "PDF is not an editable template; an approximate DOCX was created and minor layout differences may occur."
    return {"file_path": str(destination), "template_preserved": False, "source_file_type": file_type, "template_note": note}
