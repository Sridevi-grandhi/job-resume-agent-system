"""Regression: accepted user evidence must flow into generation, gaps, and optimized score."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.ats_resume_generation_service import generate_with_llm_or_fallback
from app.services.ats_scoring import calculate_weighted_ats_score
from app.services.keyword_injection_service import plan_keyword_injection
from app.services.keyword_utils import select_supported_keywords
from app.services.llm_client import LLMClient, LLMGenerationError
from app.services.resume_generator import generate_optimized_resume
from app.services.skill_analysis_service import normalize_skill_name
from app.services.skill_confirmation_service import (
    build_canonical_supported_evidence,
    skill_supported_by_evidence,
    to_scoring_evidence,
)


SDET_JOB = SimpleNamespace(
    id=99,
    title="SDET / QA Automation Engineer",
    company="Example",
    description=(
        "SDET / QA Automation Engineer role. Must have strong test automation experience. "
        "Required: Selenium or Playwright, Java, TestNG, Angular, Spring, Hibernate, SOAP, "
        "AWS or Azure, REST API testing. Preferred: Kafka."
    ),
    skills_required=(
        "Angular, Spring, Hibernate, SOAP, AWS or Azure, Selenium or Playwright, Java, TestNG"
    ),
    location="Remote",
    work_mode="Remote",
    job_type="Full-time",
)

SDET_RESUME = """
Alex Candidate
alex@example.com | 555-0100

Professional Summary
Quality engineer focused on API and UI automation.

Technical Skills
Java, Selenium, TestNG, SQL, Git, Jenkins, REST

Professional Experience
Acme | SDET | Jan 2021 - Present
- Built Selenium and TestNG suites for Java services.
- Validated REST APIs and SQL data checks in CI with Jenkins.

Education
B.S. Computer Science
""".strip()


def _confirmed_row(
    skill: str,
    *,
    status="USER_CONFIRMED_PROFESSIONAL",
    evidence_type="Current employment",
    evidence_source="user_input",
    credit_desc="Used professionally in production systems.",
):
    return SimpleNamespace(
        id=hash(skill) % 10000,
        skill_name=skill,
        normalized_skill_name=normalize_skill_name(skill),
        evidence_status=status,
        evidence_type=evidence_type,
        evidence_source=evidence_source,
        requirement_type="MANDATORY",
        is_active=True,
        confirmed=True,
        usage_description=credit_desc,
        work_completed=credit_desc,
        user_input_description=credit_desc,
        ai_rewritten_description=None,
        experience_level="Advanced",
        experience_range="2+ years",
        supporting_context=None,
        confirmed_accurate=True,
        employer_name="Acme",
        role_title="SDET",
        project_name=None,
        certification_name=None,
        provider_name=None,
        start_date="2022-01",
        end_date=None,
        currently_using=True,
        years_experience="2",
        last_used="2026",
        related_technologies=None,
        impact_description=None,
        skill_modules=None,
        updated_at=None,
        resume_id=1,
        workflow_run_id="run-1",
        superseded_at=None,
    )


class CanonicalEvidencePropagationTests(unittest.TestCase):
    def test_scoring_evidence_keys_resolve_across_normalizers(self):
        rows = [_confirmed_row("Angular"), _confirmed_row("AWS")]
        evidence = to_scoring_evidence(rows)
        # Both normalize_skill_name and normalize_keyword forms must resolve.
        self.assertIn("angular", evidence)
        self.assertIn("Angular", evidence)
        self.assertIn("aws", evidence)
        self.assertIn("AWS", evidence)

    def test_accepted_evidence_removes_missing_and_raises_optimized_score(self):
        original = calculate_weighted_ats_score(SDET_RESUME, SDET_JOB)
        self.assertEqual(original.get("confirmed_skills_used") or [], [])
        original_score = original["match_percentage"]

        rows = [
            _confirmed_row("Angular"),
            _confirmed_row("Spring"),
            _confirmed_row("Hibernate"),
            _confirmed_row("SOAP"),
            _confirmed_row("AWS"),
        ]
        supported = build_canonical_supported_evidence(
            verified_resume_skills=["java", "selenium", "jenkins"],
            confirmation_rows=rows,
        )
        generated = (
            SDET_RESUME
            + "\n\nAdditional Experience\nAcme | SDET | 2022 - Present\n"
            + "- Built Angular, Spring, Hibernate, and SOAP integrations on AWS."
        )
        for skill in ("Angular", "Spring", "Hibernate", "SOAP", "AWS"):
            self.assertTrue(
                skill_supported_by_evidence(skill, supported["supported_skill_keys"]),
                msg=f"{skill} should be supported",
            )
        self.assertTrue(skill_supported_by_evidence("AWS or Azure", supported["supported_skill_keys"]))

        optimized = calculate_weighted_ats_score(
            generated,
            SDET_JOB,
            confirmed_skills=supported["scoring_evidence"],
        )
        # Original remains based only on uploaded resume when scored without confirmations.
        self.assertEqual(
            calculate_weighted_ats_score(SDET_RESUME, SDET_JOB)["match_percentage"],
            original_score,
        )
        for skill in ("Angular", "Spring", "Hibernate", "SOAP"):
            self.assertNotIn(skill, optimized["missing_mandatory_skills"])
        for label in optimized["missing_mandatory_skills"]:
            self.assertFalse(
                skill_supported_by_evidence(label, supported["supported_skill_keys"]),
                msg=f"Remaining gap still lists supported evidence: {label}",
            )
        self.assertGreater(optimized["match_percentage"], original_score)
        self.assertTrue(optimized.get("confirmed_skills_used") or optimized.get("matched_core_skills"))

    def test_or_group_not_double_counted_as_missing(self):
        rows = [_confirmed_row("AWS")]
        supported = build_canonical_supported_evidence(
            verified_resume_skills=[],
            confirmation_rows=rows,
        )
        generated = SDET_RESUME + "\n\nAdditional Experience\n- Deployed services on AWS."
        result = calculate_weighted_ats_score(
            generated, SDET_JOB, confirmed_skills=supported["scoring_evidence"]
        )
        missing = result["missing_mandatory_skills"]
        self.assertNotIn("AWS or Azure", missing)
        self.assertNotIn("AWS", missing)

    def test_removing_confirmation_restores_gap(self):
        with_aws = build_canonical_supported_evidence(
            verified_resume_skills=[],
            confirmation_rows=[_confirmed_row("AWS")],
        )
        without = build_canonical_supported_evidence(
            verified_resume_skills=[],
            confirmation_rows=[],
        )
        scored_with = calculate_weighted_ats_score(
            SDET_RESUME + "\n\nAdditional Experience\n- Used AWS in production.",
            SDET_JOB,
            confirmed_skills=with_aws["scoring_evidence"],
        )
        scored_without = calculate_weighted_ats_score(
            SDET_RESUME, SDET_JOB, confirmed_skills=without["scoring_evidence"]
        )
        self.assertNotIn("AWS or Azure", scored_with["missing_mandatory_skills"])
        self.assertTrue(
            any("AWS" in label or "Azure" in label for label in scored_without["missing_mandatory_skills"])
        )
        self.assertGreaterEqual(scored_with["match_percentage"], scored_without["match_percentage"])

    def test_docker_or_kubernetes_or_group(self):
        job = SimpleNamespace(
            id=100,
            title="Platform Engineer",
            company="Example",
            description="Required: Docker or Kubernetes, Linux, Python.",
            skills_required="Docker or Kubernetes, Linux, Python",
            location="Remote",
            work_mode="Remote",
            job_type="Full-time",
        )
        resume = (
            "Pat\nSkills\nPython, Linux, Docker\nExperience\n"
            "Acme | Engineer | 2020 - Present\n- Built Python services on Linux with Docker."
        )
        supported = build_canonical_supported_evidence(
            verified_resume_skills=[],
            confirmation_rows=[_confirmed_row("Docker")],
        )
        result = calculate_weighted_ats_score(
            resume, job, confirmed_skills=supported["scoring_evidence"]
        )
        self.assertNotIn("Docker or Kubernetes", result["missing_mandatory_skills"])
        self.assertTrue(skill_supported_by_evidence(
            "Docker or Kubernetes", supported["supported_skill_keys"]
        ))

    def test_verified_skill_alias_forms_supported_in_injection(self):
        support = select_supported_keywords(
            ["Angular", "Spring Boot", "AWS"],
            SDET_RESUME,
            verified_skills=["angular", "spring boot"],
        )
        self.assertIn("Angular", support["supportedKeywords"])
        self.assertIn("Spring Boot", support["supportedKeywords"])
        self.assertNotIn("Angular", support["unsupportedKeywords"])

    def test_deterministic_fallback_incorporates_confirmed_evidence(self):
        rows = [_confirmed_row("Angular"), _confirmed_row("AWS")]
        supported = build_canonical_supported_evidence(
            verified_resume_skills=["java", "selenium"],
            confirmation_rows=rows,
        )
        plan = plan_keyword_injection(
            SDET_RESUME,
            SDET_JOB,
            verified_skills=[*supported["verified_resume_skills"], *supported["generation_evidence"].keys()],
        )
        generated = generate_optimized_resume(
            SDET_RESUME, SDET_JOB, plan, supported["generation_evidence"]
        )
        plain = generated["plain_text"].lower()
        self.assertIn("angular", plain)
        self.assertIn("aws", plain)
        self.assertNotEqual(
            " ".join(generated["plain_text"].split()),
            " ".join(SDET_RESUME.split()),
        )

    def test_false_positive_technologies_stay_distinct(self):
        rows = [_confirmed_row("AWS")]
        supported = build_canonical_supported_evidence(
            verified_resume_skills=[],
            confirmation_rows=rows,
        )
        self.assertTrue(skill_supported_by_evidence("AWS", supported["supported_skill_keys"]))
        self.assertFalse(skill_supported_by_evidence("Azure", supported["supported_skill_keys"]))
        # OR group is satisfied by AWS alone
        self.assertTrue(skill_supported_by_evidence("AWS or Azure", supported["supported_skill_keys"]))

    def test_multi_user_evidence_keys_are_row_scoped_not_global(self):
        user_a = [_confirmed_row("Angular")]
        user_b = []
        a = build_canonical_supported_evidence(verified_resume_skills=[], confirmation_rows=user_a)
        b = build_canonical_supported_evidence(verified_resume_skills=[], confirmation_rows=user_b)
        self.assertTrue(skill_supported_by_evidence("Angular", a["supported_skill_keys"]))
        self.assertFalse(skill_supported_by_evidence("Angular", b["supported_skill_keys"]))


class OllamaGenerationMetadataTests(unittest.TestCase):
    def test_llm_success_metadata(self):
        client = MagicMock(spec=LLMClient)
        client.enabled = True
        client.provider = "openai_compatible"
        client.model = "llama3.1:8b"
        client.service_name = "ollama"
        client.config = {"temperature": 0.1}
        client.generate_json.return_value = {
            "generated_resume_plain_text": SDET_RESUME + "\n\nAdditional Angular coverage for SDET roles.",
            "rewritten_summary": "SDET with Angular and Selenium.",
            "rewritten_bullets": [],
            "keyword_evidence": [],
            "warnings": [],
            "factual_preservation_confirmed": True,
        }
        result = generate_with_llm_or_fallback(
            resume_text=SDET_RESUME,
            job=SDET_JOB,
            injection_plan={"supportedKeywords": ["Java"], "unsupportedKeywords": [], "placement": {}},
            structure={"section_order": [], "employments": [], "projects": []},
            confirmed_evidence={},
            client=client,
        )
        self.assertEqual(result["generation_method"], "llm")
        self.assertTrue(result["llm_used"])
        self.assertFalse(result["fallback_used"])
        self.assertIsNone(result["fallback_reason"])
        self.assertEqual(result["llm_model"], "llama3.1:8b")

    def test_llm_failure_falls_back_with_reason(self):
        client = MagicMock(spec=LLMClient)
        client.enabled = True
        client.provider = "openai_compatible"
        client.model = "llama3.1:8b"
        client.service_name = "ollama"
        client.config = {"temperature": 0.1}
        client.generate_json.side_effect = LLMGenerationError("connection_failed")
        result = generate_with_llm_or_fallback(
            resume_text=SDET_RESUME,
            job=SDET_JOB,
            injection_plan={
                "supportedKeywords": ["Java", "Selenium"],
                "unsupportedKeywords": ["Angular"],
                "placement": {"summary": ["Java"], "skills": ["Java", "Selenium"]},
            },
            structure={"section_order": [], "employments": [], "projects": []},
            confirmed_evidence={
                "angular": {
                    "skill": "Angular",
                    "evidence_type": "Current employment",
                    "evidence_status": "USER_CONFIRMED_PROFESSIONAL",
                    "usage_description": "Built Angular test harnesses.",
                    "employer_name": "Acme",
                }
            },
            client=client,
        )
        self.assertEqual(result["generation_method"], "deterministic_fallback")
        self.assertTrue(result["fallback_used"])
        self.assertEqual(result["fallback_reason"], "connection_failed")
        self.assertTrue(result["plain_text"].strip())

    def test_llm_not_configured_reason(self):
        client = MagicMock(spec=LLMClient)
        client.enabled = False
        client.provider = "none"
        client.model = "deterministic-fallback"
        client.service_name = None
        result = generate_with_llm_or_fallback(
            resume_text=SDET_RESUME,
            job=SDET_JOB,
            injection_plan={"supportedKeywords": ["Java"], "unsupportedKeywords": [], "placement": {}},
            structure={"section_order": [], "employments": [], "projects": []},
            client=client,
        )
        self.assertTrue(result["fallback_used"])
        self.assertEqual(result["fallback_reason"], "llm_not_configured")


if __name__ == "__main__":
    unittest.main()
