"""Regression tests for the section_order truthfulness-validation false positive
that blocked all resume downloads (DOCX/PDF/TXT).

Live bug: `final_resume_validator.validate_final_resume` compared raw,
whitespace/case-normalized heading strings for section order. Combined with an
all-caps heading-detection bug in the parsers (covered separately in
test_resume_structure_parser.py), this caused:
  - genuine wording aliases ("Work Experience" vs "Professional Experience")
    to be treated as different sections, and
  - experience sub-labels / employer names to fragment "Professional
    Experience" into bogus extra top-level sections,
both of which could flip the section_order subsequence check to "failed" and
block every download format identically via OptimizedResume.validation_status.
"""

from __future__ import annotations

import logging

from docx import Document

from app.models.optimized_resume_model import OptimizedResume
from app.services.final_resume_validator import validate_final_resume
from app.services.resume_document_service import document_sections_for_validation, plain_text_to_document
from app.services.resume_structure_parser import parse_resume_structure


ALLCAPS_EXPERIENCE_RESUME = """
Jane Doe
jane.doe@example.com | 555-222-3333

PROFESSIONAL SUMMARY
SDET with 6+ years of automation experience across Java and Selenium stacks.

TECHNICAL SKILLS
Programming Languages: Java, Python
Backend: Spring Boot, REST APIs
Databases: PostgreSQL, MySQL
Cloud & DevOps: AWS, Docker, Jenkins

PROFESSIONAL EXPERIENCE
CISCO SYSTEMS, San Jose, CA
Role: SDET
CLIENT: Internal Platform Team
Jun 2019 - Present
RESPONSIBILITIES
- Engineered end-to-end Selenium/TestNG regression suites integrated into CI/CD.
- Designed JMeter load-test scenarios covering peak-traffic API scenarios.
ENVIRONMENT: Selenium, TestNG, JMeter, Jenkins

DELOITTE, New York, NY
Role: QA Engineer
CLIENT: Retail Analytics
Jan 2016 - May 2019
RESPONSIBILITIES
- Automated regression suites using Selenium and TestNG.
- Built performance test plans in JMeter.
ENVIRONMENT: Selenium, TestNG, JMeter

EDUCATION
B.S. Computer Science, Example University
""".strip()


def _seed_optimized(db_session, test_user, test_resume, java_job, text, validation_status="passed"):
    optimized = OptimizedResume(
        user_id=test_user.id,
        resume_id=test_resume.id,
        job_id=java_job.id,
        optimized_text=text,
        generated_resume_text=text,
        final_resume_text=text,
        original_score=70,
        generated_score=80,
        edited_score=80,
        template_preserved=True,
        source_file_type="docx",
        validation_status=validation_status,
    )
    db_session.add(optimized)
    db_session.commit()
    db_session.refresh(optimized)
    return optimized


# --- Part 13: exact same headings, different case -> PASS -----------------


def test_exact_same_headings_different_case_passes():
    original_structure = {
        "sections": [
            {"heading": "PROFESSIONAL SUMMARY", "content": "x"},
            {"heading": "TECHNICAL SKILLS", "content": "x"},
            {"heading": "PROFESSIONAL EXPERIENCE", "content": "x"},
        ]
    }
    generated_sections = [
        {"heading": "Professional Summary", "content": "x"},
        {"heading": "Technical Skills", "content": "x"},
        {"heading": "Professional Experience", "content": "x"},
    ]
    result = validate_final_resume(
        original_text="Jane Doe",
        generated_text="Jane Doe",
        original_structure=original_structure,
        generated_sections=generated_sections,
        unsupported_keywords=[],
        selected_technologies=[],
    )
    assert result["structurePreserved"] is True
    assert not any("Section order" in e for e in result["errors"])


# --- Part 14: alias headings canonicalize and PASS -------------------------


def test_alias_experience_heading_canonicalizes_and_passes():
    original_structure = {
        "sections": [
            {"heading": "Professional Summary", "content": "x"},
            {"heading": "Technical Skills", "content": "x"},
            {"heading": "Professional Experience", "content": "x"},
        ]
    }
    generated_sections = [
        {"heading": "Summary", "content": "x"},
        {"heading": "Skills", "content": "x"},
        {"heading": "Work Experience", "content": "x"},
    ]
    result = validate_final_resume(
        original_text="Jane Doe",
        generated_text="Jane Doe",
        original_structure=original_structure,
        generated_sections=generated_sections,
        unsupported_keywords=[],
        selected_technologies=[],
    )
    assert result["structurePreserved"] is True
    assert not any("Section order" in e for e in result["errors"])


# --- Part 15: real reorder MUST FAIL ---------------------------------------


def test_real_section_reorder_is_rejected():
    original_structure = {
        "sections": [
            {"heading": "Professional Summary", "content": "x"},
            {"heading": "Technical Skills", "content": "x"},
            {"heading": "Professional Experience", "content": "x"},
        ]
    }
    generated_sections = [
        {"heading": "Professional Experience", "content": "x"},
        {"heading": "Professional Summary", "content": "x"},
        {"heading": "Technical Skills", "content": "x"},
    ]
    result = validate_final_resume(
        original_text="Jane Doe",
        generated_text="Jane Doe",
        original_structure=original_structure,
        generated_sections=generated_sections,
        unsupported_keywords=[],
        selected_technologies=[],
    )
    assert result["status"] == "failed"
    assert result["structurePreserved"] is False
    assert any("Section order" in e for e in result["errors"])


def test_deleted_education_section_is_rejected():
    original_structure = {
        "sections": [
            {"heading": "Professional Summary", "content": "x"},
            {"heading": "Professional Experience", "content": "x"},
            {"heading": "Education", "content": "x"},
        ]
    }
    generated_sections = [
        {"heading": "Professional Summary", "content": "x"},
        {"heading": "Professional Experience", "content": "x"},
    ]
    result = validate_final_resume(
        original_text="Jane Doe",
        generated_text="Jane Doe",
        original_structure=original_structure,
        generated_sections=generated_sections,
        unsupported_keywords=[],
        selected_technologies=[],
    )
    assert result["status"] == "failed"
    assert result["structurePreserved"] is False


def test_duplicate_section_heading_is_rejected():
    """Original has one Experience section; generated splits it into two
    top-level Experience entries around Education, which must still fail —
    canonicalization must not make duplicate real sections invisible."""
    original_structure = {
        "sections": [
            {"heading": "Professional Summary", "content": "x"},
            {"heading": "Professional Experience", "content": "x"},
            {"heading": "Education", "content": "x"},
        ]
    }
    generated_sections = [
        {"heading": "Professional Summary", "content": "x"},
        {"heading": "Education", "content": "x"},
        {"heading": "Professional Experience", "content": "x"},
    ]
    result = validate_final_resume(
        original_text="Jane Doe",
        generated_text="Jane Doe",
        original_structure=original_structure,
        generated_sections=generated_sections,
        unsupported_keywords=[],
        selected_technologies=[],
    )
    assert result["status"] == "failed"
    assert result["structurePreserved"] is False


# --- Part 6: legitimately-absent optional sections are not required -------


def test_missing_optional_sections_in_original_are_not_required_in_generated():
    """If the source resume never had Education/Certifications/Projects, the
    validator must not manufacture or require them — only the sections that
    actually exist in the original constrain relative order."""
    original_structure = {
        "sections": [
            {"heading": "Professional Summary", "content": "x"},
            {"heading": "Technical Skills", "content": "x"},
            {"heading": "Professional Experience", "content": "x"},
        ]
    }
    generated_sections = [
        {"heading": "Professional Summary", "content": "x"},
        {"heading": "Technical Skills", "content": "x"},
        {"heading": "Professional Experience", "content": "x"},
    ]
    result = validate_final_resume(
        original_text="Jane Doe",
        generated_text="Jane Doe",
        original_structure=original_structure,
        generated_sections=generated_sections,
        unsupported_keywords=[],
        selected_technologies=[],
    )
    assert result["structurePreserved"] is True
    assert not any("Section order" in e for e in result["errors"])


def test_truthfully_added_section_does_not_fail_order_check():
    """A truthful NEW section appended for confirmed evidence (e.g. Projects)
    must not fail order validation just because the original never had it."""
    original_structure = {
        "sections": [
            {"heading": "Professional Summary", "content": "x"},
            {"heading": "Technical Skills", "content": "x"},
            {"heading": "Professional Experience", "content": "x"},
        ]
    }
    generated_sections = [
        {"heading": "Professional Summary", "content": "x"},
        {"heading": "Technical Skills", "content": "x"},
        {"heading": "Professional Experience", "content": "x"},
        {"heading": "Projects", "content": "x"},
    ]
    result = validate_final_resume(
        original_text="Jane Doe",
        generated_text="Jane Doe",
        original_structure=original_structure,
        generated_sections=generated_sections,
        unsupported_keywords=[],
        selected_technologies=[],
    )
    assert result["structurePreserved"] is True
    assert not any("Section order" in e for e in result["errors"])


# --- Part 16: experience subheadings extract exactly once ------------------


def test_experience_subheadings_extract_as_single_professional_experience_section():
    structure = parse_resume_structure(ALLCAPS_EXPERIENCE_RESUME)
    headings = [s["heading"] for s in structure["sections"]]
    assert headings.count("Professional Experience") == 1
    for bogus in ("CISCO SYSTEMS", "DELOITTE", "RESPONSIBILITIES", "CLIENT", "ENVIRONMENT"):
        assert bogus not in headings

    document = plain_text_to_document(ALLCAPS_EXPERIENCE_RESUME)
    doc_sections = document_sections_for_validation(document)
    doc_headings = [s["heading"] for s in doc_sections]
    experience_count = sum(1 for h in doc_headings if h.strip().lower() == "professional experience")
    assert experience_count == 1
    experience_section = next(s for s in doc_sections if s["heading"].strip().lower() == "professional experience")
    assert "Cisco Systems" in experience_section["content"] or "CISCO SYSTEMS" in experience_section["content"]
    assert "Responsibilities" in experience_section["content"] or "RESPONSIBILITIES" in experience_section["content"] \
        or "Engineered end-to-end Selenium" in experience_section["content"]


# --- Part 17: skills table categories extract exactly once -----------------


def test_skill_categories_extract_as_single_technical_skills_section():
    document = plain_text_to_document(ALLCAPS_EXPERIENCE_RESUME)
    doc_sections = document_sections_for_validation(document)
    doc_headings = [s["heading"] for s in doc_sections]
    skills_count = sum(1 for h in doc_headings if h.strip().lower() == "technical skills")
    assert skills_count == 1
    for bogus in ("Programming Languages", "Backend", "Databases", "Cloud & DevOps"):
        assert bogus not in doc_headings


# --- Part 18: live download regression (all three formats) -----------------


def test_download_docx_pdf_txt_all_succeed_after_autosave_with_allcaps_experience_resume(
    client, db_session, test_user, test_resume, java_job, tmp_path, monkeypatch,
):
    """Exact live-bug reproduction: an original resume containing all-caps
    employer names and experience sub-labels (the pattern that used to
    fragment section_order and false-fail every download)."""
    source = tmp_path / "source.docx"
    Document().save(source)
    test_resume.stored_file_path = str(source)
    test_resume.file_type = "docx"
    test_resume.parsed_text = ALLCAPS_EXPERIENCE_RESUME
    db_session.add(test_resume)
    db_session.commit()

    optimized = _seed_optimized(
        db_session, test_user, test_resume, java_job, ALLCAPS_EXPERIENCE_RESUME, validation_status="failed",
    )

    patch_response = client.patch(
        f"/api/optimizer/resume/{test_user.id}/{java_job.id}",
        json={
            "optimized_resume_id": optimized.id,
            "edited_resume_text": ALLCAPS_EXPERIENCE_RESUME,
            "action": "autosave",
            "persist": True,
            "skip_score": True,
        },
    )
    assert patch_response.status_code == 200

    db_session.refresh(optimized)
    assert optimized.validation_status in {"passed", "passed_with_warnings"}

    docx_response = client.get(
        f"/api/optimizer/resume/{test_user.id}/{java_job.id}/{optimized.id}/download?format=docx"
    )
    assert docx_response.status_code == 200

    txt_response = client.get(
        f"/api/optimizer/resume/{test_user.id}/{java_job.id}/{optimized.id}/download?format=txt"
    )
    assert txt_response.status_code == 200

    fake_pdf = tmp_path / "converted.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4\n" + b"0" * 600)

    def _fake_convert(docx_path, destination=None):
        from pathlib import Path

        target = Path(destination) if destination else fake_pdf
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(fake_pdf.read_bytes())
        return target

    monkeypatch.setattr("app.routes.optimizer_edit_routes.convert_docx_to_pdf", _fake_convert)
    monkeypatch.setattr(
        "app.routes.optimizer_edit_routes.validate_pdf_export",
        lambda *a, **k: {"valid": True, "page_count": 1},
    )
    pdf_response = client.get(
        f"/api/optimizer/resume/{test_user.id}/{java_job.id}/{optimized.id}/download?format=pdf"
    )
    assert pdf_response.status_code == 200


# --- Part 19: harmless manual edit still passes and appears in the export --


def test_manual_summary_and_bullet_edit_preserves_order_and_appears_in_download(
    client, db_session, test_user, test_resume, java_job, tmp_path,
):
    source = tmp_path / "source.docx"
    Document().save(source)
    test_resume.stored_file_path = str(source)
    test_resume.file_type = "docx"
    test_resume.parsed_text = ALLCAPS_EXPERIENCE_RESUME
    db_session.add(test_resume)
    db_session.commit()

    optimized = _seed_optimized(db_session, test_user, test_resume, java_job, ALLCAPS_EXPERIENCE_RESUME)

    marker_summary = "Now also skilled in cloud-native SDET practices."
    marker_bullet = "Built a brand-new UNIQUE_MANUAL_BULLET_EDIT_MARKER regression harness."
    edited = ALLCAPS_EXPERIENCE_RESUME.replace(
        "SDET with 6+ years of automation experience across Java and Selenium stacks.",
        "SDET with 6+ years of automation experience across Java and Selenium stacks. " + marker_summary,
    ).replace(
        "- Engineered end-to-end Selenium/TestNG regression suites integrated into CI/CD.",
        f"- {marker_bullet}",
    )

    patch_response = client.patch(
        f"/api/optimizer/resume/{test_user.id}/{java_job.id}",
        json={
            "optimized_resume_id": optimized.id,
            "edited_resume_text": edited,
            "action": "autosave",
            "persist": True,
            "skip_score": True,
        },
    )
    assert patch_response.status_code == 200

    db_session.refresh(optimized)
    assert optimized.validation_status in {"passed", "passed_with_warnings"}

    docx_response = client.get(
        f"/api/optimizer/resume/{test_user.id}/{java_job.id}/{optimized.id}/download?format=docx"
    )
    assert docx_response.status_code == 200
    from io import BytesIO

    loaded = Document(BytesIO(docx_response.content))
    text = "\n".join(p.text for p in loaded.paragraphs)
    assert marker_summary in text
    assert marker_bullet in text


# --- Part 12: safe diagnostics on failure, no raw resume text leaked -------


def test_section_order_failure_logs_diagnostics_without_leaking_resume_text(caplog):
    original_structure = {
        "sections": [
            {"heading": "Professional Summary", "content": "SECRET_ORIGINAL_SUMMARY_TEXT"},
            {"heading": "Technical Skills", "content": "x"},
            {"heading": "Professional Experience", "content": "x"},
        ]
    }
    generated_sections = [
        {"heading": "Professional Experience", "content": "SECRET_GENERATED_EXPERIENCE_TEXT"},
        {"heading": "Professional Summary", "content": "x"},
        {"heading": "Technical Skills", "content": "x"},
    ]
    with caplog.at_level(logging.WARNING, logger="app.services.final_resume_validator"):
        result = validate_final_resume(
            original_text="Jane Doe SECRET_ORIGINAL_SUMMARY_TEXT",
            generated_text="Jane Doe SECRET_GENERATED_EXPERIENCE_TEXT",
            original_structure=original_structure,
            generated_sections=generated_sections,
            unsupported_keywords=[],
            selected_technologies=[],
            workflow_id=4242,
        )
    assert result["status"] == "failed"
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "expected_sections=" in log_text
    assert "actual_sections=" in log_text
    assert "first_mismatch_index=" in log_text
    assert "workflow_id=4242" in log_text
    assert "SECRET_ORIGINAL_SUMMARY_TEXT" not in log_text
    assert "SECRET_GENERATED_EXPERIENCE_TEXT" not in log_text
