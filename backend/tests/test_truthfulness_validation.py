"""Truthfulness validation diagnostics and confirmed-evidence allowance tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.ats_resume_generation_service import (
    GenerationValidationError,
    _build_generation_payload,
    _normalize_evidence_id,
    _user_evidence_claims,
    _validate_llm_result,
    generate_with_llm_or_fallback,
)
from app.services.final_resume_validator import validate_final_resume
from app.services.keyword_injection_service import plan_keyword_injection
from app.services.llm_client import LLMClient
from app.services.resume_structure_parser import parse_resume_structure


JOB = SimpleNamespace(
    title="SDET",
    company="Acme",
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


def test_evidence_claim_ids_align_with_evidence_claims(java_resume_text):
    structure = parse_resume_structure(java_resume_text)
    plan = plan_keyword_injection(
        java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [],
    )
    payload = _build_generation_payload(
        resume_text=java_resume_text,
        job=JOB,
        injection_plan=plan,
        structure=structure,
        confirmed_evidence={
            "Azure": {
                "evidence_status": "USER_CONFIRMED_PROFESSIONAL",
                "usage_description": "Used Azure pipelines.",
            },
            "azure": {
                "evidence_status": "USER_CONFIRMED_PROFESSIONAL",
                "usage_description": "Used Azure pipelines.",
            },
        },
        denied_skills=["Appium", "Cucumber"],
        verified_resume_skills=["Java"],
        correction_instructions=None,
    )
    assert set(payload["evidence_claim_ids"]) == set(payload["evidence_claims"])
    assert list(payload["accepted_user_evidence"].keys()) == ["UC1"]
    assert payload["accepted_user_evidence"]["UC1"]["skill"] in {"Azure", "azure"}


def test_normalize_evidence_id_case_and_zero_padding():
    assert _normalize_evidence_id("e01") == "E1"
    assert _normalize_evidence_id("UC02") == "UC2"
    assert _normalize_evidence_id(" E3 ") == "E3"


def test_original_resume_fact_accepted(java_resume_text):
    result = _validate_llm_result(
        {
            "generated_resume_plain_text": java_resume_text + "\nImproved Java summary for SDET roles.",
            "rewritten_bullets": [{"text": "Built Java APIs.", "supported_by": ["E1"]}],
            "factual_preservation_confirmed": True,
        },
        resume_text=java_resume_text,
        valid_evidence_ids={"E1", "UC1"},
    )
    assert result["rewritten_bullets"][0]["supported_by"] == ["E1"]


def test_user_confirmed_professional_evidence_accepted(java_resume_text):
    optimized = (
        java_resume_text
        + "\nAzure: Implemented cloud test environments using Azure.\n"
        "JMeter: Designed load tests with JMeter."
    )
    result = _validate_llm_result(
        {
            "generated_resume_plain_text": optimized,
            "rewritten_bullets": [
                {"text": "Implemented Azure test environments.", "supported_by": ["UC1"]},
                {"text": "Designed JMeter load tests.", "supported_by": ["uc02"]},
            ],
            "factual_preservation_confirmed": True,
        },
        resume_text=java_resume_text,
        valid_evidence_ids={"E1", "UC1", "UC2"},
        allowed_confirmed_skills=["Azure", "JMeter"],
        confirmed_evidence_text="Used Azure professionally. Used JMeter professionally.",
        unsupported_keywords=["Appium", "Cucumber"],
        denied_skills=["Appium", "Cucumber"],
    )
    assert result["rewritten_bullets"][1]["supported_by"] == ["UC2"]


def test_rejected_appium_blocked(java_resume_text):
    with pytest.raises(GenerationValidationError, match="Appium"):
        _validate_llm_result(
            {
                "generated_resume_plain_text": java_resume_text + "\nBuilt Appium mobile automation suites.",
                "factual_preservation_confirmed": True,
            },
            resume_text=java_resume_text,
            valid_evidence_ids={"E1"},
            denied_skills=["Appium"],
            unsupported_keywords=["Appium", "Cucumber"],
        )


def test_rejected_cucumber_blocked(java_resume_text):
    with pytest.raises(GenerationValidationError, match="Cucumber"):
        _validate_llm_result(
            {
                "generated_resume_plain_text": java_resume_text + "\nCreated Cucumber BDD frameworks.",
                "factual_preservation_confirmed": True,
            },
            resume_text=java_resume_text,
            valid_evidence_ids={"E1"},
            denied_skills=["Cucumber"],
            unsupported_keywords=["Appium", "Cucumber"],
        )


def test_invented_metric_blocked():
    original = "Jane Doe\nJava developer.\nBuilt Selenium automation for web applications."
    with pytest.raises(GenerationValidationError, match="metrics"):
        _validate_llm_result(
            {
                "generated_resume_plain_text": original + "\nIncreased test coverage by 45%.",
                "factual_preservation_confirmed": True,
            },
            resume_text=original,
            valid_evidence_ids={"E1"},
        )


def test_invented_certification_blocked_by_final_validator(java_resume_text):
    structure = parse_resume_structure(java_resume_text)
    structure["sections"] = [
        {"heading": "Certifications", "content": "AWS Certified Developer"},
    ]
    result = validate_final_resume(
        original_text=java_resume_text,
        generated_text=java_resume_text.replace(
            "AWS Certified Developer",
            "Google Professional Cloud Architect",
        ) if "AWS Certified Developer" in java_resume_text else (
            java_resume_text + "\n\nCertifications\nGoogle Professional Cloud Architect"
        ),
        original_structure=structure,
        generated_sections=[{"heading": "Certifications", "content": "Google Professional Cloud Architect"}],
        unsupported_keywords=[],
        selected_technologies=["Java"],
    )
    assert result["status"] == "failed"
    assert any("Protected section modified" in err for err in result["errors"])


def test_invented_employer_blocked_by_final_validator(java_resume_text):
    structure = parse_resume_structure(java_resume_text)
    if not structure.get("employments"):
        structure["employments"] = [{"company": "Acme Corp", "dates": "2020-2024", "bullets": ["Built APIs"]}]
    company = structure["employments"][0]["company"]
    result = validate_final_resume(
        original_text=java_resume_text,
        generated_text=(java_resume_text or "").replace(company, "Totally Fake Employer LLC"),
        original_structure=structure,
        generated_sections=[],
        unsupported_keywords=[],
        selected_technologies=["Java"],
    )
    assert result["status"] == "failed"
    assert any("Company name missing" in err for err in result["errors"])


def test_normalized_case_insensitive_confirmed_skill_accepted():
    result = validate_final_resume(
        original_text="Jane Doe\nJava developer with Selenium.",
        generated_text="Jane Doe\nJava developer with Selenium and azure devops pipelines.",
        original_structure={"name": "Jane Doe", "employments": [], "sections": []},
        generated_sections=[],
        unsupported_keywords=["Azure"],
        selected_technologies=["Java", "Azure"],
        allowed_confirmed_skills=["azure"],
    )
    assert result["status"] in {"passed", "passed_with_warnings"}
    assert not any("Unsupported technology introduced: Azure" in err for err in result["errors"])


def test_supported_scrum_methodology_passes_final_validation():
    result = validate_final_resume(
        original_text="Jane Doe\nWorked in Agile/Scrum teams and participated in sprint planning.",
        generated_text="Jane Doe\nWorked within Agile/Scrum teams, participating in sprint planning and iterative delivery.",
        original_structure={"name": "Jane Doe", "employments": [], "sections": []},
        generated_sections=[],
        unsupported_keywords=["Scrum"],
        selected_technologies=[],
    )
    assert result["status"] in {"passed", "passed_with_warnings"}
    assert not result["errors"]


def test_indirect_scrum_signal_requires_confirmed_evidence():
    result = validate_final_resume(
        original_text="Jane Doe\nParticipated in Agile sprint planning and retrospectives.",
        generated_text="Jane Doe\nWorked within Scrum teams and participated in sprint planning.",
        original_structure={"name": "Jane Doe", "employments": [], "sections": []},
        generated_sections=[],
        unsupported_keywords=["Scrum"],
        selected_technologies=[],
    )
    assert "Unsupported methodology introduced: Scrum" in result["errors"]


def test_confirmed_scrum_methodology_passes_final_validation():
    result = validate_final_resume(
        original_text="Jane Doe\nParticipated in Agile sprint planning and retrospectives.",
        generated_text="Jane Doe\nWorked within Scrum teams and participated in sprint planning.",
        original_structure={"name": "Jane Doe", "employments": [], "sections": []},
        generated_sections=[],
        unsupported_keywords=["Scrum"],
        selected_technologies=[],
        allowed_confirmed_skills=["Scrum methodology"],
    )
    assert result["status"] in {"passed", "passed_with_warnings"}
    assert not result["errors"]


def test_unsupported_go_remains_a_technology_error():
    result = validate_final_resume(
        original_text="Jane Doe\nJava developer.",
        generated_text="Jane Doe\nJava developer building Go services.",
        original_structure={"name": "Jane Doe", "employments": [], "sections": []},
        generated_sections=[],
        unsupported_keywords=["Go"],
        selected_technologies=[],
    )
    assert "Unsupported language introduced: Go" in result["errors"]


def test_scrum_participation_does_not_authorize_scrum_master_role():
    result = validate_final_resume(
        original_text="Jane Doe\nWorked in Agile/Scrum teams and participated in sprint planning.",
        generated_text="Jane Doe\nServed as Scrum Master for Agile teams and sprint planning.",
        original_structure={"name": "Jane Doe", "employments": [], "sections": []},
        generated_sections=[],
        unsupported_keywords=["Scrum"],
        selected_technologies=[],
    )
    assert "Unsupported professional role introduced: Scrum Master" in result["errors"]


def test_valid_paraphrasing_does_not_false_reject(java_resume_text):
    paraphrased = java_resume_text.replace("Developed", "Built").replace("developed", "built")
    if paraphrased == java_resume_text:
        paraphrased = java_resume_text + "\nDelivered additional Java test automation support."
    result = _validate_llm_result(
        {
            "generated_resume_plain_text": paraphrased,
            "rewritten_bullets": [{"text": "Built Java services.", "supported_by": ["E1"]}],
            "factual_preservation_confirmed": True,
        },
        resume_text=java_resume_text,
        valid_evidence_ids={"E1"},
        unsupported_keywords=["Appium", "Cucumber"],
        denied_skills=["Appium", "Cucumber"],
        allowed_confirmed_skills=["Azure", "JMeter"],
    )
    assert "generated_resume_plain_text" in result or result.get("rewritten_bullets")


def test_end_to_end_confirmed_evidence_keeps_llm_method(java_resume_text):
    structure = parse_resume_structure(java_resume_text)
    plan = plan_keyword_injection(
        java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [],
    )
    plan["unsupportedKeywords"] = ["Appium", "Cucumber"]
    llm_json = {
        "generated_resume_plain_text": (
            java_resume_text
            + "\nAzure professional experience with cloud test environments."
            + "\nJMeter load testing for APIs."
        ),
        "rewritten_bullets": [
            {"text": "Supported Azure environments.", "supported_by": ["UC1"]},
            {"text": "Executed JMeter suites.", "supported_by": ["UC2"]},
        ],
        "factual_preservation_confirmed": True,
    }
    confirmed = {
        "Azure": {
            "evidence_status": "USER_CONFIRMED_PROFESSIONAL",
            "usage_description": "Used Azure for test environments.",
            "skill": "Azure",
        },
        "JMeter": {
            "evidence_status": "USER_CONFIRMED_PROFESSIONAL",
            "usage_description": "Used JMeter for load testing.",
            "skill": "JMeter",
        },
    }
    with patch.object(_client().__class__, "generate_json", return_value=llm_json):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text,
            job=JOB,
            injection_plan=plan,
            structure=structure,
            confirmed_evidence=confirmed,
            denied_skills=["Appium", "Cucumber"],
            client=_client(),
            workflow_id=170,
        )
    assert result["generation_method"] == "llm"
    assert result["fallback_used"] is False


def test_user_evidence_claims_exclude_rejected():
    claims = _user_evidence_claims({
        "Azure": {"evidence_status": "USER_CONFIRMED_PROFESSIONAL", "usage_description": "ok"},
        "Appium": {"evidence_status": "REJECTED", "usage_description": "no"},
        "Cucumber": {"evidence_status": "REJECTED", "usage_description": "no"},
    })
    assert list(claims.keys()) == ["UC1"]
    assert claims["UC1"]["skill"] == "Azure"
