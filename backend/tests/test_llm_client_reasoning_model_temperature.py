"""Regression tests for two llm_client.py bugs found while diagnosing why
full-resume generation always fell back to the deterministic generator:

1. Reasoning-tier OpenAI models (o1/o3/gpt-5 family, ...) reject any
   non-default `temperature` with HTTP 400 — every call failed permanently
   until the client learned to retry once without temperature.
2. After exhausting retries, generate_json() used to raise a generic
   RuntimeError that discarded the real failure reason/type, so every
   exhausted-retry failure was misclassified as "unexpected_provider_error"
   regardless of what actually happened (timeout, 404, etc.).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import requests

from app.services.llm_client import LLMClient, LLMGenerationError, safe_llm_failure_reason


def _config(**overrides):
    config = {
        "provider": "openai",
        "model": "gpt-5-mini",
        "api_key": "sk-test",
        "base_url": "https://api.openai.com/v1",
        "timeout": 5,
        "max_retries": 0,
        "temperature": 0.1,
        "enabled": True,
        "prompt_version": "ats-optimizer-v1",
    }
    config.update(overrides)
    return config


def _response(status_code, body=None, text=""):
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    response.json.return_value = body or {}
    return response


def test_temperature_unsupported_retries_without_temperature_and_succeeds():
    rejected = _response(
        400,
        text=json.dumps({"error": {"message": "Unsupported value: 'temperature' does not support 0.1 with this model. Only the default (1) value is supported."}}),
    )
    rejected.text = "Unsupported value: 'temperature' does not support 0.1 with this model. Only the default (1) value is supported."
    accepted = _response(200, {"choices": [{"message": {"content": '{"ok": true}'}}]})

    client = LLMClient(config=_config())
    with patch("requests.post", side_effect=[rejected, accepted]) as mock_post:
        result = client.generate_json(system_prompt="sys", user_prompt="user")

    assert result == {"ok": True}
    assert mock_post.call_count == 2
    second_call_payload = mock_post.call_args_list[1].kwargs["json"]
    assert "temperature" not in second_call_payload


def test_temperature_retry_payload_keeps_response_format():
    rejected = _response(400, text="temperature does not support 0.1")
    accepted = _response(200, {"choices": [{"message": {"content": '{"ok": true}'}}]})
    client = LLMClient(config=_config())
    with patch("requests.post", side_effect=[rejected, accepted]) as mock_post:
        client.generate_json(system_prompt="sys", user_prompt="user")
    second_call_payload = mock_post.call_args_list[1].kwargs["json"]
    assert second_call_payload.get("response_format") == {"type": "json_object"}


def test_persistent_failure_after_retries_preserves_real_reason_not_generic():
    """Before the fix, generate_json's final raise discarded the original
    exception entirely, so safe_llm_failure_reason always saw a generic
    RuntimeError("LLM request failed: RuntimeError") and returned
    "unexpected_provider_error" no matter what actually failed."""
    client = LLMClient(config=_config(max_retries=0))
    with patch("requests.post", side_effect=requests.exceptions.Timeout("timed out")):
        try:
            client.generate_json(system_prompt="sys", user_prompt="user")
            assert False, "expected LLMGenerationError"
        except LLMGenerationError as error:
            assert error.reason == "timeout"
            assert safe_llm_failure_reason(error) == "timeout"


def test_persistent_model_not_found_preserves_reason():
    response = _response(404, text="model_not_found")
    client = LLMClient(config=_config(max_retries=0))
    with patch("requests.post", return_value=response):
        try:
            client.generate_json(system_prompt="sys", user_prompt="user")
            assert False, "expected LLMGenerationError"
        except LLMGenerationError as error:
            assert error.reason == "model_not_found"
