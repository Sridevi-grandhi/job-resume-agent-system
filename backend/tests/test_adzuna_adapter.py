from unittest.mock import MagicMock, patch
import requests
from app.job_sources.adzuna import AdzunaAdapter
from app.services.job_normalization_service import resolve_apply_action

SPEC = {"job_title": "DevOps Engineer", "location": "Austin, TX"}
RAW = {"id": "42", "title": "DevOps Engineer", "company": {"display_name": "Acme"}, "location": {"display_name": "Austin, TX"}, "description": "<p>Build reliable platform automation with Kubernetes.</p>", "created": "2026-01-02T00:00:00Z", "salary_min": 100000, "salary_max": 140000, "contract_type": "permanent", "contract_time": "full_time", "redirect_url": "https://www.adzuna.com/details/42"}
def response(status=200, payload=None):
    r=MagicMock(status_code=status); r.json.return_value=payload or {"results":[RAW]}; r.raise_for_status.side_effect=requests.HTTPError() if status>=400 else None; return r
def test_normalizes_and_keeps_adzuna_redirect_as_listing():
    job=AdzunaAdapter().normalize_job(RAW); assert job["source"]=="adzuna" and job["source_job_id"]=="42"; assert job["company"]=="Acme" and "Kubernetes" in job["description"]; assert job["unverified_application_url"] is None; assert resolve_apply_action(job)["action_label"]=="View Listing"

def test_normalizes_separate_adzuna_outbound_apply_url_only_as_untrusted_candidate():
    job = AdzunaAdapter().normalize_job({**RAW, "external_apply_url": "https://acme.wd1.myworkdayjobs.com/en-US/Careers/job/Austin/DevOps-Engineer_R42"})
    assert job["source_url"] == RAW["redirect_url"]
    assert job["unverified_application_url"] == "https://acme.wd1.myworkdayjobs.com/en-US/Careers/job/Austin/DevOps-Engineer_R42"
    assert job["application_url"] is None and job["direct_url"] is None
def test_fetch_uses_env_credentials_and_bounded_page_parameters(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID","id"); monkeypatch.setenv("ADZUNA_APP_KEY","secret")
    adapter=AdzunaAdapter([SPEC])
    with patch("app.job_sources.adzuna.url_safety.safe_get", return_value=response()) as get: assert len(adapter.fetch_jobs(5))==1
    assert get.call_args.kwargs["params"]["app_id"]=="id" and get.call_args.kwargs["params"]["app_key"]=="secret" and "/us/search/1" in get.call_args.args[1]
def test_failures_and_malformed_payload_return_no_fake_jobs(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID","id"); monkeypatch.setenv("ADZUNA_APP_KEY","secret")
    for value in (response(401), response(429), response(500), response(200,{"results":"bad"})):
        with patch("app.job_sources.adzuna.url_safety.safe_get", return_value=value): assert AdzunaAdapter([SPEC]).fetch_jobs()==[]

def test_adzuna_redirect_does_not_replace_a_verified_direct_destination():
    from app.services.job_normalization_service import resolve_apply_action
    direct = {"source": "jsearch", "application_url": "https://acme.example/jobs/12345", "direct_url": "https://acme.example/jobs/12345", "source_url": "https://www.adzuna.com/details/42", "availability_status": "ACTIVE"}
    assert resolve_apply_action(direct)["action_label"] == "Apply"
