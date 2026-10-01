"""Official collectors: every HTTP/DNS/browser interaction is mocked."""
import json
import socket
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from app.job_sources.career_sources import adapter, greenhouse, lever, ashby, smartrecruiters, workday, generic_html
from app.job_sources.career_sources.base import CareerSource, Collection
from app.job_sources.career_sources.detector import detect, embedded_board, same_board
from app.job_sources.career_sources.http import CollectionError, Document, PublicClient

DESCRIPTION = "Build reliable services with Python, SQL and distributed systems."
SOURCE = CareerSource("Acme Widgets", "https://careers.acmewidgets.com/jobs")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Unmocked HTTP/DNS is forbidden")
    monkeypatch.setattr(requests.Session, "request", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setenv("COMPANY_CAREER_SOURCES", "[]")
    monkeypatch.setenv("COMPANY_CAREER_PLAYWRIGHT_ENABLED", "false")


def fake_client(*payloads):
    client = MagicMock()
    client.fetch.side_effect = [Document("https://careers.acmewidgets.com/jobs", 200, json.dumps(value), "application/json") for value in payloads]
    return client


def posting(**overrides):
    return {"@type": "JobPosting", "title": "Senior Python Engineer", "description": DESCRIPTION,
            "identifier": {"value": "12345"}, "url": "/jobs/12345/senior-python-engineer",
            "hiringOrganization": {"name": "Acme Widgets"}, **overrides}


def html_for(value):
    return '<script type="application/ld+json">' + json.dumps(value) + '</script>'


@pytest.mark.parametrize("url,provider,board", [
    ("https://boards.greenhouse.io/acme", "greenhouse", "acme"),
    ("https://boards.greenhouse.io/embed/job_board?for=acme", "greenhouse", "acme"),
    ("https://jobs.lever.co/acme", "lever", "acme"),
    ("https://jobs.eu.lever.co/acme", "lever", "acme"),
    ("https://acme.wd1.myworkdayjobs.com/en-US/External", "workday", "External"),
    ("https://jobs.ashbyhq.com/acme", "ashby", "acme"),
    ("https://careers.smartrecruiters.com/Acme", "smartrecruiters", "Acme"),
    ("https://careers-acme.icims.com/jobs", "icims", "careers-acme.icims.com"),
    ("https://acme.successfactors.com/careers", "successfactors", "acme.successfactors.com"),
    ("https://acme.jobs2web.com/search", "successfactors", "acme.jobs2web.com"),
    ("https://careers.acmewidgets.com/jobs", "custom", ""),
    ("https://greenhouse.io.evil.example/acme", "custom", ""),
])
def test_provider_detection(url, provider, board):
    assert (detect(url).provider, detect(url).board) == (provider, board)


def test_embedded_board_requires_explicit_link():
    assert embedded_board('<iframe src="https://jobs.lever.co/acme"></iframe>', SOURCE.career_url).board == "acme"
    assert embedded_board('<p>https://jobs.lever.co/acme</p>', SOURCE.career_url) is None
    assert same_board("https://boards.greenhouse.io/acme", "https://job-boards.greenhouse.io/acme/jobs/123")
    assert same_board("https://careers.smartrecruiters.com/Acme", "https://jobs.smartrecruiters.com/Acme/123")
    assert not same_board("https://jobs.ashbyhq.com/acme", "https://jobs.ashbyhq.com/other/123")


def test_configuration_is_opt_in_validated_bounded_and_deduplicated(monkeypatch):
    valid = {"company_name": "Acme", "career_url": SOURCE.career_url}
    monkeypatch.setenv("COMPANY_CAREER_SOURCES", json.dumps([
        valid, valid, {**valid, "enabled": False}, {**valid, "career_url": "http://127.0.0.1"},
        {**valid, "career_url": "https://dice.com/job-detail/123"}, {**valid, "provider": "imaginary"},
    ]))
    assert len(adapter.configured_sources()) == 1
    assert len(adapter.source_key(adapter.configured_sources()[0])) <= 40
    monkeypatch.setenv("COMPANY_CAREER_SOURCES", "invalid JSON")
    assert adapter.configured_sources() == []


def test_greenhouse_normalization_retains_department_date_and_untrusted_url():
    source = CareerSource("Acme Widgets", "https://boards.greenhouse.io/acmewidgets")
    client = fake_client({"jobs": [{"id": 12345, "title": "Senior Python Engineer", "content": DESCRIPTION,
        "location": {"name": "Austin, TX"}, "updated_at": "2026-09-01T10:00:00Z",
        "departments": [{"name": "Engineering"}], "absolute_url": "https://job-boards.greenhouse.io/acmewidgets/jobs/12345"}]})
    result = greenhouse.collect(client, source, detect(source.career_url), 20)
    collector = adapter.CompanyCareerAdapter(source)
    collector._platform = "greenhouse"
    job = collector.normalize_job(result.jobs[0])
    assert result.complete and job["source_job_id"] == "12345"
    assert job["posted_at"].year == 2026 and job["department"] == "Engineering"
    assert job["skills"] == "" and job["job_type"] == "Not specified"
    assert job["application_url"] is None and job["direct_url"] is None
    assert job["unverified_application_url"].endswith("/12345")
    assert json.loads(job["career_metadata"])["source_platform"] == "greenhouse"
    assert "content=true" in client.fetch.call_args.args[0]


def test_lever_normalization_retains_salary_workplace_apply_and_requirements():
    source = CareerSource("Acme Widgets", "https://jobs.eu.lever.co/acmewidgets")
    client = fake_client([{"id": "abc", "text": "Senior Python Engineer", "descriptionPlain": DESCRIPTION,
        "categories": {"location": "Austin, TX", "commitment": "Contract", "department": "Engineering"},
        "workplaceType": "hybrid", "hostedUrl": source.career_url + "/abc", "applyUrl": source.career_url + "/abc/apply",
        "lists": [{"text": "Requirements", "content": "<li>Python and SQL</li>"}],
        "salaryRange": {"min": 100000, "max": 150000, "currency": "USD", "interval": "per-year"}}])
    result = lever.collect(client, source, detect(source.career_url), 20)
    job = result.jobs[0]
    assert result.complete
    assert job["salary_min"] == 100000 and job["salary_currency"] == "USD"
    assert job["department"] == "Engineering" and job["work_mode"] == "hybrid"
    assert job["job_type"] == "Contract" and "Python and SQL" in job["requirements"]
    assert job["application_url"].endswith("/apply") and job["skills"] == ""
    assert client.fetch.call_args.args[0].startswith("https://api.eu.lever.co/")


def test_ashby_public_api_and_unlisted_jobs():
    source = CareerSource("Acme Widgets", "https://jobs.ashbyhq.com/acmewidgets")
    row = {"id": "abc", "title": "Engineer", "descriptionPlain": DESCRIPTION, "location": "Remote",
           "jobUrl": source.career_url + "/abc", "applyUrl": source.career_url + "/abc/application",
           "employmentType": "FullTime", "isRemote": True, "department": "Engineering"}
    result = ashby.collect(fake_client({"jobs": [row, {**row, "id": "private", "isListed": False}]}), source, detect(source.career_url), 20)
    assert len(result.jobs) == 1 and result.jobs[0]["work_mode"] == "Remote"
    assert not result.complete


def test_smartrecruiters_uses_public_list_and_detail():
    source = CareerSource("Acme Widgets", "https://careers.smartrecruiters.com/AcmeWidgets")
    client = fake_client({"content": [{"id": "12345"}], "totalFound": 1}, {
        "id": "12345", "name": "Python Engineer", "location": {"city": "Austin", "country": "US"},
        "jobAd": {"sections": {"jobDescription": {"text": DESCRIPTION}, "qualifications": {"text": "Python"}}},
        "postingUrl": "https://jobs.smartrecruiters.com/AcmeWidgets/12345", "typeOfEmployment": {"label": "Contract"}})
    result = smartrecruiters.collect(client, source, detect(source.career_url), 20)
    assert result.complete and result.jobs[0]["requirements"] == "Python"
    assert client.fetch.call_count == 2


def test_workday_website_read_contract_is_bounded():
    source = CareerSource("Acme Widgets", "https://acme.wd1.myworkdayjobs.com/en-US/External")
    client = fake_client({"jobPostings": [{"externalPath": "/job/Austin/Engineer_R123"}], "total": 1}, {
        "jobPostingInfo": {"jobReqId": "R123", "title": "Python Engineer", "location": "Austin", "jobDescription": DESCRIPTION}})
    result = workday.collect(client, source, detect(source.career_url), 20)
    assert result.complete and result.jobs[0]["source_job_id"] == "R123"
    request = client.fetch.call_args_list[0]
    assert request.kwargs["method"] == "POST" and request.kwargs["payload"]["limit"] == 20
    assert request.args[0].endswith("/wday/cxs/acme/External/jobs")


def test_generic_jsonld_preserves_optional_fields_without_fabrication():
    value = posting(jobLocation={"address": {"addressLocality": "Austin", "addressRegion": "TX"}},
        jobLocationType="TELECOMMUTE", skills=["Python", "SQL"], preferredSkills=["Redis"],
        baseSalary={"currency": "USD", "value": {"minValue": 100000, "maxValue": 140000, "unitText": "YEAR"}},
        datePosted="2026-09-01", applyUrl="/jobs/12345/apply")
    jobs, _ = generic_html.parse_html(html_for({"@graph": [value]}), SOURCE, SOURCE.career_url)
    job = jobs[0]
    assert job["location"] == "Austin, TX" and job["work_mode"] == "Remote"
    assert job["skills"] == "Python, SQL" and job["preferred_skills"] == "Redis"
    assert job["salary_max"] == 140000 and job["job_type"] == "Not specified"
    assert job["source_job_id"] == "12345" and job["posted_at"].year == 2026


@pytest.mark.parametrize("overrides", [
    {"title": ""}, {"description": "short"}, {"validThrough": "2000-01-01"},
    {"hiringOrganization": {"name": "Unrelated Employer"}},
])
def test_generic_missing_expired_or_wrong_employer_rejected(overrides):
    jobs, _ = generic_html.parse_html(html_for(posting(**overrides)), SOURCE, SOURCE.career_url)
    assert jobs == []


def test_static_cards_and_linked_details_without_browser():
    listing = '<a href="/jobs/12345/senior-python-engineer">Python Engineer</a>'
    detail = '<h1>Senior Python Engineer</h1><div class="job-description">' + DESCRIPTION + '</div><span class="required-skills">Python</span>'
    client = MagicMock()
    client.fetch.side_effect = [Document(SOURCE.career_url, 200, listing, "text/html"),
                               Document(SOURCE.career_url + "/12345/senior-python-engineer", 200, detail, "text/html")]
    result = generic_html.collect(client, SOURCE, detect(SOURCE.career_url), 20)
    assert len(result.jobs) == 1 and result.jobs[0]["skills"] == "Python"
    assert not result.complete  # no authoritative inventory assertion from arbitrary HTML


def test_browser_fallback_only_when_static_has_no_usable_listing(monkeypatch):
    from app.job_sources.career_sources import playwright_fallback
    render = MagicMock(return_value=html_for(posting()))
    monkeypatch.setattr(playwright_fallback, "render_page", render)
    client = MagicMock()
    initial = Document(SOURCE.career_url, 200, '<div id="app"></div>', "text/html")
    assert generic_html.collect(client, SOURCE, detect(SOURCE.career_url), 20, initial=initial).jobs == []
    render.assert_not_called()
    assert len(generic_html.collect(client, SOURCE, detect(SOURCE.career_url), 20, initial=initial, render=True).jobs) == 1
    render.assert_called_once()


@pytest.mark.parametrize("count,complete", [(0, True), (3, False), (None, False)])
def test_html_aging_requires_explicit_complete_inventory(count, complete):
    initial = Document(SOURCE.career_url, 200, html_for({"@type": "ItemList", "numberOfItems": count, "itemListElement": []}), "text/html")
    result = generic_html.collect(MagicMock(), SOURCE, detect(SOURCE.career_url), 20, initial=initial)
    assert result.complete is complete


def test_malformed_html_or_json_never_fabricates_jobs():
    jobs, links = generic_html.parse_html('<script type="application/ld+json">invalid</script><a href="https://[invalid">bad</a><h1>Careers</h1>', SOURCE, SOURCE.career_url)
    assert jobs == [] and links == []


def test_adapter_api_first_bounded_and_failed_company_safe(monkeypatch):
    source = CareerSource("Acme Widgets", "https://boards.greenhouse.io/acmewidgets")
    collector = adapter.CompanyCareerAdapter(source)
    client = MagicMock()
    monkeypatch.setattr(adapter, "PublicClient", lambda: client)
    collect = MagicMock(return_value=Collection([], True))
    monkeypatch.setattr(greenhouse, "collect", collect)
    assert collector.fetch_normalized_jobs(limit=500) == []
    assert collect.call_args.args[-1] == 20 and collector.complete_inventory
    client.fetch.assert_not_called()
    collect.side_effect = CollectionError("access_restricted")
    assert collector.fetch_jobs() == []
    assert not collector.health_check().healthy and not collector.complete_inventory
    assert collector.health_check().error == "access_restricted"


def test_adapter_rejects_off_tenant_listing_and_bounds_identifier():
    source = CareerSource("Acme Widgets", "https://jobs.ashbyhq.com/acmewidgets")
    collector = adapter.CompanyCareerAdapter(source)
    job = {"title": "Engineer", "description": DESCRIPTION, "source_url": "https://jobs.ashbyhq.com/other/123"}
    assert collector.normalize_job(job) is None
    job.update(source_url=source.career_url + "/123", source_job_id="x" * 900)
    normalized = collector.normalize_job(job)
    assert len(normalized["source_job_id"]) == 64 and len(normalized["external_id"]) <= 600


def response(status=200, body="", headers=None):
    result = MagicMock(status_code=status, headers=headers or {})
    result.iter_content.return_value = [body.encode()]
    result.__enter__.return_value = result
    return result


def mock_transport(monkeypatch, responses, ip="8.8.8.8"):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))])
    from app.job_sources.career_sources import http
    monkeypatch.setattr(http.time, "sleep", lambda duration: None)
    request = MagicMock(side_effect=responses)
    monkeypatch.setattr(requests.Session, "request", request)
    return request


def test_transport_reuses_robots_and_response_cache_no_auth_or_redirects(monkeypatch):
    request = mock_transport(monkeypatch, [response(404), response(body="[]")])
    with PublicClient() as client:
        assert client.fetch(SOURCE.career_url).json() == []
        client.fetch(SOURCE.career_url)
        assert client.session.trust_env is False
    assert request.call_count == 2
    options = request.call_args.kwargs
    assert options["allow_redirects"] is False and "Authorization" not in options["headers"]
    assert options["timeout"] == (5, 12)


@pytest.mark.parametrize("status,body,error", [
    (200, "User-agent: *\nDisallow: /jobs", "robots_disallowed"),
    (403, "", "access_restricted"), (429, "", "access_restricted"),
    (200, "<html>Verify you are human</html>", "access_challenge"),
])
def test_robots_or_access_denial_stops_without_retry(monkeypatch, status, body, error):
    request = mock_transport(monkeypatch, [response(status, body)])
    with PublicClient() as client, pytest.raises(CollectionError, match=error):
        client.fetch(SOURCE.career_url)
    assert request.call_count == 1


def test_private_dns_blocked_before_http(monkeypatch):
    request = mock_transport(monkeypatch, [], ip="127.0.0.1")
    from app.services.url_safety import UnsafeUrlError
    with PublicClient() as client, pytest.raises(UnsafeUrlError):
        client.fetch(SOURCE.career_url)
    request.assert_not_called()


def test_redirect_to_private_host_blocked_and_timeout_not_retried(monkeypatch):
    request = mock_transport(monkeypatch, [response(404), response(302, headers={"Location": "https://127.0.0.1/jobs"})])
    with PublicClient() as client, pytest.raises(CollectionError, match="unsafe_url"):
        client.fetch(SOURCE.career_url)
    assert request.call_count == 2
    request.side_effect = requests.Timeout()
    request.reset_mock()
    with PublicClient() as client, pytest.raises(requests.Timeout):
        client.fetch(SOURCE.career_url)
    assert request.call_count == 1


def test_transport_request_and_body_caps(monkeypatch):
    from app.job_sources.career_sources import http
    request = mock_transport(monkeypatch, [response(404), response(body="X" * 20)])
    monkeypatch.setattr(http, "MAX_BYTES", 10)
    with PublicClient() as client, pytest.raises(CollectionError, match="response_too_large"):
        client.fetch(SOURCE.career_url)
    request.reset_mock()
    with PublicClient() as client, pytest.raises(CollectionError, match="collection_budget_exhausted"):
        client.count = http.MAX_REQUESTS
        client.fetch(SOURCE.career_url)
    request.assert_not_called()


def test_browser_closes_on_timeout_and_blocks_unchecked_network(monkeypatch):
    from app.job_sources.career_sources.playwright_fallback import render_page
    manager = MagicMock()
    browser = manager.__enter__.return_value.chromium.launch.return_value
    context = browser.new_context.return_value
    context.new_page.return_value.goto.side_effect = TimeoutError()
    monkeypatch.setitem(sys.modules, "playwright.sync_api", SimpleNamespace(sync_playwright=lambda: manager))
    client = MagicMock()
    with pytest.raises(TimeoutError):
        render_page(client, SOURCE.career_url)
    browser.close.assert_called_once()
    assert browser.new_context.call_args.kwargs["service_workers"] == "block"
    context.route_web_socket.assert_called_once()
    guarded = context.route.call_args.args[1]
    route = MagicMock()
    route.request.url, route.request.resource_type, route.request.method = "https://unrelated.example/track", "fetch", "GET"
    guarded(route)
    route.abort.assert_called_once()
    client.fetch.assert_not_called()
    route.continue_.assert_not_called()
