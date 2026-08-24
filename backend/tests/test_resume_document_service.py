"""Structured resume document conversion for the WYSIWYG editor."""

from app.services.resume_document_service import document_to_plain_text, plain_text_to_document


SAMPLE = """Jane Doe
Software Engineer
jane@example.com | 555-0100 | Remote

Professional Summary
Backend engineer with Java and Spring Boot experience.

Technical Skills
Java, Spring Boot, PostgreSQL, Docker

Professional Experience
Senior Engineer | Acme Corp | 2021-Present
- Built APIs with Java and Spring Boot
- Improved deployment reliability with Docker

Projects
Inventory Platform | 2020
- Delivered inventory tracking APIs

Education
B.S. Computer Science
"""


def test_plain_text_round_trip_preserves_core_content():
    document = plain_text_to_document(SAMPLE)
    assert document["header"]["name"] == "Jane Doe"
    types = [section["type"] for section in document["sections"]]
    assert "summary" in types
    assert "skills" in types
    assert "experience" in types
    text = document_to_plain_text(document)
    assert "Jane Doe" in text
    assert "Spring Boot" in text
    assert "Acme Corp" in text
    assert "Inventory Platform" in text


def test_presentation_fields_do_not_require_role_hardcoding():
    document = plain_text_to_document(SAMPLE, template_id="classic-ats", page_size="a4")
    assert document["templateId"] == "classic-ats"
    assert document["pageSize"] == "a4"
    # Content intact after presentation-only expectation
    assert any(section["type"] == "experience" for section in document["sections"])
