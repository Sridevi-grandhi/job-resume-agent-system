"""
Normalize and repair LLM resume-generation JSON before Pydantic validation.

Handles common Ollama / open-weight model output quirks without inventing resume facts.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from pydantic import ValidationError

logger = logging.getLogger(__name__)

# Explicit contract echoed in prompts and schema-repair requests.
GENERATION_RESPONSE_SCHEMA = {
    "generated_resume_plain_text": "string (required, complete resume text)",
    "rewritten_summary": "string (optional)",
    "rewritten_bullets": "array of {text: string, supported_by: [E# or UC# ids]} (optional)",
    "keyword_evidence": "array (optional)",
    "warnings": "array of strings (optional)",
    "factual_preservation_confirmed": "boolean (required)",
}

_PLAIN_TEXT_KEYS = (
    "generated_resume_plain_text",
    "generated_resume",
    "resume_plain_text",
    "resume_text",
    "plain_text",
    "resume",
    "optimized_resume",
    "output",
)

_WRAPPER_KEYS = ("response", "result", "data", "json", "output", "answer")


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "yes", "1"}:
        return True
    if text in {"false", "no", "0"}:
        return False
    return default


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.strip()
        return [value] if value else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _sanitize_preview(raw: str, limit: int = 240) -> str:
    """Short sanitized preview for dev logs — no full resume content."""
    text = re.sub(r"\s+", " ", (raw or "")).strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def summarize_validation_errors(error: ValidationError) -> dict[str, Any]:
    missing_fields: list[str] = []
    invalid_field_types: list[str] = []
    unexpected_fields: list[str] = []
    validation_errors: list[str] = []

    for item in error.errors():
        loc = ".".join(str(part) for part in item.get("loc") or ())
        msg = item.get("msg") or "invalid"
        kind = item.get("type") or ""
        validation_errors.append(f"{loc}: {msg}" if loc else str(msg))
        if kind == "missing":
            missing_fields.append(loc or str(item.get("loc")))
        elif "type" in kind:
            invalid_field_types.append(loc or kind)
        else:
            invalid_field_types.append(loc or kind)

    return {
        "validation_errors": validation_errors[:20],
        "missing_fields": missing_fields[:20],
        "invalid_field_types": invalid_field_types[:20],
        "unexpected_fields": unexpected_fields[:20],
    }


def unwrap_response_object(raw: Any) -> tuple[Any, bool]:
    """Unwrap a single obvious wrapper object. Returns (value, repair_used)."""
    if not isinstance(raw, dict):
        return raw, False
    if any(key in raw for key in _PLAIN_TEXT_KEYS):
        return raw, False
    for key in _WRAPPER_KEYS:
        inner = raw.get(key)
        if isinstance(inner, dict) and any(k in inner for k in _PLAIN_TEXT_KEYS):
            return inner, True
    return raw, False


def normalize_generation_response(raw: Any) -> tuple[dict[str, Any], bool]:
    """
    Map alternate keys / types into the StructuredOptimizerResponse contract.
    Returns (normalized_dict, json_repair_used).
    """
    repair_used = False
    value, unwrapped = unwrap_response_object(raw)
    repair_used = repair_used or unwrapped
    if not isinstance(value, dict):
        raise ValueError("LLM JSON response was not an object.")

    normalized: dict[str, Any] = dict(value)

    plain = ""
    for key in _PLAIN_TEXT_KEYS:
        candidate = normalized.get(key)
        if isinstance(candidate, str) and candidate.strip():
            plain = candidate.strip()
            if key != "generated_resume_plain_text":
                repair_used = True
            break
    if plain:
        normalized["generated_resume_plain_text"] = plain

    if "rewritten_summary" not in normalized and isinstance(normalized.get("summary"), str):
        normalized["rewritten_summary"] = normalized["summary"]
        repair_used = True

    bullets_raw = normalized.get("rewritten_bullets")
    if bullets_raw is None and isinstance(normalized.get("bullets"), list):
        bullets_raw = normalized["bullets"]
        repair_used = True

    cleaned_bullets: list[dict[str, Any]] = []
    if isinstance(bullets_raw, list):
        for item in bullets_raw:
            if isinstance(item, str) and item.strip():
                cleaned_bullets.append({"text": item.strip(), "supported_by": []})
                repair_used = True
                continue
            if not isinstance(item, dict):
                repair_used = True
                continue
            text = str(item.get("text") or item.get("bullet") or "").strip()
            supported = item.get("supported_by") or item.get("evidence_ids") or item.get("evidence")
            supported_list = _coerce_str_list(supported)
            if isinstance(supported, str) and supported.strip():
                repair_used = True
            if text and supported_list:
                cleaned_bullets.append({"text": text, "supported_by": supported_list})
            elif text or supported_list:
                repair_used = True
    normalized["rewritten_bullets"] = cleaned_bullets

    for list_key, alt_keys in (
        ("keyword_evidence", ("keywords", "keywordEvidence")),
        ("warnings", ("warning", "notes")),
    ):
        if list_key not in normalized:
            for alt in alt_keys:
                if alt in normalized:
                    normalized[list_key] = normalized.pop(alt)
                    repair_used = True
                    break
        if not isinstance(normalized.get(list_key), list):
            normalized[list_key] = _coerce_str_list(normalized.get(list_key))

    if "factual_preservation_confirmed" not in normalized:
        for alt in ("factual_preservation", "facts_preserved", "preserved_facts"):
            if alt in normalized:
                normalized["factual_preservation_confirmed"] = _coerce_bool(normalized.pop(alt))
                repair_used = True
                break
        else:
            normalized["factual_preservation_confirmed"] = True
            repair_used = True
    else:
        before = normalized["factual_preservation_confirmed"]
        normalized["factual_preservation_confirmed"] = _coerce_bool(before, default=True)
        if normalized["factual_preservation_confirmed"] != before:
            repair_used = True

    normalized.setdefault("rewritten_summary", "")
    normalized.setdefault("keyword_evidence", [])
    normalized.setdefault("warnings", [])

    return normalized, repair_used


def extract_and_parse_json(raw: str) -> tuple[dict[str, Any], bool]:
    """
    Extract JSON from raw LLM text with safe normalization passes.
    Returns (parsed_dict, extraction_repair_used).
    """
    repair_used = False
    text = (raw or "").strip()
    if not text:
        raise RuntimeError("Empty LLM response")

    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S | re.I)
    if fence_match:
        text = fence_match.group(1).strip()
        repair_used = True

    parsed: Any
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            snippet = text[start : end + 1]
            snippet = re.sub(r",\s*([}\]])", r"\1", snippet)
            parsed = json.loads(snippet)
            repair_used = True
        else:
            raise

    if not isinstance(parsed, dict):
        raise RuntimeError("LLM JSON response was not an object.")

    normalized, norm_repair = normalize_generation_response(parsed)
    return normalized, repair_used or norm_repair


def log_schema_validation_failure(
    *,
    workflow_id: int | None,
    model: str,
    response_length: int,
    error: ValidationError,
    raw_preview: str | None = None,
) -> dict[str, Any]:
    summary = summarize_validation_errors(error)
    logger.warning(
        "LLM_SCHEMA_VALIDATION_FAILED workflow_id=%s model=%s response_length=%s "
        "validation_errors=%s missing_fields=%s invalid_field_types=%s unexpected_fields=%s",
        workflow_id or "n/a",
        model,
        response_length,
        summary["validation_errors"],
        summary["missing_fields"],
        summary["invalid_field_types"],
        summary["unexpected_fields"],
    )
    if os.getenv("APP_ENV", "development").lower() in {"dev", "development", "local", "test"} and raw_preview:
        logger.debug(
            "LLM_SCHEMA_VALIDATION_PREVIEW workflow_id=%s preview=%s",
            workflow_id or "n/a",
            _sanitize_preview(raw_preview),
        )
    return summary


def build_schema_repair_prompt(
    invalid_payload: dict[str, Any],
    validation_summary: dict[str, Any],
) -> str:
    schema_text = json.dumps(GENERATION_RESPONSE_SCHEMA, indent=2)
    errors_text = json.dumps(validation_summary.get("validation_errors") or [], indent=2)
    payload_text = json.dumps(invalid_payload, ensure_ascii=False)[:3500]
    return (
        "The following JSON failed schema validation.\n"
        f"Validation errors:\n{errors_text}\n\n"
        f"Required schema:\n{schema_text}\n\n"
        "Invalid JSON (fix structure only — do not add employers, dates, skills, or metrics):\n"
        f"{payload_text}\n\n"
        "Return the same resume content corrected only to match the schema. Do not add facts. "
        "Return JSON only — no markdown, no explanation."
    )
