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


TWO_EXPERIENCE_SECTIONS = """Jane Doe
jane@example.com

Professional Experience
Acme Corp | 2021 - Present
- Built APIs with Java and Spring Boot.

Career History
Independent Consultant | 2018 - 2020
- Delivered contract projects for small businesses.
"""


def test_two_experience_shaped_sections_each_keep_their_own_employer():
    """Regression: plain_text_to_document used to drain one shared,
    pre-aggregated employments list via a single running index across every
    section classified as "experience" type — an earlier section could
    silently steal entries meant for a later one. Each section must now be
    parsed from its own content only."""
    document = plain_text_to_document(TWO_EXPERIENCE_SECTIONS)
    experience_sections = [s for s in document["sections"] if s["type"] == "experience"]
    assert len(experience_sections) == 2

    companies = [item["company"] for section in experience_sections for item in section["items"]]
    assert companies == ["Acme Corp", "Independent Consultant"]

    acme_section = next(s for s in experience_sections if any(i["company"] == "Acme Corp" for i in s["items"]))
    freelance_section = next(
        s for s in experience_sections if any(i["company"] == "Independent Consultant" for i in s["items"])
    )
    acme_bullets = [b["text"] for item in acme_section["items"] for b in item["bullets"]]
    freelance_bullets = [b["text"] for item in freelance_section["items"] for b in item["bullets"]]
    assert acme_bullets == ["Built APIs with Java and Spring Boot."]
    assert freelance_bullets == ["Delivered contract projects for small businesses."]
