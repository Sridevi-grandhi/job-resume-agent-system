"""Regression tests for Step 5 score consistency, evidence, assembly, and export."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models.skill_confirmation_model import UserConfirmedSkill
from app.routes.optimizer_edit_routes import _score_payload
from app.services.ats_scoring import calculate_weighted_ats_score
from app.services.resume_assembly_service import _pick_summary, assemble_optimized_resume
from app.services.resume_document_service import (
    dedupe_experience_employers,
    plain_text_to_document,
    sanitize_resume_public_text,
)
from app.services.skill_confirmation_service import (
    build_effective_evidence_decisions,
    to_scoring_evidence,
)


JAVA_JOB = SimpleNamespace(
    title="Senior SDET",
    company="Acme",
    description=(
        "Senior SDET with Java, Selenium, JMeter, Appium, Cucumber, Azure, "
        "and TestNG for API/UI automation."
    ),
    skills_required="Java, Selenium, JMeter, Appium, Cucumber, Azure, TestNG",
)

BASE_RESUME = """
Jane Developer
jane@example.com | 555-111-2222

Professional Summary
Java SDET with Selenium and TestNG automation experience across enterprise delivery.

Technical Skills
Languages: Java
Testing: Selenium, TestNG
Cloud: AWS
Tools: Jenkins, Git

Professional Experience
Acme Corp | SDET | 2020 - Present
- Built Java Selenium suites for regression testing.
- Automated API checks with TestNG.

Education
B.S. Computer Science
""".strip()


def _row(skill: str, status: str, source: str = "user_selection") -> UserConfirmedSkill:
    row = UserConfirmedSkill(
        user_id=1,
        job_id=1,
        resume_id=47,
        skill_name=skill,
        normalized_skill_name=skill.lower(),
        evidence_status=status,
        evidence_source=source,
        evidence_type="Current employment",
        usage_description=f"Professional experience with {skill}.",
        is_active=True,
    )
    return row


def test_top_score_equals_panel_score_source():
    """Banner and panel must share edited_score / match_percentage."""
    confirmed = to_scoring_evidence([
        _row("Azure", "USER_CONFIRMED_PROFESSIONAL"),
        _row("JMeter", "USER_CONFIRMED_PROFESSIONAL"),
        _row("Appium", "REJECTED"),
        _row("Cucumber", "REJECTED"),
    ])
    text = BASE_RESUME + "\nCloud: Azure\n- Performed load testing with JMeter."
    score = calculate_weighted_ats_score(
        text,
        JAVA_JOB,
        confirmed_skills=confirmed,
        original_resume_text=BASE_RESUME,
        denied_skills=["Appium", "Cucumber"],
        evidence_gated=True,
    )
    view = _score_payload(score, build_effective_evidence_decisions([
        _row("Azure", "USER_CONFIRMED_PROFESSIONAL"),
        _row("JMeter", "USER_CONFIRMED_PROFESSIONAL"),
        _row("Appium", "REJECTED"),
        _row("Cucumber", "REJECTED"),
    ]))
    assert score["match_percentage"] == view["jd_match"]


def test_azure_jmeter_remain_confirmed_appium_cucumber_rejected():
    rows = [
        _row("Azure", "USER_CONFIRMED_PROFESSIONAL"),
        _row("JMeter", "USER_CONFIRMED_PROFESSIONAL"),
        _row("Appium", "REJECTED"),
        _row("Cucumber", "REJECTED"),
    ]
    confirmed = to_scoring_evidence(rows)
    with_manual = BASE_RESUME + "\nCloud: Azure, Appium, Cucumber\n- Used JMeter for load testing."
    score = calculate_weighted_ats_score(
        with_manual,
        JAVA_JOB,
        confirmed_skills=confirmed,
        original_resume_text=BASE_RESUME,
        denied_skills=["Appium", "Cucumber"],
        evidence_gated=True,
    )
    decisions = build_effective_evidence_decisions(rows)
    view = _score_payload(score, decisions)
    warnings = " ".join(view.get("skill_credit_warnings") or [])
    assert "Azure" not in warnings or "Unverified" not in warnings
    assert "JMeter" not in warnings or "Unverified" not in warnings
    assert any("Appium" in w and "Rejected" in w for w in view.get("skill_credit_warnings") or [])
    assert any("Cucumber" in w and "Rejected" in w for w in view.get("skill_credit_warnings") or [])
    assert "Azure" in (score.get("confirmed_present_requirements") or score.get("confirmed_skills_used") or [])


def test_legacy_evidence_source_still_counts_for_scoring():
    rows = [_row("Azure", "USER_CONFIRMED_PROFESSIONAL", source="")]
    scoring = to_scoring_evidence(rows)
    assert scoring, "Empty evidence_source must not drop USER_CONFIRMED rows"


def test_rejected_cucumber_does_not_change_jd_match():
    denied = ["Cucumber"]
    baseline = calculate_weighted_ats_score(
        BASE_RESUME,
        JAVA_JOB,
        original_resume_text=BASE_RESUME,
        denied_skills=denied,
        evidence_gated=True,
    )
    with_cuke = BASE_RESUME.replace("TestNG", "TestNG, Cucumber")
    after = calculate_weighted_ats_score(
        with_cuke,
        JAVA_JOB,
        original_resume_text=BASE_RESUME,
        denied_skills=denied,
        evidence_gated=True,
    )
    assert after["match_percentage"] == baseline["match_percentage"]


def test_single_optimized_professional_summary():
    summary = _pick_summary(
        original_content="Old paragraph one. Old paragraph two. Old paragraph three.",
        rewritten_summary="Senior SDET with Java and Selenium expertise.",
        llm_sections={"summary": "Senior SDET with Java and Selenium expertise."},
    )
    assert "Old paragraph" not in summary
    assert summary.count(".") <= 5


def test_assembly_summary_replaces_instead_of_appending():
    assembled = assemble_optimized_resume(
        resume_text=BASE_RESUME,
        structure=None,
        llm_result={
            "rewritten_summary": "Concise optimized summary for ATS alignment.",
            "rewritten_bullets": [],
            "generated_resume_plain_text": "",
        },
        confirmed_evidence={},
        denied_skills=["Appium", "Cucumber"],
    )
    plain = assembled["plain_text"]
    assert plain.count("Concise optimized summary") == 1
    assert "Java SDET with Selenium and TestNG automation experience across enterprise delivery." not in plain


def test_sanitize_removes_internal_labels_and_client_status_meetings():
    dirty = (
        "Professional Summary\n"
        "Strong SDET (USER CONFIRMED)\n"
        "USER_CONFIRMED_PROFESSIONAL\n"
        "Client: status meetings.\n"
        "- Built Java Selenium suites."
    )
    clean = sanitize_resume_public_text(dirty)
    assert "USER CONFIRMED" not in clean
    assert "USER_CONFIRMED" not in clean
    assert "status meetings" not in clean.lower()
    assert "Built Java Selenium" in clean


def test_sanitize_removes_editor_ui_labels_before_download():
    """Regression: none of the resume-workspace editor's action labels or
    inline change badges may leak into the downloaded DOCX/PDF content."""
    dirty = (
        "Professional Experience\n"
        "Acme Corp | SDET | 2020 - Present\n"
        "- Built Java Selenium regression suites.\n"
        "✨ AI Improved\n"
        "✨ AI Generated\n"
        "Review\n"
        "AI Improve\n"
        "✓ Accepted\n"
        "Accept\n"
        "Reject\n"
        "Try Again\n"
        "Delete\n"
        "Edited manually\n"
        "No change recommended.\n"
        "- Automated REST Assured API validation."
    )
    clean = sanitize_resume_public_text(dirty)
    for label in [
        "AI Improved", "AI Generated", "Review", "AI Improve", "Accepted", "Accept", "Reject",
        "Try Again", "Delete", "Edited manually", "No change recommended",
    ]:
        assert label not in clean, f"{label!r} leaked into sanitized download text"
    assert "Built Java Selenium regression suites." in clean
    assert "Automated REST Assured API validation." in clean


def test_sanitize_does_not_strip_ordinary_resume_prose_containing_the_same_words():
    """The editor-label patterns are whole-line matches only — a normal
    resume bullet that happens to use words like "review"/"reject"/"delete"
    in a full sentence must never be stripped."""
    text = (
        "Professional Experience\n"
        "Acme Corp | Senior Engineer | 2020 - Present\n"
        "- Conducted code review sessions for a team of 5 engineers.\n"
        "- Automated cleanup jobs to delete stale build artifacts nightly.\n"
        "- Investigated and helped reject invalid vendor invoices during audits.\n"
    )
    clean = sanitize_resume_public_text(text)
    assert "Conducted code review sessions" in clean
    assert "delete stale build artifacts" in clean
    assert "helped reject invalid vendor invoices" in clean


def test_dedupe_experience_employers():
    merged = dedupe_experience_employers([
        {"company": "Acme Corp", "bullets": ["Built suites."]},
        {"company": "Acme Corp", "bullets": ["Built suites.", "Automated APIs."]},
        {"company": "Beta LLC", "bullets": ["Led team."]},
    ])
    assert len(merged) == 2
    acme = next(item for item in merged if item["company"] == "Acme Corp")
    assert len(acme["bullets"]) == 2


def test_professional_summary_editable_in_document_model():
    text = BASE_RESUME.replace(
        "Java SDET with Selenium",
        "Edited summary for persistence test.",
    )
    doc = plain_text_to_document(text)
    summary = next(section for section in doc["sections"] if section["type"] == "summary")
    assert "Edited summary" in summary["content"]


def test_ai_and_deterministic_scores_may_match():
    score_a = calculate_weighted_ats_score(BASE_RESUME, JAVA_JOB, evidence_gated=False)
    score_b = calculate_weighted_ats_score(BASE_RESUME, JAVA_JOB, evidence_gated=False)
    assert score_a["match_percentage"] == score_b["match_percentage"]
