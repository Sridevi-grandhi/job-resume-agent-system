"""Tests for LLM schema normalization, repair retry, and generation metadata."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.ats_resume_generation_service import generate_with_llm_or_fallback
from app.services.keyword_injection_service import plan_keyword_injection
from app.services.llm_client import LLMClient
from app.services.resume_structure_parser import parse_resume_structure


JOB = SimpleNamespace(
    title="Java Developer",
    company="Acme",
    description="Java Spring Boot React PostgreSQL AWS Docker role.",
    skills_required="Java, Spring Boot, React, PostgreSQL, AWS, Docker",
)


def _client():
    return LLMClient(config={
        "provider": "openai_compatible",
        "model": "llama3.1:8b",
        "api_key": "ollama",
        "base_url": "http://localhost:11434/v1",
        "timeout": 5,
        "max_retries": 0,
        "temperature": 0.1,
        "enabled": True,
        "prompt_version": "ats-optimizer-v1",
    })


def _plan_and_structure(resume_text):
    structure = parse_resume_structure(resume_text)
    plan = plan_keyword_injection(
        resume_text,
        JOB,
        structure.get("employments") or [],
        structure.get("projects") or [],
    )
    return structure, plan


def test_missing_factual_field_succeeds_after_normalization(java_resume_text):
    structure, plan = _plan_and_structure(java_resume_text)
    llm_json = {"generated_resume_plain_text": "Optimized resume with Java and Spring Boot improvements."}
    with patch.object(_client().__class__, "generate_json", return_value=llm_json):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text,
            job=JOB,
            injection_plan=plan,
            structure=structure,
            client=_client(),
            workflow_id=99,
        )
    assert result["generation_method"] == "llm"
    assert result["schema_validation_passed"] is True
    assert result["json_repair_used"] is True


def test_alternate_resume_key_succeeds_as_llm(java_resume_text):
    structure, plan = _plan_and_structure(java_resume_text)
    llm_json = {
        "resume": "Truthful optimized resume with Java, Spring Boot, and PostgreSQL.",
        "factual_preservation_confirmed": True,
    }
    with patch.object(_client().__class__, "generate_json", return_value=llm_json):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text,
            job=JOB,
            injection_plan=plan,
            structure=structure,
            client=_client(),
        )
    assert result["generation_method"] == "llm"
    assert result["json_repair_used"] is True


def test_schema_repair_retry_success(java_resume_text):
    structure, plan = _plan_and_structure(java_resume_text)
    invalid = {"summary": "Missing required plain text field."}
    repaired = {
        "generated_resume_plain_text": "Repaired resume with Java and Spring Boot.",
        "factual_preservation_confirmed": True,
    }
    calls = {"count": 0}

    def fake_generate_json(self, *, system_prompt, user_prompt, temperature):
        calls["count"] += 1
        if calls["count"] == 1:
            return invalid
        return repaired

    with patch.object(_client().__class__, "generate_json", fake_generate_json):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text,
            job=JOB,
            injection_plan=plan,
            structure=structure,
            client=_client(),
            workflow_id=100,
        )
    assert result["generation_method"] == "llm"
    assert result["schema_repair_retry_used"] is True
    assert calls["count"] == 2


def test_schema_repair_retry_failure_falls_back(java_resume_text):
    structure, plan = _plan_and_structure(java_resume_text)
    invalid = {"summary": "Still invalid."}

    with patch.object(_client().__class__, "generate_json", return_value=invalid):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text,
            job=JOB,
            injection_plan=plan,
            structure=structure,
            client=_client(),
        )
    assert result["generation_method"] == "deterministic_fallback"
    assert result["fallback_reason"] == "schema_validation_failed"
    assert result["llm_response_received"] is True


def test_invalid_evidence_still_truthfulness_fallback(java_resume_text):
    structure, plan = _plan_and_structure(java_resume_text)
    llm_json = {
        "generated_resume_plain_text": "Optimized resume body with unsupported claims.",
        "rewritten_bullets": [{"text": "Fabricated.", "supported_by": ["E999"]}],
        "factual_preservation_confirmed": True,
    }
    with patch.object(_client().__class__, "generate_json", return_value=llm_json):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text,
            job=JOB,
            injection_plan=plan,
            structure=structure,
            client=_client(),
        )
    assert result["generation_method"] == "deterministic_fallback"
    assert result["fallback_reason"] == "truthfulness_validation_failed"
