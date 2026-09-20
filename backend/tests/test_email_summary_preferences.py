"""Phase-2 tests: Email Summary settings persist only as inert opt-ins."""

from unittest.mock import patch

from app.models.email_log_model import EmailLog
from app.models.email_summary_preference_model import EmailSummaryPreference


def test_new_user_gets_safe_defaults_without_a_row(client, test_user, db_session):
    response = client.get(f"/api/email/preferences/{test_user.id}")
    assert response.status_code == 200
    assert response.json() == {
        "daily_summary_delivery_enabled": False,
        "automatic_gmail_scan_enabled": False,
        "daily_summary_recipient": None,
        "effective_daily_summary_recipient": test_user.email,
        "gmail_scan_frequency": "daily",
        "email_types_to_scan": ["job_opportunities", "recruiter_messages", "application_updates", "interview_invitations"],
    }
    assert db_session.query(EmailSummaryPreference).count() == 0


def test_settings_save_independently_of_existing_job_preferences(client, test_user, test_preferences, db_session):
    original_time = str(test_preferences.schedule_time)
    original_timezone = test_preferences.timezone
    response = client.patch(f"/api/email/preferences/{test_user.id}", json={
        "daily_summary_delivery_enabled": True,
        "automatic_gmail_scan_enabled": True,
        "daily_summary_recipient": "digest@example.com",
    })
    assert response.status_code == 200
    assert response.json()["daily_summary_delivery_enabled"] is True
    assert response.json()["automatic_gmail_scan_enabled"] is True
    assert response.json()["daily_summary_recipient"] == "digest@example.com"
    db_session.refresh(test_preferences)
    assert str(test_preferences.schedule_time) == original_time
    assert test_preferences.timezone == original_timezone


def test_settings_can_be_disabled_and_recipient_can_be_cleared(client, test_user):
    client.patch(f"/api/email/preferences/{test_user.id}", json={
        "daily_summary_delivery_enabled": True,
        "automatic_gmail_scan_enabled": True,
        "daily_summary_recipient": "digest@example.com",
    })
    response = client.patch(f"/api/email/preferences/{test_user.id}", json={
        "daily_summary_delivery_enabled": False,
        "automatic_gmail_scan_enabled": False,
        "daily_summary_recipient": None,
    })
    assert response.status_code == 200
    assert response.json()["daily_summary_delivery_enabled"] is False
    assert response.json()["automatic_gmail_scan_enabled"] is False
    assert response.json()["daily_summary_recipient"] is None
    assert response.json()["effective_daily_summary_recipient"] == test_user.email


def test_invalid_recipient_and_non_boolean_values_are_rejected(client, test_user):
    invalid_email = client.patch(f"/api/email/preferences/{test_user.id}", json={"daily_summary_recipient": "not-an-email"})
    invalid_flag = client.patch(f"/api/email/preferences/{test_user.id}", json={"daily_summary_delivery_enabled": "true"})
    assert invalid_email.status_code == 422
    assert invalid_flag.status_code == 422


def test_settings_update_never_delivers_or_scans(client, test_user, db_session):
    with patch("app.services.daily_summary_delivery_service.deliver_generated_summary") as deliver, patch(
        "app.services.job_email_scan_service.scan_for_job_emails"
    ) as scan:
        response = client.patch(f"/api/email/preferences/{test_user.id}", json={
            "daily_summary_delivery_enabled": True,
            "automatic_gmail_scan_enabled": True,
        })
    assert response.status_code == 200
    deliver.assert_not_called()
    scan.assert_not_called()
    assert db_session.query(EmailLog).filter(EmailLog.user_id == test_user.id).count() == 0


def test_email_summary_preference_users_are_isolated(client, test_user, second_user):
    client.patch(f"/api/email/preferences/{test_user.id}", json={"daily_summary_delivery_enabled": True})
    mine = client.get(f"/api/email/preferences/{test_user.id}").json()
    theirs = client.get(f"/api/email/preferences/{second_user.id}").json()
    assert mine["daily_summary_delivery_enabled"] is True
    assert theirs["daily_summary_delivery_enabled"] is False


# ---------------------------------------------------------------------------
# Email Summary redesign: scan frequency + "Email Types to Scan" categories
# ---------------------------------------------------------------------------

def test_scan_frequency_and_email_types_round_trip(client, test_user, db_session):
    response = client.patch(f"/api/email/preferences/{test_user.id}", json={
        "gmail_scan_frequency": "every_6_hours",
        "email_types_to_scan": ["recruiter_messages", "interview_invitations"],
    })
    assert response.status_code == 200
    assert response.json()["gmail_scan_frequency"] == "every_6_hours"
    assert response.json()["email_types_to_scan"] == ["recruiter_messages", "interview_invitations"]

    refetched = client.get(f"/api/email/preferences/{test_user.id}").json()
    assert refetched["gmail_scan_frequency"] == "every_6_hours"
    assert refetched["email_types_to_scan"] == ["recruiter_messages", "interview_invitations"]

    preference = db_session.query(EmailSummaryPreference).filter(EmailSummaryPreference.user_id == test_user.id).first()
    assert preference.gmail_scan_frequency == "every_6_hours"


def test_invalid_scan_frequency_and_email_type_are_rejected(client, test_user):
    invalid_frequency = client.patch(f"/api/email/preferences/{test_user.id}", json={"gmail_scan_frequency": "hourly"})
    invalid_type = client.patch(f"/api/email/preferences/{test_user.id}", json={"email_types_to_scan": ["not_a_real_category"]})
    assert invalid_frequency.status_code == 422
    assert invalid_type.status_code == 422


def test_email_types_default_to_all_four_until_saved(client, test_user, db_session):
    # Saving an unrelated field must not implicitly narrow the categories —
    # NULL/unset always reads back as "all four" (see
    # EmailSummaryPreference.email_types_to_scan's own docstring).
    client.patch(f"/api/email/preferences/{test_user.id}", json={"automatic_gmail_scan_enabled": True})
    payload = client.get(f"/api/email/preferences/{test_user.id}").json()
    assert payload["email_types_to_scan"] == [
        "job_opportunities", "recruiter_messages", "application_updates", "interview_invitations",
    ]
    assert payload["gmail_scan_frequency"] == "daily"
