"""Tests for LLM response normalization and JSON extraction."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.services.llm_response_normalizer import (
    extract_and_parse_json,
    normalize_generation_response,
    summarize_validation_errors,
)
from app.services.ats_resume_generation_service import StructuredOptimizerResponse


def test_normalize_maps_resume_alias_to_plain_text():
    raw = {"resume": "Complete resume body text here.", "factual_preservation_confirmed": True}
    normalized, repair_used = normalize_generation_response(raw)
    assert normalized["generated_resume_plain_text"] == "Complete resume body text here."
    assert repair_used is True


def test_normalize_defaults_missing_factual_preservation_confirmed():
    raw = {"generated_resume_plain_text": "Resume text body."}
    normalized, repair_used = normalize_generation_response(raw)
    assert normalized["factual_preservation_confirmed"] is True
    assert repair_used is True


def test_normalize_unwraps_response_wrapper():
    raw = {
        "response": {
            "generated_resume_plain_text": "Wrapped resume.",
            "factual_preservation_confirmed": True,
        }
    }
    normalized, repair_used = normalize_generation_response(raw)
    assert normalized["generated_resume_plain_text"] == "Wrapped resume."
    assert repair_used is True


def test_extract_json_from_markdown_fence():
    raw = 'Here is the resume:\n```json\n{"generated_resume_plain_text": "Fenced.", "factual_preservation_confirmed": true}\n```'
    parsed, repair_used = extract_and_parse_json(raw)
    assert parsed["generated_resume_plain_text"] == "Fenced."
    assert repair_used is True


def test_extract_json_with_leading_explanation():
    raw = 'Sure! {"generated_resume_plain_text": "Explained.", "factual_preservation_confirmed": true}'
    parsed, repair_used = extract_and_parse_json(raw)
    assert parsed["generated_resume_plain_text"] == "Explained."
    assert repair_used is True


def test_optional_sections_default_to_empty_lists():
    raw = {"generated_resume_plain_text": "Body only.", "factual_preservation_confirmed": True}
    normalized, _ = normalize_generation_response(raw)
    result = StructuredOptimizerResponse.model_validate(normalized)
    assert result.rewritten_bullets == []
    assert result.keyword_evidence == []
    assert result.warnings == []


def test_malformed_json_raises():
    with pytest.raises(json.JSONDecodeError):
        extract_and_parse_json("{not valid json")


def test_schema_mismatch_missing_plain_text_fails_validation():
    normalized, _ = normalize_generation_response({"factual_preservation_confirmed": True})
    with pytest.raises(ValidationError) as exc:
        StructuredOptimizerResponse.model_validate(normalized)
    summary = summarize_validation_errors(exc.value)
    assert summary["missing_fields"] or summary["validation_errors"]


def test_coerce_string_supported_by_on_bullets():
    raw = {
        "generated_resume_plain_text": "Resume.",
        "rewritten_bullets": [{"text": "Did Java work.", "supported_by": "E1"}],
        "factual_preservation_confirmed": True,
    }
    normalized, repair_used = normalize_generation_response(raw)
    assert normalized["rewritten_bullets"][0]["supported_by"] == ["E1"]
    assert repair_used is True
