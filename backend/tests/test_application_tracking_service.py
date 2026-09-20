"""Tests for app/services/application_tracking_service.py — status
transition validation (Part 10), immutable history (Part 8), matching
(Part 9), and that Selected Jobs (app.models.job_model.Job) is only ever
read, never mutated (Part 7)."""

from __future__ import annotations

from app.models.application_status_history_model import ApplicationStatusHistory
from app.models.job_model import Job
from app.services import application_tracking_service as tracking


def _application(db_session, user_id, **overrides):
    defaults = dict(user_id=user_id, job_id=None, company="ABC Corp", title="Java Developer", status="APPLIED", status_source="system")
    defaults.update(overrides)
    from app.models.application_tracking_model import ApplicationTracking
    application = ApplicationTracking(**defaults)
    db_session.add(application)
    db_session.commit()
    db_session.refresh(application)
    return application


# ---------------------------------------------------------------------------
# Status transition validation
# ---------------------------------------------------------------------------

def test_valid_forward_transition_allowed():
    assert tracking.can_auto_transition("APPLIED", "INTERVIEW_REQUESTED") is True


def test_backward_automatic_transition_blocked():
    assert tracking.can_auto_transition("INTERVIEW_REQUESTED", "APPLIED") is False


def test_terminal_status_blocks_further_automatic_transitions():
    assert tracking.can_auto_transition("OFFER", "APPLICATION_CONFIRMED") is False
    assert tracking.can_auto_transition("REJECTED", "INTERVIEW_REQUESTED") is False


def test_email_status_update_respects_transition_rules(db_session, test_user):
    application = _application(db_session, test_user.id, status="OFFER")
    result = tracking.apply_email_status_update(
        db_session, application=application, new_status="APPLIED", message_id="msg-1", confidence=0.9,
    )
    assert result.status == "OFFER"  # unchanged — terminal status protected even from a direct call
    assert db_session.query(ApplicationStatusHistory).filter_by(application_id=application.id).count() == 0


def test_email_status_update_applies_and_records_history(db_session, test_user):
    application = _application(db_session, test_user.id, status="APPLIED")
    tracking.apply_email_status_update(
        db_session, application=application, new_status="INTERVIEW_REQUESTED", message_id="msg-1", confidence=0.9,
    )
    db_session.refresh(application)
    assert application.status == "INTERVIEW_REQUESTED"
    assert application.status_source == "email"
    history = db_session.query(ApplicationStatusHistory).filter_by(application_id=application.id).all()
    assert len(history) == 1
    assert history[0].old_status == "APPLIED"
    assert history[0].new_status == "INTERVIEW_REQUESTED"
    assert history[0].source == "email"
    assert history[0].email_message_id == "msg-1"


# ---------------------------------------------------------------------------
# Manual correction (Part 18) — always allowed, always appends history
# ---------------------------------------------------------------------------

def test_manual_update_allowed_even_backward_or_from_terminal(db_session, test_user):
    application = _application(db_session, test_user.id, status="OFFER")
    updated = tracking.apply_manual_status_update(db_session, user_id=test_user.id, application_id=application.id, new_status="INTERVIEW_SCHEDULED")
    assert updated.status == "INTERVIEW_SCHEDULED"
    assert updated.status_source == "manual"
    history = db_session.query(ApplicationStatusHistory).filter_by(application_id=application.id).all()
    assert len(history) == 1
    assert history[0].source == "manual"


def test_manual_update_rejects_unknown_status(db_session, test_user):
    application = _application(db_session, test_user.id)
    try:
        tracking.apply_manual_status_update(db_session, user_id=test_user.id, application_id=application.id, new_status="NOT_A_REAL_STATUS")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_manual_update_cross_user_blocked(db_session, test_user, second_user):
    application = _application(db_session, test_user.id)
    try:
        tracking.apply_manual_status_update(db_session, user_id=second_user.id, application_id=application.id, new_status="OFFER")
        assert False, "expected LookupError"
    except LookupError:
        pass


# ---------------------------------------------------------------------------
# Selected Jobs is read-only from here
# ---------------------------------------------------------------------------

def test_get_or_create_application_never_mutates_the_job_row(db_session, test_user, java_job):
    original_title = java_job.title
    tracking.get_or_create_application(db_session, user_id=test_user.id, job_id=java_job.id, company=java_job.company, title=java_job.title)
    db_session.refresh(java_job)
    assert java_job.title == original_title


def test_get_or_create_application_is_idempotent_per_job(db_session, test_user, java_job):
    first = tracking.get_or_create_application(db_session, user_id=test_user.id, job_id=java_job.id, company=java_job.company, title=java_job.title)
    second = tracking.get_or_create_application(db_session, user_id=test_user.id, job_id=java_job.id, company=java_job.company, title=java_job.title)
    assert first.id == second.id


# ---------------------------------------------------------------------------
# Matching (Part 9)
# ---------------------------------------------------------------------------

def test_match_email_to_application_matches_by_company_and_title(db_session, test_user, java_job):
    application, confidence = tracking.match_email_to_application(
        db_session, user_id=test_user.id,
        sender=f"hr@{java_job.company.lower().replace(' ', '')}.com",
        subject=f"Your application to {java_job.company}",
        body=f"Thank you for applying for the {java_job.title} role.",
    )
    assert application is not None
    assert application.job_id == java_job.id
    assert confidence > 0.5


def test_match_email_to_application_no_match_returns_none(db_session, test_user, java_job):
    # A sender domain deliberately unrelated to java_job's company/title —
    # avoid "example.com", which would coincidentally resemble the
    # "Example Co" fixture company name.
    application, confidence = tracking.match_email_to_application(
        db_session, user_id=test_user.id,
        sender="newsletter@totally-unrelated-domain-xyz.net", subject="Weekly Newsletter", body="Nothing job related here at all.",
    )
    assert application is None
    assert confidence == 0.0


def test_match_email_to_application_scoped_to_user(db_session, test_user, second_user, java_job):
    application, confidence = tracking.match_email_to_application(
        db_session, user_id=second_user.id,
        sender=f"hr@{java_job.company.lower().replace(' ', '')}.com",
        subject=f"Your application to {java_job.company}",
        body=f"Thank you for applying for the {java_job.title} role.",
    )
    assert application is None  # java_job belongs to test_user, never visible to second_user


# ---------------------------------------------------------------------------
# RELIABLE_MATCH_CONFIDENCE_THRESHOLD — a "best available" candidate is not
# automatically a trustworthy one (CURRENT VERIFIED BUG regression coverage)
# ---------------------------------------------------------------------------

def test_reliable_match_confidence_threshold_named_constant_exists():
    """Requirement 3: a clear, named, non-blind threshold."""
    assert 0.0 < tracking.RELIABLE_MATCH_CONFIDENCE_THRESHOLD < tracking.SAFE_AUTO_UPDATE_THRESHOLD


def test_generic_title_overlap_alone_is_insufficient(db_session, test_user):
    """Requirement E: "Software Engineer"-style overlap alone, with no
    company-name or sender-domain evidence, must never reach the reliable
    threshold — it's real-world common across unrelated employers."""
    from app.models.application_tracking_model import ApplicationTracking

    unrelated = ApplicationTracking(
        user_id=test_user.id, job_id=None, company="Computer Merchant",
        title="Software Development Engineer", status="APPLIED", status_source="system",
    )
    db_session.add(unrelated)
    db_session.commit()

    application, confidence = tracking.match_email_to_application(
        db_session, user_id=test_user.id,
        sender="no-reply@some-ats-vendor.example.com",
        subject="Application Received - CareerBoost Test",
        body="Thank you for applying for the Software Engineer position at Test Company.",
    )
    # The weak title-overlap-only candidate is still the "best available"
    # one found, but must not be reported as reliable.
    assert confidence < tracking.RELIABLE_MATCH_CONFIDENCE_THRESHOLD


def test_test_company_email_does_not_become_trusted_match_to_unrelated_company(db_session, test_user):
    """Requirement A: the exact reported bug — a "Test Company" email must
    not become a reliable match to a tracked "Computer Merchant" application
    merely because it's the only/best-scoring candidate."""
    from app.models.application_tracking_model import ApplicationTracking

    db_session.add(ApplicationTracking(
        user_id=test_user.id, job_id=None, company="Computer Merchant",
        title="Software Engineer", status="APPLIED", status_source="system",
    ))
    db_session.commit()

    application, confidence = tracking.match_email_to_application(
        db_session, user_id=test_user.id,
        sender="careers@testcompany-noreply.example.com",
        subject="Application Received - CareerBoost Test",
        body="Thank you for applying for the Software Engineer position at Test Company.",
    )
    assert confidence < tracking.RELIABLE_MATCH_CONFIDENCE_THRESHOLD


def test_exact_company_and_job_evidence_still_produces_a_reliable_match(db_session, test_user, java_job):
    """Requirement D: strong evidence (company name in text, matching the
    tracked application) must still work — existing legitimate matches are
    not collateral damage of the fix."""
    application, confidence = tracking.match_email_to_application(
        db_session, user_id=test_user.id,
        sender="careers@example.com",
        subject=f"Application Received - {java_job.company}",
        body=f"Thank you for applying for the {java_job.title} position at {java_job.company}.",
    )
    assert application is not None
    assert application.job_id == java_job.id
    assert confidence >= tracking.RELIABLE_MATCH_CONFIDENCE_THRESHOLD


def test_sender_domain_match_alone_is_reliable_evidence(db_session, test_user):
    """Requirement 4: sender domain is explicitly listed as strong evidence
    that can justify trusting a match even without a company-name mention
    in the body."""
    from app.models.application_tracking_model import ApplicationTracking

    application = ApplicationTracking(
        user_id=test_user.id, job_id=None, company="Acme Robotics",
        title="Backend Engineer", status="APPLIED", status_source="system",
    )
    db_session.add(application)
    db_session.commit()

    score = tracking._match_score(
        company=application.company, title=application.title,
        sender="careers@acmerobotics.com", subject="Update on your application", body="We wanted to give you an update.",
    )
    assert score >= tracking.RELIABLE_MATCH_CONFIDENCE_THRESHOLD


# ---------------------------------------------------------------------------
# CURRENT LIVE BUG regression: a short company name/acronym ("SES") must
# never match merely because it's a substring of an unrelated word
# ("Assessment") — and a company mismatch must reject the candidate outright
# even when generic title-word overlap is strong (Requirements 4-8, A-E).
# ---------------------------------------------------------------------------

_ASSESSMENT_SUBJECT = "Assessment Request - Software Development Engineer (SDET) at Computer Merchant"
_ASSESSMENT_BODY = (
    "Hi, thank you for applying to the Software Development Engineer (SDET) role at "
    "Computer Merchant. We would like you to complete an online technical assessment "
    "as the next step in our process."
)


def _seed_ses_and_computer_merchant(db_session, user_id):
    """Both tracked as pre-existing ApplicationTracking rows, mirroring the
    reported bug: two live applications whose title words ("developer"/
    "engineer"-style terms) can plausibly overlap with an unrelated email."""
    from app.models.application_tracking_model import ApplicationTracking

    ses = ApplicationTracking(
        user_id=user_id, job_id=None, company="SES",
        title="Sr. Java Developer (Java 21, Apache Kafka, SQL, AWS)",
        status="APPLIED", status_source="system",
    )
    computer_merchant = ApplicationTracking(
        user_id=user_id, job_id=None, company="Computer Merchant",
        title="Software Development Engineer (SDET)",
        status="APPLIED", status_source="system",
    )
    db_session.add_all([ses, computer_merchant])
    db_session.commit()
    db_session.refresh(ses)
    db_session.refresh(computer_merchant)
    return ses, computer_merchant


def test_company_name_substring_of_unrelated_word_is_not_a_mention():
    """Root cause, isolated: "ses" is a literal substring of "assessment"
    (as"SES"sment) — the old `company.lower() in haystack` check treated
    that as a company mention. It must not."""
    assert tracking._company_mentioned("SES", _ASSESSMENT_SUBJECT.lower()) is False
    assert tracking._company_mentioned("SES", _ASSESSMENT_BODY.lower()) is False
    # Sanity check the fixture actually exercises the collision this guards against.
    assert "ses" in _ASSESSMENT_SUBJECT.lower()


def test_computer_merchant_assessment_matches_computer_merchant_sdet_application(db_session, test_user):
    """Requirement A."""
    ses, computer_merchant = _seed_ses_and_computer_merchant(db_session, test_user.id)

    application, confidence = tracking.match_email_to_application(
        db_session, user_id=test_user.id,
        sender="ats@computermerchant-noreply.example.com",
        subject=_ASSESSMENT_SUBJECT, body=_ASSESSMENT_BODY,
    )
    assert application is not None
    assert application.id == computer_merchant.id
    assert confidence >= tracking.RELIABLE_MATCH_CONFIDENCE_THRESHOLD


def test_computer_merchant_assessment_does_not_match_ses_java_developer(db_session, test_user):
    """Requirement B — the exact reported bug: the SES Sr. Java Developer
    application must never be the winning candidate for a Computer Merchant
    assessment email, even though its title shares generic "developer"/
    "engineer"-style words and the raw text happens to contain the letters
    "ses" (inside "Assessment")."""
    ses, computer_merchant = _seed_ses_and_computer_merchant(db_session, test_user.id)

    application, confidence = tracking.match_email_to_application(
        db_session, user_id=test_user.id,
        sender="ats@computermerchant-noreply.example.com",
        subject=_ASSESSMENT_SUBJECT, body=_ASSESSMENT_BODY,
    )
    assert application is None or application.id != ses.id

    ses_score = tracking._match_score(
        company=ses.company, title=ses.title,
        sender="ats@computermerchant-noreply.example.com",
        subject=_ASSESSMENT_SUBJECT, body=_ASSESSMENT_BODY,
        other_companies={"computer merchant"},
    )
    assert ses_score == 0.0


def test_generic_title_overlap_with_company_mismatch_is_rejected():
    """Requirement C: even where title words overlap (both are "developer"/
    "engineer"-style roles), an explicit, different, known company in the
    email text must zero out the mismatched candidate's score outright —
    title overlap must never buy back a clear company mismatch."""
    score = tracking._match_score(
        company="SES", title="Sr. Java Developer (Java 21, Apache Kafka, SQL, AWS)",
        sender="ats@computermerchant-noreply.example.com",
        subject=_ASSESSMENT_SUBJECT, body=_ASSESSMENT_BODY,
        other_companies={"computer merchant"},
    )
    assert score == 0.0


def test_exact_match_confidence_of_half_with_contradictory_company_is_not_confirmable(db_session, test_user):
    """Requirement D: a numeric match_confidence of exactly 0.5 must NOT be
    treated as confirmable when the event's own recorded text carries
    explicit, contradictory company evidence — is_match_still_reliable is
    the confirm-route's independent re-check and must reject this even if
    some other (stale/legacy) code path had stored exactly the threshold
    value on the event."""
    ses, computer_merchant = _seed_ses_and_computer_merchant(db_session, test_user.id)

    # A stale event: match_confidence sits exactly at the threshold and
    # matched_application_id points at SES, but the recorded email text is
    # unambiguously about Computer Merchant.
    reliable = tracking.is_match_still_reliable(
        db_session, user_id=test_user.id, application=ses,
        sender="ats@computermerchant-noreply.example.com",
        subject=_ASSESSMENT_SUBJECT, body=_ASSESSMENT_BODY,
    )
    assert reliable is False


def test_exact_company_and_strong_title_evidence_produces_a_valid_match(db_session, test_user):
    """Requirement E: exact company-name evidence plus strong, matching
    title evidence must still produce a valid, reliable match — the fix is
    not collateral damage to legitimate matches."""
    _, computer_merchant = _seed_ses_and_computer_merchant(db_session, test_user.id)

    reliable = tracking.is_match_still_reliable(
        db_session, user_id=test_user.id, application=computer_merchant,
        sender="ats@computermerchant-noreply.example.com",
        subject=_ASSESSMENT_SUBJECT, body=_ASSESSMENT_BODY,
    )
    assert reliable is True

    score = tracking._match_score(
        company=computer_merchant.company, title=computer_merchant.title,
        sender="ats@computermerchant-noreply.example.com",
        subject=_ASSESSMENT_SUBJECT, body=_ASSESSMENT_BODY,
        other_companies={"ses"},
    )
    assert score >= tracking.RELIABLE_MATCH_CONFIDENCE_THRESHOLD


def test_system_one_interview_match_still_works(db_session, test_user):
    """Requirement H: an existing, legitimate matching scenario (a "System
    One" staffing-agency interview invite) must be unaffected by the
    company-mismatch/contradiction gate when no other company is
    referenced."""
    from app.models.application_tracking_model import ApplicationTracking

    application = ApplicationTracking(
        user_id=test_user.id, job_id=None, company="System One",
        title="Java Backend Developer", status="APPLIED", status_source="system",
    )
    db_session.add(application)
    db_session.commit()
    db_session.refresh(application)

    matched, confidence = tracking.match_email_to_application(
        db_session, user_id=test_user.id,
        sender="recruiter@systemone.com",
        subject="Interview Invitation - Java Backend Developer",
        body="We would like to schedule an interview with you for the Java Backend Developer role at System One.",
    )
    assert matched is not None
    assert matched.id == application.id
    assert confidence >= tracking.RELIABLE_MATCH_CONFIDENCE_THRESHOLD


# ---------------------------------------------------------------------------
# Company-name canonicalization (this task): "Computer Merchant" and
# "Computer Merchant, Ltd., The" must be recognized as the same employer
# for matching purposes, without weakening the SES substring/contradiction
# protections from the prior fix (Requirements 1-7, A-J).
# ---------------------------------------------------------------------------

_FIX_TEST_SUBJECT = "Assessment Request Fix Test - Software Development Engineer (SDET) at Computer Merchant"
_FIX_TEST_BODY = (
    "Hi, thank you for applying to the Software Development Engineer (SDET) role at "
    "Computer Merchant. We would like you to complete an online technical assessment "
    "as the next step in our process."
)


def test_canonicalize_computer_merchant_variants_are_equal():
    """Requirement A/2: canonical("Computer Merchant, Ltd., The") =
    canonical("Computer Merchant") — exact equality of canonical forms."""
    assert tracking._canonicalize_company("Computer Merchant, Ltd., The") == tracking._canonicalize_company("Computer Merchant")
    assert tracking._canonicalize_company("Computer Merchant, Ltd., The") == "computer merchant"


def test_canonicalize_is_case_and_punctuation_insensitive():
    """Requirement B."""
    variants = ["COMPUTER MERCHANT", "computer   merchant", "Computer, Merchant.", "  Computer Merchant  "]
    canonical = tracking._canonicalize_company("Computer Merchant")
    for variant in variants:
        assert tracking._canonicalize_company(variant) == canonical


def test_canonicalize_strips_legal_suffixes_conservatively():
    """Requirement C: only the specific listed legal-entity suffixes (and a
    leading/trailing "the") are stripped — a company's actual distinguishing
    words are never touched, and a word that merely resembles a suffix
    inside the name itself is not affected."""
    assert tracking._canonicalize_company("Acme Corp") == "acme"
    assert tracking._canonicalize_company("Acme Corporation") == "acme"
    assert tracking._canonicalize_company("Acme Inc.") == "acme"
    assert tracking._canonicalize_company("Acme Incorporated") == "acme"
    assert tracking._canonicalize_company("Acme LLC") == "acme"
    assert tracking._canonicalize_company("Acme LLP") == "acme"
    assert tracking._canonicalize_company("The Acme Company PLC") == "acme company"
    # Conservative: "Company" itself is never stripped, only the exact
    # legal-suffix tokens — no fuzzy matching, no dropping interior words.
    assert tracking._canonicalize_company("Quality Corp Solutions") == "quality corp solutions"


def test_ses_still_does_not_match_inside_assessment_after_canonicalization():
    """Requirement D — canonicalization must not reopen the CURRENT VERIFIED
    BUG: "SES" has no article/suffix to strip, so it canonicalizes to
    itself, and the word-boundary check must still reject the "assessment"
    substring collision."""
    assert tracking._canonicalize_company("SES") == "ses"
    assert tracking._company_mentioned("SES", _FIX_TEST_SUBJECT.lower()) is False
    assert tracking._company_mentioned("SES", _FIX_TEST_BODY.lower()) is False


def test_computer_merchant_does_not_match_ses_after_canonicalization(db_session, test_user):
    """Requirement E: the semantic contradiction gate still rejects SES for
    a Computer Merchant email, even with canonicalization active."""
    ses, computer_merchant = _seed_ses_and_computer_merchant(db_session, test_user.id)

    application, confidence = tracking.match_email_to_application(
        db_session, user_id=test_user.id,
        sender="ats@computermerchant-noreply.example.com",
        subject=_FIX_TEST_SUBJECT, body=_FIX_TEST_BODY,
    )
    assert application is None or application.id != ses.id

    ses_score = tracking._match_score(
        company=ses.company, title=ses.title,
        sender="ats@computermerchant-noreply.example.com",
        subject=_FIX_TEST_SUBJECT, body=_FIX_TEST_BODY,
        other_companies={"computer merchant, ltd., the"},
    )
    assert ses_score == 0.0


def test_generic_title_overlap_without_company_identity_remains_unsafe(db_session, test_user):
    """Requirement F: canonicalization must never become a loophole for
    generic title-overlap-only matches — an email with no company identity
    evidence at all for a candidate still can't reach the reliable bar."""
    from app.models.application_tracking_model import ApplicationTracking

    unrelated = ApplicationTracking(
        user_id=test_user.id, job_id=None, company="Computer Merchant, Ltd., The",
        title="Software Development Engineer", status="APPLIED", status_source="system",
    )
    db_session.add(unrelated)
    db_session.commit()

    application, confidence = tracking.match_email_to_application(
        db_session, user_id=test_user.id,
        sender="no-reply@some-ats-vendor.example.com",
        subject="Application Received - CareerBoost Test",
        body="Thank you for applying for the Software Engineer position at Test Company.",
    )
    assert confidence < tracking.RELIABLE_MATCH_CONFIDENCE_THRESHOLD


def test_computer_merchant_sdet_email_resolves_to_the_legal_named_application(db_session, test_user):
    """Requirement G: the exact reported scenario — CareerBoost stores the
    messy legal company name, the email uses the short form, and matching
    must still resolve to the correct application/job, not drop the match
    (matched_application_id/matched_job_id = null) as it did before
    canonicalization."""
    from app.models.application_tracking_model import ApplicationTracking

    computer_merchant_job = Job(
        user_id=test_user.id, title="Software Development Engineer (SDET)", company="Computer Merchant, Ltd., The",
        location="Remote", work_mode="Remote", job_type="Full-time",
        description="SDET role at Computer Merchant.", skills_required="", source="User Selected Job",
        job_url="https://example.com/jobs/cm-sdet",
    )
    db_session.add(computer_merchant_job)
    db_session.commit()
    db_session.refresh(computer_merchant_job)

    application = ApplicationTracking(
        user_id=test_user.id, job_id=computer_merchant_job.id, company="Computer Merchant, Ltd., The",
        title="Software Development Engineer (SDET)", status="APPLIED", status_source="system",
    )
    db_session.add(application)
    db_session.commit()
    db_session.refresh(application)

    matched, confidence = tracking.match_email_to_application(
        db_session, user_id=test_user.id,
        sender="ats@computermerchant-noreply.example.com",
        subject=_FIX_TEST_SUBJECT, body=_FIX_TEST_BODY,
    )
    assert matched is not None
    assert matched.id == application.id
    assert matched.job_id == computer_merchant_job.id
    assert confidence >= tracking.RELIABLE_MATCH_CONFIDENCE_THRESHOLD


def test_confirm_revalidation_accepts_the_canonical_company_match(db_session, test_user):
    """Requirement H: is_match_still_reliable (the /resolve confirm
    endpoint's independent re-check) must accept a match backed by
    canonical — not literal — company-name equality."""
    from app.models.application_tracking_model import ApplicationTracking

    application = ApplicationTracking(
        user_id=test_user.id, job_id=None, company="Computer Merchant, Ltd., The",
        title="Software Development Engineer (SDET)", status="APPLIED", status_source="system",
    )
    db_session.add(application)
    db_session.commit()
    db_session.refresh(application)

    reliable = tracking.is_match_still_reliable(
        db_session, user_id=test_user.id, application=application,
        sender="ats@computermerchant-noreply.example.com",
        subject=_FIX_TEST_SUBJECT, body=_FIX_TEST_BODY,
    )
    assert reliable is True
