"""Mocked Dice contract tests: no live provider calls or developer credentials."""
import logging
import socket
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.job_sources.apify_dice import ApifyDiceAdapter
from app.services.job_enrichment_service import enrich_job
from app.services.job_normalization_service import resolve_apply_action
RAW={"id":"dice-1","title":"Platform Engineer","company":"Acme","city":"Austin","state":"TX","description":"Build platform automation with Kubernetes and Terraform.","url":"https://www.dice.com/job-detail/1","applyUrl":"https://other.example/job/1","employmentType":"FULL_TIME","remote":False}
TEST_TOKEN = "apify_api_TEST_ONLY_no_live_access"
SPEC = {"job_title": "Platform Engineer", "location": "Austin, TX"}


@pytest.fixture(autouse=True)
def isolated_dice_environment(monkeypatch):
    monkeypatch.setenv("APIFY_DICE_ENABLED", "true")
    monkeypatch.setenv("APIFY_API_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("APIFY_DICE_ACTOR_ID", "abotapi/dice-com-scraper")

    def no_network(*args, **kwargs):
        pytest.fail("External requests/DNS must be mocked in Dice tests")

    monkeypatch.setattr(requests.sessions.Session, "request", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)


def _response(payload, status=201):
    response = MagicMock(status_code=status)
    response.json.return_value = payload
    return response


@pytest.fixture
def post(monkeypatch):
    mock = MagicMock(return_value=_response([RAW]))
    monkeypatch.setattr(requests.Session, "post", mock)
    return mock

def test_disabled_dice_never_requests(monkeypatch):
    monkeypatch.setenv("APIFY_DICE_ENABLED","false")
    with patch("app.job_sources.apify_dice.requests.Session.post") as post: assert ApifyDiceAdapter([{"job_title":"Platform Engineer"}]).fetch_jobs()==[]; post.assert_not_called()
def test_dice_listing_stays_view_listing():
    job=ApifyDiceAdapter().normalize_job(RAW)
    assert job["source"]=="apify_dice" and job["application_url"] is None and resolve_apply_action(job)["action_label"]=="View Listing"
def test_apply_url_is_preserved_only_as_unverified_candidate():
    raw={**RAW,"applyType":"APPLY_TO_URL","applyUrl":"https://careers.acme.example/jobs/12345"}
    job=ApifyDiceAdapter().normalize_job(raw)
    assert job["unverified_application_url"]==raw["applyUrl"] and job["application_url"] is None and job["direct_url"] is None

def test_mismatched_ats_apply_url_is_rejected_and_listing_is_retained():
    raw = {**RAW, "company": "Company A", "applyType": "APPLY_TO_URL", "applyUrl": "https://company-b.wd1.myworkdayjobs.com/en-US/careers/job/12345"}
    job = ApifyDiceAdapter().normalize_job(raw)
    result = enrich_job(job, provider=lambda query: [])
    assert result.status == "REJECTED"
    assert resolve_apply_action(job)["action_label"] == "View Listing"

def test_matching_apply_url_can_pass_the_shared_verification_pipeline():
    raw = {**RAW, "company": "EPAM Systems", "title": "Lead DevOps Engineer", "applyType": "APPLY_TO_URL", "applyUrl": "https://careers.epam.com/job/12345/lead-devops-engineer"}
    job = ApifyDiceAdapter().normalize_job(raw)
    with patch("app.services.job_enrichment_service.availability.check_url_availability", return_value={"status": "ACTIVE", "reason": "http_200", "final_url": None}):
        result = enrich_job(job, provider=lambda query: [])
    assert result.status == "ENRICHED" and result.url == raw["applyUrl"] and result.provider_name == "provider_apply_url"

def test_apply_to_email_never_creates_an_unverified_url_candidate():
    job = ApifyDiceAdapter().normalize_job({**RAW, "applyType": "APPLY_TO_EMAIL", "applyUrl": "mailto:jobs@example.com"})
    assert job["unverified_application_url"] is None and resolve_apply_action(job)["action_label"] == "View Listing"
@pytest.mark.parametrize("title", ["Java Full Stack Developer", "DevOps Engineer", "SDET", "Python Developer"])
def test_enabled_dice_uses_preference_title_and_bounded_input(post, title):
    assert ApifyDiceAdapter([{"job_title": f"  {title}  ", "location": "Texas"}]).fetch_jobs() == [RAW]
    assert post.call_args.kwargs["json"] == {
        "mode": "search", "queries": [title], "location": "Texas",
        "maxPages": 1, "maxListings": 20, "fetchDetails": True,
    }


@pytest.mark.parametrize("configured,expected", [
    ("abotapi/dice-com-scraper", "abotapi~dice-com-scraper"),
    ("abotapi~dice-com-scraper", "abotapi~dice-com-scraper"),
    ("  abotapi/dice-com-scraper  ", "abotapi~dice-com-scraper"),
    ("AbCdEf01234567890", "AbCdEf01234567890"),
])
def test_actor_id_and_endpoint(monkeypatch, post, configured, expected):
    monkeypatch.setenv("APIFY_DICE_ACTOR_ID", configured)
    assert ApifyDiceAdapter([SPEC]).fetch_jobs() == [RAW]
    assert post.call_args.args[0] == f"https://api.apify.com/v2/actors/{expected}/run-sync-get-dataset-items"


@pytest.mark.parametrize("invalid", [
    "owner/actor/extra", "../actor", "https://apify.com/owner/actor",
    "owner%2Factor", "owner/actor?token=secret", "owner/actor\nsecret",
])
def test_invalid_actor_id_does_not_start_run(monkeypatch, post, invalid):
    monkeypatch.setenv("APIFY_DICE_ACTOR_ID", invalid)
    adapter = ApifyDiceAdapter([SPEC])
    assert adapter.fetch_jobs() == []
    assert "reason=invalid_actor_id" in adapter.health_check().error
    post.assert_not_called()


@pytest.mark.parametrize("missing", ["APIFY_API_TOKEN", "APIFY_DICE_ACTOR_ID"])
def test_missing_configuration_does_not_request(monkeypatch, post, missing):
    monkeypatch.delenv(missing)
    adapter = ApifyDiceAdapter([SPEC])
    assert adapter.fetch_jobs() == []
    assert not adapter.health_check().healthy
    assert adapter.health_check().error == "APIFY_DICE_NOT_CONFIGURED"
    post.assert_not_called()


@pytest.mark.parametrize("specs", [[], [{}], [{"job_title": "   "}], [{"job_title": None}]])
def test_no_title_never_uses_actor_default_role(post, specs):
    assert ApifyDiceAdapter(specs).fetch_jobs() == []
    post.assert_not_called()


def test_distinct_role_searches_keep_their_own_inputs(post):
    specs = [{"job_title": "Java Backend Developer", "location": "Chicago, IL"},
             {"job_title": "SDET", "location": "Dallas, TX"}]
    ApifyDiceAdapter(specs).fetch_jobs()
    first, second = [call.kwargs["json"] for call in post.call_args_list]
    assert first["queries"] == ["Java Backend Developer"]
    assert second["queries"] == ["SDET"]
    assert first["location"] == "Chicago, IL" and second["location"] == "Dallas, TX"
    assert specs[0]["job_title"] == "Java Backend Developer"


def test_documented_auth_and_bounded_sync_request(post):
    from app.job_sources.apify_dice import RUN_TIMEOUT_SECONDS, TIMEOUT

    ApifyDiceAdapter([{"job_title": "Cloud Engineer"}]).fetch_jobs()
    kwargs = post.call_args.kwargs
    assert kwargs["headers"] == {"Accept": "application/json", "Authorization": f"Bearer {TEST_TOKEN}"}
    assert TEST_TOKEN not in post.call_args.args[0]
    assert "token" not in kwargs["params"]
    assert "location" not in kwargs["json"]
    assert "keywords" not in kwargs["json"] and "fetchDetailPages" not in kwargs["json"]
    assert kwargs["allow_redirects"] is False
    assert kwargs["params"]["format"] == "json"
    assert kwargs["params"]["restartOnError"] == "false"
    assert kwargs["timeout"] == TIMEOUT
    assert 0 < kwargs["params"]["timeout"] == RUN_TIMEOUT_SECONDS < TIMEOUT[1]
    assert RUN_TIMEOUT_SECONDS < 300
    post.assert_called_once()
    post.return_value.close.assert_called_once()


@pytest.mark.parametrize("limit,expected", [(1, 1), (7, 7), (20, 20), (100, 20)])
def test_result_and_page_bounds_even_for_oversized_response(post, limit, expected):
    post.return_value = _response([RAW] * 30)
    assert len(ApifyDiceAdapter([SPEC, SPEC]).fetch_jobs(limit)) == expected
    post.assert_called_once()
    assert post.call_args.kwargs["json"]["maxPages"] == 1
    assert post.call_args.kwargs["json"]["maxListings"] == expected
    assert post.call_args.kwargs["json"]["fetchDetails"] is True
    params = post.call_args.kwargs["params"]
    assert "maxItems" not in params
    assert "maxTotalChargeUsd" not in params
    assert params == {
        "format": "json", "limit": expected,
        "timeout": 120, "restartOnError": "false",
    }


@pytest.mark.parametrize("limit", [0, -1])
def test_non_positive_limit_never_starts_actor(post, limit):
    assert ApifyDiceAdapter([SPEC]).fetch_jobs(limit) == []
    post.assert_not_called()


def test_total_twenty_listing_budget_across_specs(post):
    post.side_effect = [_response([RAW] * 12), _response([RAW] * 8)]
    assert len(ApifyDiceAdapter([SPEC, SPEC, SPEC]).fetch_jobs(100)) == 20
    assert post.call_count == 2
    assert [call.kwargs["json"]["maxListings"] for call in post.call_args_list] == [20, 8]
    assert [call.kwargs["params"]["limit"] for call in post.call_args_list] == [20, 8]
    assert all("maxItems" not in call.kwargs["params"] for call in post.call_args_list)


@pytest.mark.parametrize("status", [200, 201])
def test_successful_dataset_enters_existing_normalization(post, status):
    post.return_value = _response([{**RAW, "applyType": "APPLY_TO_URL"}], status)
    adapter = ApifyDiceAdapter([SPEC])
    jobs = adapter.fetch_normalized_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job["source"] == "apify_dice" and job["title"] == RAW["title"]
    assert job["company"] == RAW["company"] and job["source_url"] == RAW["url"]
    assert job["unverified_application_url"] == RAW["applyUrl"]
    assert job["application_url"] is None and job["direct_url"] is None
    assert resolve_apply_action(job)["action_label"] == "View Listing"
    assert adapter.health_check().healthy and adapter.health_check().error is None


@pytest.mark.parametrize("url", ["mailto:jobs@example.com", "https://careers.acme.example/jobs/12345", None])
def test_email_dataset_never_creates_direct_candidate(post, url):
    post.return_value = _response([{**RAW, "applyType": "APPLY_TO_EMAIL", "applyUrl": url}])
    job = ApifyDiceAdapter([SPEC]).fetch_normalized_jobs()[0]
    assert job["unverified_application_url"] is None
    assert job["application_url"] is None and job["direct_url"] is None
    assert resolve_apply_action(job)["action_label"] == "View Listing"


def test_empty_dataset_is_not_failure(post, caplog):
    post.return_value = _response([])
    adapter = ApifyDiceAdapter([SPEC])
    with caplog.at_level(logging.INFO, logger="app.job_sources.apify_dice"):
        assert adapter.fetch_jobs() == []
    assert adapter.health_check().healthy and adapter.health_check().error is None
    assert "outcome=empty_dataset" in caplog.text


@pytest.mark.parametrize("payload", [None, {}, {"data": [RAW]}, "not a dataset", [RAW, None], [42]])
def test_malformed_dataset_is_not_success(post, payload):
    post.return_value = _response(payload)
    adapter = ApifyDiceAdapter([SPEC])
    assert adapter.fetch_jobs() == []
    assert not adapter.health_check().healthy
    assert "reason=malformed_dataset" in adapter.health_check().error


def test_invalid_json_is_malformed_dataset(post):
    post.return_value.json.side_effect = ValueError("Not JSON")
    adapter = ApifyDiceAdapter([SPEC])
    assert adapter.fetch_jobs() == []
    assert "reason=malformed_dataset" in adapter.health_check().error


@pytest.mark.parametrize("status,code,reason", [
    (400, "invalid-input", "invalid_request"),
    (401, "invalid-token", "authentication_failed"),
    (403, "insufficient-permissions", "insufficient_permission"),
    (403, "missing-actor-rights", "insufficient_permission"),
    (404, "actor-not-found", "actor_or_resource_not_found"),
    (429, "rate-limit-exceeded", "rate_limited"),
    (408, "run-timeout-exceeded", "actor_timeout"),
    (500, "internal-server-error", "server_error"),
    (502, "internal-server-error", "server_error"),
    (503, "internal-server-error", "server_error"),
    (400, "actor-run-failed", "actor_execution_failed"),
    (500, "run-failed", "actor_execution_failed"),
    (200, "actor-run-failed", "actor_execution_failed"),
])
def test_http_failure_diagnostics_and_no_retry(post, caplog, status, code, reason):
    post.return_value = _response({"error": {"type": code, "message": f"Authorization: Bearer {TEST_TOKEN}"}}, status)
    adapter = ApifyDiceAdapter([SPEC, SPEC])
    assert adapter.fetch_jobs() == []
    health = adapter.health_check()
    assert not health.healthy
    assert f"http_status={status}" in health.error
    assert f"provider_code={code}" in health.error
    assert f"reason={reason}" in health.error
    assert "provider=apify_dice" in caplog.text and health.error in caplog.text
    assert TEST_TOKEN not in caplog.text + health.error
    assert "Authorization" not in caplog.text + health.error
    post.assert_called_once()
    post.return_value.close.assert_called_once()


def test_non_json_error_retains_http_status(post):
    post.return_value = _response(None, 403)
    post.return_value.json.side_effect = ValueError("<html>Forbidden</html>")
    adapter = ApifyDiceAdapter([SPEC])
    assert adapter.fetch_jobs() == []
    assert "http_status=403" in adapter.health_check().error
    assert "reason=insufficient_permission" in adapter.health_check().error


def test_redirect_response_is_not_followed(post):
    post.return_value = _response(None, 302)
    adapter = ApifyDiceAdapter([SPEC])
    assert adapter.fetch_jobs() == []
    assert "http_status=302" in adapter.health_check().error
    assert post.call_args.kwargs["allow_redirects"] is False
    post.assert_called_once()


@pytest.mark.parametrize("error,reason", [
    (requests.ConnectTimeout, "request_timeout"),
    (requests.ReadTimeout, "request_timeout"),
    (requests.ConnectionError, "network_error"),
    (requests.exceptions.SSLError, "network_error"),
    (RuntimeError, "client_error"),
])
def test_transport_failures_do_not_echo_exception_or_retry(post, caplog, error, reason):
    post.side_effect = error(f"https://api.apify.com/?token={TEST_TOKEN} Authorization: Bearer other-secret")
    adapter = ApifyDiceAdapter([SPEC, SPEC])
    assert adapter.fetch_jobs() == []
    assert f"reason={reason}" in adapter.health_check().error
    assert not adapter.health_check().healthy
    assert TEST_TOKEN not in caplog.text and "other-secret" not in caplog.text
    assert "https://" not in caplog.text
    post.assert_called_once()


@pytest.mark.parametrize("code", [TEST_TOKEN, f"invalid-input-{TEST_TOKEN}", "unknown\nAuthorization: hidden", {"secret": TEST_TOKEN}])
def test_untrusted_error_fields_cannot_leak_secrets(post, caplog, code):
    post.return_value = _response({"error": {"type": code, "message": f"https://api.apify.com/?token={TEST_TOKEN}"}}, 403)
    adapter = ApifyDiceAdapter([SPEC])
    adapter.fetch_jobs()
    assert "provider_code=unreported" in adapter.health_check().error
    assert TEST_TOKEN not in caplog.text + adapter.health_check().error
    assert "Authorization" not in caplog.text and "https://" not in caplog.text


def test_success_never_logs_dataset_or_credentials(post, caplog):
    post.return_value = _response([{**RAW, "description": TEST_TOKEN}])
    with caplog.at_level(logging.INFO, logger="app.job_sources.apify_dice"):
        ApifyDiceAdapter([SPEC]).fetch_jobs()
    assert "outcome=success" in caplog.text
    assert TEST_TOKEN not in caplog.text


def test_failure_retains_earlier_query_results(post):
    post.side_effect = [_response([RAW]), _response({"error": {"type": "insufficient-permissions"}}, 403)]
    adapter = ApifyDiceAdapter([SPEC, SPEC, SPEC])
    assert adapter.fetch_jobs() == [RAW]
    assert not adapter.health_check().healthy
    assert "http_status=403" in adapter.health_check().error
    assert post.call_count == 2


def test_later_success_clears_previous_error(post):
    post.side_effect = [_response(None, 429), _response([RAW])]
    adapter = ApifyDiceAdapter([SPEC])
    assert adapter.fetch_jobs() == []
    assert "reason=rate_limited" in adapter.health_check().error
    assert adapter.fetch_jobs() == [RAW]
    assert adapter.health_check().healthy and adapter.health_check().error is None


def test_health_check_has_no_extra_requests(post):
    adapter = ApifyDiceAdapter([SPEC])
    adapter.fetch_jobs()
    adapter.health_check()
    adapter.health_check()
    post.assert_called_once()
