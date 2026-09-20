"""Deterministic job-application email classifier (Part 6).

Rules run first and are always authoritative on their own; Ollama is
consulted only to help pick between multiple plausible event types when the
rules genuinely can't decide (confidence below AMBIGUOUS_THRESHOLD) — its
output only ever feeds back into this classifier's own confidence-scored
result, and is itself capped so it can never manufacture a confident,
auto-actionable classification out of nothing. Nothing here writes to the
database; see application_tracking_service for the validated update step
("classifier -> confidence check -> matching -> proposed event -> validated
update").
"""

from __future__ import annotations

import re
from typing import Any

EVENT_TYPES = (
    "APPLICATION_CONFIRMATION",
    "APPLICATION_UPDATE",
    "RECRUITER_OUTREACH",
    "INTERVIEW_INVITE",
    "ASSESSMENT_REQUEST",
    "OFFER",
    "REJECTION",
    "OTHER",
)

AMBIGUOUS_THRESHOLD = 0.55
# LLM-assisted classifications are capped here — never as confident as a
# clean deterministic match, so they can inform a "needs confirmation" UI
# prompt but never clear the auto-update threshold in application_tracking_service.
LLM_ASSIST_CONFIDENCE_CAP = 0.6

# Ordered most-specific-first: once a pattern set matches, later ones are
# still checked so a stronger/more specific signal can still win.
_RULES: list[tuple[str, tuple[str, ...], float]] = [
    ("OFFER", (
        r"\bpleased to offer\b", r"\bjob offer\b", r"\boffer letter\b",
        r"\bextend(?:ing)? an offer\b", r"\bwe(?:'d| would) like to offer\b",
    ), 0.95),
    ("REJECTION", (
        r"\bdecided not to move forward\b", r"\bwill not be moving forward\b",
        r"\bnot be proceeding\b", r"\bpursu(?:e|ing) other candidates\b",
        r"\bmoved forward with other candidates\b", r"\bposition has been filled\b",
        r"\bwe regret to inform\b",
    ), 0.9),
    ("ASSESSMENT_REQUEST", (
        r"\bcomplete (?:this|the) assessment\b", r"\bcoding challenge\b",
        r"\btake-home (?:assignment|assessment|test)\b", r"\bonline assessment\b",
        r"\bassessment link\b", r"\btechnical assessment\b", r"\bcomplete a short test\b",
    ), 0.9),
    ("INTERVIEW_INVITE", (
        r"\bschedule (?:an|your) interview\b", r"\binterview invitation\b",
        r"\bwould like to (?:interview|speak with) you\b", r"\bbook a time\b",
        r"\bschedule a call\b", r"\bavailable for a call\b", r"\bnext step is an interview\b",
        r"\bphone screen\b",
    ), 0.9),
    ("APPLICATION_CONFIRMATION", (
        r"\bthank you for applying\b", r"\bapplication (?:has been )?received\b",
        r"\bwe(?:'ve| have) received your application\b", r"\bconfirm(?:ing|ation of)? your application\b",
        r"\bsuccessfully submitted\b",
    ), 0.9),
    ("APPLICATION_UPDATE", (
        r"\bupdate on your application\b", r"\bstill under review\b",
        r"\bapplication status\b", r"\breviewing your application\b",
        r"\bcurrently being reviewed\b",
    ), 0.75),
    ("RECRUITER_OUTREACH", (
        r"\bcame across your profile\b", r"\bexciting opportunity\b",
        r"\breaching out (?:regarding|about|to you)\b", r"\bI'?m a recruiter\b",
        r"\bwould you be open to\b", r"\bare you open to new opportunities\b",
    ), 0.7),
]

# Phrase-level, never a bare generic word — see CURRENT VERIFIED BUG: the
# old single-word check (which included a bare "offer") false-positived on
# a Shutterfly "...50% off ALL cards!" promotional email, since retail copy
# routinely uses words like "offer"/"application"/"position" in a non-job
# sense ("special offer", "submit your rewards application", "position your
# order"...). Each of these phrases only occurs in a genuine employment
# context.
_STRONG_JOB_CONTEXT_PATTERNS = (
    r"\bapplication (?:received|id|number)\b",
    r"\byour application\b",
    r"\bjob application\b",
    r"\bemployment application\b",
    r"\bthank you for applying\b",
    r"\bcandidate id\b",
    r"\byour candidacy\b",
    r"\brecruiter\b",
    r"\brecruiting\b",
    r"\btalent acquisition\b",
    r"\bhiring (?:team|manager|process)\b",
    r"\binterview\b",
    r"\bschedule a call\b",
    r"\bcoding challenge\b",
    r"\btechnical assessment\b",
    r"\bemployment offer\b",
    r"\boffer letter\b",
    r"\bjob offer\b",
    r"\bnot moving forward\b",
    r"\bposition has been filled\b",
    r"\bbackground check\b",
    r"\bonboarding\b",
    r"\bi-9\b",
    r"\bhuman resources\b",
    r"\bapplicant tracking\b",
    r"\bposition you applied\b",
    r"\brole you applied\b",
    r"\bjob posting\b",
)

# A single generic word from this set (position/role/job/resume/cv/
# applicant/candidate/opportunity) is NOT, on its own, evidence of a
# job-related email — each is common outside employment contexts too. Only
# combined with at least one OTHER distinct weak signal (e.g. both
# "position" and "candidate" appear) does that combination count — see
# Part 4: "job/position/role combined with employment context", never bare.
_WEAK_JOB_CONTEXT_PATTERN = re.compile(
    r"\b(position|role|job|resume|cv|applicant|candidate|opportunity)\b", re.IGNORECASE,
)

_RECRUITER_SENDER_PATTERN = re.compile(r"(recruiter|talent|hiring|careers|jobs)", re.IGNORECASE)

_ATS_SENDER_DOMAINS = (
    "greenhouse.io", "lever.co", "myworkday.com", "icims.com", "smartrecruiters.com",
    "workable.com", "bamboohr.com", "jobvite.com", "ashbyhq.com", "successfactors.com",
)


def _is_job_related(subject: str, sender: str, body: str) -> bool:
    haystack = f"{subject}\n{body}".lower()
    if any(re.search(pattern, haystack) for pattern in _STRONG_JOB_CONTEXT_PATTERNS):
        return True
    sender_lower = (sender or "").lower()
    if any(domain in sender_lower for domain in _ATS_SENDER_DOMAINS) or _RECRUITER_SENDER_PATTERN.search(sender_lower):
        return True
    # Weak signals only count in combination — a single generic word
    # (e.g. just "position") is never sufficient by itself.
    return len(set(_WEAK_JOB_CONTEXT_PATTERN.findall(haystack))) >= 2


def _rule_match(subject: str, body: str) -> tuple[str | None, float]:
    subject_lower = (subject or "").lower()
    body_lower = (body or "").lower()
    best_type: str | None = None
    best_confidence = 0.0
    for event_type, patterns, base_confidence in _RULES:
        for pattern in patterns:
            in_subject = re.search(pattern, subject_lower)
            in_body = re.search(pattern, body_lower)
            if not in_subject and not in_body:
                continue
            confidence = base_confidence + (0.05 if in_subject and in_body else 0.0)
            confidence = min(0.99, confidence)
            if confidence > best_confidence:
                best_type, best_confidence = event_type, confidence
    return best_type, best_confidence


def classify_email_rules_only(*, subject: str, sender: str, body: str) -> dict[str, Any]:
    """The deterministic pass alone — always safe to call, never touches
    the network, always returns a result."""
    job_related = _is_job_related(subject, sender, body)
    event_type, confidence = _rule_match(subject, body)
    if event_type is None:
        return {
            "job_related": job_related,
            "event_type": "OTHER" if job_related else None,
            "confidence": 0.4 if job_related else 0.0,
        }
    return {"job_related": True, "event_type": event_type, "confidence": confidence}


def _llm_assist(*, subject: str, sender: str, body: str, rules_result: dict[str, Any]) -> dict[str, Any]:
    """Only called for ambiguous cases. Never allowed to raise past the
    classifier — any LLM/parsing failure just keeps the rules-only result."""
    try:
        from app.services.llm_client import LLMClient, bullet_rewrite_llm_config

        client = LLMClient(config=bullet_rewrite_llm_config())
        if not client.enabled:
            return rules_result

        system_prompt = (
            "You classify a job-application-related email into exactly one type: "
            + ", ".join(EVENT_TYPES) + ". "
            "Respond with strict JSON: {\"event_type\": \"...\", \"confidence\": 0.0-1.0}. "
            "Never invent facts not present in the email. If unsure, use OTHER with low confidence."
        )
        user_prompt = f"Subject: {subject}\nFrom: {sender}\nBody:\n{(body or '')[:2000]}"
        result = client.generate_json(system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.0)
        event_type = str(result.get("event_type") or "").strip().upper()
        if event_type not in EVENT_TYPES:
            return rules_result
        llm_confidence = float(result.get("confidence") or 0.0)
        capped_confidence = min(LLM_ASSIST_CONFIDENCE_CAP, max(0.0, llm_confidence))
        if capped_confidence <= rules_result.get("confidence", 0.0):
            return rules_result
        return {"job_related": True, "event_type": event_type, "confidence": capped_confidence}
    except Exception:
        return rules_result


def classify_email(*, subject: str, sender: str, body: str, allow_llm_assist: bool = True) -> dict[str, Any]:
    """Full classification: deterministic rules, then optional bounded
    Ollama assist only when the rules result is genuinely ambiguous. The
    LLM never updates any database record itself — see module docstring.
    `matched_job_id` is added by the caller once job-matching (Part 9) runs;
    it is always None here."""
    result = classify_email_rules_only(subject=subject, sender=sender, body=body)
    if allow_llm_assist and result["job_related"] and result["confidence"] < AMBIGUOUS_THRESHOLD:
        result = _llm_assist(subject=subject, sender=sender, body=body, rules_result=result)
    result["matched_job_id"] = None
    return result
