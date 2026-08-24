"""Tests for app/services/technology_consistency_validator.py."""

from __future__ import annotations

from app.services.technology_consistency_validator import validate_technology_consistency


def test_no_conflict_when_only_one_group_member_in_jd():
    result = validate_technology_consistency(
        jd_keywords=["React"], resume_text="Built apps with React.", supported_keywords=["React"],
    )
    assert result["selectedTechnologies"] == ["React"]
    assert result["rejectedTechnologies"] == []


def test_conflict_resolved_in_favor_of_resume_supported_technology():
    result = validate_technology_consistency(
        jd_keywords=["React", "Angular"],
        resume_text="Built apps with Angular for years.",
        supported_keywords=["React", "Angular"],
    )
    assert "Angular" in result["selectedTechnologies"]
    assert "React" in result["rejectedTechnologies"]
    assert result["conflictWarnings"]


def test_conflict_with_no_resume_support_keeps_first_selected():
    result = validate_technology_consistency(
        jd_keywords=["Jenkins", "GitHub Actions"],
        resume_text="No CI/CD tooling mentioned.",
        supported_keywords=["Jenkins"],
    )
    assert result["selectedTechnologies"] == ["Jenkins"]
    assert "GitHub Actions" in result["rejectedTechnologies"]


def test_python_frameworks_conflict_resolved_by_resume_support():
    result = validate_technology_consistency(
        jd_keywords=["Django", "Flask"],
        resume_text="Experienced with Flask microservices.",
        supported_keywords=["Django", "Flask"],
    )
    assert "Flask" in result["selectedTechnologies"]
    assert "Django" in result["rejectedTechnologies"]


def test_multi_cloud_kept_only_when_resume_shows_multiple_clouds():
    # "Cloud Providers" is itself a CONFLICT_GROUPS entry, so a cloud only reaches
    # the dedicated multi-cloud block below if it wasn't already resolved there —
    # i.e. it's absent from jd_keywords (so present_in_jd has <=1 member) but still
    # arrives in `selected` via supported_keywords directly.
    result = validate_technology_consistency(
        jd_keywords=["AWS"],
        resume_text="Deployed services on AWS only.",
        supported_keywords=["AWS", "Azure"],
    )
    assert result["selectedTechnologies"] == ["AWS"]
    assert "Azure" in result["rejectedTechnologies"]
    assert any("multi-cloud" in w for w in result["conflictWarnings"])


def test_multi_cloud_kept_when_resume_genuinely_shows_multiple_clouds():
    result = validate_technology_consistency(
        jd_keywords=["AWS"],
        resume_text="Deployed services on both AWS and Azure in production.",
        supported_keywords=["AWS", "Azure"],
    )
    assert set(result["selectedTechnologies"]) == {"AWS", "Azure"}
    assert result["rejectedTechnologies"] == []


def test_nextjs_rejected_without_explicit_resume_support():
    result = validate_technology_consistency(
        jd_keywords=["Next.js"], resume_text="Experienced with React.", supported_keywords=["Next.js"],
    )
    assert "Next.js" not in result["selectedTechnologies"]
    assert "Next.js" in result["rejectedTechnologies"]
    assert any("Next.js" in w for w in result["conflictWarnings"])


def test_nextjs_kept_with_explicit_resume_support():
    result = validate_technology_consistency(
        jd_keywords=["Next.js"], resume_text="Built server-rendered apps with Next.js.",
        supported_keywords=["Next.js"],
    )
    assert "Next.js" in result["selectedTechnologies"]


def test_monitoring_tools_conflict_resolved_by_resume_support():
    result = validate_technology_consistency(
        jd_keywords=["Splunk", "Dynatrace"],
        resume_text="Monitored production systems with Dynatrace dashboards.",
        supported_keywords=["Splunk", "Dynatrace"],
    )
    assert "Dynatrace" in result["selectedTechnologies"]
    assert "Splunk" in result["rejectedTechnologies"]


def test_empty_inputs_return_empty_results():
    result = validate_technology_consistency(jd_keywords=[], resume_text="", supported_keywords=[])
    assert result == {"selectedTechnologies": [], "rejectedTechnologies": [], "conflictWarnings": []}
