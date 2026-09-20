"""Tests for app/services/gmail_service.py's message-timestamp parsing
(_parse_internal_date, _parse_date_header, parse_message) — the fix for
JobEmailEvent.received_at always coming back null. No real Gmail call:
these operate on raw dicts shaped like Gmail's messages.get response.

Also covers body/snippet whitespace extraction (_extract_body, _html_to_text,
_normalize_extracted_text) — the fix for body text losing spaces at HTML
element boundaries (e.g. "Thank youfor applying", "SystemOne").
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone

from app.services.gmail_service import (
    _extract_body,
    _extract_display_body,
    _html_to_display_text,
    _html_to_text,
    _normalize_display_text,
    _normalize_extracted_text,
    _parse_date_header,
    _parse_internal_date,
    parse_message,
)


def _raw_message(*, internal_date=None, date_header=None, headers=None, message_id="msg1"):
    header_list = list(headers or [])
    if date_header is not None:
        header_list.append({"name": "Date", "value": date_header})
    header_list += [
        {"name": "From", "value": "hr@abccorp.com"},
        {"name": "Subject", "value": "Interview Invitation"},
    ]
    raw = {"id": message_id, "threadId": "thread1", "snippet": "snippet text", "payload": {"headers": header_list, "mimeType": "text/plain", "body": {}}}
    if internal_date is not None:
        raw["internalDate"] = internal_date
    return raw


# ---------------------------------------------------------------------------
# Requirement A — internalDate (milliseconds since epoch) -> UTC datetime
# ---------------------------------------------------------------------------

def test_internal_date_converted_correctly_to_utc_datetime():
    result = _parse_internal_date({"internalDate": "1728998400000"})
    assert result == datetime.fromtimestamp(1728998400, tz=timezone.utc)
    assert result.tzinfo is not None
    assert result.utcoffset().total_seconds() == 0


def test_parse_message_prefers_internal_date_over_date_header():
    """internalDate is Gmail's own authoritative record — preferred even
    when a Date header is also present and would parse to something
    different (e.g. a misconfigured sender clock)."""
    raw = _raw_message(internal_date="1728998400000", date_header="Tue, 15 Oct 2024 23:59:59 +0000")
    message = parse_message(raw)
    assert message.received_at == datetime.fromtimestamp(1728998400, tz=timezone.utc)


# ---------------------------------------------------------------------------
# Requirement D — malformed internalDate never crashes the scan
# ---------------------------------------------------------------------------

def test_malformed_internal_date_returns_none_without_raising():
    assert _parse_internal_date({"internalDate": "not-a-number"}) is None
    assert _parse_internal_date({"internalDate": ""}) is None
    assert _parse_internal_date({"internalDate": "-100"}) is None
    assert _parse_internal_date({"internalDate": None}) is None
    assert _parse_internal_date({}) is None


def test_parse_message_falls_back_to_date_header_when_internal_date_malformed():
    raw = _raw_message(internal_date="garbage", date_header="Tue, 15 Oct 2024 14:30:00 -0700")
    message = parse_message(raw)
    assert message.received_at is not None
    assert message.received_at.tzinfo is not None


# ---------------------------------------------------------------------------
# Requirement E — missing internalDate/Date header stays safely None
# ---------------------------------------------------------------------------

def test_missing_internal_date_and_date_header_leaves_received_at_none():
    raw = _raw_message(internal_date=None, date_header=None)
    message = parse_message(raw)
    assert message.received_at is None


def test_date_header_fallback_used_when_internal_date_missing():
    raw = _raw_message(internal_date=None, date_header="Tue, 15 Oct 2024 14:30:00 -0700")
    message = parse_message(raw)
    assert message.received_at is not None
    assert message.received_at == datetime(2024, 10, 15, 21, 30, 0, tzinfo=timezone.utc)


def test_malformed_date_header_alone_never_raises():
    assert _parse_date_header("not a real date") is None
    assert _parse_date_header("") is None
    assert _parse_date_header(None) is None


def test_naive_date_header_is_treated_as_utc_not_raising():
    result = _parse_date_header("Tue, 15 Oct 2024 14:30:00")  # no offset
    assert result is not None
    assert result.tzinfo is not None


# ---------------------------------------------------------------------------
# received_at is never the current time
# ---------------------------------------------------------------------------

def test_parse_message_never_uses_current_time_as_received_at():
    before = datetime.now(timezone.utc)
    raw = _raw_message(internal_date="1728998400000")  # a fixed date well in the past
    message = parse_message(raw)
    assert message.received_at is not None
    assert message.received_at < before


# ---------------------------------------------------------------------------
# Body/snippet whitespace extraction — CURRENT VERIFIED BUG:
# "Thank youfor applying", "SystemOne" (words glued together because tags/
# newlines were dropped instead of being treated as word separators).
# ---------------------------------------------------------------------------

def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def _plain_payload(text: str) -> dict:
    return {"mimeType": "text/plain", "body": {"data": _b64(text)}}


def _html_payload(html: str) -> dict:
    return {"mimeType": "text/html", "body": {"data": _b64(html)}}


# Requirement A — plain text newline between words preserves a space.
def test_plain_text_newline_between_words_preserves_space():
    assert _extract_body(_plain_payload("Thank you\nfor applying")) == "Thank you for applying"


# Requirement B — HTML paragraph boundaries preserve a space.
def test_html_paragraph_boundaries_preserve_space():
    assert _html_to_text("<p>Thank you</p><p>for applying</p>") == "Thank you for applying"
    assert "youfor" not in _html_to_text("<p>Thank you</p><p>for applying</p>")


# Requirement C — <br> preserves separation.
def test_html_br_preserves_separation():
    assert _html_to_text("Regards,<br>System One") == "Regards, System One"
    assert _html_to_text("Regards,<br/>System One") == "Regards, System One"
    assert "SystemOne" not in _html_to_text("Regards,<br>System One")


# Requirement D — multipart/alternative prefers text/plain when present.
def test_multipart_alternative_prefers_text_plain_when_present():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            _plain_payload("Plain version here"),
            _html_payload("<p>HTML version here</p>"),
        ],
    }
    assert _extract_body(payload) == "Plain version here"


# Requirement E — HTML fallback works when text/plain is absent.
def test_html_fallback_used_when_text_plain_absent():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [_html_payload("<p>Thank you</p><p>for applying</p>")],
    }
    assert _extract_body(payload) == "Thank you for applying"


# Item 6 (stabilization pass): nested multipart — a multipart/mixed message
# (e.g. an attachment alongside the body) whose real content lives inside a
# nested multipart/alternative sub-part must still be found and correctly
# extracted, with the same text/plain preference as a top-level alternative.
def test_nested_multipart_mixed_containing_alternative_extracts_correctly():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    _plain_payload("Thank you for applying for the Java Developer role."),
                    _html_payload("<p>Thank you for applying for the Java Developer role.</p>"),
                ],
            },
            {"mimeType": "application/pdf", "body": {"attachmentId": "att-1"}},
        ],
    }
    assert _extract_body(payload) == "Thank you for applying for the Java Developer role."


# Item 6 — the same punctuation-boundary comparison (Requirement J) also
# applies inside a nested multipart/alternative, not just a top-level one.
def test_nested_multipart_alternative_still_applies_boundary_comparison():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    _plain_payload("position at SES.Your background in Java, Apache Kafka, SQL, AWS."),
                    _html_payload("<p>position at SES.</p><p>Your background in Java, Apache Kafka, SQL, AWS.</p>"),
                ],
            },
        ],
    }
    result = _extract_body(payload)
    assert "SES.Your" not in result
    assert "SES. Your background" in result


# Requirement F — HTML entities decode correctly.
def test_html_entities_decode_correctly():
    assert _html_to_text("Salary: $100,000 &amp; benefits included") == "Salary: $100,000 & benefits included"
    assert _html_to_text("Regards,&nbsp;System One") == "Regards, System One"


# Requirement G — repeated whitespace collapses cleanly.
def test_repeated_whitespace_collapses_cleanly():
    assert _normalize_extracted_text("Thank   you\n\n\tfor applying") == "Thank you for applying"
    assert _normalize_extracted_text("  leading and trailing  \n") == "leading and trailing"


# Requirement H — body no longer contains "youfor"/"SystemOne" for the
# exact reported examples, end-to-end through parse_message().
def test_reported_bug_examples_no_longer_glue_words_together():
    raw = {
        "id": "bug1",
        "threadId": "thread-bug1",
        "internalDate": "1728998400000",
        "snippet": "",
        "payload": {
            "headers": [
                {"name": "From", "value": "hr@abccorp.com"},
                {"name": "Subject", "value": "Thank you for applying"},
            ],
            "mimeType": "text/html",
            "body": {
                "data": _b64(
                    "<p>Thank you</p><p>for applying for the Java Developer position at ABC Corp.</p>"
                    "<p>Regards,</p><p>System One</p>"
                )
            },
        },
    }
    message = parse_message(raw)
    assert "youfor" not in message.body
    assert "SystemOne" not in message.body
    assert "Thank you for applying for the Java Developer position at ABC Corp." in message.body
    assert "Regards, System One" in message.body


# ---------------------------------------------------------------------------
# CURRENT VERIFIED ISSUE (this task): some Gmail body snippets still showed
# glued words ("position atComputer Merchant", "SQL,AWS", etc.) despite the
# earlier fix above. Root cause traced to job_email_scan_service.py storing
# Gmail's own server-generated `snippet` field (which strips HTML tags
# WITHOUT inserting a separating space) in preference to the properly
# extracted/normalized `message.body` produced by THIS module — see the
# corresponding fix and test in test_job_email_scan_service.py. The tests
# below confirm gmail_service's own extraction (which IS the source of
# truth once the priority fix is applied) already handles every one of the
# reported boundary shapes correctly, and stays safe around protected text.
# ---------------------------------------------------------------------------

# Requirement A — inline element boundary with no literal space in the
# source markup ("...at</span><span>Computer...", the classic ATS-template
# adjacent-inline-tag shape) still separates the words.
def test_inline_tag_boundary_with_no_source_whitespace_preserves_space():
    html = "<p>Sr. Java Developer position at</p><span>Computer Merchant</span>"
    assert _html_to_text(html) == "Sr. Java Developer position at Computer Merchant"
    assert "atComputer" not in _html_to_text(html)


# Requirement B — a MIME/HTML boundary (here, a multipart/alternative HTML
# part reached via _extract_body) between "for" and "your" is preserved.
def test_html_boundary_between_for_and_your_preserves_space():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [_html_payload("<p>Please complete this assessment for</p><p>your application.</p>")],
    }
    assert _extract_body(payload) == "Please complete this assessment for your application."
    assert "foryour" not in _extract_body(payload)


# Requirement C — a block/paragraph boundary between "After" and "careful"
# (no case-transition signal at all — provably only fixable by treating the
# source boundary itself as a separator, never by guessing from letter case).
def test_html_boundary_between_after_and_careful_preserves_space():
    html = "<div>After</div><div>careful consideration, we have decided not to move forward.</div>"
    assert _html_to_text(html) == "After careful consideration, we have decided not to move forward."
    assert "Aftercareful" not in _html_to_text(html)


# Requirement D — a <br> boundary between "with" and "your" is preserved.
def test_br_boundary_between_with_and_your_preserves_space():
    html = "Thank you for your interest, but we have decided not to move forward<br>with your application."
    assert _html_to_text(html) == "Thank you for your interest, but we have decided not to move forward with your application."
    assert "withyour" not in _html_to_text(html)


# Requirement E — a boundary immediately after a trailing period safely gets
# a following space, without any punctuation-rewriting heuristic: the tag
# itself (not the period) is what supplies the separator.
def test_boundary_after_trailing_period_is_safe():
    assert _html_to_text("<p>SES.</p><p>Your background is a strong fit.</p>") == "SES. Your background is a strong fit."
    assert _html_to_text("SES.<br>Your background is a strong fit.") == "SES. Your background is a strong fit."
    assert "SES.Your" not in _html_to_text("SES.<br>Your background is a strong fit.")


# Requirement F — a boundary immediately after a trailing comma safely gets
# a following space, same mechanism (tag-as-separator), same safety.
def test_boundary_after_trailing_comma_is_safe():
    assert _html_to_text("<p>Required skills: SQL,</p><p>AWS, and Docker.</p>") == "Required skills: SQL, AWS, and Docker."
    assert _html_to_text("Required skills: SQL,<br>AWS, and Docker.") == "Required skills: SQL, AWS, and Docker."
    assert "SQL,AWS" not in _html_to_text("Required skills: SQL,<br>AWS, and Docker.")


# Requirement G — plain text that already has correct spacing is passed
# through unchanged (no over-normalization side effects).
def test_correctly_spaced_plain_text_is_unchanged():
    text = "Thank you for applying for the Java Developer position at Computer Merchant."
    assert _extract_body(_plain_payload(text)) == text
    assert _normalize_extracted_text(text) == text


# Requirement H — URLs, email addresses, decimals, version numbers, and
# specific protected compound terms must never be split or otherwise
# damaged by this fix (zero-width-character normalization touches only
# invisible separator codepoints; nothing here rewrites punctuation or case
# transitions, so none of these are ever at risk).
def test_protected_text_is_never_damaged():
    text = (
        "Visit https://example.com/jobs/123 or email hr@example.com. "
        "Requires Python 3.11.2, v2.0, and 3.14 experience. "
        "Skills: JavaDeveloper, SpringBoot, GitHub, LinkedIn, SDET, AWS, SQL."
    )
    normalized = _normalize_extracted_text(text)
    assert "https://example.com/jobs/123" in normalized
    assert "hr@example.com" in normalized
    assert "3.11.2" in normalized
    assert "v2.0" in normalized
    assert "3.14" in normalized
    assert "JavaDeveloper" in normalized
    assert "SpringBoot" in normalized
    assert "GitHub" in normalized
    assert "LinkedIn" in normalized
    assert "SDET" in normalized
    assert "AWS" in normalized
    assert "SQL" in normalized


# ---------------------------------------------------------------------------
# Zero-width/invisible-character normalization — a narrow, safe extra
# safety net for the "text-node joining" investigation category: some
# HTML-email template generators insert an invisible zero-width character
# between adjacent inline elements instead of a literal space.
# ---------------------------------------------------------------------------

def test_zero_width_space_between_words_is_treated_as_a_separator():
    assert _normalize_extracted_text("position at​Computer Merchant") == "position at Computer Merchant"
    assert _normalize_extracted_text("with⁠your application") == "with your application"


def test_byte_order_mark_is_dropped_not_treated_as_a_separator():
    # A BOM is pure encoding noise, never intentional content — it must be
    # removed outright, not converted into a spurious extra space.
    assert _normalize_extracted_text("﻿Thank you for applying") == "Thank you for applying"


# ---------------------------------------------------------------------------
# LIVE VERIFIED BUG (this task): "position at SES.Your background" still
# came out glued after the previous body/snippet fix, while other spacing
# in the SAME event ("SQL, AWS", "After careful consideration", "with your
# application") was already correct. Root cause: _extract_body always
# preferred a message's text/plain MIME alternative unconditionally, even
# when that specific part's own template/generator dropped a separating
# space at one punctuation boundary while the paired text/html alternative
# preserved it correctly via an explicit tag boundary. The fix compares the
# two extractions and only ever prefers the HTML one when it is PROVABLY
# the same content (identical once all whitespace is stripped from both)
# with strictly more separators preserved — never a punctuation-rewriting
# regex.
# ---------------------------------------------------------------------------

def _alternative_payload(*, plain: str, html: str) -> dict:
    return {
        "mimeType": "multipart/alternative",
        "parts": [_plain_payload(plain), _html_payload(html)],
    }


# Requirement A — reproduce the exact live failure and prove the fix.
def test_ses_your_boundary_reproduces_live_bug_and_is_corrected():
    plain = "position at SES.Your background in Java, Apache Kafka, SQL, AWS."
    html = "<p>position at SES.</p><p>Your background in Java, Apache Kafka, SQL, AWS.</p>"
    result = _extract_body(_alternative_payload(plain=plain, html=html))
    assert "SES.Your" not in result
    assert "position at SES. Your background in Java, Apache Kafka, SQL, AWS." == result


# Requirement B — a plain-text part that is ALREADY correctly spaced must
# stay exactly as-is (no unnecessary switch to the HTML alternative).
def test_already_correct_plain_text_boundary_is_unchanged():
    plain = "position at SES. Your background in Java, Apache Kafka, SQL, AWS."
    html = "<p>position at SES.</p><p>Your background in Java, Apache Kafka, SQL, AWS.</p>"
    result = _extract_body(_alternative_payload(plain=plain, html=html))
    assert result == plain


# Requirement C — the same mechanism fixes a comma boundary when caused by
# an actual extraction boundary (the plain-text alternative glued it, the
# HTML alternative's tag boundary did not).
def test_sql_aws_comma_boundary_is_corrected_via_multipart_comparison():
    plain = "Required skills: SQL,AWS, and Docker."
    html = "<p>Required skills: SQL,</p><p>AWS, and Docker.</p>"
    result = _extract_body(_alternative_payload(plain=plain, html=html))
    assert "SQL,AWS" not in result
    assert "SQL, AWS" in result


# Requirements D/E/F/G/H/I — protected forms must never be damaged, whether
# they appear in a plain-only body or survive the multipart comparison
# unchanged (no matching HTML boundary evidence, so no substitution occurs).
def test_decimals_versions_urls_emails_and_protected_terms_survive_multipart_comparison():
    plain = (
        "Requires 3.14 accuracy, v2.0 and 3.11.2 compatibility. "
        "See https://example.com/test or example.com. Contact user@example.com. "
        "Built with ASP.NET and Node.js. Skills: JavaDeveloper, SpringBoot, GitHub, LinkedIn, U.S. based."
    )
    html = f"<p>{plain}</p>"  # identical content, single paragraph -> no boundary evidence either way
    result = _extract_body(_alternative_payload(plain=plain, html=html))
    assert result == plain
    for protected in (
        "3.14", "v2.0", "3.11.2", "https://example.com/test", "example.com",
        "user@example.com", "ASP.NET", "Node.js", "JavaDeveloper", "SpringBoot",
        "GitHub", "LinkedIn", "U.S.",
    ):
        assert protected in result


# Requirement J — multipart/alternative where plain and HTML differ at a
# punctuation boundary: HTML preserves it, plain-text drops it, HTML wins
# only because it's provably the same content with strictly more spacing.
def test_multipart_alternative_html_wins_only_when_provably_same_content_with_more_spacing():
    plain_glued = "Thank you for applying. SES.Your background looks like a great fit."
    html_correct = "<p>Thank you for applying.</p><p>SES.</p><p>Your background looks like a great fit.</p>"
    result = _extract_body(_alternative_payload(plain=plain_glued, html=html_correct))
    assert result == "Thank you for applying. SES. Your background looks like a great fit."

    # But when the two alternatives are NOT provably the same content (e.g.
    # the HTML carries extra boilerplate the plain-text lacks), the existing
    # text/plain preference is kept — never guessing which is "more correct".
    plain_only = "Thank you for applying. SES.Your background looks like a great fit."
    html_with_extra_content = (
        "<p>Thank you for applying.</p><p>SES.</p><p>Your background looks like a great fit.</p>"
        "<p>Unsubscribe from these emails at any time.</p>"
    )
    fallback_result = _extract_body(_alternative_payload(plain=plain_only, html=html_with_extra_content))
    assert fallback_result == plain_only


# Requirement K, at the extraction level — parse_message() end-to-end
# through _extract_body picks up the corrected, better-spaced body.
def test_parse_message_end_to_end_picks_up_corrected_body():
    raw = {
        "id": "bug2",
        "threadId": "thread-bug2",
        "internalDate": "1728998400000",
        "snippet": "position at SES.Your background in Java, Apache Kafka, SQL, AWS.",
        "payload": {
            "headers": [
                {"name": "From", "value": "recruiter@ses.com"},
                {"name": "Subject", "value": "Recruiter Spacing Test - Sr. Java Developer at SES"},
            ],
            "mimeType": "multipart/alternative",
            "parts": [
                _plain_payload("position at SES.Your background in Java, Apache Kafka, SQL, AWS."),
                _html_payload("<p>position at SES.</p><p>Your background in Java, Apache Kafka, SQL, AWS.</p>"),
            ],
        },
    }
    message = parse_message(raw)
    assert "SES.Your" not in message.body
    assert "position at SES. Your background in Java, Apache Kafka, SQL, AWS." == message.body


# ---------------------------------------------------------------------------
# Paragraph-preserving display extraction (_extract_display_body /
# _html_to_display_text / _normalize_display_text) — Email Summary's View
# Details full email body. Display-only: never used for classification or
# matching, which keep using _extract_body/message.body exactly as before
# (every test above this section is untouched and must keep passing as-is).
# ---------------------------------------------------------------------------

def test_plain_text_line_breaks_are_preserved_for_display():
    text = "Hi,\n\nThank you for applying for the Java Developer position at DemoTech.\n\nBest regards,\nDemoTech Recruiting Team"
    assert _extract_display_body(_plain_payload(text)) == text


def test_plain_text_display_still_collapses_horizontal_whitespace_and_trims_lines():
    text = "Hi,   \n\n  Thank   you for applying.  \n\nBest regards,\nDemoTech"
    result = _extract_display_body(_plain_payload(text))
    assert result == "Hi,\n\nThank you for applying.\n\nBest regards,\nDemoTech"


def test_plain_text_excessive_blank_lines_collapse_to_one_paragraph_gap():
    text = "Hi,\n\n\n\n\nThank you for applying."
    assert _extract_display_body(_plain_payload(text)) == "Hi,\n\nThank you for applying."


def test_html_paragraph_boundaries_become_paragraph_breaks_for_display():
    html = "<p>Hi,</p><p>Thank you for applying for the Java Developer position at DemoTech.</p><p>Best regards,<br>DemoTech Recruiting Team</p>"
    result = _html_to_display_text(html)
    assert result == "Hi,\n\nThank you for applying for the Java Developer position at DemoTech.\n\nBest regards,\nDemoTech Recruiting Team"


def test_html_br_becomes_a_single_line_break_for_display_not_a_paragraph_gap():
    result = _html_to_display_text("Regards,<br>System One")
    assert result == "Regards,\nSystem One"


def test_display_extraction_prefers_text_plain_exactly_like_extract_body():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            _plain_payload("Hi,\n\nPlain version here.\n\nBest regards,\nTeam"),
            _html_payload("<p>Hi,</p><p>HTML version here.</p><p>Best regards,<br>Team</p>"),
        ],
    }
    assert _extract_display_body(payload) == "Hi,\n\nPlain version here.\n\nBest regards,\nTeam"


def test_display_extraction_falls_back_to_html_when_text_plain_absent():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [_html_payload("<p>Hi,</p><p>Thank you for applying.</p>")],
    }
    assert _extract_display_body(payload) == "Hi,\n\nThank you for applying."


def test_display_extraction_never_glues_words_at_boundaries_either():
    html = "<p>Thank you</p><p>for applying</p>"
    result = _html_to_display_text(html)
    assert "youfor" not in result
    assert result == "Thank you\n\nfor applying"


def test_parse_message_populates_body_display_without_changing_body():
    raw = {
        "id": "fmt1",
        "threadId": "thread-fmt1",
        "snippet": "snippet",
        "payload": {
            "headers": [
                {"name": "From", "value": "hr@demotech.example.com"},
                {"name": "Subject", "value": "Application Received"},
            ],
            "mimeType": "multipart/alternative",
            "parts": [
                _plain_payload("Hi,\n\nThank you for applying.\n\nBest regards,\nDemoTech"),
                _html_payload("<p>Hi,</p><p>Thank you for applying.</p><p>Best regards,<br>DemoTech</p>"),
            ],
        },
    }
    message = parse_message(raw)
    assert message.body == "Hi, Thank you for applying. Best regards, DemoTech"  # flattened, classification-facing text unchanged
    assert message.body_display == "Hi,\n\nThank you for applying.\n\nBest regards,\nDemoTech"


def test_normalize_display_text_strips_zero_width_and_bom_like_the_flattened_version():
    text = "Hi​,\n\nThank you﻿ for applying."
    assert _normalize_display_text(text) == "Hi ,\n\nThank you for applying."
