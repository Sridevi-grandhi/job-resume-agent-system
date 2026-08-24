"""Tests for app/services/ats_resume_generation_service.py — the LLM-enabled
generation path. LLM calls are always mocked (never real, per project rules)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.services.ats_resume_generation_service import generate_with_llm_or_fallback
from app.services.keyword_injection_service import plan_keyword_injection
from app.services.llm_client import LLMClient, LLMGenerationError
from app.services.resume_structure_parser import parse_resume_structure


JOB = SimpleNamespace(
    title="Java Developer", company="Acme",
    description="Java Spring Boot React PostgreSQL AWS Docker role.",
    skills_required="Java, Spring Boot, React, PostgreSQL, AWS, Docker",
)


def _client(enabled=True):
    return LLMClient(config={
        "provider": "openai", "model": "gpt-4o", "api_key": "sk-test",
        "base_url": "https://api.openai.com/v1", "timeout": 5, "max_retries": 0,
        "temperature": 0.1, "enabled": enabled, "prompt_version": "ats-optimizer-v1",
    })


def _structure(resume_text):
    return parse_resume_structure(resume_text)


def test_llm_success_returns_structured_result(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    llm_json = {
        "generated_resume_plain_text": "Optimized resume body with Java and Spring Boot.",
        "rewritten_summary": "Java engineer.",
        "rewritten_bullets": [{"text": "Built REST APIs with Java.", "supported_by": ["E1"]}],
        "keyword_evidence": [{"keyword": "Java", "support_level": "strong"}],
        "warnings": [],
        "factual_preservation_confirmed": True,
    }
    with patch.object(_client().__class__, "generate_json", return_value=llm_json):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text, job=JOB, injection_plan=plan, structure=structure,
            client=_client(),
        )
    assert result["generation_mode"] == "llm"
    assert result["plain_text"] == llm_json["generated_resume_plain_text"]
    assert result["factual_preservation_confirmed"] is True
    assert result["generation_metadata"] == {
        "generation_method": "llm", "llm_used": True,
        "llm_provider": "openai", "llm_model": "gpt-4o", "llm_service": None,
        "fallback_used": False, "fallback_reason": None,
    }


def test_llm_bullet_with_invalid_evidence_id_falls_back(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    llm_json = {
        "generated_resume_plain_text": "Optimized resume body.",
        "rewritten_bullets": [{"text": "Fabricated claim.", "supported_by": ["E999"]}],
        "factual_preservation_confirmed": True,
    }
    with patch.object(_client().__class__, "generate_json", return_value=llm_json):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text, job=JOB, injection_plan=plan, structure=structure,
            client=_client(),
        )
    assert result["generation_mode"] == "deterministic_fallback"
    assert "LLM generation failed" in result["generation_warnings"][0]


def test_llm_empty_plain_text_falls_back(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    llm_json = {"generated_resume_plain_text": "   ", "factual_preservation_confirmed": True}
    with patch.object(_client().__class__, "generate_json", return_value=llm_json):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text, job=JOB, injection_plan=plan, structure=structure,
            client=_client(),
        )
    assert result["generation_mode"] == "deterministic_fallback"


def test_llm_malformed_response_missing_required_field_falls_back(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    llm_json = {"generated_resume_plain_text": "Optimized body."}  # missing factual_preservation_confirmed
    with patch.object(_client().__class__, "generate_json", return_value=llm_json):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text, job=JOB, injection_plan=plan, structure=structure,
            client=_client(),
        )
    assert result["generation_mode"] == "deterministic_fallback"
    assert "Optimized" not in result["plain_text"] or "jane@example.com" in result["plain_text"]


def test_llm_exception_during_dispatch_falls_back(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    with patch.object(_client().__class__, "generate_json", side_effect=RuntimeError("LLM request failed")):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text, job=JOB, injection_plan=plan, structure=structure,
            client=_client(),
        )
    assert result["generation_mode"] == "deterministic_fallback"
    assert "provider_error" in result["generation_warnings"][0] or "failed" in result["generation_warnings"][0].lower()


def test_llm_uses_confirmed_evidence_and_correction_instructions(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    confirmed_evidence = {
        "kubernetes": {
            "evidence_type": "Current employment", "evidence_status": "USER_CONFIRMED_PROFESSIONAL",
            "employer_name": "Acme Corp", "role_title": "Engineer", "project_name": None,
            "start_date": "2022-01", "end_date": None, "currently_using": True,
            "usage_description": "Ran Kubernetes clusters.",
        }
    }
    llm_json = {
        "generated_resume_plain_text": "Optimized body with Kubernetes.",
        "factual_preservation_confirmed": True,
    }
    captured = {}

    def fake_generate_json(self, *, system_prompt, user_prompt, temperature):
        captured["user_prompt"] = user_prompt
        return llm_json

    with patch.object(_client().__class__, "generate_json", fake_generate_json):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text, job=JOB, injection_plan=plan, structure=structure,
            confirmed_evidence=confirmed_evidence, correction_instructions="Fix the bullet.",
            client=_client(),
        )
    assert result["generation_mode"] == "llm"
    assert "correction_instructions" in captured["user_prompt"]
    assert "kubernetes" in captured["user_prompt"].lower()


def test_llm_prompt_explicitly_includes_user_denied_skills(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    captured = {}

    def fake_generate_json(self, *, system_prompt, user_prompt, temperature):
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return {"generated_resume_plain_text": "Truthful resume.", "factual_preservation_confirmed": True}

    with patch.object(_client().__class__, "generate_json", fake_generate_json):
        generate_with_llm_or_fallback(
            resume_text=java_resume_text, job=JOB, injection_plan=plan, structure=structure,
            denied_skills=["gradle"], verified_resume_skills=["java", "spring boot"], client=_client(),
        )
    assert "user_denied_skills" in captured["system_prompt"]
    assert '"user_denied_skills": ["gradle"]' in captured["user_prompt"]
    assert '"verified_resume_skills": ["java", "spring boot"]' in captured["user_prompt"]


def test_llm_disabled_client_uses_deterministic_generator_directly(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    result = generate_with_llm_or_fallback(
        resume_text=java_resume_text, job=JOB, injection_plan=plan, structure=structure,
        client=_client(enabled=False),
    )
    assert result["generation_mode"] == "deterministic"
    assert "LLM not configured" in result["generation_warnings"][0]
    assert result["llm_used"] is False
    assert result["fallback_used"] is True
    assert result["fallback_reason"] == "llm_not_configured"


def test_safe_fallback_reason_metadata(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    for reason in ["connection_failed", "timeout", "invalid_json", "provider_error"]:
        with patch.object(LLMClient, "generate_json", side_effect=LLMGenerationError(reason)):
            result = generate_with_llm_or_fallback(
                resume_text=java_resume_text, job=JOB, injection_plan=plan,
                structure=structure, client=_client(),
            )
        assert result["generation_method"] == "deterministic_fallback"
        assert result["llm_used"] is False
        assert result["fallback_used"] is True
        assert result["fallback_reason"] == reason


def test_structured_validation_failure_has_safe_reason(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    with patch.object(LLMClient, "generate_json", return_value={"generated_resume_plain_text": "text"}):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text, job=JOB, injection_plan=plan,
            structure=structure, client=_client(),
        )
    assert result["fallback_reason"] == "validation_failed"
    assert result["llm_used"] is False


def test_local_openai_compatible_is_identified_as_ollama(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    client = LLMClient(config={**_client().config, "provider": "openai_compatible", "model": "llama3.1:8b", "base_url": "http://localhost:11434/v1"})
    accepted = {"generated_resume_plain_text": "Truthful resume", "factual_preservation_confirmed": True}
    with patch.object(LLMClient, "generate_json", return_value=accepted):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text, job=JOB, injection_plan=plan,
            structure=structure, client=client,
        )
    assert result["llm_service"] == "ollama"
    assert result["llm_used"] is True
    assert result["fallback_used"] is False


def test_skip_llm_uses_deterministic_fallback(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    with patch.object(LLMClient, "generate_json") as mock_gen:
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text, job=JOB, injection_plan=plan,
            structure=structure, client=_client(), skip_llm=True,
        )
    mock_gen.assert_not_called()
    assert result["fallback_used"] is True
    assert result["fallback_reason"] == "llm_skipped"
    assert result["plain_text"]
