"""Tests for app/services/bullet_rewrite_service.py — the Ollama-backed
bullet-level "Try Again" rewrite. Covers: success, empty/malformed/timeout
never producing a blank result, retry producing meaningfully different
wording, near-identical detection, and the invented-token truthfulness guard.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from app.services.bullet_rewrite_service import (
    guard_invented_tokens,
    is_near_identical,
    rewrite_bullet_with_ollama,
)


def _enabled_env(monkeypatch):
    monkeypatch.setenv("BULLET_REWRITE_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("BULLET_REWRITE_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("BULLET_REWRITE_LLM_MODEL", "llama3.1")
    monkeypatch.setenv("BULLET_REWRITE_LLM_API_KEY", "ollama")


def _response(status_code=200, json_body=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body or {}
    response.text = ""
    return response


def test_disabled_when_no_model_configured(monkeypatch):
    monkeypatch.delenv("BULLET_REWRITE_LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("BULLET_REWRITE_LLM_PROVIDER", "openai_compatible")
    text, provider, no_change = rewrite_bullet_with_ollama(original="Built REST APIs using Java.")
    assert text is None
    assert provider is None
    assert no_change is False


def test_successful_rewrite_returns_ollama_label(monkeypatch):
    _enabled_env(monkeypatch)
    payload = {"choices": [{"message": {"content": '{"rewritten_bullet": "Engineered scalable REST APIs in Java."}'}}]}
    with patch("requests.post", return_value=_response(200, payload)):
        text, provider, no_change = rewrite_bullet_with_ollama(
            original="Built REST APIs using Java.",
            job_title="Java Developer",
            job_description="Java, Spring Boot",
        )
    assert text == "Engineered scalable REST APIs in Java."
    assert provider == "ollama"
    assert no_change is False


def test_empty_response_never_returned_as_suggestion(monkeypatch):
    _enabled_env(monkeypatch)
    payload = {"choices": [{"message": {"content": '{"rewritten_bullet": ""}'}}]}
    with patch("requests.post", return_value=_response(200, payload)):
        text, provider, no_change = rewrite_bullet_with_ollama(original="Built REST APIs using Java.")
    assert text is None
    assert provider is None
    assert no_change is False


def test_whitespace_only_response_never_returned_as_suggestion(monkeypatch):
    _enabled_env(monkeypatch)
    payload = {"choices": [{"message": {"content": '{"rewritten_bullet": "   "}'}}]}
    with patch("requests.post", return_value=_response(200, payload)):
        text, provider, no_change = rewrite_bullet_with_ollama(original="Built REST APIs using Java.")
    assert text is None


def test_malformed_json_never_raises_and_returns_none(monkeypatch):
    _enabled_env(monkeypatch)
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"choices": [{"message": {"content": "not json at all"}}]}
    response.text = ""
    with patch("requests.post", return_value=response):
        text, provider, no_change = rewrite_bullet_with_ollama(original="Built REST APIs using Java.")
    assert text is None
    assert provider is None
    assert no_change is False


def test_timeout_never_raises_and_returns_none(monkeypatch):
    _enabled_env(monkeypatch)
    with patch("requests.post", side_effect=requests.exceptions.Timeout("timed out")):
        text, provider, no_change = rewrite_bullet_with_ollama(original="Built REST APIs using Java.")
    assert text is None
    assert provider is None
    assert no_change is False


def test_connection_error_never_raises_and_returns_none(monkeypatch):
    _enabled_env(monkeypatch)
    with patch("requests.post", side_effect=requests.exceptions.ConnectionError("refused")):
        text, provider, no_change = rewrite_bullet_with_ollama(original="Built REST APIs using Java.")
    assert text is None
    assert provider is None
    assert no_change is False


def test_retry_rejects_result_identical_to_previous_suggestion(monkeypatch):
    _enabled_env(monkeypatch)
    # Every call (including the internal near-identical retry) returns the
    # SAME wording — Ollama truly has nothing different to offer.
    payload = {"choices": [{"message": {"content": '{"rewritten_bullet": "Same suggestion text."}'}}]}
    with patch("requests.post", return_value=_response(200, payload)):
        text, provider, no_change = rewrite_bullet_with_ollama(
            original="Built REST APIs using Java.",
            previous_suggestion="Same suggestion text.",
        )
    assert text is None  # must not silently "succeed" with a non-different result
    assert no_change is True  # this is a real "no change recommended", not a technical failure


def test_retry_accepts_meaningfully_different_result(monkeypatch):
    _enabled_env(monkeypatch)
    payload = {"choices": [{"message": {"content": '{"rewritten_bullet": "Delivered production REST APIs in Java."}'}}]}
    with patch("requests.post", return_value=_response(200, payload)):
        text, provider, no_change = rewrite_bullet_with_ollama(
            original="Built REST APIs using Java.",
            previous_suggestion="Engineered scalable REST APIs in Java.",
        )
    assert text == "Delivered production REST APIs in Java."
    assert no_change is False


def test_first_suggestion_near_identical_to_original_retries_then_recommends_no_change(monkeypatch):
    _enabled_env(monkeypatch)
    # Ollama just echoes the original wording back (trivial punctuation change only).
    payload = {"choices": [{"message": {"content": '{"rewritten_bullet": "Built REST APIs using Java!"}'}}]}
    with patch("requests.post", return_value=_response(200, payload)) as mock_post:
        text, provider, no_change = rewrite_bullet_with_ollama(original="Built REST APIs using Java.")
    assert text is None
    assert no_change is True
    # One initial attempt plus one "give me something different" retry.
    assert mock_post.call_count == 2


def test_try_again_result_identical_to_original_is_also_rejected_not_just_previous_suggestion(monkeypatch):
    """Regression: a Try Again retry that circles back to the ORIGINAL
    wording is exactly as meaningless as one that repeats the previous
    suggestion — both must be rejected, not just the latter."""
    _enabled_env(monkeypatch)
    responses = [
        # First attempt: back to the original wording (not identical to the
        # *previous suggestion*, so the old single-baseline check would have
        # accepted this).
        _response(200, {"choices": [{"message": {"content": '{"rewritten_bullet": "Built REST APIs using Java."}'}}]}),
        # Retry: also circles back to the original.
        _response(200, {"choices": [{"message": {"content": '{"rewritten_bullet": "Built REST APIs using Java!"}'}}]}),
    ]
    with patch("requests.post", side_effect=responses):
        text, provider, no_change = rewrite_bullet_with_ollama(
            original="Built REST APIs using Java.",
            previous_suggestion="Engineered scalable REST APIs in Java.",
        )
    assert text is None
    assert no_change is True


def test_first_suggestion_near_identical_but_retry_produces_real_change(monkeypatch):
    _enabled_env(monkeypatch)
    responses = [
        _response(200, {"choices": [{"message": {"content": '{"rewritten_bullet": "Built REST APIs using Java!"}'}}]}),
        _response(200, {"choices": [{"message": {"content": '{"rewritten_bullet": "Engineered scalable REST APIs in Java for high-volume account workflows."}'}}]}),
    ]
    with patch("requests.post", side_effect=responses):
        text, provider, no_change = rewrite_bullet_with_ollama(original="Built REST APIs using Java.")
    assert text == "Engineered scalable REST APIs in Java for high-volume account workflows."
    assert no_change is False


def test_is_near_identical_exact_and_whitespace_case_variants():
    assert is_near_identical("Built REST APIs using Java.", "Built REST APIs using Java.")
    assert is_near_identical("Built REST APIs using Java.", "built rest apis using java.")
    assert is_near_identical("Built REST APIs using Java.", "Built   REST APIs using Java.")


def test_is_near_identical_false_for_genuinely_different_wording():
    assert not is_near_identical(
        "Built REST APIs using Java.",
        "Engineered scalable REST APIs in Java for high-volume account workflows.",
    )


def test_guard_invented_tokens_blocks_new_technology_not_present_anywhere():
    original = "Built REST APIs using Java."
    suggested = "Built REST APIs using Java and Kubernetes."
    guarded = guard_invented_tokens(suggested, original=original, resume_text=original, job_description="")
    assert guarded == original  # "Kubernetes" was invented — reject the whole suggestion


def test_guard_invented_tokens_allows_terms_present_in_resume_or_jd():
    original = "Built REST APIs using Java."
    suggested = "Delivered REST APIs in Java, aligned with Spring Boot requirements."
    guarded = guard_invented_tokens(
        suggested, original=original, resume_text="Java Spring Boot developer.",
        job_description="Looking for Spring Boot experience.",
    )
    assert guarded == suggested
