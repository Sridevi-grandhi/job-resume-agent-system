"""Email Summary's own application tracking: matching a job-related email
to an existing application (Part 9), validated status transitions (Part
10), and immutable history (Part 8). Never touches Saved Jobs (app.models.
job_model.Job) — only reads it, read-only, to seed a tracking row's
company/title and to help matching.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.application_status_history_model import ApplicationStatusHistory
from app.models.application_tracking_model import APPLICATION_STATUSES, ApplicationTracking
from app.models.job_model import Job

TERMINAL_STATUSES = {"OFFER", "REJECTED", "WITHDRAWN"}

# Coarse forward-progression rank — used only to block an AUTOMATIC
# (email-sourced) transition from moving a status backward. Manual
# corrections (Part 18) are never blocked by this.
_STATUS_RANK = {status: index for index, status in enumerate((
    "SAVED", "APPLIED", "APPLICATION_CONFIRMED", "UNDER_REVIEW",
    "RECRUITER_CONTACTED", "ASSESSMENT_REQUESTED", "INTERVIEW_REQUESTED",
    "INTERVIEW_SCHEDULED", "OFFER", "REJECTED", "WITHDRAWN",
))}

EVENT_TYPE_TO_STATUS = {
    "APPLICATION_CONFIRMATION": "APPLICATION_CONFIRMED",
    "APPLICATION_UPDATE": "UNDER_REVIEW",
    "RECRUITER_OUTREACH": "RECRUITER_CONTACTED",
    "INTERVIEW_INVITE": "INTERVIEW_REQUESTED",
    "ASSESSMENT_REQUEST": "ASSESSMENT_REQUESTED",
    "OFFER": "OFFER",
    "REJECTION": "REJECTED",
}

# Combined classifier-confidence x match-confidence bar for a "safe to
# auto-apply" status update. Gmail scanning (job_email_scan_service) does
# NOT use this — Email Summary's Gmail integration is read-only-detect-only
# by design, so every newly scanned event stays NEEDS_CONFIRMATION
# regardless of confidence, and only the user's explicit Confirm action may
# change ApplicationTracking (see apply_email_status_update). Left defined
# here only in case another, non-Gmail feature ever wants this bar.
SAFE_AUTO_UPDATE_THRESHOLD = 0.75

# Minimum _match_score() an email-to-application candidate must reach before
# it may be presented as "this is the application" at all (matched_job_id /
# matched_application_id on a JobEmailEvent, and therefore one-click
# Confirm). Below this, the candidate is discarded entirely rather than
# shown as if it were reliable — the user must explicitly choose/link the
# right application instead.
#
# Chosen from _match_score()'s own point budget, not picked blindly:
# company-name-in-email match = 0.5, sender-domain match = 0.5 (each on its
# own is real evidence of who the email is from/about), generic job-title
# word overlap = up to 0.3 (deliberately capped below this threshold on its
# own — "Software Engineer"/"Java Developer"-style overlap is common across
# unrelated employers and must never alone make a candidate "reliable", see
# CURRENT VERIFIED BUG: a 0.135 combined-confidence title-only match was
# being offered as a one-click-confirmable candidate). A candidate therefore
# only clears this bar with company-name evidence, sender-domain evidence,
# or a strong combination of weaker signals — never title overlap alone.
RELIABLE_MATCH_CONFIDENCE_THRESHOLD = 0.5

_STOPWORDS = {"the", "a", "an", "and", "or", "of", "for", "with", "in", "to", "on", "at", "senior", "sr", "junior", "jr"}


def is_valid_status(status: str) -> bool:
    return status in APPLICATION_STATUSES


def can_auto_transition(current_status: str, new_status: str) -> bool:
    """An automatic (email-sourced) transition is allowed only forward (or
    sideways within the same rank) and never out of a terminal status."""
    if current_status in TERMINAL_STATUSES:
        return False
    if new_status not in _STATUS_RANK or current_status not in _STATUS_RANK:
        return True
    return _STATUS_RANK[new_status] >= _STATUS_RANK[current_status]


def _record_history(db: Session, application: ApplicationTracking, *, new_status: str, source: str, email_message_id: str | None, confidence: float | None) -> None:
    db.add(ApplicationStatusHistory(
        application_id=application.id,
        old_status=application.status,
        new_status=new_status,
        source=source,
        email_message_id=email_message_id,
        confidence=confidence,
    ))


def apply_manual_status_update(db: Session, *, user_id: int, application_id: int, new_status: str) -> ApplicationTracking:
    if not is_valid_status(new_status):
        raise ValueError(f"Unknown status: {new_status}")
    application = (
        db.query(ApplicationTracking)
        .filter(ApplicationTracking.id == application_id, ApplicationTracking.user_id == user_id)
        .first()
    )
    if application is None:
        raise LookupError("Application not found")

    _record_history(db, application, new_status=new_status, source="manual", email_message_id=None, confidence=None)
    application.status = new_status
    application.status_source = "manual"
    application.last_status_update_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(application)
    return application


def apply_email_status_update(
    db: Session, *, application: ApplicationTracking, new_status: str, message_id: str, confidence: float,
) -> ApplicationTracking:
    """The only place a Gmail-sourced event may ever change an
    ApplicationTracking row's status — called exclusively from the user's
    explicit Confirm action (see email_routes.resolve_job_email_event).
    Gmail scanning itself (job_email_scan_service) never calls this: Gmail
    is read-only for Email Summary, so scanning only ever detects/proposes
    a possible update (JobEmailEvent.resolution stays NEEDS_CONFIRMATION),
    regardless of confidence. SAFE_AUTO_UPDATE_THRESHOLD is kept defined
    below for any other feature that wants a "safe to auto-apply" bar, but
    nothing in the Gmail scan path reads it anymore. This function still
    re-checks can_auto_transition so a terminal/backward transition can
    never slip through even from a miscalled site."""
    if not can_auto_transition(application.status, new_status):
        return application
    _record_history(db, application, new_status=new_status, source="email", email_message_id=message_id, confidence=confidence)
    application.status = new_status
    application.status_source = "email"
    application.last_status_update_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(application)
    return application


def get_or_create_application(
    db: Session, *, user_id: int, job_id: int | None, company: str, title: str,
) -> ApplicationTracking:
    query = db.query(ApplicationTracking).filter(ApplicationTracking.user_id == user_id)
    application = None
    if job_id is not None:
        application = query.filter(ApplicationTracking.job_id == job_id).first()
    if application is None and job_id is None:
        application = query.filter(
            ApplicationTracking.job_id.is_(None),
            ApplicationTracking.company == company,
            ApplicationTracking.title == title,
        ).first()
    if application is not None:
        return application

    application = ApplicationTracking(
        user_id=user_id, job_id=job_id, company=company, title=title,
        status="APPLIED", status_source="system",
        applied_at=datetime.now(timezone.utc), last_status_update_at=datetime.now(timezone.utc),
    )
    db.add(application)
    db.commit()
    db.refresh(application)
    _record_history(db, application, new_status="APPLIED", source="system", email_message_id=None, confidence=None)
    db.commit()
    return application


def list_applications(db: Session, *, user_id: int) -> list[ApplicationTracking]:
    return (
        db.query(ApplicationTracking)
        .filter(ApplicationTracking.user_id == user_id)
        .order_by(ApplicationTracking.last_status_update_at.desc().nullslast(), ApplicationTracking.created_at.desc())
        .all()
    )


def list_history(db: Session, *, user_id: int, application_id: int) -> list[ApplicationStatusHistory]:
    owned = (
        db.query(ApplicationTracking)
        .filter(ApplicationTracking.id == application_id, ApplicationTracking.user_id == user_id)
        .first()
    )
    if owned is None:
        return []
    return (
        db.query(ApplicationStatusHistory)
        .filter(ApplicationStatusHistory.application_id == application_id)
        .order_by(ApplicationStatusHistory.created_at.asc())
        .all()
    )


# ---------------------------------------------------------------------------
# Email-to-application matching (Part 9)
# ---------------------------------------------------------------------------

def _domain_of(email_address: str) -> str:
    match = re.search(r"@([\w.-]+)", email_address or "")
    return (match.group(1) if match else "").lower()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _significant_words(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9+#]+", (text or "").lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


# Common legal-entity suffixes stripped by _canonicalize_company — a
# deliberately short, explicit list (never a fuzzy/typo-tolerant match) so
# canonicalization stays conservative: it only removes what's unambiguously
# corporate-suffix noise, never a word that could be part of a company's
# actual distinguishing name.
_LEGAL_SUFFIXES = {"ltd", "limited", "llc", "llp", "inc", "incorporated", "corp", "corporation", "plc"}


def _normalize_company_text(text: str) -> str:
    """Case-fold and turn punctuation into spaces, then collapse whitespace
    — shared by company-name canonicalization AND haystack normalization so
    a word-boundary comparison never trips over stray commas/periods (e.g.
    matching "Ltd" against "Ltd.," or "Computer Merchant," inside a
    sentence)."""
    text = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
    return " ".join(text.split())


def _canonicalize_company(company: str) -> str:
    """Conservative company-name canonicalization for MATCHING only (never
    for display/storage): normalizes case/punctuation/whitespace, then
    strips a leading/trailing "the" and a common legal-entity suffix
    (_LEGAL_SUFFIXES) from the ends only — repeated, since "Ltd., The" can
    leave both at once. Deliberately conservative: no fuzzy/typo matching,
    and interior words are never dropped, so two companies that merely
    share generic words never canonicalize the same.

    canonicalize("Computer Merchant, Ltd., The") == canonicalize("Computer
    Merchant") == "computer merchant" — but canonicalize("SES") stays "ses"
    (no suffix/article to strip), so the CURRENT VERIFIED BUG's word-
    boundary protection is unaffected."""
    tokens = _normalize_company_text(company).split()
    changed = True
    while changed and tokens:
        changed = False
        if tokens[0] == "the":
            tokens = tokens[1:]
            changed = True
        if tokens and tokens[-1] in _LEGAL_SUFFIXES:
            tokens = tokens[:-1]
            changed = True
        if tokens and tokens[-1] == "the":
            tokens = tokens[:-1]
            changed = True
    return " ".join(tokens)


def _company_mentioned(company: str, haystack: str) -> bool:
    """Whole-word/whole-phrase company match — NOT a raw substring test.
    CURRENT VERIFIED BUG: a raw `company.lower() in haystack` check let a
    short company name/acronym match merely because it happens to occur
    inside an unrelated word — e.g. company "SES" was "found" inside
    "Assessment" (as"SES"sment), scoring a Sr. Java Developer/SES
    application as a match for a Computer Merchant SDET assessment email
    that never mentions SES at all.

    Matches on the CANONICAL company name (Requirement 2: exact equality of
    canonical names is strong identity evidence) against a punctuation/
    whitespace-normalized haystack, so "Computer Merchant, Ltd., The" (the
    messy legal name CareerBoost may have stored) is recognized as the same
    company as an email that just says "Computer Merchant" — while "SES"
    still never matches inside "assessment" (canonicalizing "ses" changes
    nothing about its own letters, and the word-boundary check on the
    normalized haystack still holds)."""
    canonical = _canonicalize_company(company)
    if not canonical:
        return False
    normalized_haystack = _normalize_company_text(haystack)
    pattern = r"(?<![a-z0-9])" + re.escape(canonical) + r"(?![a-z0-9])"
    return re.search(pattern, normalized_haystack) is not None


def _sender_domain_matches(company: str, sender: str) -> bool:
    sender_domain = _domain_of(sender)
    company_slug = _slug(_canonicalize_company(company))
    return bool(sender_domain and company_slug and company_slug in sender_domain.replace(".", ""))


def has_strong_identity_evidence(*, company: str, sender: str, subject: str, body: str) -> bool:
    """Requirement 7's semantic reliability gate: whether THIS candidate has
    independently strong, deterministic evidence tying the email to it — an
    explicit whole-word company-name mention or a sender-domain match.
    Generic job-title word overlap never satisfies this on its own,
    regardless of any raw numeric score reached by combining it with other
    signals — see RELIABLE_MATCH_CONFIDENCE_THRESHOLD's own comment on why
    title overlap is deliberately capped below the reliable bar."""
    haystack = f"{subject}\n{body}".lower()
    if company and _company_mentioned(company, haystack):
        return True
    return _sender_domain_matches(company, sender)


def _known_companies(entities: list) -> set[str]:
    return {(getattr(entity, "company", "") or "").strip().lower() for entity in entities if getattr(entity, "company", None)}


def _contradicted_by_other_company(*, company: str, other_companies: set[str], subject: str, body: str) -> bool:
    """Requirements 5/7: an explicit, different KNOWN company named in the
    email is positive contradicting evidence — not just an absence of a
    match for this candidate — and must reject the candidate outright, so
    generic job-title overlap can never compensate for a clear company
    mismatch. Only checked when this candidate's OWN company is not itself
    explicitly present (a legitimate email can mention a staffing
    agency/parent company alongside the employer actually being applied
    to).

    "Different" is judged by CANONICAL company identity (Requirement 2),
    not raw string equality — two records for the same employer stored
    under different spellings (e.g. "Computer Merchant" vs "Computer
    Merchant, Ltd., The") must never be treated as contradicting each
    other."""
    haystack = f"{subject}\n{body}".lower()
    own_canonical = _canonicalize_company(company)
    if own_canonical and _company_mentioned(company, haystack):
        return False
    for other in other_companies:
        if not other or _canonicalize_company(other) == own_canonical:
            continue
        if _company_mentioned(other, haystack):
            return True
    return False


def _match_score(*, company: str, title: str, sender: str, subject: str, body: str, other_companies: set[str] = frozenset()) -> float:
    """Company-name-in-text and sender-domain matches are each worth 0.5 —
    real evidence of *which employer* this email is about/from. Job-title
    word overlap is capped at 0.3 and is the ONLY signal that can never by
    itself reach RELIABLE_MATCH_CONFIDENCE_THRESHOLD (0.5) — generic titles
    like "Software Engineer" repeat across unrelated employers constantly
    and must never alone make a candidate look reliable. A candidate scores
    0.0 outright (see _contradicted_by_other_company) when the email
    explicitly names a different, known company and never names this one —
    title-overlap points must never buy back a clear company mismatch."""
    haystack = f"{subject}\n{body}".lower()
    if _contradicted_by_other_company(company=company, other_companies=other_companies, subject=subject, body=body):
        return 0.0

    score = 0.0
    if company and _company_mentioned(company, haystack):
        score += 0.5
    title_words = _significant_words(title)
    if title_words and title_words & _significant_words(haystack):
        overlap_ratio = len(title_words & _significant_words(haystack)) / len(title_words)
        score += 0.3 * overlap_ratio
    if _sender_domain_matches(company, sender):
        score += 0.5
    return min(1.0, score)


def match_email_to_application(
    db: Session, *, user_id: int, sender: str, subject: str, body: str,
) -> tuple[ApplicationTracking | None, float]:
    """Scores existing ApplicationTracking rows (falling back to Job/Saved
    Jobs rows with no tracking yet) and returns the single best match with
    its confidence — never fabricates a company/title that isn't already
    known to CareerBoost."""
    applications = db.query(ApplicationTracking).filter(ApplicationTracking.user_id == user_id).all()
    jobs = db.query(Job).filter(Job.user_id == user_id).all()
    known_companies = _known_companies(applications) | _known_companies(jobs)

    candidates: list[tuple[Any, float, str]] = []
    for application in applications:
        other_companies = known_companies - {(application.company or "").strip().lower()}
        score = _match_score(company=application.company, title=application.title, sender=sender, subject=subject, body=body, other_companies=other_companies)
        if score > 0:
            candidates.append((application, score, "application"))

    for job in jobs:
        already_tracked = any(c[2] == "application" and c[0].job_id == job.id for c in candidates)
        if already_tracked:
            continue
        other_companies = known_companies - {(job.company or "").strip().lower()}
        score = _match_score(company=job.company, title=job.title, sender=sender, subject=subject, body=body, other_companies=other_companies)
        if score > 0:
            candidates.append((job, score, "job"))

    if not candidates:
        return None, 0.0

    best_entity, best_score, kind = max(candidates, key=lambda item: item[1])
    if kind == "application":
        return best_entity, best_score

    application = get_or_create_application(
        db, user_id=user_id, job_id=best_entity.id, company=best_entity.company, title=best_entity.title,
    )
    return application, best_score


def is_match_still_reliable(
    db: Session, *, user_id: int, application: ApplicationTracking, sender: str, subject: str, body: str,
) -> bool:
    """Requirements 8/G: the /resolve confirm endpoint's independent
    re-verification — never trusts a stored matched_application_id or a
    match_confidence that merely clears RELIABLE_MATCH_CONFIDENCE_THRESHOLD
    alone (a row can be stale from before this fix, or a future bug could
    persist matched_application_id without going through
    match_email_to_application at all). Recomputes both the
    strong-identity-evidence gate and the contradicting-other-company gate
    against the CURRENT application row and the event's own recorded
    subject/sender/body-snippet."""
    if not has_strong_identity_evidence(company=application.company, sender=sender, subject=subject, body=body):
        return False

    other_applications = (
        db.query(ApplicationTracking)
        .filter(ApplicationTracking.user_id == user_id, ApplicationTracking.id != application.id)
        .all()
    )
    jobs = db.query(Job).filter(Job.user_id == user_id).all()
    other_companies = _known_companies(other_applications) | _known_companies(jobs)
    if _contradicted_by_other_company(company=application.company, other_companies=other_companies, subject=subject, body=body):
        return False
    return True
