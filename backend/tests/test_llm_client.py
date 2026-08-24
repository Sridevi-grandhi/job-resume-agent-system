"""Tests for app/services/llm_client.py. All HTTP calls are mocked — never a
real provider (rule: no real LLM in tests)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.services.llm_client import LLMClient, llm_config


def test_provider_disabled_when_env_is_none(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "none")
    monkeypatch.setenv("LLM_API_KEY", "")
    config = llm_config()
    assert config["enabled"] is False


def test_missing_api_key_disables_client(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "")
    config = llm_config()
    assert config["enabled"] is False


def test_ollama_placeholder_api_key_enables_openai_compatible(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_API_KEY", "ollama")
    monkeypatch.setenv("LLM_MODEL", "llama3.1:8b")
    config = llm_config()
    assert config["enabled"] is True
    assert config["api_key"] == "ollama"
    assert config["model"] == "llama3.1:8b"


def test_valid_provider_configuration_is_enabled(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "sk-test-key")
    config = llm_config()
    assert config["enabled"] is True
    assert config["provider"] == "openai"


def test_supported_model_selection_defaults_per_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    config = llm_config()
    assert config["model"] == "claude-sonnet-4-20250514"


def test_openai_default_model_when_unspecified(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    config = llm_config()
    assert config["model"] == "gpt-4o"


def test_low_temperature_default_and_clamped(monkeypatch):
    monkeypatch.setenv("LLM_TEMPERATURE", "5")
    config = llm_config()
    assert config["temperature"] == 1.0

    monkeypatch.setenv("LLM_TEMPERATURE", "-2")
    config = llm_config()
    assert config["temperature"] == 0.0


def test_timeout_configuration_defaults(monkeypatch):
    monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)
    config = llm_config()
    assert config["timeout"] == 180


def test_max_retries_configuration_defaults(monkeypatch):
    monkeypatch.delenv("LLM_MAX_RETRIES", raising=False)
    config = llm_config()
    assert config["max_retries"] == 2


def test_base_url_defaults_per_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    config = llm_config()
    assert config["base_url"] == "https://api.anthropic.com"


def test_generate_json_raises_when_disabled():
    client = LLMClient(config={"enabled": False, "provider": "none", "model": "x", "max_retries": 0})
    with pytest.raises(RuntimeError, match="not configured"):
        client.generate_json(system_prompt="sys", user_prompt="user")


def _enabled_openai_config(**overrides):
    config = {
        "provider": "openai",
        "model": "gpt-4o",
        "api_key": "sk-test",
        "base_url": "https://api.openai.com/v1",
        "timeout": 5,
        "connect_timeout": 5,
        "max_retries": 0,
        "max_tokens": 1024,
        "temperature": 0.1,
        "enabled": True,
        "prompt_version": "ats-optimizer-v2",
    }
    config.update(overrides)
    return config


def _mock_response(status_code=200, json_body=None, raise_for_json=False):
    response = MagicMock()
    response.status_code = status_code
    if raise_for_json:
        response.json.side_effect = ValueError("not json")
    else:
        response.json.return_value = json_body or {}
    return response


def test_successful_structured_json_response():
    payload = {"plain_text": "resume text"}
    response = _mock_response(200, {"choices": [{"message": {"content": json.dumps(payload)}}]})
    client = LLMClient(config=_enabled_openai_config())
    with patch("requests.post", return_value=response) as mock_post:
        result = client.generate_json(system_prompt="sys", user_prompt="user")
    assert result == payload
    mock_post.assert_called_once()


def test_empty_response_raises_after_retries():
    response = _mock_response(200, {"choices": [{"message": {"content": ""}}]})
    client = LLMClient(config=_enabled_openai_config())
    with patch("requests.post", return_value=response):
        with pytest.raises(RuntimeError, match="LLM request failed"):
            client.generate_json(system_prompt="sys", user_prompt="user")


def test_invalid_json_response_raises_after_retries():
    response = _mock_response(200, {"choices": [{"message": {"content": "not-json-at-all"}}]})
    client = LLMClient(config=_enabled_openai_config())
    with patch("requests.post", return_value=response):
        with pytest.raises(RuntimeError, match="LLM request failed"):
            client.generate_json(system_prompt="sys", user_prompt="user")


def test_json_extracted_from_surrounding_text():
    content = "Here is the result: {\"a\": 1} -- done"
    response = _mock_response(200, {"choices": [{"message": {"content": content}}]})
    client = LLMClient(config=_enabled_openai_config())
    with patch("requests.post", return_value=response):
        result = client.generate_json(system_prompt="sys", user_prompt="user")
    assert result == {"a": 1}


def test_markdown_fenced_json_parses_successfully():
    content = "Here is the result:\n```json\n{\"a\": 1}\n```"
    response = _mock_response(200, {"choices": [{"message": {"content": content}}]})
    client = LLMClient(config=_enabled_openai_config())
    with patch("requests.post", return_value=response):
        result = client.generate_json(system_prompt="sys", user_prompt="user")
    assert result == {"a": 1}


def test_non_dict_json_response_raises():
    response = _mock_response(200, {"choices": [{"message": {"content": "[1, 2, 3]"}}]})
    client = LLMClient(config=_enabled_openai_config())
    with patch("requests.post", return_value=response):
        with pytest.raises(RuntimeError, match="LLM request failed"):
            client.generate_json(system_prompt="sys", user_prompt="user")


def test_provider_timeout_raises_after_retries():
    client = LLMClient(config=_enabled_openai_config())
    with patch("requests.post", side_effect=requests.exceptions.Timeout("timed out")):
        with pytest.raises(RuntimeError, match="LLM request failed"):
            client.generate_json(system_prompt="sys", user_prompt="user")


def test_connection_error_raises_after_retries():
    client = LLMClient(config=_enabled_openai_config())
    with patch("requests.post", side_effect=requests.exceptions.ConnectionError("refused")):
        with pytest.raises(RuntimeError, match="LLM request failed"):
            client.generate_json(system_prompt="sys", user_prompt="user")


def test_rate_limit_response_is_treated_as_transient():
    response = _mock_response(429)
    client = LLMClient(config=_enabled_openai_config())
    with patch("requests.post", return_value=response):
        with pytest.raises(RuntimeError, match="LLM request failed"):
            client.generate_json(system_prompt="sys", user_prompt="user")


def test_authentication_failure_is_non_transient_but_still_raises():
    response = _mock_response(401)
    client = LLMClient(config=_enabled_openai_config())
    with patch("requests.post", return_value=response):
        with pytest.raises(RuntimeError, match="LLM request failed"):
            client.generate_json(system_prompt="sys", user_prompt="user")


def test_ollama_model_not_found_response_is_handled():
    response = _mock_response(404, {"error": {"message": "model not found"}})
    client = LLMClient(config=_enabled_openai_config(
        provider="openai_compatible", base_url="http://localhost:11434/v1",
        model="missing-model",
    ))
    with patch.object(LLMClient, "_ollama_probe", return_value={"reachable": True, "model_found": True}), \
         patch("requests.post", return_value=response):
        with pytest.raises(RuntimeError, match="model_not_found"):
            client.generate_json(system_prompt="sys", user_prompt="user")


def test_server_error_is_treated_as_transient():
    response = _mock_response(503)
    client = LLMClient(config=_enabled_openai_config())
    with patch("requests.post", return_value=response):
        with pytest.raises(RuntimeError, match="LLM request failed"):
            client.generate_json(system_prompt="sys", user_prompt="user")


def test_retry_count_is_limited_to_max_retries():
    client = LLMClient(config=_enabled_openai_config(max_retries=2))
    with patch("requests.post", return_value=_mock_response(429)) as mock_post, \
         patch("time.sleep", return_value=None):
        with pytest.raises(RuntimeError):
            client.generate_json(system_prompt="sys", user_prompt="user")
    assert mock_post.call_count == 3  # initial attempt + 2 retries


def test_timeout_does_not_retry():
    client = LLMClient(config=_enabled_openai_config(max_retries=2))
    with patch("requests.post", side_effect=requests.exceptions.Timeout("timed out")) as mock_post, \
         patch("time.sleep", return_value=None):
        with pytest.raises(RuntimeError, match="timeout"):
            client.generate_json(system_prompt="sys", user_prompt="user")
    assert mock_post.call_count == 1


def test_connection_error_does_not_retry():
    client = LLMClient(config=_enabled_openai_config(max_retries=2))
    with patch("requests.post", side_effect=requests.exceptions.ConnectionError("refused")) as mock_post, \
         patch("time.sleep", return_value=None):
        with pytest.raises(RuntimeError, match="connection_refused"):
            client.generate_json(system_prompt="sys", user_prompt="user")
    assert mock_post.call_count == 1


def test_retryable_error_eventually_succeeds():
    ok_response = _mock_response(200, {"choices": [{"message": {"content": '{"ok": true}'}}]})
    client = LLMClient(config=_enabled_openai_config(max_retries=2))
    with patch(
        "requests.post",
        side_effect=[_mock_response(429), ok_response],
    ), patch("time.sleep", return_value=None):
        result = client.generate_json(system_prompt="sys", user_prompt="user")
    assert result == {"ok": True}


def test_api_key_never_appears_in_raised_error_message():
    client = LLMClient(config=_enabled_openai_config(api_key="super-secret-key"))
    with patch("requests.post", side_effect=requests.exceptions.ConnectionError("down")), \
         patch("time.sleep", return_value=None):
        with pytest.raises(RuntimeError) as excinfo:
            client.generate_json(system_prompt="sys", user_prompt="user")
    assert "super-secret-key" not in str(excinfo.value)


def test_api_key_never_appears_in_logs(caplog):
    client = LLMClient(config=_enabled_openai_config(api_key="super-secret-key"))
    with caplog.at_level("INFO"), patch(
        "requests.post", side_effect=requests.exceptions.ConnectionError("refused")
    ):
        with pytest.raises(RuntimeError):
            client.generate_json(system_prompt="secret-system", user_prompt="private-resume")
    assert "super-secret-key" not in caplog.text
    assert "secret-system" not in caplog.text
    assert "private-resume" not in caplog.text


def test_llm_logs_request_response_and_json_stages_without_premature_success(caplog):
    response = _mock_response(200, {"choices": [{"message": {"content": '{"ok": true}'}}]})
    client = LLMClient(config=_enabled_openai_config())
    with caplog.at_level("INFO"), patch("requests.post", return_value=response):
        assert client.generate_json(system_prompt="sys", user_prompt="user") == {"ok": True}
    assert "LLM_REQUEST_STARTED" in caplog.text
    assert "LLM_RESPONSE_RECEIVED" in caplog.text
    assert "LLM_PARSE_SUCCESS" in caplog.text
    assert "LLM_GENERATION_SUCCESS" not in caplog.text


def test_openai_compatible_provider_uses_ollama_url():
    response = _mock_response(200, {"choices": [{"message": {"content": '{"a": 1}'}}]})
    client = LLMClient(config=_enabled_openai_config(provider="openai_compatible", base_url="http://localhost:11434/v1", model="ollama-model"))
    with patch.object(LLMClient, "_ollama_probe", return_value={"reachable": True, "model_found": True}), \
         patch("requests.post", return_value=response) as mock_post:
        result = client.generate_json(system_prompt="sys", user_prompt="user")
    assert result == {"a": 1}
    assert mock_post.call_args.kwargs["json"]["model"] == "ollama-model"
    assert mock_post.call_args.kwargs["json"]["temperature"] == 0.1
    assert mock_post.call_args.kwargs["json"]["stream"] is False
    assert mock_post.call_args.kwargs["json"]["response_format"]["type"] == "json_object"
    assert mock_post.call_args.kwargs["json"]["messages"][0]["role"] == "system"
    assert mock_post.call_args.kwargs["json"]["messages"][1]["content"] == "user"
    assert "http://localhost:11434/v1/chat/completions" in mock_post.call_args.kwargs["url"]


def test_openai_compatible_falls_back_when_response_format_not_supported():
    bad_response = _mock_response(400, {"error": "unsupported response_format"})
    bad_response.text = "response_format not supported"
    ok_response = _mock_response(200, {"choices": [{"message": {"content": '{"a": 1}'}}]})
    client = LLMClient(config=_enabled_openai_config(provider="openai_compatible", base_url="http://localhost:11434/v1", model="ollama-model"))
    with patch.object(LLMClient, "_ollama_probe", return_value={"reachable": True, "model_found": True}), \
         patch("requests.post", side_effect=[bad_response, ok_response]) as mock_post:
        result = client.generate_json(system_prompt="sys", user_prompt="user")
    assert result == {"a": 1}
    assert mock_post.call_count == 2


def test_llm_status_reports_online_for_openai_compatible(monkeypatch):
    client = LLMClient(config=_enabled_openai_config(provider="openai_compatible", base_url="http://localhost:11434/v1", model="ollama-model"))
    mock_get = MagicMock()
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {"models": [{"name": "ollama-model"}]}
    monkeypatch.setattr("requests.get", mock_get)
    status = client.llm_status()
    assert status["configured"] is True
    assert status["provider"] == "openai_compatible"
    assert status["model"] == "ollama-model"
    assert status["reachable"] is True
    assert status["model_found"] is True


def test_llm_status_reports_disabled_when_not_configured():
    client = LLMClient(config={"enabled": False, "provider": "none", "model": "x", "max_retries": 0})
    status = client.llm_status()
    assert status["configured"] is False
    assert status["provider"] == "none"
    assert status["model"] == "x"
    assert status["reachable"] is False


def test_anthropic_dispatch_builds_expected_request():
    response = _mock_response(200, {"content": [{"type": "text", "text": '{"a": 1}'}]})
    client = LLMClient(config=_enabled_openai_config(provider="anthropic", base_url="https://api.anthropic.com"))
    with patch("requests.post", return_value=response) as mock_post:
        result = client.generate_json(system_prompt="sys", user_prompt="user")
    assert result == {"a": 1}
    called_headers = mock_post.call_args.kwargs["headers"]
    assert "x-api-key" in called_headers
    assert called_headers["x-api-key"] == "sk-test"
    client = LLMClient(config=_enabled_openai_config(provider="unknown-provider"))
    with pytest.raises(RuntimeError, match="LLM request failed"):
        client.generate_json(system_prompt="sys", user_prompt="user")


def test_client_properties_reflect_config():
    client = LLMClient(config=_enabled_openai_config())
    assert client.enabled is True
    assert client.provider == "openai"
    assert client.model == "gpt-4o"


def test_client_defaults_to_llm_config_when_none_given(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "none")
    monkeypatch.setenv("LLM_API_KEY", "")
    client = LLMClient()
    assert client.enabled is False
    assert client.provider == "none"
    assert client.model == "deterministic-fallback"


def test_generation_timeout_config_defaults(monkeypatch):
    monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("ATS_GENERATION_TIMEOUT_SECONDS", raising=False)
    from app.services.llm_client import generation_timeout_config
    cfg = generation_timeout_config()
    assert cfg["llm_timeout_seconds"] == 180
    assert cfg["generation_timeout_seconds"] == 300
    assert cfg["poll_timeout_seconds"] == 300
    assert cfg["poll_interval_ms"] == 2000


def test_ollama_probe_failure_skips_generation_post():
    client = LLMClient(config=_enabled_openai_config(
        provider="openai_compatible", base_url="http://localhost:11434/v1", model="llama3.1:8b",
        max_retries=2,
    ))
    with patch.object(LLMClient, "_ollama_probe", return_value={"reachable": False, "model_found": None}), \
         patch("requests.post") as mock_post:
        with pytest.raises(RuntimeError, match="connection_failed"):
            client.generate_json(system_prompt="sys", user_prompt="user")
    mock_post.assert_not_called()


def test_openai_compatible_sends_stream_false_and_joins_url():
    payload = {"ok": True}
    response = _mock_response(200, {"choices": [{"message": {"content": json.dumps(payload)}}]})
    client = LLMClient(config=_enabled_openai_config(
        provider="openai_compatible",
        base_url="http://host.docker.internal:11434/v1",
        model="llama3.1:8b",
        api_key="ollama",
    ))
    with patch.object(LLMClient, "_ollama_probe", return_value={"reachable": True, "model_found": True}), \
         patch("requests.post", return_value=response) as mock_post:
        result = client.generate_json(system_prompt="sys", user_prompt="user")
    assert result == payload
    args, kwargs = mock_post.call_args
    assert kwargs["url"] == "http://host.docker.internal:11434/v1/chat/completions"
    assert kwargs["json"]["stream"] is False
    assert "response_format" in kwargs["json"]
    assert kwargs["json"]["max_tokens"] >= 256


def test_model_not_found_probe_skips_post():
    client = LLMClient(config=_enabled_openai_config(
        provider="openai_compatible", base_url="http://localhost:11434/v1", model="missing-model",
    ))
    with patch.object(LLMClient, "_ollama_probe", return_value={"reachable": True, "model_found": False}), \
         patch("requests.post") as mock_post:
        with pytest.raises(RuntimeError, match="model_not_found"):
            client.generate_json(system_prompt="sys", user_prompt="user")
    mock_post.assert_not_called()
