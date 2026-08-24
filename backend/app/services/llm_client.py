"""
Provider-independent LLM client.

Configured via environment variables:
  LLM_PROVIDER   - openai | openai_compatible | anthropic | none
  LLM_MODEL      - model id (e.g. gpt-4o, claude-sonnet-4-20250514, llama3.1:8b)
  LLM_API_KEY    - secret key (backend only; Ollama accepts any non-empty placeholder)
  LLM_BASE_URL   - optional API base (defaults per provider)
  LLM_TIMEOUT_SECONDS - read timeout per LLM attempt (default 180; local models may need more)
  ATS_GENERATION_TIMEOUT_SECONDS - overall generate+fallback budget (default 300)
  ATS_POLL_INTERVAL_MS - frontend poll interval hint (default 2000)
  LLM_MAX_RETRIES     - retries for transient/invalid JSON only (default 2)
  LLM_CONNECT_TIMEOUT_SECONDS - TCP connect timeout (default 5)
  LLM_TEMPERATURE     - generation temperature (default 0.1)
  LLM_MAX_TOKENS      - completion cap (default 6144)

Business rules stay outside this module. Validators remain deterministic.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any
from urllib.parse import urljoin

import requests


logger = logging.getLogger(__name__)

PROMPT_VERSION = "ats-optimizer-v2"


class LLMGenerationError(RuntimeError):
    """Provider failure carrying only a safe, normalized reason."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"LLM request failed: {reason}")


def safe_llm_failure_reason(error: Exception) -> str:
    if isinstance(error, LLMGenerationError):
        return error.reason
    if isinstance(error, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(error, requests.exceptions.ConnectionError):
        message = str(error).lower()
        if "refused" in message or "10061" in message:
            return "connection_refused"
        return "connection_failed"
    if isinstance(error, (json.JSONDecodeError, ValueError)):
        return "invalid_json"
    message = str(error).lower()
    if "model_not_found" in message or "http 404" in message:
        return "model_not_found"
    if "unsupported" in message and "provider" in message:
        return "unsupported_provider"
    return "provider_error"


def generation_timeout_config() -> dict[str, int]:
    """Aligned LLM / workflow / frontend poll budgets. Never log secrets."""
    llm_timeout = int(os.getenv("LLM_TIMEOUT_SECONDS") or "180")
    connect_timeout = int(os.getenv("LLM_CONNECT_TIMEOUT_SECONDS") or "5")
    overall = int(os.getenv("ATS_GENERATION_TIMEOUT_SECONDS") or "300")
    poll_interval_ms = int(os.getenv("ATS_POLL_INTERVAL_MS") or "2000")
    # Leave room after one LLM attempt for deterministic fallback + scoring.
    overall = max(overall, llm_timeout + 60)
    return {
        "llm_timeout_seconds": max(1, llm_timeout),
        "connect_timeout_seconds": max(1, min(connect_timeout, llm_timeout)),
        "generation_timeout_seconds": overall,
        "poll_interval_ms": max(250, poll_interval_ms),
        "poll_timeout_seconds": overall,
    }


def llm_config() -> dict[str, Any]:
    provider = (os.getenv("LLM_PROVIDER") or "none").strip().lower()
    model = (os.getenv("LLM_MODEL") or "").strip()
    api_key = (os.getenv("LLM_API_KEY") or "").strip()
    base_url = (os.getenv("LLM_BASE_URL") or "").strip().rstrip("/")
    budgets = generation_timeout_config()
    timeout = budgets["llm_timeout_seconds"]
    connect_timeout = budgets["connect_timeout_seconds"]
    max_retries = int(os.getenv("LLM_MAX_RETRIES") or "2")
    temperature = float(os.getenv("LLM_TEMPERATURE") or "0.1")
    max_tokens = int(os.getenv("LLM_MAX_TOKENS") or "6144")

    if not base_url:
        if provider in ("openai", "openai_compatible"):
            base_url = "https://api.openai.com/v1"
        elif provider == "anthropic":
            base_url = "https://api.anthropic.com"
        else:
            base_url = ""

    if provider in ("", "none", "off", "disabled"):
        model = "deterministic-fallback"
    elif not model:
        if provider == "anthropic":
            model = "claude-sonnet-4-20250514"
        elif provider in ("openai", "openai_compatible"):
            model = "gpt-4o"
        else:
            model = "deterministic-fallback"

    return {
        "provider": provider,
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
        "timeout": timeout,
        "connect_timeout": connect_timeout,
        "generation_timeout_seconds": budgets["generation_timeout_seconds"],
        "poll_interval_ms": budgets["poll_interval_ms"],
        "poll_timeout_seconds": budgets["poll_timeout_seconds"],
        "max_retries": max_retries,
        "temperature": max(0.0, min(1.0, temperature)),
        "max_tokens": max(256, max_tokens),
        "enabled": provider not in ("", "none", "off", "disabled") and bool(api_key),
        "prompt_version": PROMPT_VERSION,
    }


def _join_url(base: str, path: str) -> str:
    """Join base + path without duplicating segments like /v1/v1/."""
    root = (base or "").rstrip("/") + "/"
    rel = (path or "").lstrip("/")
    return urljoin(root, rel)


class LLMClient:
    """Thin HTTP client for chat/completions. No resume business rules."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or llm_config()

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled"))

    @property
    def provider(self) -> str:
        return self.config.get("provider") or "none"

    @property
    def model(self) -> str:
        return self.config.get("model") or "deterministic-fallback"

    @property
    def service_name(self) -> str | None:
        if self.provider != "openai_compatible":
            return None
        base_url = str(self.config.get("base_url") or "").lower()
        local_hosts = ("localhost", "127.0.0.1", "host.docker.internal")
        if "ollama" in base_url or (":11434" in base_url and any(host in base_url for host in local_hosts)):
            return "ollama"
        return None

    def _request_timeout(self) -> tuple[int, int]:
        connect = int(self.config.get("connect_timeout") or 5)
        read = int(self.config.get("timeout") or 180)
        return (max(1, connect), max(1, read))

    def _should_retry(self, error: Exception) -> bool:
        reason = safe_llm_failure_reason(error)
        # Timeouts and refused connections must fall back immediately — do not
        # burn the overall generation budget on repeated long waits.
        if reason in {"timeout", "connection_failed", "connection_refused", "model_not_found"}:
            return False
        message = str(error).lower()
        if "transient" in message or "invalid_json" in reason:
            return True
        if isinstance(error, (json.JSONDecodeError, ValueError)):
            return True
        return reason in {"invalid_json", "provider_error"} and "http 4" not in message

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        """
        Call the configured provider and parse a JSON object response.
        Raises RuntimeError on hard failure after retries.
        """
        if not self.enabled:
            raise RuntimeError("LLM is not configured (set LLM_PROVIDER and LLM_API_KEY).")

        prompt_chars = len(system_prompt or "") + len(user_prompt or "")
        logger.info(
            "LLM_ATTEMPT_STARTED provider=%s model=%s service=%s base_url=%s "
            "timeout_s=%s connect_s=%s prompt_chars=%s",
            self.provider,
            self.model,
            self.service_name or "n/a",
            self.config.get("base_url"),
            self.config.get("timeout"),
            self.config.get("connect_timeout"),
            prompt_chars,
        )
        if self.service_name == "ollama" or ":11434" in str(self.config.get("base_url") or ""):
            try:
                health = self._ollama_probe()
                if not health.get("reachable"):
                    logger.info("LLM_PROBE_FAILED provider=%s reason=connection_failed", self.provider)
                    raise LLMGenerationError("connection_failed")
                if health.get("model_found") is False:
                    logger.info(
                        "LLM_PROBE_FAILED provider=%s reason=model_not_found model=%s",
                        self.provider,
                        self.model,
                    )
                    raise LLMGenerationError("model_not_found")
            except LLMGenerationError:
                raise
            except Exception as error:
                logger.info(
                    "LLM_PROBE_FAILED provider=%s reason=%s",
                    self.provider,
                    safe_llm_failure_reason(error),
                )
                raise LLMGenerationError(safe_llm_failure_reason(error)) from error

        logger.info(
            "LLM_REQUEST_STARTED provider=%s model=%s prompt_chars=%s",
            self.provider,
            self.model,
            prompt_chars,
        )
        last_error = None
        retries = max(0, int(self.config.get("max_retries") or 0))
        for attempt in range(retries + 1):
            try:
                raw = self._dispatch(system_prompt, user_prompt, temperature)
                logger.info("LLM_RESPONSE_RECEIVED provider=%s model=%s", self.provider, self.model)
                parsed = self._extract_json(raw)
                if not isinstance(parsed, dict):
                    raise RuntimeError("LLM JSON response was not an object.")
                logger.info("LLM_PARSE_SUCCESS provider=%s model=%s", self.provider, self.model)
                return parsed
            except Exception as error:
                last_error = error
                reason = safe_llm_failure_reason(error)
                if "Empty LLM" in str(error) or "not an object" in str(error) or "JSON" in str(error):
                    logger.info("LLM_PARSE_FAILED provider=%s model=%s reason=%s", self.provider, self.model, reason)
                logger.error(
                    "LLM_REQUEST_ATTEMPT_FAILED provider=%s model=%s attempt=%s reason=%s",
                    self.provider,
                    self.model,
                    attempt + 1,
                    reason,
                )
                if attempt < retries and self._should_retry(error):
                    time.sleep(min(8, 2 ** attempt))
                    continue
                break
        raise LLMGenerationError(safe_llm_failure_reason(last_error))

    def _dispatch(self, system_prompt: str, user_prompt: str, temperature: float) -> str:
        provider = self.provider
        if provider in ("openai", "openai_compatible"):
            return self._openai_compatible(system_prompt, user_prompt, temperature)
        if provider == "anthropic":
            return self._anthropic(system_prompt, user_prompt, temperature)
        raise LLMGenerationError("unsupported_provider")

    def _openai_compatible(self, system_prompt: str, user_prompt: str, temperature: float) -> str:
        url = _join_url(self.config["base_url"], "chat/completions")
        headers = {
            "Authorization": f"Bearer {self.config['api_key']}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "temperature": temperature,
            "stream": False,
            "max_tokens": int(self.config.get("max_tokens") or 6144),
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        response = requests.post(
            url=url,
            headers=headers,
            json=payload,
            timeout=self._request_timeout(),
        )
        if response.status_code == 400 and "response_format" in (response.text or ""):
            payload.pop("response_format", None)
            response = requests.post(
                url=url,
                headers=headers,
                json=payload,
                timeout=self._request_timeout(),
            )
        if response.status_code == 404:
            raise LLMGenerationError("model_not_found")
        if response.status_code == 429 or response.status_code >= 500:
            raise RuntimeError(f"Transient LLM HTTP {response.status_code}")
        if response.status_code != 200:
            raise RuntimeError(f"LLM HTTP {response.status_code}")
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("Empty LLM choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if content is None and isinstance(message.get("content"), list):
            content = "".join(
                part.get("text", "") for part in message["content"] if isinstance(part, dict)
            )
        if not content:
            raise RuntimeError("Empty LLM response")
        return content

    def _anthropic(self, system_prompt: str, user_prompt: str, temperature: float) -> str:
        url = _join_url(self.config["base_url"], "v1/messages")
        headers = {
            "x-api-key": self.config["api_key"],
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": int(self.config.get("max_tokens") or 8000),
            "temperature": temperature,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": user_prompt + "\n\nRespond with a single JSON object only.",
                }
            ],
        }
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=self._request_timeout(),
        )
        if response.status_code == 404:
            raise LLMGenerationError("model_not_found")
        if response.status_code == 429 or response.status_code >= 500:
            raise RuntimeError(f"Transient LLM HTTP {response.status_code}")
        if response.status_code != 200:
            raise RuntimeError(f"LLM HTTP {response.status_code}")
        data = response.json()
        parts = data.get("content") or []
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        return text

    def llm_status(self, *, probe: bool = True) -> dict[str, Any]:
        status = {
            "configured": self.enabled,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.config.get("base_url") or "",
            "service": self.service_name,
            "timeout_seconds": self.config.get("timeout"),
            "reachable": False,
            "model_found": None,
        }
        if not self.enabled or not probe:
            return status
        try:
            if self.provider == "openai":
                status["reachable"] = self._check_openai_health()
            elif self.provider == "openai_compatible":
                probe_result = self._ollama_probe() if self.service_name == "ollama" or ":11434" in str(self.config.get("base_url") or "") else {
                    "reachable": self._check_openai_health(),
                    "model_found": None,
                }
                status["reachable"] = bool(probe_result.get("reachable"))
                status["model_found"] = probe_result.get("model_found")
            elif self.provider == "anthropic":
                status["reachable"] = self._check_anthropic_health()
        except Exception:
            status["reachable"] = False
        return status

    def _service_root(self) -> str:
        base = self.config.get("base_url", "")
        if base.endswith("/v1"):
            return base[: -len("/v1")]
        return base

    def _check_openai_health(self) -> bool:
        url = _join_url(self.config["base_url"], "models")
        headers = {
            "Authorization": f"Bearer {self.config['api_key']}",
        }
        response = requests.get(url, headers=headers, timeout=min(5, int(self.config.get("timeout") or 5)))
        return response.status_code == 200

    def _ollama_probe(self) -> dict[str, Any]:
        root = self._service_root()
        url = _join_url(root, "api/tags")
        headers = {
            "Authorization": f"Bearer {self.config['api_key']}",
        }
        response = requests.get(url, headers=headers, timeout=min(5, int(self.config.get("timeout") or 5)))
        if response.status_code != 200:
            return {"reachable": False, "model_found": None}
        try:
            payload = response.json() or {}
        except Exception:
            return {"reachable": True, "model_found": None}
        names = {
            str(item.get("name") or item.get("model") or "").strip()
            for item in (payload.get("models") or [])
            if isinstance(item, dict)
        }
        target = (self.model or "").strip()
        found = None
        if target:
            found = target in names or any(
                name == target or name.startswith(f"{target}:") or target.startswith(f"{name}:")
                for name in names
            )
        return {"reachable": True, "model_found": found}

    def _check_ollama_health(self) -> bool:
        return bool(self._ollama_probe().get("reachable"))

    def _check_anthropic_health(self) -> bool:
        url = _join_url(self.config["base_url"], "v1/models")
        headers = {
            "x-api-key": self.config["api_key"],
        }
        response = requests.get(url, headers=headers, timeout=min(5, int(self.config.get("timeout") or 5)))
        return response.status_code == 200

    @staticmethod
    def _extract_json(raw: str) -> Any:
        text = (raw or "").strip()
        if not text:
            raise RuntimeError("Empty LLM response")

        # Strip common Markdown fences and surrounding explanation.
        fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
        if fence_match:
            text = fence_match.group(1).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start : end + 1])
            raise
