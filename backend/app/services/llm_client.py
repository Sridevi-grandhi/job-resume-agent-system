"""
Provider-independent LLM client.

Configured via environment variables:
  LLM_PROVIDER   - openai | openai_compatible | anthropic | none
  LLM_MODEL      - model id (e.g. gpt-4o, claude-sonnet-4-20250514)
  LLM_API_KEY    - secret key (backend only)
  LLM_BASE_URL   - optional API base (defaults per provider)
  LLM_TIMEOUT_SECONDS - request timeout (default 60)
  LLM_MAX_RETRIES     - transient retry count (default 2)
  LLM_TEMPERATURE     - generation temperature (default 0.1)

Business rules stay outside this module. Validators remain deterministic.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import requests


logger = logging.getLogger(__name__)

PROMPT_VERSION = "ats-optimizer-v1"


def llm_config() -> dict[str, Any]:
    provider = (os.getenv("LLM_PROVIDER") or "none").strip().lower()
    model = (os.getenv("LLM_MODEL") or "").strip()
    api_key = (os.getenv("LLM_API_KEY") or "").strip()
    base_url = (os.getenv("LLM_BASE_URL") or "").strip().rstrip("/")
    timeout = int(os.getenv("LLM_TIMEOUT_SECONDS") or "60")
    max_retries = int(os.getenv("LLM_MAX_RETRIES") or "2")
    temperature = float(os.getenv("LLM_TEMPERATURE") or "0.1")

    if not base_url:
        if provider in ("openai", "openai_compatible"):
            base_url = "https://api.openai.com/v1"
        elif provider == "anthropic":
            base_url = "https://api.anthropic.com"
        else:
            base_url = ""

    if not model:
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
        "max_retries": max_retries,
        "temperature": max(0.0, min(1.0, temperature)),
        "enabled": provider not in ("", "none", "off", "disabled") and bool(api_key),
        "prompt_version": PROMPT_VERSION,
    }


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

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        """
        Call the configured provider and parse a JSON object response.
        Raises RuntimeError on hard failure after retries.
        """
        if not self.enabled:
            raise RuntimeError("LLM is not configured (set LLM_PROVIDER and LLM_API_KEY).")

        last_error = None
        retries = max(0, int(self.config.get("max_retries") or 0))
        for attempt in range(retries + 1):
            try:
                raw = self._dispatch(system_prompt, user_prompt, temperature)
                parsed = self._extract_json(raw)
                if not isinstance(parsed, dict):
                    raise RuntimeError("LLM JSON response was not an object.")
                return parsed
            except Exception as error:
                last_error = error
                logger.error(
                    "LLM attempt failed provider=%s model=%s attempt=%s error=%s",
                    self.provider,
                    self.model,
                    attempt + 1,
                    type(error).__name__,
                )
                if attempt < retries:
                    time.sleep(min(8, 2 ** attempt))
        raise RuntimeError(f"LLM request failed: {type(last_error).__name__}")

    def _dispatch(self, system_prompt: str, user_prompt: str, temperature: float) -> str:
        provider = self.provider
        if provider in ("openai", "openai_compatible"):
            return self._openai_compatible(system_prompt, user_prompt, temperature)
        if provider == "anthropic":
            return self._anthropic(system_prompt, user_prompt, temperature)
        raise RuntimeError(f"Unsupported LLM_PROVIDER: {provider}")

    def _openai_compatible(self, system_prompt: str, user_prompt: str, temperature: float) -> str:
        url = f"{self.config['base_url']}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config['api_key']}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=self.config["timeout"],
        )
        if response.status_code == 429 or response.status_code >= 500:
            raise RuntimeError(f"Transient LLM HTTP {response.status_code}")
        if response.status_code != 200:
            raise RuntimeError(f"LLM HTTP {response.status_code}")
        data = response.json()
        return data["choices"][0]["message"]["content"]

    def _anthropic(self, system_prompt: str, user_prompt: str, temperature: float) -> str:
        url = f"{self.config['base_url']}/v1/messages"
        headers = {
            "x-api-key": self.config["api_key"],
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": 8000,
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
            timeout=self.config["timeout"],
        )
        if response.status_code == 429 or response.status_code >= 500:
            raise RuntimeError(f"Transient LLM HTTP {response.status_code}")
        if response.status_code != 200:
            raise RuntimeError(f"LLM HTTP {response.status_code}")
        data = response.json()
        parts = data.get("content") or []
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        return text

    @staticmethod
    def _extract_json(raw: str) -> Any:
        text = (raw or "").strip()
        if not text:
            raise RuntimeError("Empty LLM response")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start : end + 1])
            raise
