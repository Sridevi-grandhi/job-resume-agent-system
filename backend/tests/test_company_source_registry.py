"""Tests for the CompanySource registry (app/services/job_source_registry_service.py).

Covers: real companies are sourced only from operator configuration (never
hardcoded), syncing is idempotent, and demo_mode defaults off.
"""

from __future__ import annotations

from app.models.company_source_model import CompanySource
from app.models.job_source_model import JobSource
from app.services import job_source_registry_service as registry


def test_demo_mode_defaults_off(monkeypatch):
    monkeypatch.delenv("UPDATED_JOB_DEMO_MODE", raising=False)
    assert registry.demo_mode_enabled() is False


def test_demo_mode_enabled_via_env(monkeypatch):
    monkeypatch.setenv("UPDATED_JOB_DEMO_MODE", "true")
    assert registry.demo_mode_enabled() is True


def test_sync_company_sources_parses_configured_pairs_only(db_session, monkeypatch):
    monkeypatch.setenv("GREENHOUSE_COMPANY_SOURCES", "Example Employer:example-employer, Another Co:another-co")
    monkeypatch.delenv("LEVER_COMPANY_SOURCES", raising=False)

    registry.sync_company_sources_from_env(db_session)

    rows = db_session.query(CompanySource).filter_by(source_type="GREENHOUSE").all()
    names = {row.company_name for row in rows}
    assert names == {"Example Employer", "Another Co"}
    slugs = {row.source_identifier for row in rows}
    assert slugs == {"example-employer", "another-co"}


def test_sync_company_sources_ignores_malformed_pairs(db_session, monkeypatch):
    monkeypatch.setenv("GREENHOUSE_COMPANY_SOURCES", "no-colon-here, :missing-name, Missing Slug:")
    # Isolate from whatever LEVER_COMPANY_SOURCES the real environment's
    # .env may have loaded (e.g. a leftover manual diagnostic entry) — this
    # test only asserts about the malformed GREENHOUSE value above.
    monkeypatch.delenv("LEVER_COMPANY_SOURCES", raising=False)
    registry.sync_company_sources_from_env(db_session)
    assert db_session.query(CompanySource).count() == 0


def test_sync_company_sources_is_idempotent(db_session, monkeypatch):
    monkeypatch.setenv("GREENHOUSE_COMPANY_SOURCES", "Example Employer:example-employer")
    registry.sync_company_sources_from_env(db_session)
    registry.sync_company_sources_from_env(db_session)
    registry.sync_company_sources_from_env(db_session)
    assert db_session.query(CompanySource).filter_by(source_type="GREENHOUSE").count() == 1


def test_removing_a_company_from_env_disables_its_stale_row(db_session, monkeypatch):
    """Exact production regression: LEVER_COMPANY_SOURCES=Favor:askfavor was
    a temporary diagnostic entry, later removed from .env — but the
    CompanySource row it created stayed enabled forever (sync only ever
    upserted rows present in the env var, never pruned rows no longer
    there), so Lever kept fetching a company the operator had removed."""
    monkeypatch.setenv("LEVER_COMPANY_SOURCES", "Favor:askfavor")
    registry.sync_company_sources_from_env(db_session)
    favor = db_session.query(CompanySource).filter_by(source_type="LEVER", company_slug="askfavor").one()
    assert favor.enabled is True

    monkeypatch.delenv("LEVER_COMPANY_SOURCES", raising=False)
    registry.sync_company_sources_from_env(db_session)

    db_session.refresh(favor)
    assert favor.enabled is False  # disabled, never deleted — same soft-close pattern as DiscoveredJob
    assert registry.get_company_sources(db_session, "LEVER") == []


def test_lever_adapter_fetches_nothing_once_favor_is_removed_from_env(db_session, monkeypatch):
    """End-to-end version of the regression above, through get_adapters —
    the exact call path run_poll_cycle uses."""
    monkeypatch.setenv("LEVER_COMPANY_SOURCES", "Favor:askfavor")
    registry.get_adapters(db_session)  # first poll cycle: Favor configured

    monkeypatch.delenv("LEVER_COMPANY_SOURCES", raising=False)
    adapters = registry.get_adapters(db_session)  # second poll cycle: Favor removed from .env
    lever = next(a for a in adapters if a.source_name == "lever")
    assert lever.live_enabled is False
    assert lever.company_sources == []
    assert lever.fetch_jobs(limit=10) == []


def test_replacing_one_company_with_another_disables_only_the_removed_one(db_session, monkeypatch):
    monkeypatch.setenv("GREENHOUSE_COMPANY_SOURCES", "Old Co:old-co,Keep Co:keep-co")
    registry.sync_company_sources_from_env(db_session)

    monkeypatch.setenv("GREENHOUSE_COMPANY_SOURCES", "Keep Co:keep-co")
    registry.sync_company_sources_from_env(db_session)

    enabled = {row["company_slug"] for row in registry.get_company_sources(db_session, "GREENHOUSE")}
    assert enabled == {"keep-co"}
    old = db_session.query(CompanySource).filter_by(source_type="GREENHOUSE", company_slug="old-co").one()
    assert old.enabled is False


def test_get_company_sources_only_returns_enabled_rows_for_the_requested_connector(db_session):
    db_session.add(CompanySource(company_name="Example Employer", company_slug="example-employer", source_type="GREENHOUSE", source_identifier="example-employer", enabled=True))
    db_session.add(CompanySource(company_name="Disabled Co", company_slug="disabled-co", source_type="GREENHOUSE", source_identifier="disabled-co", enabled=False))
    db_session.add(CompanySource(company_name="Lever Co", company_slug="lever-co", source_type="LEVER", source_identifier="lever-co", enabled=True))
    db_session.commit()

    greenhouse_companies = registry.get_company_sources(db_session, "GREENHOUSE")
    assert [c["company_name"] for c in greenhouse_companies] == ["Example Employer"]


def test_count_configured_companies(db_session, monkeypatch):
    monkeypatch.setenv("GREENHOUSE_COMPANY_SOURCES", "Example Employer:example-employer")
    monkeypatch.setenv("LEVER_COMPANY_SOURCES", "Another Employer:another-employer")
    registry.sync_company_sources_from_env(db_session)
    assert registry.count_configured_companies(db_session) == 2


def test_no_companies_configured_by_default(db_session, monkeypatch):
    monkeypatch.delenv("GREENHOUSE_COMPANY_SOURCES", raising=False)
    monkeypatch.delenv("LEVER_COMPANY_SOURCES", raising=False)
    registry.sync_company_sources_from_env(db_session)
    assert registry.count_configured_companies(db_session) == 0


def test_get_adapters_wires_configured_companies_into_greenhouse_and_lever(db_session, monkeypatch):
    monkeypatch.setenv("GREENHOUSE_COMPANY_SOURCES", "Example Employer:example-employer")
    monkeypatch.delenv("LEVER_COMPANY_SOURCES", raising=False)
    monkeypatch.delenv("UPDATED_JOB_DEMO_MODE", raising=False)

    adapters = registry.get_adapters(db_session)
    by_name = {adapter.source_name: adapter for adapter in adapters}

    assert by_name["greenhouse"].live_enabled is True
    assert by_name["greenhouse"].company_sources[0]["company_name"] == "Example Employer"
    assert by_name["lever"].live_enabled is False
    assert by_name["workday"].demo_mode is False


# ---------------------------------------------------------------------------
# Broad provider (JSearch) selection + Remotive default-off gating
# ---------------------------------------------------------------------------

def test_primary_provider_defaults_to_jsearch(monkeypatch):
    monkeypatch.delenv("UPDATED_JOB_PRIMARY_PROVIDER", raising=False)
    assert registry.primary_provider_name() == "jsearch"


def test_remotive_disabled_by_default(monkeypatch):
    monkeypatch.delenv("UPDATED_JOB_REMOTIVE_ENABLED", raising=False)
    assert registry.remotive_enabled() is False


def test_remotive_can_be_enabled_via_env(monkeypatch):
    monkeypatch.setenv("UPDATED_JOB_REMOTIVE_ENABLED", "true")
    assert registry.remotive_enabled() is True


def test_get_adapters_excludes_remotive_by_default(db_session, monkeypatch):
    monkeypatch.delenv("UPDATED_JOB_REMOTIVE_ENABLED", raising=False)
    names = {adapter.source_name for adapter in registry.get_adapters(db_session)}
    assert "remotive" not in names
    assert "jsearch" in names  # Remotive's code is not deleted or unreachable — see next test


def test_get_adapters_includes_remotive_when_explicitly_enabled(db_session, monkeypatch):
    monkeypatch.setenv("UPDATED_JOB_REMOTIVE_ENABLED", "true")
    names = {adapter.source_name for adapter in registry.get_adapters(db_session)}
    assert "remotive" in names


def test_sync_registry_disables_a_source_that_becomes_unreachable(db_session, monkeypatch):
    """Remotive turned off after previously being on: its JobSource row
    must flip to disabled rather than keep reporting whatever enabled/mode
    it had while still active — otherwise diagnostics keeps calling it
    live/healthy long after it stopped running."""
    monkeypatch.setenv("UPDATED_JOB_REMOTIVE_ENABLED", "true")
    registry.sync_registry(db_session)
    row = db_session.query(JobSource).filter_by(source_name="remotive").one()
    assert row.enabled is True

    monkeypatch.setenv("UPDATED_JOB_REMOTIVE_ENABLED", "false")
    registry.sync_registry(db_session)
    db_session.refresh(row)
    assert row.enabled is False
    assert registry.classify_source_status(row.source_name, row.enabled, row.mode) == "DISABLED"


def test_get_adapters_always_includes_jsearch_slot_regardless_of_api_key(db_session, monkeypatch):
    """jsearch is constructed the same way Greenhouse/Lever always are, even
    unconfigured — so it reports its own real unavailable state through
    health_check() rather than silently vanishing from diagnostics."""
    monkeypatch.delenv("JSEARCH_API_KEY", raising=False)
    adapters = registry.get_adapters(db_session)
    by_name = {adapter.source_name: adapter for adapter in adapters}
    assert "jsearch" in by_name
    assert by_name["jsearch"].is_configured is False


def test_get_adapters_adds_configured_adzuna_without_replacing_jsearch(db_session, monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "test-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "test-key")
    by_name = {adapter.source_name: adapter for adapter in registry.get_adapters(db_session)}
    assert by_name["jsearch"].source_type == "broad_provider"
    assert by_name["adzuna"].source_type == "broad_provider"
    assert by_name["adzuna"].preference_queries == by_name["jsearch"].preference_queries


def test_get_broad_provider_adapter_builds_queries_from_preferences(db_session, test_preferences):
    adapter = registry.get_broad_provider_adapter(db_session)
    assert adapter is not None
    assert adapter.source_name == "jsearch"
    assert len(adapter.preference_queries) == 2
    assert adapter.preference_queries[0]["job_title"] == test_preferences.job_title
    assert adapter.preference_queries[1]["job_title"] == "Spring Boot Developer"
    assert adapter.preference_queries[1]["location"] == test_preferences.location


def test_get_broad_provider_adapter_returns_none_for_unknown_provider(db_session, monkeypatch):
    monkeypatch.setenv("UPDATED_JOB_PRIMARY_PROVIDER", "some_future_provider")
    assert registry.get_broad_provider_adapter(db_session) is None


def test_unknown_primary_provider_still_leaves_supplemental_sources_working(db_session, monkeypatch):
    monkeypatch.setenv("UPDATED_JOB_PRIMARY_PROVIDER", "unknown")
    monkeypatch.setenv("GREENHOUSE_COMPANY_SOURCES", "Example Employer:example-employer")
    names = {adapter.source_name for adapter in registry.get_adapters(db_session)}
    assert "jsearch" not in names
    assert "greenhouse" in names


# ---------------------------------------------------------------------------
# provider_config_status — never leaks the API key
# ---------------------------------------------------------------------------

def test_provider_config_status_reports_configured_true_with_key(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "super-secret-key")
    monkeypatch.setenv("UPDATED_JOB_PRIMARY_PROVIDER", "jsearch")
    status = registry.provider_config_status()
    assert status["primary_provider"] == "jsearch"
    assert status["configured"] is True
    assert "super-secret-key" not in str(status)


def test_provider_config_status_reports_configured_false_without_key(monkeypatch):
    monkeypatch.delenv("JSEARCH_API_KEY", raising=False)
    monkeypatch.setenv("UPDATED_JOB_PRIMARY_PROVIDER", "jsearch")
    status = registry.provider_config_status()
    assert status["configured"] is False


def test_provider_config_status_per_source_breakdown_is_accurate_and_secret_free(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "super-secret-key")
    monkeypatch.setenv("GREENHOUSE_COMPANY_SOURCES", "Example Employer:example-employer")
    monkeypatch.delenv("LEVER_COMPANY_SOURCES", raising=False)
    monkeypatch.setenv("UPDATED_JOB_REMOTIVE_ENABLED", "false")

    status = registry.provider_config_status()
    sources = status["sources"]
    assert sources["jsearch"]["configured"] is True
    assert sources["greenhouse"]["configured"] is True
    assert sources["lever"]["configured"] is False
    assert sources["workday"]["available"] is False
    assert sources["remotive"]["enabled"] is False
    assert "super-secret-key" not in str(status)


def test_provider_config_status_never_includes_a_key_field(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "super-secret-key")
    status = registry.provider_config_status()
    assert "api_key" not in status
    assert "JSEARCH_API_KEY" not in status
