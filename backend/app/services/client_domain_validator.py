"""Client / project domain validation for truthful resume generation."""

from __future__ import annotations

import re

BANKING_CLIENTS = [
    "jpmorgan", "jp morgan", "chase", "wells fargo", "bank of america", "citi",
    "citibank", "goldman", "morgan stanley", "capital one", "us bank",
]

DOMAIN_TERMS = {
    "banking": [
        "payments", "accounts", "transactions", "fraud detection", "loan processing",
        "customer portals", "digital banking", "financial reporting", "banking",
        "finance", "trading", "settlement",
    ],
    "healthcare": [
        "claims", "patient records", "provider portals", "healthcare apis",
        "eligibility", "member services", "hipaa", "ehr", "clinical",
    ],
    "retail": [
        "orders", "inventory", "product catalog", "customer experience",
        "checkout", "fulfillment", "e-commerce", "ecommerce",
    ],
    "insurance": [
        "policy management", "claims processing", "premium calculation",
        "underwriting", "insurance",
    ],
}


def detect_client_domain(client_name: str = "", project_text: str = "", company: str = "") -> str:
    blob = f"{client_name} {company} {project_text}".lower()
    if any(name in blob for name in BANKING_CLIENTS) or "bank" in blob:
        return "banking"
    scores = {}
    for domain, terms in DOMAIN_TERMS.items():
        scores[domain] = sum(1 for term in terms if term in blob)
    best = max(scores, key=scores.get)
    if scores[best] >= 2:
        return best
    return ""


def validate_client_domain(
    employments: list[dict],
    projects: list[dict],
    generated_text: str,
) -> dict:
    warnings = []
    removed = []
    detected = ""

    contexts = []
    for emp in employments or []:
        text = " ".join(emp.get("raw_lines") or []) + " " + " ".join(emp.get("bullets") or [])
        domain = detect_client_domain(emp.get("client", ""), text, emp.get("company", ""))
        if domain:
            detected = detected or domain
            contexts.append((domain, emp.get("client") or emp.get("company") or "role", text))
    for project in projects or []:
        text = f"{project.get('description', '')} {' '.join(project.get('bullets') or [])}"
        domain = detect_client_domain(project.get("client", ""), text, project.get("name", ""))
        if domain:
            detected = detected or domain
            contexts.append((domain, project.get("name") or "project", text))

    if not detected:
        return {
            "detectedClientDomain": "",
            "clientDomainWarnings": [],
            "invalidProjectContentRemoved": [],
        }

    foreign_domains = [d for d in DOMAIN_TERMS if d != detected]
    generated_lower = (generated_text or "").lower()
    for foreign in foreign_domains:
        for term in DOMAIN_TERMS[foreign]:
            if term in generated_lower and term not in " ".join(t for _, _, t in contexts).lower():
                # Foreign domain language introduced
                pattern = re.compile(re.escape(term), re.I)
                if pattern.search(generated_text or ""):
                    warnings.append(
                        f"Removed or flagged '{term}' language inconsistent with {detected} domain."
                    )
                    removed.append(term)

    return {
        "detectedClientDomain": detected,
        "clientDomainWarnings": list(dict.fromkeys(warnings)),
        "invalidProjectContentRemoved": list(dict.fromkeys(removed)),
    }


def scrub_foreign_domain_language(text: str, detected_domain: str) -> str:
    if not detected_domain or not text:
        return text
    result = text
    for domain, terms in DOMAIN_TERMS.items():
        if domain == detected_domain:
            continue
        for term in terms:
            # Only strip if clearly foreign phrase; keep shared words carefully
            if term in {"claims"} and detected_domain == "insurance":
                continue
            result = re.sub(rf"(?i)\b{re.escape(term)}\b", "", result)
    result = re.sub(r"\s{2,}", " ", result)
    result = re.sub(r"\s+([.,;:])", r"\1", result)
    return result.strip()
