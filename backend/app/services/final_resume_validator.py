"""Final validation gate before persisting an optimized resume."""

from __future__ import annotations

import re

from app.services.keyword_utils import contains_keyword
from app.services.resume_generator import FAKE_METRIC_PATTERN


def validate_final_resume(
    original_text: str,
    generated_text: str,
    original_structure: dict,
    generated_sections: list[dict],
    unsupported_keywords: list[str],
    selected_technologies: list[str],
) -> dict:
    warnings = []
    errors = []

    original_contact = (original_structure or {}).get("contact") or ""
    generated_contact = (generated_text or "").split("\n\n")[0] if generated_text else ""

    # Contact / identity
    for field in ("email", "phone", "name"):
        value = (original_structure or {}).get(field) or ""
        if value and value not in (generated_text or ""):
            errors.append(f"Protected field changed or missing: {field}")

    for link in (original_structure or {}).get("links") or []:
        if link and link not in (generated_text or ""):
            warnings.append(f"Contact link missing from generated resume: {link}")

    # Section order / headings — original sections must keep their relative order and
    # none may be removed, but truthful NEW sections (e.g. a "Projects" or "Currently
    # Learning" section added for confirmed evidence) may be appended without failing.
    original_order = (original_structure or {}).get("section_order") or [
        s.get("heading") for s in (original_structure or {}).get("sections") or []
    ]
    generated_order = [s.get("heading") for s in generated_sections or []]

    def _is_subsequence(needle: list, haystack: list) -> bool:
        it = iter(haystack)
        return all(item in it for item in needle)

    structure_preserved = _is_subsequence(original_order, generated_order) if original_order else True
    if original_order and not structure_preserved:
        errors.append("Section order or headings were not preserved.")

    # Employers / dates
    for emp in (original_structure or {}).get("employments") or []:
        company = emp.get("company") or ""
        dates = emp.get("dates") or ""
        if company and company not in (generated_text or ""):
            errors.append(f"Company name missing or changed: {company}")
        if dates and dates not in (generated_text or ""):
            errors.append(f"Employment dates missing or changed: {dates}")
        client = emp.get("client") or ""
        if client and client not in (generated_text or ""):
            warnings.append(f"Client name missing from generated resume: {client}")

    # Education / certifications sections exact-ish preservation
    for section in (original_structure or {}).get("sections") or []:
        heading = (section.get("heading") or "").lower()
        if re.search(r"education|certification|certificate", heading):
            original_content = (section.get("content") or "").strip()
            generated_content = ""
            for gen in generated_sections or []:
                if (gen.get("heading") or "").lower() == (section.get("heading") or "").lower():
                    generated_content = (gen.get("content") or "").strip()
                    break
            if original_content and generated_content != original_content:
                errors.append(f"Protected section modified: {section.get('heading')}")

    # Unsupported JD technologies must not appear as newly added experience claims
    for tech in unsupported_keywords or []:
        if contains_keyword(generated_text or "", tech) and not contains_keyword(original_text or "", tech):
            errors.append(f"Unsupported technology introduced: {tech}")

    # Fake metrics
    if FAKE_METRIC_PATTERN.search(generated_text or "") and not FAKE_METRIC_PATTERN.search(original_text or ""):
        errors.append("Generated resume introduced metrics not present in the original resume.")

    if not (generated_text or "").strip():
        errors.append("Generated resume is empty.")

    if len((generated_text or "").split()) < 40:
        warnings.append("Generated resume is unusually short.")

    # Selected technologies should appear at least once
    missing_selected = [
        tech for tech in (selected_technologies or [])[:10]
        if not contains_keyword(generated_text or "", tech)
    ]
    if missing_selected:
        warnings.append(
            "Some selected technologies were not visible in the generated text: "
            + ", ".join(missing_selected)
        )

    status = "passed"
    if errors:
        status = "failed"
    elif warnings:
        status = "passed_with_warnings"

    return {
        "status": status,
        "warnings": warnings,
        "errors": errors,
        "protectedFieldsVerified": not any("Protected field" in e for e in errors),
        "structurePreserved": structure_preserved and not any("Section order" in e for e in errors),
    }
