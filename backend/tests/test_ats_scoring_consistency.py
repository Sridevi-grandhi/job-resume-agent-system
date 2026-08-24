"""ATS scoring consistency: one authoritative result for gaps, coverage, and score."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.ats_scoring import build_score_view, calculate_weighted_ats_score
from app.services.skill_analysis_service import normalize_skill_name
from app.services.skill_confirmation_service import (
    build_canonical_supported_evidence,
    to_scoring_evidence,
)


def _job(*, description: str, skills_required: str = ""):
    return SimpleNamespace(
        id=1,
        title="QA Automation Engineer",
        company="Example",
        description=description,
        skills_required=skills_required or description,
        location="Remote",
        work_mode="Remote",
        job_type="Full-time",
    )


BASE_RESUME = """
Alex Candidate
alex@example.com

Professional Summary
Automation engineer focused on Java services and API testing.

Technical Skills
Java, Selenium, TestNG, SQL, Git, Jenkins, REST

Professional Experience
Acme | SDET | 2021 - Present
- Built Selenium and TestNG automation for Java microservices.
- Validated REST APIs and SQL data checks in Jenkins CI pipelines.
""".strip()


def _confirmed_row(skill: str):
    return SimpleNamespace(
        id=hash(skill) % 10000,
        skill_name=skill,
        normalized_skill_name=normalize_skill_name(skill),
        evidence_status="USER_CONFIRMED_PROFESSIONAL",
        evidence_type="Current employment",
        evidence_source="user_input",
        requirement_type="MANDATORY",
        is_active=True,
        confirmed=True,
        usage_description=f"Used {skill} professionally in production systems.",
        work_completed=f"Used {skill} professionally in production systems.",
        user_input_description=f"Used {skill} professionally in production systems.",
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


class AtsScoringConsistencyTests(unittest.TestCase):
    def test_original_score_ignores_confirmed_evidence(self):
        job = _job(description="Required: Java, Selenium, Cucumber, Rest Assured, Healthcare.")
        original = calculate_weighted_ats_score(BASE_RESUME, job)
        supported = build_canonical_supported_evidence(
            verified_resume_skills=["java", "selenium"],
            confirmation_rows=[_confirmed_row("Cucumber"), _confirmed_row("Rest Assured")],
        )
        with_confirmed_on_original = calculate_weighted_ats_score(
            BASE_RESUME,
            job,
            confirmed_skills=supported["scoring_evidence"],
        )
        self.assertEqual(original["match_percentage"], with_confirmed_on_original["match_percentage"])
        self.assertEqual(original.get("confirmed_skills_used") or [], [])

    def test_confirmed_evidence_requires_presence_in_scored_document(self):
        job = _job(description="Required: Java, Selenium, Cucumber, Rest Assured, Healthcare.")
        supported = build_canonical_supported_evidence(
            verified_resume_skills=["java", "selenium"],
            confirmation_rows=[_confirmed_row("Cucumber"), _confirmed_row("Rest Assured")],
        )
        without_in_doc = calculate_weighted_ats_score(
            BASE_RESUME,
            job,
            confirmed_skills=supported["scoring_evidence"],
        )
        generated = (
            BASE_RESUME
            + "\n\nAdditional Experience\nAcme | SDET | 2022 - Present\n"
            + "- Delivered BDD automation with Cucumber and Rest Assured for healthcare-adjacent APIs."
        )
        with_in_doc = calculate_weighted_ats_score(
            generated,
            job,
            confirmed_skills=supported["scoring_evidence"],
        )
        self.assertIn("Cucumber", with_in_doc.get("matched_core_skills", []) + with_in_doc.get("matched_keywords", []))
        self.assertIn("Cucumber", without_in_doc["missing_mandatory_skills"])
        self.assertGreater(with_in_doc["mandatory_satisfied_count"], without_in_doc["mandatory_satisfied_count"])

    def test_mandatory_coverage_matches_remaining_gaps(self):
        job = _job(description="Required: Java, Selenium, Cucumber, Rest Assured, Healthcare.")
        generated = (
            BASE_RESUME
            + "\n\nAdditional Experience\n- Implemented Cucumber and Rest Assured suites for Java services."
        )
        supported = build_canonical_supported_evidence(
            verified_resume_skills=["java", "selenium"],
            confirmation_rows=[_confirmed_row("Cucumber"), _confirmed_row("Rest Assured")],
        )
        score = calculate_weighted_ats_score(generated, job, confirmed_skills=supported["scoring_evidence"])
        view = build_score_view(score)
        mandatory = view["mandatory_requirements"]
        missing = view["remaining_gaps"]["unsupported"]
        self.assertEqual(mandatory["covered"] + len(mandatory["missing"]), mandatory["total"])
        self.assertEqual(len(missing), len(mandatory["missing"]))
        self.assertIn("Healthcare", missing)
        self.assertNotIn("Cucumber", missing)
        self.assertNotIn("Rest Assured", missing)

    def test_build_score_view_keyword_vs_mandatory_are_distinct_fields(self):
        job = _job(description="Required: Java, Selenium, Cucumber. Preferred: Kafka.")
        score = calculate_weighted_ats_score(BASE_RESUME, job)
        view = build_score_view(score)
        self.assertIn("keyword_coverage", view)
        self.assertIn("mandatory_requirements", view)
        self.assertIsNotNone(view["keyword_coverage"]["required"])
        self.assertIsNotNone(view["mandatory_requirements"]["total"])


if __name__ == "__main__":
    unittest.main()
