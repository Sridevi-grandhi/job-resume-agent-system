"""Deterministic protected-field resume assembly tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.ats_resume_generation_service import generate_with_llm_or_fallback
from app.services.final_resume_validator import validate_final_resume
from app.services.keyword_injection_service import plan_keyword_injection
from app.services.llm_client import LLMClient
from app.services.resume_assembly_service import (
    assemble_optimized_resume,
    normalize_section_heading,
)
from app.services.resume_structure_parser import parse_resume_structure


SAMPLE = """Jane Doe
jane.doe@email.com
(555) 111-2222
https://linkedin.com/in/janedoe

Professional Summary
Java developer with Selenium automation experience.

Technical Skills
Java, Selenium, TestNG, REST Assured

Professional Experience
Acme Corp | SDET | 2020 - Present
- Developed Java Selenium suites for web applications.
- Built API checks with REST Assured.

Education
B.S. Computer Science, State University, 2018

Certifications
ISTQB Foundation
"""


JOB = SimpleNamespace(
    title="SDET",
    company="Target Co",
    description="Java Selenium Azure JMeter Appium Cucumber role.",
    skills_required="Java, Selenium, Azure, JMeter, Appium, Cucumber",
)


def _client():
    return LLMClient(config={
        "provider": "openai",
        "model": "gpt-5-mini",
        "api_key": "sk-test",
        "base_url": "https://api.openai.com/v1",
        "timeout": 5,
        "max_retries": 0,
        "temperature": 0.1,
        "enabled": True,
        "prompt_version": "ats-optimizer-v2",
    })


def _confirmed():
    return {
        "Azure": {
            "evidence_status": "USER_CONFIRMED_PROFESSIONAL",
            "evidence_type": "Current employment",
            "employer_name": "Acme Corp",
            "usage_description": "Used Azure DevOps pipelines for test environments.",
            "skill": "Azure",
        },
        "JMeter": {
            "evidence_status": "USER_CONFIRMED_PROFESSIONAL",
            "evidence_type": "Current employment",
            "employer_name": "Acme Corp",
            "usage_description": "Designed JMeter load tests for APIs.",
            "skill": "JMeter",
        },
    }


def test_llm_omits_name_restored():
    structure = parse_resume_structure(SAMPLE)
    assembled = assemble_optimized_resume(
        resume_text=SAMPLE,
        structure=structure,
        llm_result={
            "generated_resume_plain_text": (
                "Professional Summary\nStrong SDET profile.\n\n"
                "Technical Skills\nJava, Selenium\n\n"
                "Professional Experience\nSomeone Else | SDET | 2020 - Present\n- Built tests.\n"
            ),
            "rewritten_summary": "SDET focused on Java Selenium quality engineering.",
            "rewritten_bullets": [{"text": "Built reliable Java Selenium suites.", "supported_by": ["E1"]}],
        },
    )
    assert "Jane Doe" in assembled["plain_text"]
    result = validate_final_resume(
        original_text=SAMPLE,
        generated_text=assembled["plain_text"],
        original_structure=structure,
        generated_sections=assembled["sections"],
        unsupported_keywords=["Appium", "Cucumber"],
        selected_technologies=["Java", "Selenium"],
        allowed_confirmed_skills=["Azure", "JMeter"],
        confirmed_evidence_text="Azure JMeter",
    )
    assert result["status"] in {"passed", "passed_with_warnings"}


def test_llm_changes_name_restored():
    structure = parse_resume_structure(SAMPLE)
    assembled = assemble_optimized_resume(
        resume_text=SAMPLE,
        structure=structure,
        llm_result={
            "generated_resume_plain_text": SAMPLE.replace("Jane Doe", "Totally Fake Name"),
            "rewritten_summary": "Quality engineer specializing in automation.",
            "rewritten_bullets": [],
        },
    )
    assert "Jane Doe" in assembled["plain_text"]
    assert "Totally Fake Name" not in assembled["plain_text"].split("Professional Summary")[0]


@pytest.mark.parametrize("field,original,changed", [
    ("email", "jane.doe@email.com", "hacker@evil.test"),
    ("phone", "(555) 111-2222", "(999) 000-0000"),
])
def test_llm_omits_or_changes_contact_restored(field, original, changed):
    structure = parse_resume_structure(SAMPLE)
    omitted = SAMPLE.replace(original, "")
    assembled_omit = assemble_optimized_resume(
        resume_text=SAMPLE,
        structure=structure,
        llm_result={"generated_resume_plain_text": omitted, "rewritten_summary": "Rewritten summary for SDET."},
    )
    assert original in assembled_omit["plain_text"]

    assembled_change = assemble_optimized_resume(
        resume_text=SAMPLE,
        structure=structure,
        llm_result={
            "generated_resume_plain_text": SAMPLE.replace(original, changed),
            "rewritten_summary": "Rewritten summary for SDET roles.",
        },
    )
    assert original in assembled_change["plain_text"]
    # Changed value may appear in body only if identical substring; contact header uses original.
    assert assembled_change["plain_text"].startswith("Jane Doe") or original in assembled_change["plain_text"]


def test_llm_changes_employer_and_dates_preserved():
    structure = parse_resume_structure(SAMPLE)
    company = structure["employments"][0]["company"]
    dates = structure["employments"][0]["dates"]
    assembled = assemble_optimized_resume(
        resume_text=SAMPLE,
        structure=structure,
        llm_result={
            "generated_resume_plain_text": SAMPLE.replace(company, "Fake Employer LLC").replace(dates, "1999 - 2001"),
            "rewritten_summary": "Automation engineer with strong delivery focus.",
            "rewritten_bullets": [{"text": "Owned end-to-end Java Selenium delivery.", "supported_by": ["E1"]}],
        },
    )
    assert company in assembled["plain_text"]
    assert dates in assembled["plain_text"]
    assert "Fake Employer LLC" not in assembled["plain_text"]
    assert "Owned end-to-end Java Selenium delivery." in assembled["plain_text"]


def test_llm_reorders_sections_original_order_restored():
    structure = parse_resume_structure(SAMPLE)
    reordered = """Jane Doe
jane.doe@email.com
(555) 111-2222

Education
B.S. Computer Science, State University, 2018

Professional Experience
Acme Corp | SDET | 2020 - Present
- Developed Java Selenium suites for web applications.

Professional Summary
Totally reordered summary.

Technical Skills
Java, Selenium
"""
    assembled = assemble_optimized_resume(
        resume_text=SAMPLE,
        structure=structure,
        llm_result={
            "generated_resume_plain_text": reordered,
            "rewritten_summary": "SDET summary rewritten for ATS alignment.",
        },
    )
    headings = [s["heading"] for s in assembled["sections"]]
    assert headings == structure["section_order"]


def test_heading_case_variation_handled():
    assert normalize_section_heading("PROFESSIONAL SUMMARY:") == normalize_section_heading("Professional Summary")
    structure = parse_resume_structure(SAMPLE)
    generated_sections = [
        {"heading": h.upper() + ":", "content": s["content"]}
        for h, s in zip(structure["section_order"], structure["sections"])
    ]
    # Use assembled plain with original contact/employers.
    assembled = assemble_optimized_resume(
        resume_text=SAMPLE,
        structure=structure,
        llm_result={"generated_resume_plain_text": SAMPLE, "rewritten_summary": "Case-safe summary rewrite."},
    )
    result = validate_final_resume(
        original_text=SAMPLE,
        generated_text=assembled["plain_text"],
        original_structure=structure,
        generated_sections=generated_sections,
        unsupported_keywords=[],
        selected_technologies=["Java"],
    )
    assert "Section order or headings were not preserved." not in result["errors"]


def test_confirmed_azure_jmeter_accepted_rejected_excluded():
    structure = parse_resume_structure(SAMPLE)
    assembled = assemble_optimized_resume(
        resume_text=SAMPLE,
        structure=structure,
        llm_result={
            "generated_resume_plain_text": SAMPLE,
            "rewritten_summary": "SDET with cloud and performance testing depth.",
            "rewritten_bullets": [
                {"text": "Delivered Java Selenium regression coverage.", "supported_by": ["E1"]},
            ],
        },
        confirmed_evidence=_confirmed(),
        denied_skills=["Appium", "Cucumber"],
        unsupported_keywords=["Appium", "Cucumber"],
    )
    plain = assembled["plain_text"]
    assert "Azure" in plain
    assert "JMeter" in plain
    assert "Appium" not in plain
    assert "Cucumber" not in plain
    result = validate_final_resume(
        original_text=SAMPLE,
        generated_text=plain,
        original_structure=structure,
        generated_sections=assembled["sections"],
        unsupported_keywords=["Appium", "Cucumber"],
        selected_technologies=["Java", "Azure", "JMeter"],
        allowed_confirmed_skills=["Azure", "JMeter"],
        confirmed_evidence_text="Used Azure DevOps. Designed JMeter load tests.",
    )
    assert result["status"] in {"passed", "passed_with_warnings"}
    assert not any("Appium" in e or "Cucumber" in e for e in result["errors"])


def test_confirmed_skill_with_only_definitional_description_stays_out_of_experience():
    """Regression: a confirmed skill whose free-text answer is a textbook
    definition (e.g. "Scrum is an Agile methodology used in sprints.")
    must never become a Professional Experience bullet — it should still
    reach the resume (Technical Skills / summary keyword placement), just
    not as a fabricated experience claim."""
    structure = parse_resume_structure(SAMPLE)
    confirmed = {
        "Scrum": {
            "evidence_status": "USER_CONFIRMED_PROFESSIONAL",
            "evidence_type": "Current employment",
            "employer_name": "Acme Corp",
            "usage_description": "Scrum is an Agile methodology/framework used to manage sprints.",
            "skill": "Scrum",
        },
    }
    assembled = assemble_optimized_resume(
        resume_text=SAMPLE,
        structure=structure,
        llm_result={
            "generated_resume_plain_text": SAMPLE,
            "rewritten_summary": "SDET with Agile delivery experience.",
            "rewritten_bullets": [],
        },
        confirmed_evidence=confirmed,
    )
    plain = assembled["plain_text"]
    assert "is an Agile methodology" not in plain
    assert "Scrum is" not in plain


def test_llm_proposed_definitional_bullet_rejected_original_kept():
    """Regression: even an LLM-proposed rewrite must be rejected if it reads
    as a technology definition — the existing real bullet at that position
    must be left untouched, not overwritten with a worse one."""
    structure = parse_resume_structure(SAMPLE)
    assembled = assemble_optimized_resume(
        resume_text=SAMPLE,
        structure=structure,
        llm_result={
            "generated_resume_plain_text": SAMPLE,
            "rewritten_summary": "SDET with Selenium automation experience.",
            "rewritten_bullets": [
                {"text": "Selenium is a browser automation tool used for testing.", "supported_by": ["E1"]},
            ],
        },
    )
    plain = assembled["plain_text"]
    assert "Selenium is a browser automation tool" not in plain
    # The original, real bullet at that position must still be present.
    assert "Developed Java Selenium suites for web applications." in plain


def test_llm_proposed_genuine_rewrite_still_applied():
    structure = parse_resume_structure(SAMPLE)
    assembled = assemble_optimized_resume(
        resume_text=SAMPLE,
        structure=structure,
        llm_result={
            "generated_resume_plain_text": SAMPLE,
            "rewritten_summary": "SDET with Selenium automation experience.",
            "rewritten_bullets": [
                {"text": "Engineered scalable Java Selenium regression suites for web applications.", "supported_by": ["E1"]},
            ],
        },
    )
    plain = assembled["plain_text"]
    assert "Engineered scalable Java Selenium regression suites" in plain


def test_invented_metric_still_blocked():
    structure = parse_resume_structure(SAMPLE)
    assembled = assemble_optimized_resume(
        resume_text=SAMPLE,
        structure=structure,
        llm_result={
            "generated_resume_plain_text": SAMPLE,
            "rewritten_summary": "Improved release quality and increased by 45 percent outcomes.",
            "rewritten_bullets": [],
        },
    )
    result = validate_final_resume(
        original_text=SAMPLE,
        generated_text=assembled["plain_text"],
        original_structure=structure,
        generated_sections=assembled["sections"],
        unsupported_keywords=[],
        selected_technologies=["Java"],
    )
    assert result["status"] == "failed"
    assert any("metrics" in e.lower() for e in result["errors"])


def test_invented_certification_still_blocked():
    structure = parse_resume_structure(SAMPLE)
    bad_sections = []
    for section in structure["sections"]:
        if "certif" in (section.get("heading") or "").lower():
            bad_sections.append({
                "heading": section["heading"],
                "content": "Google Professional Cloud Architect",
            })
        else:
            bad_sections.append(dict(section))
    result = validate_final_resume(
        original_text=SAMPLE,
        generated_text=SAMPLE + "\nGoogle Professional Cloud Architect",
        original_structure=structure,
        generated_sections=bad_sections,
        unsupported_keywords=[],
        selected_technologies=["Java"],
    )
    assert result["status"] == "failed"
    assert any("Protected section modified" in e for e in result["errors"])


def test_invented_employer_still_blocked_without_assembly():
    structure = parse_resume_structure(SAMPLE)
    company = structure["employments"][0]["company"]
    result = validate_final_resume(
        original_text=SAMPLE,
        generated_text=SAMPLE.replace(company, "Totally Fake Employer LLC"),
        original_structure=structure,
        generated_sections=structure["sections"],
        unsupported_keywords=[],
        selected_technologies=["Java"],
    )
    assert result["status"] == "failed"
    assert any("Company name missing" in e for e in result["errors"])


def test_successful_final_validation_returns_generation_method_llm():
    structure = parse_resume_structure(SAMPLE)
    plan = plan_keyword_injection(
        SAMPLE, JOB, structure.get("employments") or [], structure.get("projects") or [],
    )
    plan["unsupportedKeywords"] = ["Appium", "Cucumber"]
    llm_json = {
        "generated_resume_plain_text": (
            "Random Person\nbad@x.test\n\n"
            "Skills\nJava\n\n"
            "Work\nFake Co | 1999\n- did things\n"
        ),
        "rewritten_summary": "SDET delivering Java Selenium quality for enterprise releases.",
        "rewritten_bullets": [
            {"text": "Built maintainable Java Selenium suites for critical flows.", "supported_by": ["E1"]},
            {"text": "Expanded API coverage with REST Assured checks.", "supported_by": ["E2"]},
        ],
        "factual_preservation_confirmed": True,
    }
    with patch.object(_client().__class__, "generate_json", return_value=llm_json):
        result = generate_with_llm_or_fallback(
            resume_text=SAMPLE,
            job=JOB,
            injection_plan=plan,
            structure=structure,
            confirmed_evidence=_confirmed(),
            denied_skills=["Appium", "Cucumber"],
            client=_client(),
            workflow_id=201,
        )
    assert result["generation_method"] == "llm"
    assert result["fallback_used"] is False
    assert result.get("sections")
    assert "Jane Doe" in result["plain_text"]
    assert "jane.doe@email.com" in result["plain_text"]
    assert "(555) 111-2222" in result["plain_text"]
    assert "Acme Corp" in result["plain_text"]
    final = validate_final_resume(
        original_text=SAMPLE,
        generated_text=result["plain_text"],
        original_structure=structure,
        generated_sections=result["sections"],
        unsupported_keywords=["Appium", "Cucumber"],
        selected_technologies=["Java", "Selenium", "Azure", "JMeter"],
        allowed_confirmed_skills=["Azure", "JMeter"],
        confirmed_evidence_text="Azure JMeter",
    )
    assert final["status"] in {"passed", "passed_with_warnings"}, final["errors"]
