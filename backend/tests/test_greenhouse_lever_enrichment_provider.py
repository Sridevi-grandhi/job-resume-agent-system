"""Tests for app/services/greenhouse_lever_enrichment_provider.py — the
Phase-1 enrichment provider built on Greenhouse's and Lever's already-
integrated, key-free public job-board APIs.

No real network call is ever made: url_safety.safe_get is mocked directly
(matching this repo's established pattern — see test_jsearch_adapter.py).
Company names used throughout are generic/fictional — never a real,
specific employer.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from app.services import greenhouse_lever_enrichment_provider as provider_module
from app.services.greenhouse_lever_enrichment_provider import candidate_slugs, greenhouse_lever_provider
from app.services.job_enrichment_service import EnrichmentQuery, enrich_job
from app.services.job_normalization_service import resolve_apply_action


def _response(status_code=200, payload=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload if payload is not None else {}
    return response


def _greenhouse_payload(jobs):
    return {"jobs": jobs}


def _gh_job(job_id, title, location, token, is_direct_id=True):
    return {
        "id": job_id, "title": title,
        "location": {"name": location},
        "absolute_url": f"https://boards.greenhouse.io/{token}/jobs/{job_id}",
        "content": f"<p>{title} role description.</p>",
    }


def _lever_job(posting_id, title, location, slug):
    return {
        "id": posting_id, "text": title,
        "categories": {"location": location, "team": "Engineering", "commitment": "Full-time"},
        "hostedUrl": f"https://jobs.lever.co/{slug}/{posting_id}",
        "applyUrl": f"https://jobs.lever.co/{slug}/{posting_id}/apply",
    }


def _aggregator_job(**overrides):
    defaults = dict(
        company="Acme Widgets", title="Senior Java Developer", location="Austin, TX",
        source="jsearch", direct_url=None, application_url=None,
        source_url="https://www.linkedin.com/jobs/view/999", availability_status="UNKNOWN",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def setup_function():
    provider_module.clear_cache()


# ---------------------------------------------------------------------------
# candidate_slugs — small, bounded, deterministic
# ---------------------------------------------------------------------------

def test_candidate_slugs_are_bounded_and_deterministic():
    slugs = candidate_slugs("Acme Widgets, Inc.")
    assert 1 <= len(slugs) <= provider_module.MAX_CANDIDATES_PER_PROVIDER
    assert slugs == candidate_slugs("Acme Widgets, Inc.")  # deterministic
    assert "acmewidgets" in slugs


def test_candidate_slugs_empty_for_blank_company():
    assert candidate_slugs("") == []
    assert candidate_slugs(None) == []


# ---------------------------------------------------------------------------
# 1. Valid Greenhouse board + exact job -> enrichment success
# ---------------------------------------------------------------------------

def test_valid_greenhouse_board_with_exact_job_enriches_successfully():
    job = _aggregator_job(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX")

    def fake_safe_get(session, url, **kwargs):
        if "boards-api.greenhouse.io" in url and "acmewidgets" in url:
            return _response(200, _greenhouse_payload([
                _gh_job(48213, "Senior Java Developer", "Austin, TX", "acmewidgets"),
                _gh_job(48214, "Marketing Manager", "Austin, TX", "acmewidgets"),
            ]))
        return _response(404)

    with patch("app.services.greenhouse_lever_enrichment_provider.url_safety.safe_get", side_effect=fake_safe_get), \
         patch("app.services.job_enrichment_service.availability.check_url_availability",
               return_value={"status": "ACTIVE", "reason": "http_200", "final_url": None}):
        result = enrich_job(job, provider=greenhouse_lever_provider)

    assert result.status == "ENRICHED"
    assert result.url == "https://boards.greenhouse.io/acmewidgets/jobs/48213"
    assert result.provider_name == "greenhouse_enrichment"


# ---------------------------------------------------------------------------
# 2/3. Greenhouse board exists but wrong company / wrong title -> reject
# ---------------------------------------------------------------------------

def test_greenhouse_board_confirmed_but_no_matching_title_is_rejected():
    """Simulates a coincidental slug collision with an unrelated real
    company's board — none of ITS jobs match our target title, so nothing
    is ever enriched (same code path covers 'wrong company' and 'wrong
    title', since without a company field, title+location IS the identity
    gate)."""
    job = _aggregator_job(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX")

    def fake_safe_get(session, url, **kwargs):
        if "boards-api.greenhouse.io" in url and "acmewidgets" in url:
            return _response(200, _greenhouse_payload([
                _gh_job(48213, "Marketing Manager", "Austin, TX", "acmewidgets"),
                _gh_job(48214, "Warehouse Associate", "Austin, TX", "acmewidgets"),
            ]))
        return _response(404)

    with patch("app.services.greenhouse_lever_enrichment_provider.url_safety.safe_get", side_effect=fake_safe_get):
        result = enrich_job(job, provider=greenhouse_lever_provider)

    assert result.status == "REJECTED"
    assert job.direct_url is None


# ---------------------------------------------------------------------------
# 4. Greenhouse exact title but incompatible location -> reject
# ---------------------------------------------------------------------------

def test_greenhouse_exact_title_wrong_location_is_rejected():
    job = _aggregator_job(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX")

    def fake_safe_get(session, url, **kwargs):
        if "boards-api.greenhouse.io" in url and "acmewidgets" in url:
            return _response(200, _greenhouse_payload([
                _gh_job(48213, "Senior Java Developer", "Berlin, Germany", "acmewidgets"),
            ]))
        return _response(404)

    with patch("app.services.greenhouse_lever_enrichment_provider.url_safety.safe_get", side_effect=fake_safe_get):
        result = enrich_job(job, provider=greenhouse_lever_provider)

    assert result.status == "REJECTED"


# ---------------------------------------------------------------------------
# 5. Valid Lever slug + exact job -> enrichment success
# ---------------------------------------------------------------------------

def test_valid_lever_slug_with_exact_job_enriches_successfully():
    job = _aggregator_job(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX")

    def fake_safe_get(session, url, **kwargs):
        if "boards-api.greenhouse.io" in url:
            return _response(404)
        if "api.lever.co" in url and "acmewidgets" in url:
            return _response(200, [
                _lever_job(55210, "Senior Java Developer", "Austin, TX", "acmewidgets"),
            ])
        return _response(404)

    with patch("app.services.greenhouse_lever_enrichment_provider.url_safety.safe_get", side_effect=fake_safe_get), \
         patch("app.services.job_enrichment_service.availability.check_url_availability",
               return_value={"status": "ACTIVE", "reason": "http_200", "final_url": None}):
        result = enrich_job(job, provider=greenhouse_lever_provider)

    assert result.status == "ENRICHED"
    assert result.url == "https://jobs.lever.co/acmewidgets/55210"
    assert result.provider_name == "lever_enrichment"


# ---------------------------------------------------------------------------
# 6/7. Lever wrong company / wrong role -> reject
# ---------------------------------------------------------------------------

def test_lever_confirmed_slug_no_matching_role_is_rejected():
    job = _aggregator_job(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX")

    def fake_safe_get(session, url, **kwargs):
        if "boards-api.greenhouse.io" in url:
            return _response(404)
        if "api.lever.co" in url and "acmewidgets" in url:
            return _response(200, [_lever_job(55211, "Sales Representative", "Austin, TX", "acmewidgets")])
        return _response(404)

    with patch("app.services.greenhouse_lever_enrichment_provider.url_safety.safe_get", side_effect=fake_safe_get):
        result = enrich_job(job, provider=greenhouse_lever_provider)

    assert result.status == "REJECTED"


# ---------------------------------------------------------------------------
# 8. Nonexistent board/slug for every candidate -> unchanged View Listing
# ---------------------------------------------------------------------------

def test_nonexistent_board_and_slug_leaves_view_listing_unchanged():
    job = _aggregator_job(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX")

    with patch("app.services.greenhouse_lever_enrichment_provider.url_safety.safe_get", return_value=_response(404)):
        result = enrich_job(job, provider=greenhouse_lever_provider)

    assert result.status == "REJECTED"
    assert job.direct_url is None
    action = resolve_apply_action(job)
    assert action["action_label"] == "View Listing"


# ---------------------------------------------------------------------------
# 9. Unsafe posting URL -> reject
# ---------------------------------------------------------------------------

def test_unsafe_candidate_url_from_greenhouse_is_rejected():
    job = _aggregator_job(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX")

    def fake_safe_get(session, url, **kwargs):
        if "boards-api.greenhouse.io" in url and "acmewidgets" in url:
            payload = _greenhouse_payload([{
                "id": 1, "title": "Senior Java Developer", "location": {"name": "Austin, TX"},
                "absolute_url": "javascript:alert(1)",
            }])
            return _response(200, payload)
        return _response(404)

    with patch("app.services.greenhouse_lever_enrichment_provider.url_safety.safe_get", side_effect=fake_safe_get):
        result = enrich_job(job, provider=greenhouse_lever_provider)

    assert result.status == "REJECTED"


# ---------------------------------------------------------------------------
# 10. Generic posting URL -> reject
# ---------------------------------------------------------------------------

def test_generic_posting_url_is_rejected():
    job = _aggregator_job(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX")

    def fake_safe_get(session, url, **kwargs):
        if "boards-api.greenhouse.io" in url and "acmewidgets" in url:
            payload = _greenhouse_payload([{
                "id": 1, "title": "Senior Java Developer", "location": {"name": "Austin, TX"},
                "absolute_url": "https://boards.greenhouse.io/acmewidgets",  # board homepage, not a specific job
            }])
            return _response(200, payload)
        return _response(404)

    with patch("app.services.greenhouse_lever_enrichment_provider.url_safety.safe_get", side_effect=fake_safe_get):
        result = enrich_job(job, provider=greenhouse_lever_provider)

    assert result.status == "REJECTED"


# ---------------------------------------------------------------------------
# 11. Availability expired -> do not promote to Apply
# ---------------------------------------------------------------------------

def test_expired_candidate_availability_is_not_enriched():
    job = _aggregator_job(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX")

    def fake_safe_get(session, url, **kwargs):
        if "boards-api.greenhouse.io" in url and "acmewidgets" in url:
            return _response(200, _greenhouse_payload([_gh_job(48213, "Senior Java Developer", "Austin, TX", "acmewidgets")]))
        return _response(404)

    with patch("app.services.greenhouse_lever_enrichment_provider.url_safety.safe_get", side_effect=fake_safe_get), \
         patch("app.services.job_enrichment_service.availability.check_url_availability",
               return_value={"status": "EXPIRED", "reason": "http_404", "final_url": None}):
        result = enrich_job(job, provider=greenhouse_lever_provider)

    assert result.status == "REJECTED"
    assert job.direct_url is None
    action = resolve_apply_action(job)
    assert action["action_label"] == "View Listing"  # never falsely promoted


# ---------------------------------------------------------------------------
# 12. Cached negative lookup -> no repeated API call
# ---------------------------------------------------------------------------

def test_cached_negative_greenhouse_lookup_skips_repeated_api_call():
    job = _aggregator_job(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX")
    call_count = {"n": 0}

    def fake_safe_get(session, url, **kwargs):
        call_count["n"] += 1
        return _response(404)

    with patch("app.services.greenhouse_lever_enrichment_provider.url_safety.safe_get", side_effect=fake_safe_get):
        greenhouse_lever_provider(EnrichmentQuery(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX"))
        first_call_count = call_count["n"]
        greenhouse_lever_provider(EnrichmentQuery(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX"))
        second_call_total = call_count["n"]

    # The second run must not repeat any HTTP call for a slug already
    # confirmed nonexistent by the first run.
    assert second_call_total == first_call_count


def test_cache_respects_ttl_expiry(monkeypatch):
    import time as real_time

    provider_module._cache_set("greenhouse", "acmewidgets", False)
    assert provider_module._cache_get("greenhouse", "acmewidgets") is False

    future = real_time.time() + provider_module.CACHE_TTL_SECONDS + 1
    monkeypatch.setattr(provider_module.time, "time", lambda: future)
    assert provider_module._cache_get("greenhouse", "acmewidgets") is None


# ---------------------------------------------------------------------------
# Bounded, sequential, stop-early behavior
# ---------------------------------------------------------------------------

def test_stops_guessing_more_greenhouse_slugs_once_one_confirms():
    job_calls = []

    def fake_safe_get(session, url, **kwargs):
        job_calls.append(url)
        if "boards-api.greenhouse.io" in url and candidate_slugs("Acme Widgets")[0] in url:
            return _response(200, _greenhouse_payload([_gh_job(48213, "Senior Java Developer", "Austin, TX", "acmewidgets")]))
        return _response(404)

    with patch("app.services.greenhouse_lever_enrichment_provider.url_safety.safe_get", side_effect=fake_safe_get):
        greenhouse_lever_provider(EnrichmentQuery(company="Acme Widgets", title="Senior Java Developer", location="Austin, TX"))

    greenhouse_calls = [url for url in job_calls if "boards-api.greenhouse.io" in url]
    assert len(greenhouse_calls) == 1  # first candidate confirmed -> never tried a second Greenhouse slug
