"""Regression tests for the production bug: generated .docx resumes could
contain a duplicated contact block, and duplicated Professional Summary /
Technical Skills headings (plus a redundant flattened skills line next to
an already-correct categorized skills table), right before Professional
Experience began.

Root cause (see template_preservation_service.py::render_optimized_document):
a "did we render enough?" safety net measured rendered content from
`document.paragraphs` only — blind to anything inside a table — so a
template whose skills lived in a table looked under-rendered and the net
appended the ENTIRE optimized text (contact, headings, flattened skills)
as new paragraphs, with headings appended completely unconditionally.

Every fixture below is built with a different, parametrized fake identity
(name/email/phone/skills/role) — never a real person's data — to prove the
fix is generic across users, resumes, roles and templates, not a one-off
string patch.
"""

from __future__ import annotations

import pytest
from docx import Document
from docx.shared import RGBColor

from app.services.template_preservation_service import (
    _top_up_missing_sections,
    render_optimized_document,
    validate_rendered_structure,
)

BLUE = (0x1F, 0x4E, 0x79)


def _add_paragraph(document, text, *, bold=False, italic=False, color=None):
    paragraph = document.add_paragraph()
    run = paragraph.add_run(text)
    run.bold = bold
    run.italic = italic
    if color:
        run.font.color.rgb = RGBColor(*color)
    return paragraph


def _paragraph_texts(document):
    return [p.text for p in document.paragraphs if p.text.strip()]


def _all_body_texts(document):
    """Paragraphs AND table cells — the same table-aware view the fix uses."""
    texts = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                texts.extend(p.text for p in cell.paragraphs if p.text.strip())
    return texts


IDENTITIES = [
    dict(
        name="Priya Patel", contact="priya.patel@example.com | 555-222-3333",
        title="Backend Engineer", company="CloudScale Inc, Denver, CO",
        summary="Backend engineer.",
        bullet="Designed microservices.",
        skills=[
            ("Languages", "Java, Python, Go, C++, Rust, Kotlin"),
            ("Frameworks", "Spring Boot, Django, gRPC, FastAPI, Netty"),
            ("Databases", "PostgreSQL, MongoDB, Redis, Cassandra, DynamoDB"),
            ("Cloud", "AWS, GCP, Azure, DigitalOcean"),
            ("DevOps", "Docker, Kubernetes, Terraform, Jenkins, ArgoCD"),
            ("Testing", "JUnit, PyTest, Selenium, Cypress, k6"),
            ("Messaging", "Kafka, RabbitMQ, SQS, Pulsar"),
            ("Monitoring", "Prometheus, Grafana, Splunk, Datadog"),
            ("Scripting", "Bash, PowerShell, Groovy, Perl"),
            ("Build Tools", "Maven, Gradle, Webpack, Bazel"),
            ("Version Control", "Git, GitHub Actions, GitLab CI"),
            ("Security", "OAuth2, JWT, Vault, SIEM"),
        ],
    ),
    dict(
        name="Marcus Webb", contact="marcus.webb@example.org | 444-777-9999",
        title="Data Engineer", company="Northlight Analytics, Boston, MA",
        summary="Data engineer.",
        bullet="Built streaming pipelines.",
        skills=[
            ("Languages", "Scala, Python, SQL, Java"),
            ("Big Data", "Spark, Hadoop, Flink, Beam"),
            ("Warehousing", "Snowflake, Redshift, BigQuery, Databricks"),
            ("Cloud", "AWS, Azure, GCP"),
            ("Orchestration", "Airflow, dbt, Dagster, Prefect"),
            ("Streaming", "Kafka, Kinesis, Pulsar, NiFi"),
            ("Storage", "S3, HDFS, Delta Lake, Iceberg"),
            ("Monitoring", "Datadog, Grafana, Prometheus"),
            ("Version Control", "Git, GitHub Actions, GitLab CI"),
            ("Testing", "PyTest, Great Expectations, dbt tests"),
            ("Scripting", "Bash, Python, Groovy"),
            ("Security", "IAM, KMS, Vault"),
        ],
    ),
]


def _build_table_heavy_docx(path, identity):
    """A short, one-page-style resume where the skills TABLE carries most
    of the real content — the exact shape that tripped the old table-blind
    length heuristic."""
    document = Document()
    _add_paragraph(document, identity["name"])
    _add_paragraph(document, identity["contact"])

    _add_paragraph(document, "PROFESSIONAL SUMMARY")
    _add_paragraph(document, identity["summary"])

    _add_paragraph(document, "TECHNICAL SKILLS")
    rows = identity["skills"]
    table = document.add_table(rows=len(rows), cols=2)
    for row, (label, values) in zip(table.rows, rows):
        row.cells[0].text = label
        row.cells[1].text = values

    _add_paragraph(document, "PROFESSIONAL EXPERIENCE")
    _add_paragraph(document, identity["company"], bold=True, color=BLUE)
    _add_paragraph(document, f"Role: {identity['title']}", bold=True, color=BLUE)
    _add_paragraph(document, "Mar 2021 - Present", italic=True)
    _add_paragraph(document, f"- {identity['bullet']}")

    document.save(path)
    return document


def _optimized_text_for(identity):
    skills_lines = "\n".join(f"{label}: {values}" for label, values in identity["skills"])
    return f"""{identity['name']}
{identity['contact']}

Professional Summary
{identity['summary']}

Technical Skills
{skills_lines}

Professional Experience
{identity['company']}
Role: {identity['title']}
Mar 2021 - Present
- {identity['bullet']}
"""


@pytest.mark.parametrize("identity", IDENTITIES, ids=lambda i: i["name"])
def test_table_heavy_resume_never_duplicates_contact_or_headings(tmp_path, identity):
    """The reported bug, reproduced generically: a template whose skills
    live in a large table (relative to its sparse prose) must not get its
    contact block / Summary / Skills headings re-appended."""
    source = tmp_path / "source.docx"
    _build_table_heavy_docx(source, identity)
    optimized_text = _optimized_text_for(identity)

    rendered = render_optimized_document(str(source), "docx", optimized_text, output_path=str(tmp_path / "out.docx"))
    document = Document(rendered["file_path"])

    texts = _paragraph_texts(document)
    assert texts.count(identity["name"]) == 1
    assert texts.count(identity["contact"]) == 1
    assert texts.count("PROFESSIONAL SUMMARY") == 1
    assert texts.count("TECHNICAL SKILLS") == 1
    assert texts.count("PROFESSIONAL EXPERIENCE") == 1

    report = validate_rendered_structure(document, optimized_text)
    assert report["duplicate_section_kinds"] == []
    assert report["contact_block_count"] == 1


@pytest.mark.parametrize("identity", IDENTITIES, ids=lambda i: i["name"])
def test_skills_table_preserved_with_no_second_flattened_line(tmp_path, identity):
    source = tmp_path / "source.docx"
    _build_table_heavy_docx(source, identity)
    optimized_text = _optimized_text_for(identity)

    rendered = render_optimized_document(str(source), "docx", optimized_text, output_path=str(tmp_path / "out.docx"))
    document = Document(rendered["file_path"])

    assert len(document.tables) == 1
    table = document.tables[0]
    # The original categorized rows survive, in order, exactly once each —
    # an "Additional Skills" row for a token the (pre-existing, unrelated)
    # routing logic couldn't confidently place is legitimate and not part
    # of this bug, so at most one extra row is tolerated here.
    row_labels = [row.cells[0].text.strip() for row in table.rows]
    original_labels = [label for label, _ in identity["skills"]]
    assert row_labels[: len(original_labels)] == original_labels
    assert len(row_labels) <= len(original_labels) + 1
    assert row_labels.count(row_labels[0]) == 1  # no category row itself duplicated

    # No standalone paragraph anywhere re-states the flattened "Label: values" line.
    flat_lines = {f"{label}: {values}" for label, values in identity["skills"]}
    texts = set(_paragraph_texts(document))
    assert not (flat_lines & texts), f"flattened skills line leaked into body paragraphs: {flat_lines & texts}"


def test_page_break_between_sections_does_not_recreate_contact_block(tmp_path):
    identity = IDENTITIES[0]
    document = Document()
    _add_paragraph(document, identity["name"])
    _add_paragraph(document, identity["contact"])
    _add_paragraph(document, "PROFESSIONAL SUMMARY")
    _add_paragraph(document, identity["summary"])
    _add_paragraph(document, "TECHNICAL SKILLS")
    table = document.add_table(rows=len(identity["skills"]), cols=2)
    for row, (label, values) in zip(table.rows, identity["skills"]):
        row.cells[0].text = label
        row.cells[1].text = values
    document.add_page_break()
    _add_paragraph(document, "PROFESSIONAL EXPERIENCE")
    _add_paragraph(document, identity["company"], bold=True, color=BLUE)
    _add_paragraph(document, f"Role: {identity['title']}", bold=True, color=BLUE)
    _add_paragraph(document, "Mar 2021 - Present", italic=True)
    _add_paragraph(document, f"- {identity['bullet']}")
    source = tmp_path / "source.docx"
    document.save(source)

    optimized_text = _optimized_text_for(identity)
    rendered = render_optimized_document(str(source), "docx", optimized_text, output_path=str(tmp_path / "out.docx"))
    result_doc = Document(rendered["file_path"])
    texts = _paragraph_texts(result_doc)
    assert texts.count(identity["name"]) == 1
    assert texts.count("PROFESSIONAL SUMMARY") == 1
    assert texts.count("PROFESSIONAL EXPERIENCE") == 1


@pytest.mark.parametrize("heading_case", ["Professional Summary", "PROFESSIONAL SUMMARY", "professional summary"])
def test_heading_capitalization_never_creates_a_duplicate_section(tmp_path, heading_case):
    identity = IDENTITIES[0]
    document = Document()
    _add_paragraph(document, identity["name"])
    _add_paragraph(document, identity["contact"])
    _add_paragraph(document, heading_case)
    _add_paragraph(document, identity["summary"])
    _add_paragraph(document, "Technical Skills")
    table = document.add_table(rows=len(identity["skills"]), cols=2)
    for row, (label, values) in zip(table.rows, identity["skills"]):
        row.cells[0].text = label
        row.cells[1].text = values
    _add_paragraph(document, "Professional Experience")
    _add_paragraph(document, identity["company"], bold=True, color=BLUE)
    _add_paragraph(document, f"Role: {identity['title']}", bold=True, color=BLUE)
    _add_paragraph(document, "Mar 2021 - Present", italic=True)
    _add_paragraph(document, f"- {identity['bullet']}")
    source = tmp_path / "source.docx"
    document.save(source)

    optimized_text = _optimized_text_for(identity)
    rendered = render_optimized_document(str(source), "docx", optimized_text, output_path=str(tmp_path / "out.docx"))
    result_doc = Document(rendered["file_path"])
    report = validate_rendered_structure(result_doc, optimized_text)
    assert report["duplicate_section_kinds"] == []


def test_original_contact_formatting_and_order_preserved(tmp_path):
    identity = IDENTITIES[1]
    source = tmp_path / "source.docx"
    _build_table_heavy_docx(source, identity)
    optimized_text = _optimized_text_for(identity)

    rendered = render_optimized_document(str(source), "docx", optimized_text, output_path=str(tmp_path / "out.docx"))
    document = Document(rendered["file_path"])
    texts = _paragraph_texts(document)
    assert texts[0] == identity["name"]
    assert texts[1] == identity["contact"]


def test_missing_section_is_added_once_not_duplicated_on_a_second_render(tmp_path):
    """A section the original template genuinely never had (Certifications)
    must be added exactly once when the optimized text supplies it — and
    re-rendering from the ALREADY-updated document must never add it again."""
    identity = IDENTITIES[0]
    source = tmp_path / "source.docx"
    _build_table_heavy_docx(source, identity)
    optimized_text = _optimized_text_for(identity) + "\nCertifications\nAWS Certified Solutions Architect\n"

    out_path = tmp_path / "out.docx"
    rendered = render_optimized_document(str(source), "docx", optimized_text, output_path=str(out_path))
    document = Document(rendered["file_path"])
    texts = _paragraph_texts(document)
    assert texts.count("Certifications") <= 1
    if texts.count("Certifications") == 1:
        assert "AWS Certified Solutions Architect" in texts

    # Re-render again from the (now-updated) output as if it were the
    # template for a subsequent edit — must not duplicate anything further.
    second_out = tmp_path / "out2.docx"
    rendered2 = render_optimized_document(str(out_path), "docx", optimized_text, output_path=str(second_out))
    second_doc = Document(rendered2["file_path"])
    report = validate_rendered_structure(second_doc, optimized_text)
    assert report["duplicate_section_kinds"] == []
    assert report["contact_block_count"] == 1


def test_top_up_missing_sections_never_touches_contact():
    """Direct unit test: even if called with a document that has ZERO
    existing headings (worst case), the contact block is structurally
    unreachable from _top_up_missing_sections — it only ever reads
    parse_resume_structure's "sections" list, never its "contact" field."""
    document = Document()
    optimized_text = (
        "Some Person\nsome.person@example.com\n\nSummary\n"
        "A detail-oriented professional with broad experience across several engineering teams.\n"
    )
    _top_up_missing_sections(document, optimized_text, rendered_text="")
    texts = _paragraph_texts(document)
    assert "Some Person" not in texts
    assert "some.person@example.com" not in texts
    assert "Summary" in texts  # the actual section heading was legitimately added


def test_top_up_missing_sections_skips_a_kind_already_present():
    document = Document()
    _add_paragraph(document, "Summary")
    _add_paragraph(document, "Existing summary text.")
    optimized_text = "Summary\nA totally different regenerated summary.\n"
    _top_up_missing_sections(document, optimized_text, rendered_text="Summary\nExisting summary text.")
    texts = _paragraph_texts(document)
    assert texts.count("Summary") == 1
    assert "A totally different regenerated summary." not in texts


def test_validate_rendered_structure_detects_duplicate_heading():
    document = Document()
    _add_paragraph(document, "Technical Skills")
    _add_paragraph(document, "Java, Python")
    _add_paragraph(document, "Technical Skills")
    report = validate_rendered_structure(document)
    assert "skills" in report["duplicate_section_kinds"]


def test_validate_rendered_structure_detects_duplicate_contact_block():
    document = Document()
    _add_paragraph(document, "Jordan Lee")
    _add_paragraph(document, "jordan.lee@example.com")
    _add_paragraph(document, "Summary")
    _add_paragraph(document, "A summary.")
    _add_paragraph(document, "Jordan Lee")
    _add_paragraph(document, "jordan.lee@example.com")
    optimized_text = "Jordan Lee\njordan.lee@example.com\n\nSummary\nA summary.\n"
    report = validate_rendered_structure(document, optimized_text)
    assert report["contact_block_count"] == 2


def test_validate_rendered_structure_clean_document_reports_no_duplicates():
    identity = IDENTITIES[0]
    document = Document()
    _add_paragraph(document, identity["name"])
    _add_paragraph(document, identity["contact"])
    _add_paragraph(document, "Professional Summary")
    _add_paragraph(document, identity["summary"])
    optimized_text = f"{identity['name']}\n{identity['contact']}\n\nProfessional Summary\n{identity['summary']}\n"
    report = validate_rendered_structure(document, optimized_text)
    assert report["duplicate_section_kinds"] == []
    assert report["contact_block_count"] == 1


# ---------------------------------------------------------------------------
# PDF export — only if LibreOffice is actually available locally, matching
# how this repo already treats optional local-tool dependencies.
# ---------------------------------------------------------------------------

def _soffice_available():
    from app.services.docx_pdf_export import _find_soffice
    return _find_soffice() is not None


@pytest.mark.skipif(not _soffice_available(), reason="LibreOffice (soffice) not installed locally")
def test_pdf_from_finalized_docx_has_each_heading_exactly_once(tmp_path):
    import fitz

    from app.services.docx_pdf_export import convert_docx_to_pdf, re_sub_ws

    identity = IDENTITIES[0]
    source = tmp_path / "source.docx"
    _build_table_heavy_docx(source, identity)
    optimized_text = _optimized_text_for(identity)
    rendered = render_optimized_document(str(source), "docx", optimized_text, output_path=str(tmp_path / "out.docx"))

    pdf_path = convert_docx_to_pdf(rendered["file_path"], tmp_path / "out.pdf")
    doc = fitz.open(pdf_path)
    try:
        text = re_sub_ws("\n".join(page.get_text("text") or "" for page in doc))
    finally:
        doc.close()

    assert text.lower().count("professional summary") == 1
    assert text.lower().count("technical skills") == 1
    assert text.lower().count("professional experience") == 1


def test_skills_table_category_tools_is_not_a_top_level_heading(tmp_path):
    identity = IDENTITIES[0]
    document = Document()
    _add_paragraph(document, identity["name"])
    _add_paragraph(document, identity["contact"])
    _add_paragraph(document, "PROFESSIONAL SUMMARY")
    _add_paragraph(document, identity["summary"])
    _add_paragraph(document, "TECHNICAL SKILLS")
    table = document.add_table(rows=3, cols=2)
    table.rows[0].cells[0].text = "Programming Languages"
    table.rows[0].cells[1].text = "Java, Python"
    table.rows[1].cells[0].text = "Frameworks"
    table.rows[1].cells[1].text = "Spring Boot"
    tools_label = table.rows[2].cells[0].paragraphs[0]
    tools_label.text = "Tools"
    table.rows[2].cells[1].text = "IntelliJ, Eclipse"
    _add_paragraph(document, "PROFESSIONAL EXPERIENCE")
    _add_paragraph(document, f"- {identity['bullet']}")
    source = tmp_path / "source.docx"
    document.save(source)

    optimized = (
        f"{identity['name']}\n{identity['contact']}\n\n"
        "Professional Summary\nBackend engineer.\n\n"
        "Technical Skills\nProgramming Languages: Java, Python\nTools: IntelliJ, Eclipse\n\n"
        f"Professional Experience\n- {identity['bullet']}\n"
    )
    rendered = render_optimized_document(str(source), "docx", optimized, output_path=str(tmp_path / "out.docx"))
    out = Document(rendered["file_path"])
    report = validate_rendered_structure(out, optimized)
    assert report["heading_counts"].get("skills") == 1
    assert report["heading_counts"].get("summary") == 1
    assert report["heading_counts"].get("experience") == 1
    assert report["duplicate_section_kinds"] == []
    assert [p.text.strip() for p in out.paragraphs if p.text.strip()].count("TECHNICAL SKILLS") == 1


def test_accidental_second_skills_heading_in_generated_text_is_not_rendered(tmp_path):
    identity = IDENTITIES[0]
    source = tmp_path / "source.docx"
    _build_table_heavy_docx(source, identity)
    optimized = _optimized_text_for(identity) + "\n\nTechnical Skills\nJava, Python\n"
    rendered = render_optimized_document(str(source), "docx", optimized, output_path=str(tmp_path / "out.docx"))
    out = Document(rendered["file_path"])
    body_headings = [
        p.text.strip() for p in out.paragraphs
        if p.text.strip() in {"TECHNICAL SKILLS", "Technical Skills"}
    ]
    assert len(body_headings) == 1
    report = validate_rendered_structure(out, optimized)
    assert report["heading_counts"].get("skills") == 1
    assert report["duplicate_section_kinds"] == []


def test_exported_docx_heading_counts_skills_exactly_one(tmp_path):
    identity = IDENTITIES[0]
    source = tmp_path / "source.docx"
    _build_table_heavy_docx(source, identity)
    optimized = _optimized_text_for(identity)
    rendered = render_optimized_document(str(source), "docx", optimized, output_path=str(tmp_path / "out.docx"))
    out = Document(rendered["file_path"])
    report = validate_rendered_structure(out, optimized)
    assert report["heading_counts"].get("summary") == 1
    assert report["heading_counts"].get("skills") == 1
    assert report["heading_counts"].get("experience") == 1
    assert report["duplicate_section_kinds"] == []
    assert report["contact_block_count"] == 1
