"""Tests for app/services/application_email_classifier.py — deterministic
rules first; Ollama assist only for genuinely ambiguous cases, and never
touched in these tests (allow_llm_assist=False / mocked where exercised)."""

from __future__ import annotations

from unittest.mock import patch

from app.services.application_email_classifier import (
    AMBIGUOUS_THRESHOLD,
    LLM_ASSIST_CONFIDENCE_CAP,
    classify_email,
    classify_email_rules_only,
)


def test_application_confirmation():
    result = classify_email_rules_only(
        subject="Thank you for applying to ABC Corp",
        sender="noreply@abccorp.com",
        body="Thank you for applying. We have received your application for the Java Developer role.",
    )
    assert result["job_related"] is True
    assert result["event_type"] == "APPLICATION_CONFIRMATION"
    assert result["confidence"] >= 0.9


def test_rejection():
    result = classify_email_rules_only(
        subject="Application Update",
        sender="hr@abccorp.com",
        body="We have decided not to move forward with your application at this time.",
    )
    assert result["event_type"] == "REJECTION"
    assert result["confidence"] >= 0.85


def test_interview_invite():
    result = classify_email_rules_only(
        subject="Interview Invitation",
        sender="recruiter@abccorp.com",
        body="We would like to schedule an interview with you next week.",
    )
    assert result["event_type"] == "INTERVIEW_INVITE"
    assert result["confidence"] >= 0.85


def test_assessment_request():
    result = classify_email_rules_only(
        subject="Next Steps: Technical Assessment",
        sender="hr@abccorp.com",
        body="Please complete this assessment within 3 days. Here is the coding challenge link.",
    )
    assert result["event_type"] == "ASSESSMENT_REQUEST"
    assert result["confidence"] >= 0.85


def test_offer():
    result = classify_email_rules_only(
        subject="Job Offer - Java Developer",
        sender="hr@abccorp.com",
        body="We are pleased to offer you the position of Java Developer at ABC Corp.",
    )
    assert result["event_type"] == "OFFER"
    assert result["confidence"] >= 0.9


def test_application_update():
    """Item 9 (stabilization pass): APPLICATION_UPDATE was a real classifier
    rule with no direct test coverage — closing that gap."""
    result = classify_email_rules_only(
        subject="Update on Your Application",
        sender="hr@abccorp.com",
        body="We wanted to give you an update on your application. It is still under review by our hiring team.",
    )
    assert result["job_related"] is True
    assert result["event_type"] == "APPLICATION_UPDATE"
    assert result["confidence"] >= 0.7


def test_recruiter_outreach():
    result = classify_email_rules_only(
        subject="Exciting opportunity for you",
        sender="jane@recruiting-agency.com",
        body="I came across your profile and think you would be a great fit. Are you open to new opportunities?",
    )
    assert result["event_type"] == "RECRUITER_OUTREACH"


def test_unrelated_email_not_job_related():
    result = classify_email_rules_only(
        subject="Your Amazon order has shipped",
        sender="no-reply@amazon.com",
        body="Your package will arrive tomorrow. Track your order here.",
    )
    assert result["job_related"] is False
    assert result["event_type"] is None


# ---------------------------------------------------------------------------
# CURRENT VERIFIED BUG regression: unrelated Gmail email filtering. A bare
# generic word (the old single-word _JOB_SIGNAL_PATTERN, which included
# "offer") is no longer sufficient — only phrase-level or combined signals
# count. Requirement A + unrelated-category coverage (B-E scenarios).
# ---------------------------------------------------------------------------

def test_shutterfly_promotional_email_is_not_job_related():
    """Requirement A — the exact reported bug."""
    result = classify_email_rules_only(
        subject="Here it is: Our holiday first look & 50% off ALL cards!",
        sender="Shutterfly <shutterfly@em.shutterfly.com>",
        body=(
            "Here it is: Our holiday first look & 50% off ALL cards! Shop our "
            "newest holiday cards now and save. This exclusive offer ends soon. "
            "Use code SAVE50 at checkout. Free shipping on orders over $49. "
            "Shop Now. Unsubscribe from these emails."
        ),
    )
    assert result["job_related"] is False
    assert result["event_type"] is None
    assert result["confidence"] == 0.0


def test_banking_email_is_not_job_related():
    result = classify_email_rules_only(
        subject="Your December statement is ready",
        sender="alerts@chase.com",
        body="Your account statement for December is now available. View your balance and recent transactions online.",
    )
    assert result["job_related"] is False


def test_credit_card_notification_is_not_job_related():
    result = classify_email_rules_only(
        subject="Payment due reminder",
        sender="billing@discover.com",
        body="Your payment of $120.00 is due on 12/15. Pay now to avoid late fees.",
    )
    assert result["job_related"] is False


def test_receipt_is_not_job_related():
    result = classify_email_rules_only(
        subject="Your order receipt",
        sender="auto-confirm@amazon.com",
        body="Thanks for your order. Your package will arrive Tuesday.",
    )
    assert result["job_related"] is False


def test_personal_email_is_not_job_related():
    result = classify_email_rules_only(
        subject="Dinner Friday?",
        sender="friend@gmail.com",
        body="Hey, are you free for dinner Friday night? Let me know!",
    )
    assert result["job_related"] is False


def test_newsletter_is_not_job_related():
    result = classify_email_rules_only(
        subject="This week in tech",
        sender="news@techdigest.example.com",
        body="Top stories this week: AI advances, new gadgets, and more. Unsubscribe anytime.",
    )
    assert result["job_related"] is False


def test_social_media_notification_is_not_job_related():
    result = classify_email_rules_only(
        subject="You have a new follower",
        sender="notify@instagram.com",
        body="someone_cool started following you on Instagram.",
    )
    assert result["job_related"] is False


def test_travel_promotion_is_not_job_related():
    result = classify_email_rules_only(
        subject="Flights from $99",
        sender="deals@expedia.com",
        body="Book your next getaway now. Limited time offer on flights.",
    )
    assert result["job_related"] is False


def test_food_order_notification_is_not_job_related():
    result = classify_email_rules_only(
        subject="Your order is on its way",
        sender="orders@doordash.com",
        body="Your delivery driver is en route. Estimated arrival: 15 minutes.",
    )
    assert result["job_related"] is False


def test_security_notification_is_not_job_related():
    result = classify_email_rules_only(
        subject="New sign-in to your account",
        sender="no-reply@google.com",
        body="We noticed a new sign-in to your Google Account. If this was you, no action is needed.",
    )
    assert result["job_related"] is False


def test_generic_bare_word_alone_never_triggers_job_related():
    """Requirement 4: bare "offer"/"position"/"application"/"schedule"/
    "review"/"company" in an obvious non-job context must not, alone,
    mark an email job-related."""
    for word in ("offer", "position", "application", "schedule", "review", "company"):
        result = classify_email_rules_only(
            subject=f"Great {word} just for you",
            sender="deals@retailer.example.com",
            body=f"Check out this amazing {word} today. Limited time only.",
        )
        assert result["job_related"] is False, f"bare '{word}' incorrectly triggered job_related"


def test_genuinely_job_related_other_event_is_retained():
    """Requirement 3: job_related=True + event_type=OTHER must still be
    possible for genuinely employment-related mail that doesn't fit one of
    the seven specific event types."""
    result = classify_email_rules_only(
        subject="Next steps",
        sender="hr@abccorp.com",
        body="Please complete your onboarding paperwork and background check before your start date.",
    )
    assert result["job_related"] is True
    assert result["event_type"] == "OTHER"
    assert result["confidence"] == 0.4


def test_job_related_but_ambiguous_returns_other():
    result = classify_email_rules_only(
        subject="Regarding your candidate profile",
        sender="someone@example.com",
        body="Just checking in about your resume and the position you applied to.",
    )
    assert result["job_related"] is True
    assert result["event_type"] == "OTHER"
    assert result["confidence"] < AMBIGUOUS_THRESHOLD


def test_classify_email_never_returns_matched_job_id_itself():
    """matched_job_id is always None from the classifier alone — matching
    is a separate step (application_tracking_service), per Part 6/9."""
    result = classify_email(
        subject="Thank you for applying", sender="noreply@abccorp.com",
        body="We have received your application.", allow_llm_assist=False,
    )
    assert result["matched_job_id"] is None


def test_llm_assist_only_triggered_for_ambiguous_results():
    with patch("app.services.application_email_classifier._llm_assist") as mocked_assist:
        classify_email(
            subject="Thank you for applying", sender="noreply@abccorp.com",
            body="We have received your application.",
        )
        mocked_assist.assert_not_called()  # already confident — no LLM call needed


def test_llm_assist_called_for_ambiguous_and_capped_confidence():
    ambiguous_body = "Just following up about your candidate profile and the position."
    with patch("app.services.llm_client.LLMClient") as MockClient, \
         patch("app.services.llm_client.bullet_rewrite_llm_config", return_value={}):
        instance = MockClient.return_value
        instance.enabled = True
        instance.generate_json.return_value = {"event_type": "RECRUITER_OUTREACH", "confidence": 0.99}
        result = classify_email(subject="Following up", sender="someone@example.com", body=ambiguous_body)
    assert result["event_type"] == "RECRUITER_OUTREACH"
    assert result["confidence"] <= LLM_ASSIST_CONFIDENCE_CAP  # never as confident as a clean rule match


def test_llm_assist_failure_falls_back_to_rules_result():
    ambiguous_body = "Just following up about your candidate profile and the position."
    with patch("app.services.llm_client.LLMClient") as MockClient, \
         patch("app.services.llm_client.bullet_rewrite_llm_config", return_value={}):
        instance = MockClient.return_value
        instance.enabled = True
        instance.generate_json.side_effect = RuntimeError("ollama down")
        result = classify_email(subject="Following up", sender="someone@example.com", body=ambiguous_body)
    assert result["event_type"] == "OTHER"  # rules-only fallback, never raises
