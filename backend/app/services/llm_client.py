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
import re
import time
from typing import Any

import requests


logger = logging.getLogger(__name__)

PROMPT_VERSION = "ats-optimizer-v1"


class LLMGenerationError(RuntimeError):
    """Provider failure carrying a safe, normalized reason code."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"LLM request failed: {reason}")


def safe_llm_failure_reason(error: Exception) -> str:
    """Convert provider exceptions into stable, non-sensitive reason codes."""
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
    if "not configured" in message:
        return "llm_not_configured"
    return "unexpected_provider_error"


def generation_timeout_config() -> dict[str, int]:
    """Return aligned LLM, workflow, and frontend polling time budgets."""
    llm_timeout = max(1, int(os.getenv("LLM_TIMEOUT_SECONDS") or "180"))
    connect_timeout = max(1, int(os.getenv("LLM_CONNECT_TIMEOUT_SECONDS") or "5"))
    overall = int(os.getenv("ATS_GENERATION_TIMEOUT_SECONDS") or "300")
    poll_interval_ms = max(250, int(os.getenv("ATS_POLL_INTERVAL_MS") or "2000"))
    overall = max(overall, llm_timeout + 60)
    return {
        "llm_timeout_seconds": llm_timeout,
        "connect_timeout_seconds": min(connect_timeout, llm_timeout),
        "generation_timeout_seconds": overall,
        "poll_interval_ms": poll_interval_ms,
        "poll_timeout_seconds": overall,
        "stale_running_seconds": max(
            overall + 60,
            int(os.getenv("ATS_STALE_WORKFLOW_SECONDS") or "600"),
        ),
    }


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
        "max_retries": max_retries,
        "temperature": max(0.0, min(1.0, temperature)),
        "enabled": provider not in ("", "none", "off", "disabled") and bool(api_key),
        "prompt_version": PROMPT_VERSION,
    }


BULLET_REWRITE_PROMPT_VERSION = "bullet-rewrite-v1"


def bullet_rewrite_llm_config() -> dict[str, Any]:
    """Independent config for the small bullet-level "Try Again" rewrite.

    Deliberately separate from llm_config() (full-resume generation): Try
    Again is intentionally routed to a local Ollama instance regardless of
    what LLM_PROVIDER is set to for full generation — full-resume generation
    and one-bullet rewriting are different-sized tasks with different
    providers by design. Reuses LLM_BASE_URL/LLM_MODEL/LLM_API_KEY only when
    the *main* provider is itself already "openai_compatible" (i.e. already
    pointed at a local/Ollama-style endpoint) — never silently reuses a real
    hosted-provider API key configured for full generation.
    """
    provider = (os.getenv("BULLET_REWRITE_LLM_PROVIDER") or "openai_compatible").strip().lower()
    base_url = (os.getenv("BULLET_REWRITE_LLM_BASE_URL") or "").strip().rstrip("/")
    model = (os.getenv("BULLET_REWRITE_LLM_MODEL") or "").strip()
    api_key = (os.getenv("BULLET_REWRITE_LLM_API_KEY") or "").strip()
    timeout = max(1, int(os.getenv("BULLET_REWRITE_LLM_TIMEOUT_SECONDS") or "30"))
    max_retries = max(0, int(os.getenv("BULLET_REWRITE_LLM_MAX_RETRIES") or "1"))
    temperature = float(os.getenv("BULLET_REWRITE_LLM_TEMPERATURE") or "0.3")

    main_provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    reuse_main = main_provider == "openai_compatible"
    if not base_url:
        base_url = (os.getenv("LLM_BASE_URL") or "").strip().rstrip("/") if reuse_main else ""
        base_url = base_url or "http://localhost:11434/v1"
    if not model and reuse_main:
        model = (os.getenv("LLM_MODEL") or "").strip()
    if not api_key:
        api_key = (os.getenv("LLM_API_KEY") or "").strip() if reuse_main else ""
        api_key = api_key or "ollama"

    return {
        "provider": provider,
        "model": model or "deterministic-fallback",
        "api_key": api_key,
        "base_url": base_url,
        "timeout": timeout,
        "max_retries": max_retries,
        "temperature": max(0.0, min(1.0, temperature)),
        "enabled": provider not in ("", "none", "off", "disabled") and bool(model),
        "prompt_version": BULLET_REWRITE_PROMPT_VERSION,
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

    @property
    def service_name(self) -> str | None:
        if self.provider != "openai_compatible":
            return None
        base_url = str(self.config.get("base_url") or "").lower()
        local_hosts = ("localhost", "127.0.0.1", "host.docker.internal")
        if "ollama" in base_url or (
            ":11434" in base_url and any(host in base_url for host in local_hosts)
        ):
            return "ollama"
        return None

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

        logger.info(
            "LLM generation started provider=%s model=%s",
            self.provider,
            self.model,
        )
        last_error = None
        retries = max(0, int(self.config.get("max_retries") or 0))
        for attempt in range(retries + 1):
            try:
                raw = self._dispatch(system_prompt, user_prompt, temperature)
                parsed = self._extract_json(raw)
                if not isinstance(parsed, dict):
                    raise RuntimeError("LLM JSON response was not an object.")
                logger.info(
                    "LLM generation succeeded provider=%s model=%s",
                    self.provider,
                    self.model,
                )
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
        # Preserve the real failure reason (timeout/connection/model_not_found/...)
        # instead of discarding it into a generic wrapper — callers classify
        # fallback_reason from this exception, and a mislabeled reason here
        # both hides the true cause in logs and can defeat the workflow's
        # retry-skip logic (see ats_optimization_workflow_service.execute_workflow).
        raise LLMGenerationError(safe_llm_failure_reason(last_error))

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
            url=url,
            headers=headers,
            json=payload,
            timeout=self.config["timeout"],
        )
        if response.status_code == 400 and "response_format" in (response.text or ""):
            payload.pop("response_format", None)
            response = requests.post(
                url=url,
                headers=headers,
                json=payload,
                timeout=self.config["timeout"],
            )
        if response.status_code == 400 and "temperature" in (response.text or "").lower():
            # Reasoning-tier models (o1/o3/gpt-5 family, ...) reject any
            # non-default temperature outright. Retry once at the model's
            # own default rather than treating this as a hard failure —
            # otherwise every call to such a model 400s permanently.
            payload.pop("temperature", None)
            response = requests.post(
                url=url,
                headers=headers,
                json=payload,
                timeout=self.config["timeout"],
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise RuntimeError(f"Transient LLM HTTP {response.status_code}")
        if response.status_code != 200:
            raise RuntimeError(f"LLM HTTP {response.status_code}: {(response.text or '')[:300]}")
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

    def llm_config_status(self) -> dict[str, Any]:
        """Configuration-only status — never makes a network call. Used by
        /health, which must respond instantly for liveness/readiness probes
        rather than round-tripping to an external LLM provider on every
        check (see llm_status() for the active-probe variant, used
        elsewhere where a real reachability check is wanted)."""
        return {
            "configured": self.enabled,
            "provider": self.provider,
            "model": self.model,
            "reachable": None,
        }

    def llm_status(self) -> dict[str, Any]:
        status = {
            "configured": self.enabled,
            "provider": self.provider,
            "model": self.model,
            "reachable": False,
        }
        if not self.enabled:
            return status
        try:
            if self.provider == "openai":
                status["reachable"] = self._check_openai_health()
            elif self.provider == "openai_compatible":
                status["reachable"] = self._check_ollama_health()
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
        url = f"{self.config['base_url']}/models"
        headers = {
            "Authorization": f"Bearer {self.config['api_key']}",
        }
        response = requests.get(url, headers=headers, timeout=min(5, self.config["timeout"]))
        return response.status_code == 200

    def _check_ollama_health(self) -> bool:
        root = self._service_root()
        url = f"{root}/api/tags"
        headers = {
            "Authorization": f"Bearer {self.config['api_key']}",
        }
        response = requests.get(url, headers=headers, timeout=min(5, self.config["timeout"]))
        return response.status_code == 200

    def _check_anthropic_health(self) -> bool:
        url = f"{self.config['base_url']}/v1/models"
        headers = {
            "x-api-key": self.config["api_key"],
        }
        response = requests.get(url, headers=headers, timeout=min(5, self.config["timeout"]))
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
