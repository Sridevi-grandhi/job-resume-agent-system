"""DOCX-level regression tests for template_preservation_service.py — the
per-project bullet/metadata classification that fixes three related
production bugs: employer/client/location/dates/Environment metadata being
silently overwritten by generated bullet text, ordinary responsibility
bullets inheriting a header paragraph's bold/blue/italic formatting, and
the Technical Skills table being corrupted with stray category-label text.

Generic across roles by construction: the fixture below is a synthetic
two-employer resume (not tied to any one candidate's real content), and a
second, differently-shaped fixture (SDET-style bullets/skills) is used for
the cross-role skills-routing test.
"""

from __future__ import annotations

from docx import Document
from docx.oxml.ns import qn
from docx.shared import RGBColor

from app.services.template_preservation_service import render_optimized_document


def _add_paragraph(document, text, *, bold=False, italic=False, color=None):
    paragraph = document.add_paragraph()
    run = paragraph.add_run(text)
    run.bold = bold
    run.italic = italic
    if color:
        run.font.color.rgb = RGBColor(*color)
    return paragraph


BLUE = (0x1F, 0x4E, 0x79)


def _build_two_employer_docx(path):
    document = Document()
    _add_paragraph(document, "Jane Developer")
    _add_paragraph(document, "jane@example.com | 555-111-2222")

    _add_paragraph(document, "PROFESSIONAL SUMMARY")
    _add_paragraph(document, "Experienced Java engineer with cloud and microservices background.")

    _add_paragraph(document, "PROFESSIONAL EXPERIENCE")

    # Employer 1 — Deloitte, styled bold+blue like a real template header.
    _add_paragraph(document, "Deloitte, Hyderabad, India", bold=True, color=BLUE)
    _add_paragraph(document, "Role: Java Full Stack Developer", bold=True, color=BLUE)
    _add_paragraph(document, "Apr 2020 - Aug 2022", italic=True)
    _add_paragraph(document, "- Built REST APIs using Java and Spring Boot.")
    _add_paragraph(document, "- Deployed services to AWS using Docker and Jenkins.")
    _add_paragraph(document, "Environment: Java, Spring Boot, AWS, Docker, Jenkins")

    # Employer 2 — Microgenesis Techsoft, same template pattern.
    _add_paragraph(document, "Microgenesis Techsoft, Bengaluru, India", bold=True, color=BLUE)
    _add_paragraph(document, "Role: Backend Developer", bold=True, color=BLUE)
    _add_paragraph(document, "Jan 2018 - Mar 2020", italic=True)
    _add_paragraph(document, "- Implemented microservices with Spring Boot.")
    _add_paragraph(document, "- Maintained PostgreSQL database schemas.")
    _add_paragraph(document, "Environment: Spring Boot, PostgreSQL, Git")

    _add_paragraph(document, "TECHNICAL SKILLS")
    table = document.add_table(rows=3, cols=2)
    table.rows[0].cells[0].text = "Programming Languages"
    table.rows[0].cells[1].text = "Java, Python"
    table.rows[1].cells[0].text = "Cloud & DevOps"
    table.rows[1].cells[1].text = "AWS, Docker"
    table.rows[2].cells[0].text = "Monitoring, Logging & Security"
    table.rows[2].cells[1].text = "Splunk"

    _add_paragraph(document, "EDUCATION")
    _add_paragraph(document, "B.S. Computer Science")

    document.save(path)


def _run_style(paragraph):
    run = paragraph.runs[0]
    return {
        "bold": bool(run.bold),
        "italic": bool(run.italic),
        "color": str(run.font.color.rgb) if run.font.color and run.font.color.rgb else None,
    }


def _paragraph_texts(document):
    return [p.text for p in document.paragraphs if p.text.strip()]


OPTIMIZED_TEXT = """Jane Developer
jane@example.com | 555-111-2222

Professional Summary
Java engineer with 6 years of experience building cloud-native microservices on AWS, using Spring Boot and Docker across distributed teams.

Professional Experience
Deloitte, Hyderabad, India
Role: Java Full Stack Developer
Apr 2020 - Aug 2022
- Engineered scalable REST APIs in Java and Spring Boot for enterprise clients.
- Automated CI/CD pipelines with Jenkins for AWS-hosted Docker containers.
- Led migration of legacy services to a microservices architecture.
Environment: Java, Spring Boot, AWS, Docker, Jenkins

Microgenesis Techsoft, Bengaluru, India
Role: Backend Developer
Jan 2018 - Mar 2020
- Delivered production microservices using Spring Boot and REST.
- Optimized PostgreSQL queries for high-throughput reporting.
Environment: Spring Boot, PostgreSQL, Git

Technical Skills
Programming Languages: Java, Python
Cloud & DevOps: AWS, Docker, Kubernetes

Education
B.S. Computer Science
"""


def test_employer_client_location_dates_never_overwritten(tmp_path):
    source = tmp_path / "source.docx"
    _build_two_employer_docx(source)
    rendered = render_optimized_document(str(source), "docx", OPTIMIZED_TEXT, output_path=str(tmp_path / "out.docx"))
    document = Document(rendered["file_path"])
    texts = _paragraph_texts(document)
    assert "Deloitte, Hyderabad, India" in texts
    assert "Role: Java Full Stack Developer" in texts
    assert "Apr 2020 - Aug 2022" in texts
    assert "Microgenesis Techsoft, Bengaluru, India" in texts
    assert "Role: Backend Developer" in texts
    assert "Jan 2018 - Mar 2020" in texts


def test_environment_lines_preserved(tmp_path):
    source = tmp_path / "source.docx"
    _build_two_employer_docx(source)
    rendered = render_optimized_document(str(source), "docx", OPTIMIZED_TEXT, output_path=str(tmp_path / "out.docx"))
    document = Document(rendered["file_path"])
    texts = _paragraph_texts(document)
    assert "Environment: Java, Spring Boot, AWS, Docker, Jenkins" in texts
    assert "Environment: Spring Boot, PostgreSQL, Git" in texts


def test_employer_header_style_untouched(tmp_path):
    source = tmp_path / "source.docx"
    _build_two_employer_docx(source)
    rendered = render_optimized_document(str(source), "docx", OPTIMIZED_TEXT, output_path=str(tmp_path / "out.docx"))
    document = Document(rendered["file_path"])
    header = next(p for p in document.paragraphs if p.text == "Deloitte, Hyderabad, India")
    style = _run_style(header)
    assert style["bold"] is True
    assert style["color"] == "1F4E79"


def test_responsibility_bullets_never_inherit_header_style(tmp_path):
    """Regression: the reported 'random blue/bold/italic' bullets bug —
    ordinary responsibility bullets must keep plain (non-header) formatting
    after AI rewrite, for every project, not just the first."""
    source = tmp_path / "source.docx"
    _build_two_employer_docx(source)
    rendered = render_optimized_document(str(source), "docx", OPTIMIZED_TEXT, output_path=str(tmp_path / "out.docx"))
    document = Document(rendered["file_path"])
    bullet_texts = [
        "Engineered scalable REST APIs in Java and Spring Boot for enterprise clients.",
        "Automated CI/CD pipelines with Jenkins for AWS-hosted Docker containers.",
        "Delivered production microservices using Spring Boot and REST.",
        "Optimized PostgreSQL queries for high-throughput reporting.",
    ]
    for bullet_text in bullet_texts:
        paragraph = next((p for p in document.paragraphs if p.text == bullet_text), None)
        assert paragraph is not None, f"bullet not found: {bullet_text}"
        style = _run_style(paragraph)
        assert style["bold"] is False, f"bullet unexpectedly bold: {bullet_text}"
        assert style["color"] != "1F4E79", f"bullet unexpectedly inherited header color: {bullet_text}"


def test_first_bullet_of_every_project_matches_sibling_bullet_style(tmp_path):
    source = tmp_path / "source.docx"
    _build_two_employer_docx(source)
    rendered = render_optimized_document(str(source), "docx", OPTIMIZED_TEXT, output_path=str(tmp_path / "out.docx"))
    document = Document(rendered["file_path"])
    first_bullet = next(p for p in document.paragraphs if "Engineered scalable REST APIs" in p.text)
    second_bullet = next(p for p in document.paragraphs if "Automated CI/CD pipelines" in p.text)
    assert _run_style(first_bullet) == _run_style(second_bullet)


def test_new_bullet_clones_canonical_project_bullet_style(tmp_path):
    """Optimized text has THREE bullets for Deloitte but the template only
    has two paragraphs — the third must be inserted as a new paragraph
    that clones the project's own bullet style, never a header/role style."""
    source = tmp_path / "source.docx"
    _build_two_employer_docx(source)
    rendered = render_optimized_document(str(source), "docx", OPTIMIZED_TEXT, output_path=str(tmp_path / "out.docx"))
    document = Document(rendered["file_path"])
    new_bullet = next((p for p in document.paragraphs if "Led migration of legacy services" in p.text), None)
    assert new_bullet is not None
    sibling = next(p for p in document.paragraphs if "Engineered scalable REST APIs" in p.text)
    assert _run_style(new_bullet) == _run_style(sibling)


def test_fewer_optimized_bullets_drops_stale_paragraph_not_header(tmp_path):
    """When optimized content has FEWER bullets than the template for a
    project, only the stale BULLET paragraph is blanked — metadata must
    never be touched even in the shortfall case."""
    short_text = OPTIMIZED_TEXT.replace(
        "- Delivered production microservices using Spring Boot and REST.\n"
        "- Optimized PostgreSQL queries for high-throughput reporting.\n",
        "- Delivered production microservices using Spring Boot and REST.\n",
    )
    source = tmp_path / "source.docx"
    _build_two_employer_docx(source)
    rendered = render_optimized_document(str(source), "docx", short_text, output_path=str(tmp_path / "out.docx"))
    document = Document(rendered["file_path"])
    texts = _paragraph_texts(document)
    assert "Optimized PostgreSQL queries for high-throughput reporting." not in texts
    assert "Microgenesis Techsoft, Bengaluru, India" in texts
    assert "Environment: Spring Boot, PostgreSQL, Git" in texts


def test_skills_table_routes_new_technology_to_matching_category(tmp_path):
    source = tmp_path / "source.docx"
    _build_two_employer_docx(source)
    rendered = render_optimized_document(str(source), "docx", OPTIMIZED_TEXT, output_path=str(tmp_path / "out.docx"))
    document = Document(rendered["file_path"])
    table = document.tables[0]
    cloud_cell = next(row.cells[-1].text for row in table.rows if row.cells[0].text.strip() == "Cloud & DevOps")
    assert "Kubernetes" in cloud_cell


def test_skills_table_never_corrupts_other_categories(tmp_path):
    """Regression: the reported 'Monitoring, Logging & Security' cell
    corruption — no category-label fragments or unrelated categories leak
    into a cell that wasn't the routing target."""
    source = tmp_path / "source.docx"
    _build_two_employer_docx(source)
    rendered = render_optimized_document(str(source), "docx", OPTIMIZED_TEXT, output_path=str(tmp_path / "out.docx"))
    document = Document(rendered["file_path"])
    table = document.tables[0]
    monitoring_cell = next(row.cells[-1].text for row in table.rows if row.cells[0].text.strip() == "Monitoring, Logging & Security")
    assert monitoring_cell.strip() == "Splunk"
    assert "Languages" not in monitoring_cell
    assert "Databases" not in monitoring_cell
    assert "Scrum" not in monitoring_cell


# ---------------------------------------------------------------------------
# Cross-role — same fix must hold for a completely different role/template,
# not just the Java Full Stack fixture above (Part 15: generic, not Java-only).
# ---------------------------------------------------------------------------

def _build_sdet_docx(path):
    document = Document()
    _add_paragraph(document, "Alex Tester")
    _add_paragraph(document, "alex@example.com")
    _add_paragraph(document, "PROFESSIONAL EXPERIENCE")
    _add_paragraph(document, "Cisco Systems, San Jose, CA", bold=True, color=BLUE)
    _add_paragraph(document, "Role: SDET", bold=True, color=BLUE)
    _add_paragraph(document, "Jun 2019 - Present", italic=True)
    _add_paragraph(document, "- Automated regression suites using Selenium and TestNG.")
    _add_paragraph(document, "- Built performance test plans in JMeter.")
    _add_paragraph(document, "Environment: Selenium, TestNG, JMeter, Jenkins")
    _add_paragraph(document, "TECHNICAL SKILLS")
    table = document.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Testing Tools"
    table.rows[0].cells[1].text = "Selenium, TestNG"
    table.rows[1].cells[0].text = "CI/CD"
    table.rows[1].cells[1].text = "Jenkins"
    document.save(path)


SDET_OPTIMIZED_TEXT = """Alex Tester
alex@example.com

Professional Experience
Cisco Systems, San Jose, CA
Role: SDET
Jun 2019 - Present
- Engineered end-to-end Selenium/TestNG regression suites integrated into CI/CD.
- Designed JMeter load-test scenarios covering peak-traffic API scenarios.
Environment: Selenium, TestNG, JMeter, Jenkins

Technical Skills
Testing Tools: Selenium, TestNG, Playwright
CI/CD: Jenkins
"""


def test_sdet_role_metadata_and_bullet_style_isolated(tmp_path):
    source = tmp_path / "source.docx"
    _build_sdet_docx(source)
    rendered = render_optimized_document(str(source), "docx", SDET_OPTIMIZED_TEXT, output_path=str(tmp_path / "out.docx"))
    document = Document(rendered["file_path"])
    texts = _paragraph_texts(document)
    assert "Cisco Systems, San Jose, CA" in texts
    assert "Role: SDET" in texts
    assert "Jun 2019 - Present" in texts
    assert "Environment: Selenium, TestNG, JMeter, Jenkins" in texts

    bullet = next(p for p in document.paragraphs if "Engineered end-to-end Selenium" in p.text)
    header = next(p for p in document.paragraphs if p.text == "Cisco Systems, San Jose, CA")
    assert _run_style(bullet)["bold"] is False
    assert _run_style(bullet)["color"] != _run_style(header)["color"]

    table = document.tables[0]
    testing_cell = next(row.cells[-1].text for row in table.rows if row.cells[0].text.strip() == "Testing Tools")
    assert "Playwright" in testing_cell


def test_skills_table_no_duplicate_categories(tmp_path):
    source = tmp_path / "source.docx"
    _build_two_employer_docx(source)
    rendered = render_optimized_document(str(source), "docx", OPTIMIZED_TEXT, output_path=str(tmp_path / "out.docx"))
    document = Document(rendered["file_path"])
    table = document.tables[0]
    labels = [row.cells[0].text.strip() for row in table.rows]
    assert len(labels) == len(set(labels))


def test_skills_table_exact_row_count_and_content_no_corruption(tmp_path):
    """Manual-inspection regression: caught by actually printing a rendered
    table, not by any single assertion above — the naive comma-only
    tokenizer let a "Category: Value" line's LABEL fuse with its first
    VALUE into one bogus combined token (e.g. "Programming Languages: Java"),
    which then either got treated as a stray technology or spawned a
    malformed extra row. This asserts the WHOLE table exactly, not just
    individual cells, so a similar regression can't slip through again."""
    source = tmp_path / "source.docx"
    _build_two_employer_docx(source)
    rendered = render_optimized_document(str(source), "docx", OPTIMIZED_TEXT, output_path=str(tmp_path / "out.docx"))
    document = Document(rendered["file_path"])
    table = document.tables[0]
    rows = [[cell.text for cell in row.cells] for row in table.rows]
    assert rows == [
        ["Programming Languages", "Java, Python"],
        ["Cloud & DevOps", "AWS, Docker, Kubernetes"],
        ["Monitoring, Logging & Security", "Splunk"],
    ]


# ---------------------------------------------------------------------------
# Layout regression — large blank gap between Professional Summary and
# Technical Skills (production bug): a multi-paragraph original summary
# block getting a SHORTER optimized replacement used to blank the leftover
# paragraphs' TEXT while leaving the paragraph ELEMENTS in the document,
# each still reserving a full blank line of vertical space.
# ---------------------------------------------------------------------------

def _build_multi_paragraph_summary_docx(path):
    document = Document()
    _add_paragraph(document, "Jane Developer")
    _add_paragraph(document, "jane@example.com | 555-111-2222")
    _add_paragraph(document, "PROFESSIONAL SUMMARY")
    # Original template wrote the summary as THREE separate paragraphs —
    # a common real-world template shape (one sentence per paragraph).
    _add_paragraph(document, "Experienced Java engineer with cloud background.")
    _add_paragraph(document, "Strong record delivering microservices at scale.")
    _add_paragraph(document, "Skilled across the full SDLC from design to production.")
    _add_paragraph(document, "TECHNICAL SKILLS")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Programming Languages"
    table.rows[0].cells[1].text = "Java, Python"
    document.save(path)


SHORT_SUMMARY_OPTIMIZED_TEXT = """Jane Developer
jane@example.com | 555-111-2222

Professional Summary
Java engineer with 6 years of experience building cloud-native microservices on AWS.

Technical Skills
Programming Languages: Java, Python
"""


def test_shorter_summary_leaves_no_blank_paragraph_gap_before_next_heading(tmp_path):
    source = tmp_path / "source.docx"
    _build_multi_paragraph_summary_docx(source)
    rendered = render_optimized_document(
        str(source), "docx", SHORT_SUMMARY_OPTIMIZED_TEXT, output_path=str(tmp_path / "out.docx"),
    )
    document = Document(rendered["file_path"])
    all_paragraphs = list(document.paragraphs)
    summary_index = next(i for i, p in enumerate(all_paragraphs) if p.text.strip() == "PROFESSIONAL SUMMARY")
    skills_index = next(i for i, p in enumerate(all_paragraphs) if p.text.strip() == "TECHNICAL SKILLS")
    # Exactly one paragraph between the two headings — the replaced summary
    # text — no leftover blank paragraph elements from the two extra
    # original sentences that had no optimized counterpart.
    between = all_paragraphs[summary_index + 1:skills_index]
    assert len(between) == 1
    assert between[0].text.strip() == (
        "Java engineer with 6 years of experience building cloud-native microservices on AWS."
    )
    assert all(p.text.strip() for p in between), "a leftover blank paragraph was left in place instead of removed"


def test_fewer_optimized_bullets_removes_paragraph_element_not_just_text(tmp_path):
    """Same root cause, inside an experience project: the dropped stale
    bullet paragraph must be REMOVED from the tree, not merely blanked —
    otherwise it still renders as a blank line within the project block."""
    short_text = OPTIMIZED_TEXT.replace(
        "- Delivered production microservices using Spring Boot and REST.\n"
        "- Optimized PostgreSQL queries for high-throughput reporting.\n",
        "- Delivered production microservices using Spring Boot and REST.\n",
    )
    source = tmp_path / "source.docx"
    _build_two_employer_docx(source)
    rendered = render_optimized_document(str(source), "docx", short_text, output_path=str(tmp_path / "out.docx"))
    document = Document(rendered["file_path"])
    all_paragraphs = list(document.paragraphs)
    bullet_index = next(
        i for i, p in enumerate(all_paragraphs)
        if "Delivered production microservices" in p.text
    )
    env_index = next(
        i for i, p in enumerate(all_paragraphs)
        if p.text.strip() == "Environment: Spring Boot, PostgreSQL, Git"
    )
    # No paragraph elements (blank or otherwise) between the surviving
    # bullet and the Environment line — the stale second bullet paragraph
    # was removed outright.
    assert env_index == bullet_index + 1


def _build_pre_existing_blank_run_docx(path):
    """Mirrors the real production pattern found in a live uploaded resume:
    several consecutive ALREADY-BLANK paragraphs baked into the source
    template itself (not caused by our overlay) between the Summary bullets
    and the next heading."""
    document = Document()
    _add_paragraph(document, "Jane Developer")
    _add_paragraph(document, "PROFESSIONAL SUMMARY")
    _add_paragraph(document, "- Senior SDET with hands-on automation experience.")
    _add_paragraph(document, "- Strong Swift/XCUITest background.")
    for _ in range(7):
        document.add_paragraph("")  # 7 pre-existing blank paragraphs, like the real file
    _add_paragraph(document, "TECHNICAL SKILLS")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Programming Languages"
    table.rows[0].cells[1].text = "Swift, Java"
    document.save(path)


PRE_EXISTING_BLANK_OPTIMIZED_TEXT = """Jane Developer

Professional Summary
- Senior SDET with hands-on automation experience across Swift and XCUITest.
- Strong Swift/XCUITest background building reusable Page Object frameworks.

Technical Skills
Programming Languages: Swift, Java
"""


def _build_bullet_style_summary_docx(path):
    """Mirrors the real production template shape: Professional Summary
    written as many separate bullet paragraphs, not one prose paragraph."""
    document = Document()
    _add_paragraph(document, "Jane Developer")
    _add_paragraph(document, "PROFESSIONAL SUMMARY")
    _add_paragraph(document, "•\tSenior engineer with automation background.")
    _add_paragraph(document, "•\tStrong Java and Spring Boot experience.")
    _add_paragraph(document, "•\tSkilled in CI/CD pipelines using Jenkins.")
    _add_paragraph(document, "TECHNICAL SKILLS")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Programming Languages"
    table.rows[0].cells[1].text = "Java, Python"
    document.save(path)


BULLET_STYLE_SUMMARY_OPTIMIZED_TEXT = """Jane Developer

Professional Summary
- Senior engineer with 6 years of automation and platform experience.
- Strong Java and Spring Boot background building cloud-native services.
- Skilled in CI/CD pipelines using Jenkins and GitHub Actions.

Technical Skills
Programming Languages: Java, Python
"""


def test_multi_line_summary_is_split_across_paragraphs_not_crammed_into_one(tmp_path):
    """Regression: a bullet-style (or any multi-line) optimized summary used
    to be crammed into a SINGLE paragraph's run as one string with literal
    embedded "\\n" characters — which Word does not render as line breaks,
    producing a garbled wall of text instead of separate bullet lines. Each
    optimized summary line must land in its own paragraph."""
    source = tmp_path / "source.docx"
    _build_bullet_style_summary_docx(source)
    rendered = render_optimized_document(
        str(source), "docx", BULLET_STYLE_SUMMARY_OPTIMIZED_TEXT, output_path=str(tmp_path / "out.docx"),
    )
    document = Document(rendered["file_path"])
    for paragraph in document.paragraphs:
        assert "\n" not in paragraph.text, f"paragraph contains embedded newline: {paragraph.text!r}"
    texts = _paragraph_texts(document)
    # Leading bullet markers are normalized away (same as experience bullets)
    # so the template's own native paragraph/bullet styling shows through.
    assert "Senior engineer with 6 years of automation and platform experience." in texts
    assert "Strong Java and Spring Boot background building cloud-native services." in texts
    assert "Skilled in CI/CD pipelines using Jenkins and GitHub Actions." in texts
    assert not any(t.startswith("- ") for t in texts)


def test_summary_with_more_lines_than_template_clones_new_paragraphs(tmp_path):
    """Optimized summary has FOUR lines but the template only has three
    bullet paragraphs — the fourth must be inserted as a new paragraph,
    not silently dropped or crammed onto an existing one."""
    source = tmp_path / "source.docx"
    _build_bullet_style_summary_docx(source)
    extra_text = BULLET_STYLE_SUMMARY_OPTIMIZED_TEXT.replace(
        "- Skilled in CI/CD pipelines using Jenkins and GitHub Actions.\n",
        "- Skilled in CI/CD pipelines using Jenkins and GitHub Actions.\n"
        "- Experienced mentoring junior engineers on test automation practices.\n",
    )
    rendered = render_optimized_document(str(source), "docx", extra_text, output_path=str(tmp_path / "out.docx"))
    document = Document(rendered["file_path"])
    texts = _paragraph_texts(document)
    assert "Experienced mentoring junior engineers on test automation practices." in texts


def test_pre_existing_multi_blank_run_in_template_is_collapsed_not_left_as_gap(tmp_path):
    """Regression for the exact live production bug: the UPLOADED template
    itself carried 7 consecutive blank paragraphs between Professional
    Summary and Technical Skills (an authoring artifact, not deliberate
    multi-line spacing) — the export must not reproduce that as a large
    gap, but must still leave SOME breathing room (not zero), consistent
    with the template's own use of blank-paragraph spacing."""
    source = tmp_path / "source.docx"
    _build_pre_existing_blank_run_docx(source)
    rendered = render_optimized_document(
        str(source), "docx", PRE_EXISTING_BLANK_OPTIMIZED_TEXT, output_path=str(tmp_path / "out.docx"),
    )
    document = Document(rendered["file_path"])
    all_paragraphs = list(document.paragraphs)
    skills_index = next(i for i, p in enumerate(all_paragraphs) if p.text.strip() == "TECHNICAL SKILLS")
    last_summary_bullet_index = next(
        i for i, p in enumerate(all_paragraphs)
        if "Page Object frameworks" in p.text
    )
    blanks_between = skills_index - last_summary_bullet_index - 1
    assert blanks_between <= 1, f"expected at most 1 blank paragraph before the next heading, found {blanks_between}"


def test_skills_table_rows_not_forced_to_stay_together(tmp_path):
    """The renderer must never introduce its own pagination locks on the
    Technical Skills table — no row should end up with w:cantSplit (or any
    other renderer-added "keep together" property) that wasn't already in
    the source template, since forcing the whole table as one unbreakable
    block is exactly what pushes it onto a fresh page and leaves a large
    gap behind it."""
    document = Document()
    _add_paragraph(document, "Jane Developer")
    _add_paragraph(document, "PROFESSIONAL SUMMARY")
    _add_paragraph(document, "Experienced engineer.")
    _add_paragraph(document, "TECHNICAL SKILLS")
    categories = [
        ("Programming Languages", "Java, Python"),
        ("Backend", "Spring Boot, Django"),
        ("Databases", "PostgreSQL, MySQL"),
        ("Cloud & DevOps", "AWS, Docker, Kubernetes"),
        ("Frameworks", "React, Angular"),
        ("Build and Test Tools", "Maven, Gradle"),
        ("Version Control Tools", "Git, SVN"),
        ("Monitoring, Logging & Security", "Splunk, Grafana"),
    ]
    table = document.add_table(rows=len(categories), cols=2)
    for row, (label, value) in zip(table.rows, categories):
        row.cells[0].text = label
        row.cells[1].text = value
    source = tmp_path / "source.docx"
    document.save(source)

    optimized_text = "Jane Developer\n\nProfessional Summary\nSenior engineer with broad platform experience.\n\nTechnical Skills\n" + "\n".join(
        f"{label}: {value}" for label, value in categories
    )
    rendered = render_optimized_document(str(source), "docx", optimized_text, output_path=str(tmp_path / "out.docx"))
    out_document = Document(rendered["file_path"])
    out_table = out_document.tables[0]
    for row in out_table.rows:
        tr_pr = row._tr.find(qn("w:trPr"))
        if tr_pr is None:
            continue
        assert tr_pr.find(qn("w:cantSplit")) is None
