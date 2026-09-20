"""Tests for app/services/ats_resume_generation_service.py — the LLM-enabled
generation path. LLM calls are always mocked (never real, per project rules)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.services.ats_resume_generation_service import (
    _preserve_previously_supported_keywords,
    _preserve_years_of_experience,
    _replace_section_content,
    generate_with_llm_or_fallback,
    rewrite_summary_with_llm,
)
from app.services.ats_scoring import extract_resume_evidence
from app.services.keyword_injection_service import plan_keyword_injection
from app.services.llm_client import LLMClient, LLMGenerationError
from app.services.resume_structure_parser import parse_resume_structure


JOB = SimpleNamespace(
    title="Java Developer", company="Acme",
    description="Java Spring Boot React PostgreSQL AWS Docker role.",
    skills_required="Java, Spring Boot, React, PostgreSQL, AWS, Docker",
)


def _client(enabled=True):
    return LLMClient(config={
        "provider": "openai", "model": "gpt-4o", "api_key": "sk-test",
        "base_url": "https://api.openai.com/v1", "timeout": 5, "max_retries": 0,
        "temperature": 0.1, "enabled": enabled, "prompt_version": "ats-optimizer-v1",
    })


def _structure(resume_text):
    return parse_resume_structure(resume_text)


def test_llm_success_returns_structured_result(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    llm_json = {
        "generated_resume_plain_text": "Optimized resume body with Java and Spring Boot.",
        "rewritten_summary": "Java engineer.",
        "rewritten_bullets": [{"text": "Built REST APIs with Java.", "supported_by": ["E1"]}],
        "keyword_evidence": [{"keyword": "Java", "support_level": "strong"}],
        "warnings": [],
        "factual_preservation_confirmed": True,
    }
    with patch.object(_client().__class__, "generate_json", return_value=llm_json):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text, job=JOB, injection_plan=plan, structure=structure,
            client=_client(),
        )
    assert result["generation_mode"] == "llm"
    assert result["plain_text"] != llm_json["generated_resume_plain_text"]
    assert "Jane Developer" in result["plain_text"] or "jane@" in result["plain_text"].lower()
    assert "Built REST APIs with Java." in result["plain_text"]
    assert result.get("sections")
    assert result.get("assembly_method") == "deterministic_protected_merge"
    assert result["factual_preservation_confirmed"] is True
    meta = result["generation_metadata"]
    assert meta["generation_method"] == "llm"
    assert meta["llm_used"] is True
    assert meta["llm_provider"] == "openai"
    assert meta["llm_model"] == "gpt-4o"
    assert meta["llm_service"] is None
    assert meta["fallback_used"] is False
    assert meta["fallback_reason"] is None
    assert isinstance(meta.get("generation_duration_ms"), int)
    assert meta["generation_duration_ms"] >= 0


def test_llm_bullet_with_invalid_evidence_id_falls_back(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    llm_json = {
        "generated_resume_plain_text": "Optimized resume body.",
        "rewritten_bullets": [{"text": "Fabricated claim.", "supported_by": ["E999"]}],
        "factual_preservation_confirmed": True,
    }
    with patch.object(_client().__class__, "generate_json", return_value=llm_json):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text, job=JOB, injection_plan=plan, structure=structure,
            client=_client(),
        )
    assert result["generation_mode"] == "deterministic_fallback"
    assert "LLM generation failed" in result["generation_warnings"][0]


def test_llm_empty_plain_text_falls_back(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    llm_json = {"generated_resume_plain_text": "   ", "factual_preservation_confirmed": True}
    with patch.object(_client().__class__, "generate_json", return_value=llm_json):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text, job=JOB, injection_plan=plan, structure=structure,
            client=_client(),
        )
    assert result["generation_mode"] == "deterministic_fallback"


def test_llm_malformed_response_missing_required_field_succeeds_with_normalization(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    llm_json = {"generated_resume_plain_text": "Optimized body with Java and Spring Boot."}
    with patch.object(_client().__class__, "generate_json", return_value=llm_json):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text, job=JOB, injection_plan=plan, structure=structure,
            client=_client(),
        )
    assert result["generation_mode"] == "llm"
    assert result["json_repair_used"] is True


def test_llm_exception_during_dispatch_falls_back(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    with patch.object(_client().__class__, "generate_json", side_effect=RuntimeError("LLM request failed")):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text, job=JOB, injection_plan=plan, structure=structure,
            client=_client(),
        )
    assert result["generation_mode"] == "deterministic_fallback"
    assert "unexpected_provider_error" in result["generation_warnings"][0] or "failed" in result["generation_warnings"][0].lower()


def test_llm_uses_confirmed_evidence_and_correction_instructions(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    confirmed_evidence = {
        "kubernetes": {
            "evidence_type": "Current employment", "evidence_status": "USER_CONFIRMED_PROFESSIONAL",
            "employer_name": "Acme Corp", "role_title": "Engineer", "project_name": None,
            "start_date": "2022-01", "end_date": None, "currently_using": True,
            "usage_description": "Ran Kubernetes clusters.",
        }
    }
    llm_json = {
        "generated_resume_plain_text": "Optimized body with Kubernetes.",
        "factual_preservation_confirmed": True,
    }
    captured = {}

    def fake_generate_json(self, *, system_prompt, user_prompt, temperature):
        captured["user_prompt"] = user_prompt
        return llm_json

    with patch.object(_client().__class__, "generate_json", fake_generate_json):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text, job=JOB, injection_plan=plan, structure=structure,
            confirmed_evidence=confirmed_evidence, correction_instructions="Fix the bullet.",
            client=_client(),
        )
    assert result["generation_mode"] == "llm"
    assert "correction_instructions" in captured["user_prompt"]
    assert "kubernetes" in captured["user_prompt"].lower()


def test_llm_prompt_explicitly_includes_user_denied_skills(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    captured = {}

    def fake_generate_json(self, *, system_prompt, user_prompt, temperature):
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return {"generated_resume_plain_text": "Truthful resume.", "factual_preservation_confirmed": True}

    with patch.object(_client().__class__, "generate_json", fake_generate_json):
        generate_with_llm_or_fallback(
            resume_text=java_resume_text, job=JOB, injection_plan=plan, structure=structure,
            denied_skills=["gradle"], verified_resume_skills=["java", "spring boot"], client=_client(),
        )
    assert "user_denied_skills" in captured["system_prompt"]
    assert '"user_denied_skills": ["gradle"]' in captured["user_prompt"]
    assert '"verified_resume_skills": ["java", "spring boot"]' in captured["user_prompt"]


def test_llm_disabled_client_uses_deterministic_generator_directly(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    result = generate_with_llm_or_fallback(
        resume_text=java_resume_text, job=JOB, injection_plan=plan, structure=structure,
        client=_client(enabled=False),
    )
    assert result["generation_mode"] == "deterministic"
    assert "LLM not configured" in result["generation_warnings"][0]
    assert result["llm_used"] is False
    assert result["fallback_used"] is True
    assert result["fallback_reason"] == "llm_not_configured"


def test_safe_fallback_reason_metadata(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    for reason in ["connection_failed", "timeout", "invalid_json", "unexpected_provider_error"]:
        with patch.object(LLMClient, "generate_json", side_effect=LLMGenerationError(reason)):
            result = generate_with_llm_or_fallback(
                resume_text=java_resume_text, job=JOB, injection_plan=plan,
                structure=structure, client=_client(),
            )
        assert result["generation_method"] == "deterministic_fallback"
        assert result["llm_used"] is False
        assert result["fallback_used"] is True
        assert result["fallback_reason"] == reason
        assert result.get("generation_duration_ms") is not None
        assert result["generation_duration_ms"] >= 0


def test_structured_validation_failure_has_safe_reason(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    with patch.object(LLMClient, "generate_json", return_value={"factual_preservation_confirmed": True}):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text, job=JOB, injection_plan=plan,
            structure=structure, client=_client(),
        )
    assert result["fallback_reason"] == "schema_validation_failed"
    assert result["llm_used"] is False


def test_local_openai_compatible_is_identified_as_ollama(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    client = LLMClient(config={**_client().config, "provider": "openai_compatible", "model": "llama3.1:8b", "base_url": "http://localhost:11434/v1"})
    accepted = {"generated_resume_plain_text": "Truthful resume", "factual_preservation_confirmed": True}
    with patch.object(LLMClient, "generate_json", return_value=accepted):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text, job=JOB, injection_plan=plan,
            structure=structure, client=client,
        )
    assert result["llm_service"] == "ollama"
    assert result["llm_used"] is True
    assert result["fallback_used"] is False


def test_skip_llm_uses_deterministic_fallback(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    with patch.object(LLMClient, "generate_json") as mock_gen:
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text, job=JOB, injection_plan=plan,
            structure=structure, client=_client(), skip_llm=True,
        )
    mock_gen.assert_not_called()
    assert result["fallback_used"] is True
    assert result["fallback_reason"] == "llm_skipped"
    assert result["plain_text"]


# --- Professional Summary scoring regression (Bug 3): a JD-relevant skill that
# was already truthfully evidenced in the original resume must not silently
# lose its scoring credit just because the LLM reworded it away. ---

def test_preserve_previously_supported_keywords_restores_strong_evidence_via_summary(java_resume_text):
    job = SimpleNamespace(
        title="Java Developer", company="Acme",
        description="Java Spring Boot React PostgreSQL AWS Docker role.",
        skills_required="Java, Spring Boot, React, PostgreSQL, AWS, Docker",
    )
    # PostgreSQL is demonstrated in a Projects bullet ("Implemented ... with Java
    # and PostgreSQL") in the original resume, so it starts out "strong".
    original_evidence = extract_resume_evidence(java_resume_text, ["PostgreSQL"])
    assert original_evidence["PostgreSQL"]["evidence_strength"] == "strong"

    generated_without_postgres = (
        "Jane Developer\n"
        "jane@example.com | 555-111-2222\n\n"
        "Professional Summary\n"
        "Java Full Stack Developer with experience building enterprise applications.\n\n"
        "Technical Skills\n"
        "Java, Spring Boot, React, AWS, Docker, Jenkins, Agile\n\n"
        "Professional Experience\n"
        "Acme Corp | Java Full Stack Developer | Jan 2018 - Present\n"
        "- Developed REST APIs using Java and Spring Boot for account workflows.\n"
    )

    result_text, dropped = _preserve_previously_supported_keywords(
        generated_without_postgres, original_resume_text=java_resume_text, job=job,
    )

    assert "PostgreSQL" in dropped
    restored_evidence = extract_resume_evidence(result_text, ["PostgreSQL"])
    assert restored_evidence["PostgreSQL"]["evidence_strength"] == "strong"
    summary_block = result_text.split("Technical Skills")[0]
    assert "PostgreSQL" in summary_block


def test_preserve_previously_supported_keywords_catches_strength_downgrade_not_just_disappearance(java_resume_text):
    """Regression: a term that's still PRESENT in the generated text but
    downgraded from strong (narrative usage) to weak (bare skills-list
    mention) silently costs the same scorer credit as if it had vanished —
    a presence-only check misses this (confirmed live: "Spring Boot" moving
    from a strong original summary sentence to a Technical-Skills-only
    mention in the generated text dropped a real resume's score 94 -> 89)."""
    job = SimpleNamespace(
        title="Java Developer", company="Acme",
        description="Java Spring Boot React PostgreSQL AWS Docker role.",
        skills_required="Java, Spring Boot, React, PostgreSQL, AWS, Docker",
    )
    original_evidence = extract_resume_evidence(java_resume_text, ["PostgreSQL"])
    assert original_evidence["PostgreSQL"]["evidence_strength"] == "strong"

    # PostgreSQL is NOT absent here — it's still listed in Technical Skills —
    # but the Projects bullet that gave it "strong" evidence in the original
    # is gone, so it now only earns "weak" credit.
    generated_downgraded_only = (
        "Jane Developer\n"
        "jane@example.com | 555-111-2222\n\n"
        "Professional Summary\n"
        "Java Full Stack Developer with experience building enterprise applications.\n\n"
        "Technical Skills\n"
        "Java, Spring Boot, React, PostgreSQL, AWS, Docker, Jenkins, Agile\n\n"
        "Professional Experience\n"
        "Acme Corp | Java Full Stack Developer | Jan 2018 - Present\n"
        "- Developed REST APIs using Java and Spring Boot for account workflows.\n"
    )
    downgraded_evidence = extract_resume_evidence(generated_downgraded_only, ["PostgreSQL"])
    assert downgraded_evidence["PostgreSQL"]["evidence_strength"] == "weak"  # confirms the setup is a downgrade, not absence

    result_text, dropped = _preserve_previously_supported_keywords(
        generated_downgraded_only, original_resume_text=java_resume_text, job=job,
    )

    assert "PostgreSQL" in dropped
    restored_evidence = extract_resume_evidence(result_text, ["PostgreSQL"])
    assert restored_evidence["PostgreSQL"]["evidence_strength"] == "strong"


def test_preserve_previously_supported_keywords_restores_weak_evidence_via_skills_list_only(java_resume_text):
    job = SimpleNamespace(
        title="Java Developer", company="Acme",
        description="Java Spring Boot Agile role.",
        skills_required="Java, Spring Boot, Agile",
    )
    # "Agile" only ever appears as a bare Technical Skills entry in the
    # original — no bullet describes using it — so it starts out weak/moderate,
    # never strong.
    original_evidence = extract_resume_evidence(java_resume_text, ["Agile"])
    assert original_evidence["Agile"]["evidence_strength"] in ("weak", "moderate")

    generated_without_agile = (
        "Jane Developer\n"
        "jane@example.com\n\n"
        "Professional Summary\n"
        "Java Full Stack Developer with experience building enterprise applications.\n\n"
        "Technical Skills\n"
        "Java, Spring Boot, React, PostgreSQL, AWS, Docker, Jenkins\n\n"
        "Professional Experience\n"
        "Acme Corp | Java Full Stack Developer | Jan 2018 - Present\n"
        "- Developed REST APIs using Java and Spring Boot.\n"
    )

    result_text, dropped = _preserve_previously_supported_keywords(
        generated_without_agile, original_resume_text=java_resume_text, job=job,
    )

    assert "Agile" in dropped
    # Must not be upgraded into a fabricated "hands-on experience" claim the
    # original never made — restored at the same (non-strong) strength only.
    summary_block = result_text.split("Technical Skills")[0]
    assert "Agile" not in summary_block
    skills_block = result_text.split("Technical Skills")[1].split("Professional Experience")[0]
    assert "Agile" in skills_block


def test_preserve_previously_supported_keywords_noop_when_nothing_dropped(java_resume_text):
    job = SimpleNamespace(
        title="Java Developer", company="Acme",
        description="Java Spring Boot role.",
        skills_required="Java, Spring Boot",
    )
    result_text, dropped = _preserve_previously_supported_keywords(
        java_resume_text, original_resume_text=java_resume_text, job=job,
    )
    assert dropped == []
    assert result_text == java_resume_text


def test_preserve_previously_supported_keywords_never_introduces_unsupported_skill(java_resume_text):
    # Redis is JD-relevant but never appears anywhere in the original resume,
    # so it must never be introduced — regardless of what the generated text
    # contains.
    job = SimpleNamespace(
        title="Java Developer", company="Acme",
        description="Java Spring Boot Redis caching role.",
        skills_required="Java, Spring Boot, Redis",
    )
    generated_text = "Jane Developer\njane@example.com\n\nProfessional Summary\nJava developer.\n"
    _, dropped = _preserve_previously_supported_keywords(
        generated_text, original_resume_text=java_resume_text, job=job,
    )
    assert "Redis" not in dropped


def test_preserve_previously_supported_keywords_restores_summary_emphasized_technology_not_jd_required():
    """Part 2 extension: a technology the candidate's OWN original summary
    already emphasized (an architecture concept, here) must be restored
    even when the CURRENT job description doesn't happen to require it —
    the ticket's "architecture concepts" / "broader context" complaint,
    which strict JD-required-skill matching alone would miss."""
    original = (
        "Jane Developer\njane@example.com\n\n"
        "Professional Summary\n"
        "Java engineer with 8 years building microservices on AWS using Spring Boot and Docker "
        "across distributed teams.\n\n"
        "Technical Skills\nJava, Spring Boot, AWS, Docker, Microservices\n\n"
        "Professional Experience\n"
        "Acme Corp | Java Developer | Jan 2018 - Present\n"
        "- Designed microservices using Spring Boot and deployed them on AWS with Docker.\n"
    )
    job = SimpleNamespace(
        title="Java Developer", company="Acme",
        description="Java Spring Boot role.",  # deliberately does NOT mention microservices/AWS/Docker
        skills_required="Java, Spring Boot",
    )
    generated_without_microservices = (
        "Jane Developer\njane@example.com\n\n"
        "Professional Summary\n"
        "Java developer with strong Spring Boot experience.\n\n"
        "Technical Skills\nJava, Spring Boot\n\n"
        "Professional Experience\n"
        "Acme Corp | Java Developer | Jan 2018 - Present\n"
        "- Built REST APIs using Java and Spring Boot.\n"
    )
    result_text, dropped = _preserve_previously_supported_keywords(
        generated_without_microservices, original_resume_text=original, job=job,
    )
    assert "Microservices" in dropped
    restored_evidence = extract_resume_evidence(result_text, ["Microservices"])
    assert restored_evidence["Microservices"]["evidence_strength"] in ("strong", "moderate", "weak")


# --- Years-of-experience preservation (deterministic safety net for when
# the LLM paraphrases away a specific years-of-experience figure the
# original summary already stated, even with an explicit prompt
# instruction not to). ---

def test_preserve_years_of_experience_restores_dropped_figure():
    original = (
        "Professional Summary\n"
        "Senior Java Full Stack Developer with 8 years of experience building enterprise applications.\n\n"
        "Technical Skills\nJava, Spring Boot\n"
    )
    generated_without_years = (
        "Professional Summary\n"
        "Senior Java Full Stack Developer at Acme Corp focused on building enterprise applications.\n\n"
        "Technical Skills\nJava, Spring Boot\n"
    )
    result_text, changed = _preserve_years_of_experience(generated_without_years, original_resume_text=original)
    assert changed is True
    assert "8 years" in result_text
    # Only the summary section is touched.
    assert "Technical Skills\nJava, Spring Boot" in result_text


def test_preserve_years_of_experience_noop_when_already_present():
    original = "Professional Summary\nSenior Java Developer with 8 years of experience.\n"
    generated_with_years = "Professional Summary\nSenior Java Developer bringing 8 years of hands-on delivery experience.\n"
    result_text, changed = _preserve_years_of_experience(generated_with_years, original_resume_text=original)
    assert changed is False
    assert result_text == generated_with_years


def test_preserve_years_of_experience_noop_when_original_has_no_years_figure():
    original = "Professional Summary\nJava developer with enterprise experience.\n"
    generated = "Professional Summary\nJava developer focused on enterprise systems.\n"
    result_text, changed = _preserve_years_of_experience(generated, original_resume_text=original)
    assert changed is False
    assert result_text == generated


def test_preserve_years_of_experience_never_fabricates_a_different_number():
    """If the generated summary already states a DIFFERENT years figure
    than the original, this must not silently overwrite it — that's a
    truthfulness concern outside this deterministic safety net's scope
    (it only ever restates the candidate's own original number, never
    invents or corrects one)."""
    original = "Professional Summary\nSenior Developer with 8 years of experience.\n"
    generated_different_number = "Professional Summary\nSenior Developer with 5 years of experience.\n"
    result_text, changed = _preserve_years_of_experience(generated_different_number, original_resume_text=original)
    # A different number IS present, so nothing is injected — but the
    # original "8" is not silently discarded either; this is a case the
    # LLM must get right via the prompt, not something this safety net
    # should paper over by inserting a second, conflicting figure.
    assert changed is False


def test_llm_generation_restores_dropped_strong_evidence_keyword(java_resume_text):
    structure = _structure(java_resume_text)
    plan = plan_keyword_injection(java_resume_text, JOB, structure.get("employments") or [], structure.get("projects") or [])
    llm_json = {
        "generated_resume_plain_text": (
            "Jane Developer\njane@example.com | 555-111-2222 | linkedin.com/in/jane | github.com/jane\n\n"
            "Professional Summary\nJava Full Stack Developer skilled in Java, Spring Boot, React, AWS, and Docker.\n\n"
            "Technical Skills\nJava, Spring Boot, React, AWS, Docker, Jenkins\n\n"
            "Professional Experience\nAcme Corp | Java Full Stack Developer | Jan 2018 - Present\n"
            "- Developed REST APIs using Java and Spring Boot for account workflows.\n"
            "- Built React components for customer portals.\n"
            "- Deployed services with Docker and Jenkins on AWS.\n\n"
            "Projects\nPayments Portal\n- Implemented transaction workflows with Java.\n\n"
            "Education\nB.S. Computer Science, Example University\n\n"
            "Certifications\nAWS Cloud Practitioner"
        ),
        "factual_preservation_confirmed": True,
    }
    with patch.object(_client().__class__, "generate_json", return_value=llm_json):
        result = generate_with_llm_or_fallback(
            resume_text=java_resume_text, job=JOB, injection_plan=plan, structure=structure,
            client=_client(),
        )
    assert "PostgreSQL" in result["plain_text"]
    restored_evidence = extract_resume_evidence(result["plain_text"], ["PostgreSQL"])
    assert restored_evidence["PostgreSQL"]["evidence_strength"] == "strong"


# --- Professional Summary "Try Again" (OpenAI-backed, separate from the
# Ollama bullet path): genuine improvement, near-identical detection, and
# the quality gate that must never let a weaker rewrite replace a stronger
# current summary. ---

def test_replace_section_content_swaps_only_the_targeted_section():
    text = "Professional Summary\nOld summary text.\n\nTechnical Skills\nJava, Spring Boot\n"
    from app.services.ats_resume_generation_service import _SUMMARY_HEADING_PATTERN
    result = _replace_section_content(text, _SUMMARY_HEADING_PATTERN, "New summary text.")
    assert "New summary text." in result
    assert "Old summary text." not in result
    assert "Java, Spring Boot" in result  # untouched section preserved


def _summary_client_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)


SUMMARY_JOB = SimpleNamespace(title="Java Developer", description="Java Spring Boot role.")


def test_rewrite_summary_disabled_when_llm_not_configured(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "none")
    full_text = "Professional Summary\nJava developer.\n"
    summary, provider, no_change = rewrite_summary_with_llm(
        current_summary="Java developer.", full_resume_text=full_text,
        original_resume_text=full_text, job=SUMMARY_JOB,
    )
    assert summary is None
    assert provider is None
    assert no_change is False


def test_rewrite_summary_accepts_genuine_improvement(monkeypatch):
    _summary_client_env(monkeypatch)
    full_text = "Professional Summary\nJava developer.\n\nTechnical Skills\nJava, Spring Boot\n"
    new_summary = (
        "Senior Java developer with 8 years of experience building Spring Boot microservices "
        "for enterprise clients, delivering scalable backend systems and REST APIs. Skilled in "
        "designing maintainable service architectures, integrating with relational databases, and "
        "collaborating with cross-functional teams to deliver reliable production systems on schedule."
    )
    with patch.object(LLMClient, "generate_json", return_value={"summary": new_summary}):
        with patch("app.services.ats_scoring.calculate_weighted_ats_score", side_effect=[
            {"match_percentage": 60}, {"match_percentage": 75},
        ]):
            summary, provider, no_change = rewrite_summary_with_llm(
                current_summary="Java developer.", full_resume_text=full_text,
                original_resume_text=full_text, job=SUMMARY_JOB,
            )
    assert summary == new_summary
    assert provider == "openai"
    assert no_change is False


def test_rewrite_summary_near_identical_to_current_recommends_no_change(monkeypatch):
    _summary_client_env(monkeypatch)
    current = (
        "Senior Java developer with 8 years of experience building enterprise applications "
        "using Java, Spring Boot, and REST APIs for large-scale systems. Experienced designing "
        "maintainable backend services, integrating with relational databases, and collaborating "
        "with cross-functional teams to deliver reliable production systems on schedule."
    )
    full_text = f"Professional Summary\n{current}\n"
    with patch.object(LLMClient, "generate_json", return_value={"summary": current}):
        summary, provider, no_change = rewrite_summary_with_llm(
            current_summary=current, full_resume_text=full_text, original_resume_text=full_text, job=SUMMARY_JOB,
        )
    assert summary is None
    assert no_change is True


def test_rewrite_summary_quality_gate_retries_then_recovers(monkeypatch):
    _summary_client_env(monkeypatch)
    current = (
        "Senior Java developer with 8 years of experience in Spring Boot and SQL, "
        "delivering enterprise backend systems for large organizations. Experienced designing "
        "maintainable service architectures, integrating with relational databases, and "
        "collaborating with cross-functional teams to deliver reliable production systems on schedule."
    )
    full_text = f"Professional Summary\n{current}\n"
    responses = [
        # weaker: drops years/SQL, generic wording, still long enough to pass the length gate
        {"summary": (
            "Java developer focused on backend systems, delivering reliable software for internal "
            "teams and business stakeholders. Comfortable working across the stack to ship features, "
            "fix defects, and support ongoing maintenance of existing applications for the "
            "organization, coordinating closely with product managers and other engineers day to day."
        )},
        {"summary": (
            "With 8 years of hands-on Spring Boot and SQL experience, this senior Java engineer builds "
            "and ships enterprise backend systems for large organizations. Skilled in designing "
            "maintainable service architectures and collaborating with cross-functional teams to "
            "deliver reliable production systems on schedule."
        )},  # recovers the dropped facts
    ]
    with patch.object(LLMClient, "generate_json", side_effect=responses):
        with patch("app.services.ats_scoring.calculate_weighted_ats_score", side_effect=[
            {"match_percentage": 70},  # baseline (current summary in place)
            {"match_percentage": 55},  # first candidate — worse
            {"match_percentage": 72},  # retry candidate — better than baseline
        ]):
            summary, provider, no_change = rewrite_summary_with_llm(
                current_summary=current, full_resume_text=full_text, original_resume_text=full_text, job=SUMMARY_JOB,
            )
    assert summary == responses[1]["summary"]
    assert no_change is False


def test_rewrite_summary_quality_gate_preserves_current_when_both_attempts_weaker(monkeypatch):
    _summary_client_env(monkeypatch)
    current = (
        "Senior Java developer with 8 years of experience in Spring Boot and SQL, "
        "delivering enterprise backend systems for large organizations. Experienced designing "
        "maintainable service architectures, integrating with relational databases, and "
        "collaborating with cross-functional teams to deliver reliable production systems on schedule."
    )
    full_text = f"Professional Summary\n{current}\n"
    weak_summary = (
        "Java developer focused on backend systems, delivering reliable software for internal "
        "teams and business stakeholders. Comfortable working across the stack to ship features, "
        "fix defects, and support ongoing maintenance of existing applications for the organization."
    )
    with patch.object(LLMClient, "generate_json", return_value={"summary": weak_summary}):
        with patch("app.services.ats_scoring.calculate_weighted_ats_score", side_effect=[
            {"match_percentage": 70},  # baseline
            {"match_percentage": 55},  # first candidate — worse
            {"match_percentage": 58},  # retry candidate — still worse
        ]):
            summary, provider, no_change = rewrite_summary_with_llm(
                current_summary=current, full_resume_text=full_text, original_resume_text=full_text, job=SUMMARY_JOB,
            )
    # A quality-gate decline preserves the stronger current summary — this is
    # NOT the same outcome as "no change recommended" (the LLM DID produce
    # different wording; it just wasn't better), so the caller's deterministic
    # fallback path is used instead.
    assert summary is None
    assert no_change is False


def test_rewrite_summary_malformed_response_returns_none(monkeypatch):
    _summary_client_env(monkeypatch)
    full_text = "Professional Summary\nJava developer.\n"
    with patch.object(LLMClient, "generate_json", return_value={"summary": ""}):
        summary, provider, no_change = rewrite_summary_with_llm(
            current_summary="Java developer.", full_resume_text=full_text,
            original_resume_text=full_text, job=SUMMARY_JOB,
        )
    assert summary is None
    assert no_change is False
