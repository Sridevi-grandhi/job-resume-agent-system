"""Tests for app/services/job_email_scan_service.py — idempotency (Part 21),
bounded/incremental scanning (Part 11), and the classify -> match ->
confidence-check -> proposed-event -> validated-update pipeline (Part 6/9).
No real Gmail call: gmail_service.get_provider_for_user is replaced with a
FakeProvider implementing the EmailProvider interface directly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import base64
import json

from app.models.application_status_history_model import ApplicationStatusHistory
from app.models.application_tracking_model import ApplicationTracking
from app.models.email_connection_model import EmailConnection
from app.models.email_summary_preference_model import EmailSummaryPreference
from app.models.job_email_event_model import JobEmailEvent
from app.services import application_tracking_service as tracking
from app.services import gmail_service
from app.services import job_email_scan_service
from app.services.email_provider import ConnectionStatus, DraftResult, EmailProvider, ProviderMessage


class FakeProvider(EmailProvider):
    def __init__(self, messages: dict[str, ProviderMessage], stubs: list[dict] | None = None):
        self.access_token = "fake-token"
        self._messages = messages
        self._stubs = stubs if stubs is not None else [{"id": mid} for mid in messages]
        self.get_message_calls: list[str] = []
        self.list_recent_messages_calls: list[dict] = []

    def get_connection_status(self) -> ConnectionStatus:
        return ConnectionStatus(connected=True)

    def list_recent_messages(self, *, query: str, max_results: int = 25):
        self.list_recent_messages_calls.append({"query": query, "max_results": max_results})
        return self._stubs[:max_results]

    def get_message(self, message_id: str):
        self.get_message_calls.append(message_id)
        return self._messages.get(message_id)

    def create_draft(self, *, to, subject, body, thread_id=None):
        return DraftResult(draft_id="draft-1", created=True)

    def disconnect(self):
        pass


def _connected(db_session, user_id, *, last_history_id=None):
    connection = EmailConnection(user_id=user_id, provider="gmail", status="CONNECTED", email_address="candidate@gmail.com", last_history_id=last_history_id)
    db_session.add(connection)
    db_session.commit()
    db_session.refresh(connection)
    return connection


def _message(message_id, *, subject, sender, body, received_at=None):
    return ProviderMessage(message_id=message_id, thread_id=f"thread-{message_id}", sender=sender, subject=subject, received_at=received_at, snippet=body[:100], body=body)


def _patched_provider(provider):
    return patch("app.services.job_email_scan_service.gmail_service.get_provider_for_user", return_value=provider)


def _patched_profile(history_id="1"):
    return patch("app.services.job_email_scan_service.gmail_service.get_profile", return_value={"historyId": history_id})


# ---------------------------------------------------------------------------
# Bounded initial scan + classification pipeline
# ---------------------------------------------------------------------------

def test_initial_scan_is_bounded_and_persists_job_related_events_only(db_session, test_user):
    _connected(db_session, test_user.id)
    provider = FakeProvider({
        "m1": _message("m1", subject="Thank you for applying", sender="hr@abccorp.com", body="We have received your application for the Java Developer role at ABC Corp."),
        "m2": _message("m2", subject="Your Amazon order shipped", sender="no-reply@amazon.com", body="Tracking info inside."),
    })
    with _patched_provider(provider), _patched_profile():
        summary = job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    assert summary["messages_checked"] == 2
    events = db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).all()
    assert len(events) == 1  # the Amazon email is never persisted (data minimization, Part 12)
    assert events[0].event_type == "APPLICATION_CONFIRMATION"


# ---------------------------------------------------------------------------
# CURRENT VERIFIED BUG regression: JobEmailEvent.received_at must carry the
# message's actual Gmail-received time, never the scan's own current time
# (Requirement B, and D/E via gmail_service tests).
# ---------------------------------------------------------------------------

def test_new_job_email_event_stores_received_at(db_session, test_user):
    """Requirement B."""
    message_received_at = datetime(2024, 10, 15, 13, 20, 0, tzinfo=timezone.utc)
    _connected(db_session, test_user.id)
    provider = FakeProvider({
        "m1": _message(
            "m1", subject="Thank you for applying", sender="hr@abccorp.com",
            body="We have received your application.", received_at=message_received_at,
        ),
    })
    before_scan = datetime.now(timezone.utc)
    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    event = db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).first()
    assert event is not None
    # SQLite (this test's engine) round-trips DateTime(timezone=True) as a
    # naive datetime — compare wall-clock value, not tzinfo, which is a
    # storage-driver detail unrelated to what this test is verifying.
    stored = event.received_at if event.received_at.tzinfo else event.received_at.replace(tzinfo=timezone.utc)
    assert stored == message_received_at
    # Never the scan time — the message was "received" long before this scan ran.
    assert stored < before_scan


def test_missing_received_at_from_provider_stays_null_and_does_not_crash(db_session, test_user):
    """Requirement E, at the scan-pipeline level: a provider message with no
    resolvable received_at (both internalDate and Date header unavailable)
    must not crash the scan — the event is still created, with
    received_at=None."""
    _connected(db_session, test_user.id)
    provider = FakeProvider({
        "m1": _message("m1", subject="Thank you for applying", sender="hr@abccorp.com", body="We have received your application.", received_at=None),
    })
    with _patched_provider(provider), _patched_profile():
        summary = job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    assert summary["messages_checked"] == 1
    event = db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).first()
    assert event is not None
    assert event.received_at is None


# ---------------------------------------------------------------------------
# CURRENT VERIFIED BUG regression: unrelated mail must never persist a
# JobEmailEvent, regardless of scan mode (Requirements B-E, L, M).
# ---------------------------------------------------------------------------

_UNRELATED_EMAILS = {
    "shopping": _message("shop-1", subject="Here it is: Our holiday first look & 50% off ALL cards!", sender="Shutterfly <shutterfly@em.shutterfly.com>", body="Shop our newest holiday cards now and save. This exclusive offer ends soon. Use code SAVE50."),
    "banking": _message("bank-1", subject="Your December statement is ready", sender="alerts@chase.com", body="Your account statement for December is now available."),
    "personal": _message("pers-1", subject="Dinner Friday?", sender="friend@gmail.com", body="Hey, are you free for dinner Friday night?"),
    "newsletter": _message("news-1", subject="This week in tech", sender="news@techdigest.example.com", body="Top stories this week. Unsubscribe anytime."),
}


def test_shopping_promotion_does_not_create_job_email_event(db_session, test_user):
    """Requirement B."""
    _connected(db_session, test_user.id)
    provider = FakeProvider({"shop-1": _UNRELATED_EMAILS["shopping"]})
    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)
    assert db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).count() == 0


def test_banking_email_does_not_create_job_email_event(db_session, test_user):
    """Requirement C."""
    _connected(db_session, test_user.id)
    provider = FakeProvider({"bank-1": _UNRELATED_EMAILS["banking"]})
    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)
    assert db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).count() == 0


def test_personal_email_does_not_create_job_email_event(db_session, test_user):
    """Requirement D."""
    _connected(db_session, test_user.id)
    provider = FakeProvider({"pers-1": _UNRELATED_EMAILS["personal"]})
    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)
    assert db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).count() == 0


def test_newsletter_does_not_create_job_email_event(db_session, test_user):
    """Requirement E."""
    _connected(db_session, test_user.id)
    provider = FakeProvider({"news-1": _UNRELATED_EMAILS["newsletter"]})
    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)
    assert db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).count() == 0


def test_incremental_history_with_one_job_and_one_promotional_email_processes_only_the_job_one(db_session, test_user):
    """Requirement L — the exact "important investigation" scenario: Gmail's
    history API returns ALL new messages, not just job-related ones, so
    application-level classification (not the Gmail query) must be what
    filters them for an incremental scan."""
    _connected(db_session, test_user.id, last_history_id="existing-cursor")
    provider = FakeProvider({
        "job-1": _message("job-1", subject="Thank you for applying", sender="hr@abccorp.com", body="We have received your application for the Java Developer role."),
        "promo-1": _UNRELATED_EMAILS["shopping"],
    })
    with _patched_provider(provider), \
         patch("app.services.job_email_scan_service.gmail_service.get_history", return_value={
             "history": [{"messagesAdded": [{"message": {"id": "job-1"}}, {"message": {"id": "promo-1"}}]}],
             "historyId": "new-cursor",
         }):
        summary = job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    assert summary["messages_checked"] == 2
    assert summary["outcomes"].get("unrelated_ignored") == 1
    events = db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).all()
    assert len(events) == 1
    assert events[0].message_id == "job-1"


def test_unrelated_ignored_appears_in_diagnostics_without_storing_the_email(db_session, test_user):
    """Requirement M."""
    _connected(db_session, test_user.id)
    provider = FakeProvider({"shop-1": _UNRELATED_EMAILS["shopping"]})
    with _patched_provider(provider), _patched_profile():
        summary = job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    assert summary["outcomes"] == {"unrelated_ignored": 1}
    assert db_session.query(JobEmailEvent).count() == 0
    # The diagnostic summary itself never carries the email's subject/body.
    assert "Shutterfly" not in str(summary)
    assert "50% off" not in str(summary)


# ---------------------------------------------------------------------------
# Job-related event types still work end-to-end (Requirements F-K)
# ---------------------------------------------------------------------------

def _scan_one(db_session, test_user, message):
    _connected(db_session, test_user.id)
    provider = FakeProvider({message.message_id: message})
    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)
    return db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).first()


def test_application_confirmation_still_creates_correct_event(db_session, test_user):
    """Requirement F."""
    event = _scan_one(db_session, test_user, _message("f1", subject="Thank you for applying", sender="hr@abccorp.com", body="We have received your application."))
    assert event is not None and event.event_type == "APPLICATION_CONFIRMATION"


def test_recruiter_outreach_still_works(db_session, test_user):
    """Requirement G."""
    event = _scan_one(db_session, test_user, _message("g1", subject="Exciting opportunity for you", sender="jane@recruiting-agency.com", body="I came across your profile. Are you open to new opportunities?"))
    assert event is not None and event.event_type == "RECRUITER_OUTREACH"


def test_interview_invitation_still_works(db_session, test_user):
    """Requirement H."""
    event = _scan_one(db_session, test_user, _message("h1", subject="Interview Invitation", sender="recruiter@abccorp.com", body="We would like to schedule an interview with you."))
    assert event is not None and event.event_type == "INTERVIEW_INVITE"


def test_assessment_request_still_works(db_session, test_user):
    """Requirement I."""
    event = _scan_one(db_session, test_user, _message("i1", subject="Next Steps: Assessment", sender="hr@abccorp.com", body="Please complete this assessment. Here is the coding challenge link."))
    assert event is not None and event.event_type == "ASSESSMENT_REQUEST"


def test_offer_still_works(db_session, test_user):
    """Requirement J."""
    event = _scan_one(db_session, test_user, _message("j1", subject="Job Offer - Java Developer", sender="hr@abccorp.com", body="We are pleased to offer you the position."))
    assert event is not None and event.event_type == "OFFER"


def test_rejection_still_works(db_session, test_user):
    """Requirement K."""
    event = _scan_one(db_session, test_user, _message("k1", subject="Application Update", sender="hr@abccorp.com", body="We have decided not to move forward with your application."))
    assert event is not None and event.event_type == "REJECTION"


def test_application_update_still_works(db_session, test_user):
    """Item 9 (stabilization pass): APPLICATION_UPDATE end-to-end through
    the scan pipeline — correctly classified, NEEDS_CONFIRMATION, and never
    auto-updates ApplicationTracking (consistent with every other event type)."""
    event = _scan_one(
        db_session, test_user,
        _message("l1", subject="Update on Your Application", sender="hr@abccorp.com",
                  body="We wanted to give you an update on your application. It is still under review by our hiring team."),
    )
    assert event is not None
    assert event.event_type == "APPLICATION_UPDATE"
    assert event.resolution == "NEEDS_CONFIRMATION"


# ---------------------------------------------------------------------------
# Requirements B-G: initial-scan-cursor bug — a connection with no
# last_history_id (the state a successful OAuth callback now leaves it in)
# must run the bounded scan first, then switch to incremental thereafter.
# ---------------------------------------------------------------------------

def test_first_scan_with_no_history_id_calls_list_recent_messages(db_session, test_user):
    """Requirement B."""
    _connected(db_session, test_user.id, last_history_id=None)
    provider = FakeProvider({})
    with _patched_provider(provider), _patched_profile(), \
         patch("app.services.job_email_scan_service.gmail_service.get_history") as mocked_history:
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    assert len(provider.list_recent_messages_calls) == 1
    mocked_history.assert_not_called()


def test_first_scan_uses_bounded_query_with_newer_than(db_session, test_user, monkeypatch):
    """Requirement C."""
    monkeypatch.setenv("GMAIL_INITIAL_SCAN_DAYS", "30")
    _connected(db_session, test_user.id, last_history_id=None)
    provider = FakeProvider({})
    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    query = provider.list_recent_messages_calls[0]["query"]
    assert query.startswith(job_email_scan_service.JOB_SEARCH_QUERY)
    assert "newer_than:30d" in query


def test_first_scan_respects_default_max_messages(db_session, test_user, monkeypatch):
    """Requirement D."""
    monkeypatch.delenv("GMAIL_SCAN_MAX_MESSAGES", raising=False)
    _connected(db_session, test_user.id, last_history_id=None)
    provider = FakeProvider({})
    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    assert provider.list_recent_messages_calls[0]["max_results"] == 25  # GMAIL_SCAN_MAX_MESSAGES default


def test_after_first_bounded_scan_history_id_is_persisted(db_session, test_user):
    """Requirement E."""
    connection = _connected(db_session, test_user.id, last_history_id=None)
    provider = FakeProvider({})
    with _patched_provider(provider), _patched_profile(history_id="post-bounded-scan-id"):
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    db_session.refresh(connection)
    assert connection.last_history_id == "post-bounded-scan-id"


def test_second_scan_uses_history_api_not_bounded_scan_again(db_session, test_user):
    """Requirement F — full two-scan sequence: first scan is bounded and
    stores a cursor; the very next scan (no code changes in between,
    exactly what a real second "Scan Job Emails" click does) must use
    get_history() and must NOT call list_recent_messages() again."""
    connection = _connected(db_session, test_user.id, last_history_id=None)
    provider = FakeProvider({})
    with _patched_provider(provider), _patched_profile(history_id="cursor-after-first-scan"):
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    assert len(provider.list_recent_messages_calls) == 1
    db_session.refresh(connection)
    assert connection.last_history_id == "cursor-after-first-scan"

    with _patched_provider(provider), \
         patch("app.services.job_email_scan_service.gmail_service.get_history", return_value={"history": [], "historyId": "cursor-after-second-scan"}) as mocked_history:
        second_summary = job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    mocked_history.assert_called_once()
    assert mocked_history.call_args.kwargs["start_history_id"] == "cursor-after-first-scan"
    assert len(provider.list_recent_messages_calls) == 1  # still 1 — the bounded scan was NOT repeated
    assert second_summary["messages_checked"] == 0


def test_zero_history_messages_on_later_scan_is_valid(db_session, test_user):
    """Requirement G — an incremental scan that legitimately finds nothing
    new is a valid, successful result, not an error and not a fallback to
    the bounded scan."""
    _connected(db_session, test_user.id, last_history_id="existing-cursor")
    provider = FakeProvider({})
    with _patched_provider(provider), \
         patch("app.services.job_email_scan_service.gmail_service.get_history", return_value={"history": [], "historyId": "existing-cursor"}):
        summary = job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    assert summary == {"messages_checked": 0, "outcomes": {}}
    assert len(provider.list_recent_messages_calls) == 0


def test_high_confidence_interview_match_does_not_auto_update_status(db_session, test_user, java_job):
    """Requirement D: Gmail is read-only for Email Summary — even a
    high-confidence, reliably-matched interview email must never itself
    change ApplicationTracking. It only populates the JobEmailEvent's match
    fields and stays NEEDS_CONFIRMATION until the user clicks Confirm."""
    application = ApplicationTracking(user_id=test_user.id, job_id=java_job.id, company=java_job.company, title=java_job.title, status="APPLIED", status_source="system")
    db_session.add(application)
    db_session.commit()

    _connected(db_session, test_user.id)
    provider = FakeProvider({
        "m1": _message(
            "m1", subject=f"Interview Invitation - {java_job.title}",
            sender=f"recruiter@{java_job.company.lower().replace(' ', '')}.com",
            body=f"We would like to schedule an interview with you for the {java_job.title} role at {java_job.company}.",
        ),
    })
    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    db_session.refresh(application)
    assert application.status == "APPLIED"  # never changed by scanning
    assert application.status_source == "system"

    event = db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).first()
    assert event is not None
    assert event.resolution == "NEEDS_CONFIRMATION"
    assert event.matched_application_id == application.id
    assert event.matched_job_id == java_job.id
    assert event.match_confidence >= tracking.RELIABLE_MATCH_CONFIDENCE_THRESHOLD

    history = db_session.query(ApplicationStatusHistory).filter_by(application_id=application.id).all()
    assert history == []  # no status-history transition was ever created by scanning


def _assert_scan_never_auto_updates(db_session, test_user, java_job, application, *, subject, body, expected_event_type):
    _connected(db_session, test_user.id)
    provider = FakeProvider({
        "m1": _message(
            "m1", subject=subject,
            sender=f"recruiter@{java_job.company.lower().replace(' ', '')}.com",
            body=body,
        ),
    })
    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    db_session.refresh(application)
    assert application.status == "APPLIED"  # never changed by scanning
    assert application.status_source == "system"

    event = db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).first()
    assert event is not None
    assert event.event_type == expected_event_type
    assert event.resolution == "NEEDS_CONFIRMATION"
    assert event.matched_application_id == application.id
    assert event.match_confidence >= tracking.RELIABLE_MATCH_CONFIDENCE_THRESHOLD

    history = db_session.query(ApplicationStatusHistory).filter_by(application_id=application.id).all()
    assert history == []


def test_high_confidence_offer_match_does_not_auto_update_status(db_session, test_user, java_job):
    """Requirement A/B: a high-confidence Job Offer email must not update
    ApplicationTracking during scanning and must be stored NEEDS_CONFIRMATION."""
    application = ApplicationTracking(user_id=test_user.id, job_id=java_job.id, company=java_job.company, title=java_job.title, status="APPLIED", status_source="system")
    db_session.add(application)
    db_session.commit()
    _assert_scan_never_auto_updates(
        db_session, test_user, java_job, application,
        subject=f"Job Offer - {java_job.title}",
        body=f"We are pleased to offer you the {java_job.title} position at {java_job.company}.",
        expected_event_type="OFFER",
    )


def test_high_confidence_rejection_match_does_not_auto_update_status(db_session, test_user, java_job):
    """Requirement C."""
    application = ApplicationTracking(user_id=test_user.id, job_id=java_job.id, company=java_job.company, title=java_job.title, status="APPLIED", status_source="system")
    db_session.add(application)
    db_session.commit()
    _assert_scan_never_auto_updates(
        db_session, test_user, java_job, application,
        subject="Application Update",
        body=f"We have decided not to move forward with your application for the {java_job.title} role at {java_job.company}.",
        expected_event_type="REJECTION",
    )


def test_high_confidence_assessment_match_does_not_auto_update_status(db_session, test_user, java_job):
    """Requirement E."""
    application = ApplicationTracking(user_id=test_user.id, job_id=java_job.id, company=java_job.company, title=java_job.title, status="APPLIED", status_source="system")
    db_session.add(application)
    db_session.commit()
    _assert_scan_never_auto_updates(
        db_session, test_user, java_job, application,
        subject=f"Next Steps: Assessment for {java_job.title}",
        body=f"Please complete this assessment for the {java_job.title} role at {java_job.company}. Here is the coding challenge link.",
        expected_event_type="ASSESSMENT_REQUEST",
    )


def test_low_confidence_requires_user_confirmation(db_session, test_user):
    _connected(db_session, test_user.id)
    provider = FakeProvider({
        "m1": _message("m1", subject="Following up", sender="someone@example.com", body="Just checking in about your candidate profile and the position."),
    })
    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    event = db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).first()
    assert event is not None
    assert event.resolution == "NEEDS_CONFIRMATION"


# ---------------------------------------------------------------------------
# CURRENT VERIFIED BUG regression: a weak, title-overlap-only match must
# never be persisted as matched_application_id/matched_job_id, even though
# it's the "best available" candidate found (Requirement B).
# ---------------------------------------------------------------------------

def test_weak_match_is_not_persisted_as_matched_application(db_session, test_user):
    application = ApplicationTracking(
        user_id=test_user.id, job_id=None, company="Computer Merchant",
        title="Software Development Engineer", status="APPLIED", status_source="system",
    )
    db_session.add(application)
    db_session.commit()

    _connected(db_session, test_user.id)
    provider = FakeProvider({
        "m1": _message(
            "m1", subject="Application Received - CareerBoost Test",
            sender="no-reply@some-ats-vendor.example.com",
            body="Thank you for applying for the Software Engineer position at Test Company.",
        ),
    })
    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    event = db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).first()
    assert event is not None
    assert event.event_type == "APPLICATION_CONFIRMATION"
    # Requirement B/6: confidence and match_confidence are preserved and
    # honest (low), but the candidate is not exposed as a reliable match.
    assert event.confidence is not None and event.confidence < 0.3
    assert event.match_confidence is not None and event.match_confidence < 0.5
    assert event.matched_application_id is None
    assert event.matched_job_id is None
    assert event.resolution == "NEEDS_CONFIRMATION"

    db_session.refresh(application)
    assert application.status == "APPLIED"  # never touched by the unreliable match


# ---------------------------------------------------------------------------
# CURRENT LIVE BUG regression, end-to-end through the scan pipeline: a
# Computer Merchant assessment email must match the Computer Merchant SDET
# application, never the unrelated SES Sr. Java Developer application whose
# company name ("SES") happens to be a raw substring of "Assessment".
# ---------------------------------------------------------------------------

def test_assessment_email_matches_computer_merchant_not_ses(db_session, test_user):
    ses = ApplicationTracking(
        user_id=test_user.id, job_id=None, company="SES",
        title="Sr. Java Developer (Java 21, Apache Kafka, SQL, AWS)",
        status="APPLIED", status_source="system",
    )
    computer_merchant = ApplicationTracking(
        user_id=test_user.id, job_id=None, company="Computer Merchant",
        title="Software Development Engineer (SDET)",
        status="APPLIED", status_source="system",
    )
    db_session.add_all([ses, computer_merchant])
    db_session.commit()
    db_session.refresh(ses)
    db_session.refresh(computer_merchant)

    _connected(db_session, test_user.id)
    provider = FakeProvider({
        "m1": _message(
            "m1",
            subject="Assessment Request - Software Development Engineer (SDET) at Computer Merchant",
            sender="ats@computermerchant-noreply.example.com",
            body=(
                "Hi, thank you for applying to the Software Development Engineer (SDET) role "
                "at Computer Merchant. We would like you to complete an online technical "
                "assessment as the next step in our process."
            ),
        ),
    })
    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    event = db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).first()
    assert event is not None
    assert event.event_type == "ASSESSMENT_REQUEST"
    assert event.matched_application_id == computer_merchant.id
    assert event.matched_application_id != ses.id

    db_session.refresh(ses)
    db_session.refresh(computer_merchant)
    assert ses.status == "APPLIED"  # never touched


# ---------------------------------------------------------------------------
# Idempotency (Part 21)
# ---------------------------------------------------------------------------

def test_processing_the_same_message_twice_creates_no_duplicate_event(db_session, test_user):
    connection = _connected(db_session, test_user.id)
    message = _message("m1", subject="Thank you for applying", sender="hr@abccorp.com", body="We have received your application.")
    provider = FakeProvider({"m1": message})

    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)
    # Force the second scan through the same bounded path (rather than the
    # history-API branch, covered separately) so the idempotency guarantee
    # under test is isolated from which scan mode ran.
    connection.last_history_id = None
    db_session.commit()
    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    events = db_session.query(JobEmailEvent).filter_by(user_id=test_user.id, message_id="m1").all()
    assert len(events) == 1


def test_processing_the_same_message_twice_never_creates_status_history(db_session, test_user, java_job):
    """Gmail scanning is read-only-detect-only (this task): re-scanning the
    same reliably-matched interview email must never create ANY
    ApplicationTracking status-history row, on the first scan or a repeat —
    only the user's explicit Confirm action may ever do that."""
    application = ApplicationTracking(user_id=test_user.id, job_id=java_job.id, company=java_job.company, title=java_job.title, status="APPLIED", status_source="system")
    db_session.add(application)
    db_session.commit()

    connection = _connected(db_session, test_user.id)
    message = _message(
        "m1", subject=f"Interview Invitation - {java_job.title}",
        sender=f"recruiter@{java_job.company.lower().replace(' ', '')}.com",
        body=f"We would like to schedule an interview for the {java_job.title} role at {java_job.company}.",
    )
    provider = FakeProvider({"m1": message})

    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)
    connection.last_history_id = None
    db_session.commit()
    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    history = db_session.query(ApplicationStatusHistory).filter_by(application_id=application.id).all()
    assert history == []
    db_session.refresh(application)
    assert application.status == "APPLIED"


def test_same_message_can_produce_a_second_event_for_a_different_event_type(db_session, test_user):
    """Idempotency key is (user_id, message_id, event_type) — a later scan
    that reclassifies the SAME message differently is a legitimate second
    event, not a duplicate."""
    _connected(db_session, test_user.id)
    message = _message("m1", subject="Thank you for applying", sender="hr@abccorp.com", body="We have received your application.")
    provider = FakeProvider({"m1": message})
    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    from app.models.job_email_event_model import JobEmailEvent as Event
    existing = db_session.query(Event).filter_by(user_id=test_user.id, message_id="m1").first()
    existing.event_type = "APPLICATION_CONFIRMATION"  # confirm what's there
    db_session.commit()

    # Reset last_history_id so the bounded scan path (not history) runs again with a differently-classified message.
    connection = db_session.query(EmailConnection).filter_by(user_id=test_user.id).first()
    connection.last_history_id = None
    db_session.commit()

    message2 = _message("m1", subject="Interview Invitation", sender="hr@abccorp.com", body="We would like to schedule an interview with you.")
    provider2 = FakeProvider({"m1": message2})
    with _patched_provider(provider2), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    events = db_session.query(Event).filter_by(user_id=test_user.id, message_id="m1").all()
    event_types = {e.event_type for e in events}
    assert event_types == {"APPLICATION_CONFIRMATION", "INTERVIEW_INVITE"}


# ---------------------------------------------------------------------------
# Message fetch failure does not stop the batch
# ---------------------------------------------------------------------------

def test_one_message_fetch_failure_does_not_stop_the_batch(db_session, test_user):
    _connected(db_session, test_user.id)
    provider = FakeProvider(
        messages={"m2": _message("m2", subject="Thank you for applying", sender="hr@abccorp.com", body="We have received your application.")},
        stubs=[{"id": "m1"}, {"id": "m2"}],  # m1 has no matching message -> get_message returns None
    )
    with _patched_provider(provider), _patched_profile():
        summary = job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    assert summary["outcomes"].get("fetch_failed") == 1
    events = db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).all()
    assert len(events) == 1
    assert events[0].message_id == "m2"


# ---------------------------------------------------------------------------
# Incremental scanning (Part 11)
# ---------------------------------------------------------------------------

def test_incremental_scan_uses_history_api_when_history_id_present(db_session, test_user):
    _connected(db_session, test_user.id, last_history_id="100")
    provider = FakeProvider({"m9": _message("m9", subject="Offer", sender="hr@abccorp.com", body="We are pleased to offer you the position.")})

    with _patched_provider(provider), \
         patch("app.services.job_email_scan_service.gmail_service.get_history", return_value={"history": [{"messagesAdded": [{"message": {"id": "m9"}}]}], "historyId": "200"}) as mocked_history:
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    mocked_history.assert_called_once()
    connection = db_session.query(EmailConnection).filter_by(user_id=test_user.id).first()
    assert connection.last_history_id == "200"
    events = db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).all()
    assert len(events) == 1
    assert events[0].event_type == "OFFER"


def test_stale_history_id_falls_back_to_bounded_scan(db_session, test_user):
    _connected(db_session, test_user.id, last_history_id="100")
    provider = FakeProvider({"m1": _message("m1", subject="Thank you for applying", sender="hr@abccorp.com", body="We have received your application.")})

    with _patched_provider(provider), \
         patch("app.services.job_email_scan_service.gmail_service.get_history", side_effect=job_email_scan_service.gmail_service.GmailApiError("HTTP 404")), \
         _patched_profile():
        summary = job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    assert summary["messages_checked"] == 1


# ---------------------------------------------------------------------------
# CURRENT VERIFIED ISSUE (this task): body_snippet whitespace normalization.
# Root cause: Gmail's own server-generated `snippet` field strips HTML tags
# WITHOUT inserting a separating space, unlike gmail_service._extract_body/
# _html_to_text (already fixed previously). _process_message used to store
# `message.snippet` in preference to the properly-normalized `message.body`,
# so real scanned messages (which almost always carry a non-empty Gmail
# snippet) never actually benefited from the earlier whitespace fix for the
# one field (body_snippet) shown in the UI and re-scanned at confirm time.
# ---------------------------------------------------------------------------

def test_body_snippet_prefers_normalized_body_over_gmails_glued_snippet(db_session, test_user):
    """The exact reported bug, reproduced directly: Gmail's own snippet
    field is glued ("position atComputer Merchant", "SQL,AWS", etc. — no
    separating space at all, not fixable by re-processing since the
    boundary information is already gone) while the message's actual HTML
    body — run through gmail_service's existing, correct extraction — is
    properly spaced. The stored JobEmailEvent.body_snippet must reflect the
    correctly-spaced body, never Gmail's glued snippet."""
    _connected(db_session, test_user.id)
    glued_snippet = (
        "Assessment Request Fix Test - Software Development Engineer (SDET) at Computer "
        "Merchant Hi, thank you for applying to the SDET role at Computer Merchant. Please "
        "complete an online technical assessment foryour application. Aftercareful "
        "consideration we would like to proceed withyour application. Required skills: SQL,AWS."
    )
    html_body = (
        "<p>Hi, thank you for applying to the SDET role at Computer Merchant.</p>"
        "<p>Please complete an online technical assessment for</p><p>your application.</p>"
        "<div>After</div><div>careful consideration we would like to proceed</div>"
        "<span>with</span><span>your application.</span>"
        "<p>Required skills: SQL,</p><p>AWS.</p>"
    )
    message = ProviderMessage(
        message_id="m1", thread_id="thread-m1", sender="ats@computermerchant-noreply.example.com",
        subject="Assessment Request Fix Test - Software Development Engineer (SDET) at Computer Merchant",
        received_at=None, snippet=glued_snippet, body=job_email_scan_service.gmail_service._html_to_text(html_body),
    )
    provider = FakeProvider({"m1": message})

    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    event = db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).first()
    assert event is not None
    assert event.body_snippet == message.body
    for glued in ("atComputer", "foryour", "Aftercareful", "withyour", "SQL,AWS"):
        assert glued not in event.body_snippet
    assert "for your application" in event.body_snippet
    assert "After careful consideration" in event.body_snippet
    assert "with your application" in event.body_snippet
    assert "SQL, AWS" in event.body_snippet


def test_body_snippet_falls_back_to_gmail_snippet_when_body_is_empty(db_session, test_user):
    """The raw Gmail snippet remains a valid last-resort fallback for the
    rare message with no extractable body at all — never silently dropping
    the only available preview text."""
    _connected(db_session, test_user.id)
    message = ProviderMessage(
        message_id="m1", thread_id="thread-m1", sender="hr@abccorp.com",
        subject="Thank you for applying", received_at=None,
        snippet="We have received your application.", body="",
    )
    provider = FakeProvider({"m1": message})

    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    event = db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).first()
    assert event is not None
    assert event.body_snippet == "We have received your application."


def test_classification_and_matching_still_use_body_not_snippet(db_session, test_user, java_job):
    """Requirement 8 (task): only body_snippet's presentation changes — the
    classify/match pipeline already used message.body directly and must
    continue to, unaffected by this fix. A glued/garbage snippet must never
    influence event_type, match_confidence, or matched_application_id."""
    application = ApplicationTracking(user_id=test_user.id, job_id=java_job.id, company=java_job.company, title=java_job.title, status="APPLIED", status_source="system")
    db_session.add(application)
    db_session.commit()

    _connected(db_session, test_user.id)
    message = ProviderMessage(
        message_id="m1", thread_id="thread-m1",
        sender=f"recruiter@{java_job.company.lower().replace(' ', '')}.com",
        subject=f"Interview Invitation - {java_job.title}", received_at=None,
        snippet="totally unrelated garbage snippet text with no job content at all",
        body=f"We would like to schedule an interview with you for the {java_job.title} role at {java_job.company}.",
    )
    provider = FakeProvider({"m1": message})

    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    event = db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).first()
    assert event is not None
    assert event.event_type == "INTERVIEW_INVITE"
    assert event.matched_application_id == application.id
    assert event.match_confidence >= tracking.RELIABLE_MATCH_CONFIDENCE_THRESHOLD


# ---------------------------------------------------------------------------
# LIVE VERIFIED BUG (this task): "position at SES.Your background" — the
# multipart/alternative punctuation-boundary fix in gmail_service.py must
# reach the stored JobEmailEvent.body_snippet for a newly scanned message,
# exactly reproducing the reported "Recruiter Spacing Test" event.
# ---------------------------------------------------------------------------

def test_newly_scanned_recruiter_spacing_event_uses_corrected_body_snippet(db_session, test_user):
    """Requirement K: end-to-end through gmail_service._extract_body (the
    real multipart/alternative comparison, not a hand-built body) into the
    scan pipeline — the exact reported event's punctuation boundary must be
    corrected, while its already-correct spacing elsewhere is preserved."""
    from app.services import gmail_service

    plain = (
        "Hi, I'm reaching out regarding a Sr. Java Developer position at SES.Your background "
        "in Java, Apache Kafka, SQL, AWS looks like a great fit. After careful consideration "
        "we would like to proceed with your application."
    )
    html = (
        "<p>Hi, I'm reaching out regarding a Sr. Java Developer position at SES.</p>"
        "<p>Your background in Java, Apache Kafka, SQL, AWS looks like a great fit.</p>"
        "<p>After careful consideration we would like to proceed with your application.</p>"
    )
    # Build the multipart payload the same way parse_message() would see it.
    import base64

    def b64(text: str) -> str:
        return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")

    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": b64(plain)}},
            {"mimeType": "text/html", "body": {"data": b64(html)}},
        ],
    }
    body = gmail_service._extract_body(payload)
    assert "SES.Your" not in body  # sanity check the fixture reproduces the bug before scanning

    application = ApplicationTracking(user_id=test_user.id, job_id=None, company="SES", title="Sr. Java Developer", status="APPLIED", status_source="system")
    db_session.add(application)
    db_session.commit()

    _connected(db_session, test_user.id)
    message = ProviderMessage(
        message_id="m1", thread_id="thread-m1", sender="recruiter@ses.com",
        subject="Recruiter Spacing Test - Sr. Java Developer at SES",
        received_at=None, snippet="position at SES.Your background in Java, Apache Kafka, SQL, AWS.",
        body=body,
    )
    provider = FakeProvider({"m1": message})

    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    event = db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).first()
    assert event is not None
    assert event.event_type == "RECRUITER_OUTREACH"
    assert event.resolution == "NEEDS_CONFIRMATION"
    assert "SES.Your" not in event.body_snippet
    assert "SES. Your background" in event.body_snippet
    assert "SQL, AWS" in event.body_snippet
    assert "After careful consideration" in event.body_snippet
    assert "with your application" in event.body_snippet


# ---------------------------------------------------------------------------
# Email Summary Settings -> "Email Types to Scan" category gating. Shared by
# both the manual "Scan Job Emails" button and the automatic scheduler,
# since both call scan_for_job_emails — this is the one scan pipeline.
# ---------------------------------------------------------------------------

def _set_email_types_to_scan(db_session, user_id, categories):
    preference = db_session.query(EmailSummaryPreference).filter_by(user_id=user_id).first()
    if preference is None:
        preference = EmailSummaryPreference(user_id=user_id)
        db_session.add(preference)
    preference.email_types_to_scan = json.dumps(categories)
    db_session.commit()


def test_no_preference_row_enables_all_categories_by_default(db_session, test_user):
    """A user who has never touched Email Summary Settings gets the exact
    same behavior as before this feature existed."""
    _connected(db_session, test_user.id)
    provider = FakeProvider({
        "m1": _message("m1", subject="Exciting opportunity for you", sender="jane@recruiting-agency.com", body="I came across your profile. Are you open to new opportunities?"),
    })
    with _patched_provider(provider), _patched_profile():
        summary = job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    assert summary["outcomes"].get("category_disabled_skipped") is None
    events = db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).all()
    assert len(events) == 1
    assert events[0].event_type == "RECRUITER_OUTREACH"


def test_disabled_category_is_skipped_and_not_persisted(db_session, test_user):
    """Recruiter Outreach unchecked -> a RECRUITER_OUTREACH email is
    detected/classified but never persisted as a JobEmailEvent — same data-
    minimization stance as an unrelated email."""
    _connected(db_session, test_user.id)
    _set_email_types_to_scan(db_session, test_user.id, ["application_updates", "interview_invitations"])
    provider = FakeProvider({
        "m1": _message("m1", subject="Exciting opportunity for you", sender="jane@recruiting-agency.com", body="I came across your profile. Are you open to new opportunities?"),
    })
    with _patched_provider(provider), _patched_profile():
        summary = job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    assert summary["outcomes"] == {"category_disabled_skipped": 1}
    assert db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).count() == 0


def test_recruiter_outreach_requires_recruiter_messages_even_with_job_opportunities_enabled(db_session, test_user):
    """Turning Recruiter Messages off excludes recruiter-only mail."""
    _connected(db_session, test_user.id)
    _set_email_types_to_scan(db_session, test_user.id, ["job_opportunities", "application_updates", "interview_invitations"])
    provider = FakeProvider({
        "m1": _message("m1", subject="Exciting opportunity for you", sender="jane@recruiting-agency.com", body="I came across your profile. Are you open to new opportunities?"),
        "m2": _message("m2", subject="Interview Invitation - Python Developer", sender="hr@example.com", body="We would like to schedule a technical interview for your Python Developer application."),
    })
    with _patched_provider(provider), _patched_profile():
        summary = job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    assert summary["outcomes"] == {"category_disabled_skipped": 1, "needs_confirmation": 1}
    events = db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).all()
    assert len(events) == 1
    assert events[0].event_type == "INTERVIEW_INVITE"


def test_explicit_empty_category_list_disables_all_event_types(db_session, test_user):
    _connected(db_session, test_user.id)
    _set_email_types_to_scan(db_session, test_user.id, [])
    provider = FakeProvider({
        "m1": _message("m1", subject="Interview Invitation", sender="hr@example.com", body="We would like to schedule an interview for your job application."),
    })
    with _patched_provider(provider), _patched_profile():
        summary = job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)
    assert summary["outcomes"] == {"category_disabled_skipped": 1}
    assert db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).count() == 0


def test_application_update_category_gates_all_five_status_event_types(db_session, test_user):
    _connected(db_session, test_user.id)
    _set_email_types_to_scan(db_session, test_user.id, ["job_opportunities", "recruiter_messages", "interview_invitations"])
    provider = FakeProvider({
        "m1": _message("m1", subject="Job Offer - Java Developer", sender="hr@abccorp.com", body="We are pleased to offer you the position."),
    })
    with _patched_provider(provider), _patched_profile():
        summary = job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    assert summary["outcomes"] == {"category_disabled_skipped": 1}
    assert db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).count() == 0


def test_category_gate_never_affects_application_status(db_session, test_user, java_job):
    """Skipping a disabled category is pure data-minimization (nothing is
    persisted) — it must never itself touch ApplicationTracking, exactly
    like the unrelated-email path."""
    application = ApplicationTracking(user_id=test_user.id, job_id=java_job.id, company=java_job.company, title=java_job.title, status="APPLIED", status_source="system")
    db_session.add(application)
    db_session.commit()

    _connected(db_session, test_user.id)
    _set_email_types_to_scan(db_session, test_user.id, ["application_updates"])
    provider = FakeProvider({
        "m1": _message(
            "m1", subject=f"Interview Invitation - {java_job.title}",
            sender=f"recruiter@{java_job.company.lower().replace(' ', '')}.com",
            body=f"We would like to schedule an interview with you for the {java_job.title} role at {java_job.company}.",
        ),
    })
    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    db_session.refresh(application)
    assert application.status == "APPLIED"
    assert db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).count() == 0


# ---------------------------------------------------------------------------
# body_formatted — display-only, paragraph-preserving sibling of body_snippet
# (Email Summary's View Details full email body). Never read by
# classification/matching, which use body_snippet/message.body unchanged.
# ---------------------------------------------------------------------------

def test_body_formatted_preserves_paragraph_breaks_while_body_snippet_stays_flattened(db_session, test_user):
    _connected(db_session, test_user.id)
    message = _message("m1", subject="Thank you for applying", sender="hr@abccorp.com", body="Hi, Thank you for applying.")
    message.body_display = "Hi,\n\nThank you for applying.\n\nBest regards,\nHR Team"
    provider = FakeProvider({"m1": message})
    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    event = db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).first()
    assert event is not None
    assert event.body_snippet == "Hi, Thank you for applying."
    assert event.body_formatted == "Hi,\n\nThank you for applying.\n\nBest regards,\nHR Team"


def test_body_formatted_falls_back_to_flattened_body_when_provider_has_no_display_text(db_session, test_user):
    """A provider/test double that never populates body_display (the
    default) must still produce a usable body_formatted — same text as
    body_snippet, just without paragraph breaks to preserve."""
    _connected(db_session, test_user.id)
    message = _message("m1", subject="Thank you for applying", sender="hr@abccorp.com", body="Hi, Thank you for applying.")
    provider = FakeProvider({"m1": message})
    with _patched_provider(provider), _patched_profile():
        job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    event = db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).first()
    assert event is not None
    assert event.body_formatted == event.body_snippet == "Hi, Thank you for applying."


# ---------------------------------------------------------------------------
# CURRENT VERIFIED BUG fix, end-to-end through the REAL GmailEmailProvider
# (not FakeProvider): a single Gmail message id returning HTTP 404 used to
# raise an uncaught requests.exceptions.HTTPError from gmail_service's
# _gmail_request, crashing the whole scan before it ever reached the
# cursor-advancement code — so the next scan would hit the exact same 404
# and crash again, forever. requests.request is mocked at the HTTP boundary
# (same convention as test_gmail_oauth.py); everything above this section
# (FakeProvider-based tests) is completely unaffected by this fix.
# ---------------------------------------------------------------------------

def _http_response(status_code=200, payload=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload or {}
    response.content = b"1" if payload is not None else b""
    response.raise_for_status.side_effect = None if status_code < 400 else Exception(f"HTTP {status_code}")
    return response


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def _gmail_message_payload(message_id, *, subject, sender, body):
    return {
        "id": message_id, "threadId": f"thread-{message_id}", "snippet": body[:100],
        "internalDate": "1728998400000",
        "payload": {
            "headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}],
            "mimeType": "text/plain",
            "body": {"data": _b64(body)},
        },
    }


def _real_provider(db_session, connection):
    return gmail_service.GmailEmailProvider(db=db_session, connection=connection, access_token="fake-token")


def test_scan_skips_a_404_message_and_still_processes_valid_messages_before_and_after(db_session, test_user):
    connection = _connected(db_session, test_user.id)
    provider = _real_provider(db_session, connection)
    m1 = _gmail_message_payload("m1", subject="Thank you for applying", sender="hr@abccorp.com", body="We have received your application.")
    m3 = _gmail_message_payload("m3", subject="Interview Invitation", sender="hr@abccorp.com", body="We would like to schedule an interview with you.")

    def side_effect(method, url, **kwargs):
        if url.endswith("/profile"):
            return _http_response(200, {"historyId": "1000"})
        if url.endswith("/messages"):
            return _http_response(200, {"messages": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]})
        if url.endswith("/messages/m1"):
            return _http_response(200, m1)
        if url.endswith("/messages/m2"):
            return _http_response(404)
        if url.endswith("/messages/m3"):
            return _http_response(200, m3)
        raise AssertionError(f"unexpected Gmail call: {method} {url}")

    with patch("app.services.job_email_scan_service.gmail_service.get_provider_for_user", return_value=provider), \
         patch("app.services.gmail_service.requests.request", side_effect=side_effect):
        summary = job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    assert summary["messages_checked"] == 3
    assert summary["outcomes"].get("fetch_failed") == 1  # m2 skipped, not a crash
    events = db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).all()
    assert {event.message_id for event in events} == {"m1", "m3"}  # m2 never persisted

    db_session.refresh(connection)
    assert connection.last_history_id == "1000"  # cursor advanced — scan reached the end, didn't crash


def test_second_scan_after_a_404_never_retries_the_same_missing_message_and_creates_no_duplicates(db_session, test_user):
    connection = _connected(db_session, test_user.id)
    provider = _real_provider(db_session, connection)
    m1 = _gmail_message_payload("m1", subject="Thank you for applying", sender="hr@abccorp.com", body="We have received your application.")

    def first_scan(method, url, **kwargs):
        if url.endswith("/profile"):
            return _http_response(200, {"historyId": "1000"})
        if url.endswith("/messages"):
            return _http_response(200, {"messages": [{"id": "m1"}, {"id": "m2"}]})
        if url.endswith("/messages/m1"):
            return _http_response(200, m1)
        if url.endswith("/messages/m2"):
            return _http_response(404)
        raise AssertionError(f"unexpected Gmail call: {method} {url}")

    with patch("app.services.job_email_scan_service.gmail_service.get_provider_for_user", return_value=provider), \
         patch("app.services.gmail_service.requests.request", side_effect=first_scan):
        first_summary = job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)
    assert first_summary["outcomes"].get("fetch_failed") == 1
    db_session.refresh(connection)
    assert connection.last_history_id == "1000"

    # Second scan must go through the incremental /history endpoint (using
    # the cursor the first scan advanced to) and must NEVER call the bounded
    # /messages list or /messages/m2 again — proving the permanently-missing
    # message is not retried forever. Any other call raises immediately.
    def second_scan(method, url, **kwargs):
        if url.endswith("/history"):
            assert kwargs.get("params", {}).get("startHistoryId") == "1000"
            return _http_response(200, {"history": [], "historyId": "1001"})
        raise AssertionError(f"unexpected Gmail call on the second scan: {method} {url}")

    with patch("app.services.job_email_scan_service.gmail_service.get_provider_for_user", return_value=provider), \
         patch("app.services.gmail_service.requests.request", side_effect=second_scan):
        second_summary = job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)

    assert second_summary["messages_checked"] == 0
    events = db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).all()
    assert len(events) == 1  # still just m1 — no duplicates, no phantom retry of m2


# Item 6 — 401/403 during a scan (e.g. the token was revoked between
# messages) must still surface as the existing reconnect-required flow, not
# be silently treated as a per-message skip.
def test_scan_still_raises_auth_error_for_401_not_a_silent_skip(db_session, test_user):
    connection = _connected(db_session, test_user.id)
    provider = _real_provider(db_session, connection)

    def side_effect(method, url, **kwargs):
        if url.endswith("/profile"):
            return _http_response(200, {"historyId": "1000"})
        if url.endswith("/messages"):
            return _http_response(200, {"messages": [{"id": "m1"}]})
        if url.endswith("/messages/m1"):
            return _http_response(401)
        raise AssertionError(f"unexpected Gmail call: {method} {url}")

    with patch("app.services.job_email_scan_service.gmail_service.get_provider_for_user", return_value=provider), \
         patch("app.services.gmail_service.requests.request", side_effect=side_effect):
        try:
            job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)
            assert False, "expected GmailAuthError to propagate"
        except gmail_service.GmailAuthError:
            pass
    assert db_session.query(JobEmailEvent).filter_by(user_id=test_user.id).count() == 0


# Item 7 — a 429/5xx on the message LIST/history call (not an individual
# message) is a real infrastructure failure and must still raise, never be
# converted into a "successful" empty/partial scan.
def test_scan_still_raises_for_5xx_on_the_bounded_list_call(db_session, test_user, monkeypatch):
    monkeypatch.setattr(gmail_service.time, "sleep", lambda _seconds: None)
    connection = _connected(db_session, test_user.id)
    provider = _real_provider(db_session, connection)

    def side_effect(method, url, **kwargs):
        if url.endswith("/messages"):
            return _http_response(503)
        raise AssertionError(f"unexpected Gmail call: {method} {url}")

    with patch("app.services.job_email_scan_service.gmail_service.get_provider_for_user", return_value=provider), \
         patch("app.services.gmail_service.requests.request", side_effect=side_effect):
        try:
            job_email_scan_service.scan_for_job_emails(db_session, user_id=test_user.id)
            assert False, "expected GmailApiError to propagate"
        except gmail_service.GmailApiError as error:
            assert not isinstance(error, gmail_service.GmailMessageNotFoundError)
