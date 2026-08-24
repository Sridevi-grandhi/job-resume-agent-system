"""Regression tests for confirmation workflow readiness / stale INCOMPLETE handling."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.ats_scoring import calculate_weighted_ats_score, extract_structured_requirements
from app.services.requirement_extraction_service import group_canonical_score_evidence
from app.services.skill_confirmation_service import (
    evaluate_generation_readiness,
    reconcile_confirmations_with_analysis,
    summarize_active_evidence,
)


def _job(title, description, skills_required=""):
    return SimpleNamespace(
        id=1, title=title, company="Example", description=description,
        skills_required=skills_required, location="Remote", work_mode="Remote", job_type="Full-time",
    )


def _row(*, skill, status, requirement_type="MANDATORY", resume_id=8, is_active=True, workflow_run_id="run-1"):
    return SimpleNamespace(
        id=1,
        skill_name=skill,
        normalized_skill_name=skill,
        evidence_status=status,
        requirement_type=requirement_type,
        resume_id=resume_id,
        is_active=is_active,
        workflow_run_id=workflow_run_id,
        superseded_at=None,
        evidence_type=None,
        evidence_source="user_selection",
        usage_description=None,
        user_input_description=None,
        ai_rewritten_description=None,
        experience_level=None,
        experience_range=None,
        supporting_context=None,
        confirmed_accurate=None,
        employer_name=None,
        role_title=None,
        project_name=None,
        certification_name=None,
        provider_name=None,
        start_date=None,
        end_date=None,
        currently_using=None,
        years_experience=None,
        last_used=None,
        work_completed=None,
        related_technologies=None,
        impact_description=None,
        skill_modules=None,
        confirmed=False,
        updated_at=None,
    )


class GenerationReadinessTests(unittest.TestCase):
    RESUME = """
Jane Candidate
Professional Summary
DevOps engineer with AWS, Kubernetes, Docker, Jenkins, GitHub Actions, Linux, CI/CD.
Technical Skills
AWS, Kubernetes, Docker, Jenkins, GitHub Actions, Linux, CI/CD, Terraform, GitLab CI, Datadog
Professional Experience
Acme | Senior DevOps Engineer | Jan 2020 - Present
- Provisioned AWS infrastructure and container platforms with Kubernetes and Docker.
- Built CI/CD pipelines using Jenkins and GitHub Actions on Linux servers.
"""

    JOB = _job(
        "Senior DevOps Engineer",
        (
            "Required Skills & Experience\n"
            "AWS, Kubernetes, Docker, Linux, CI/CD pipelines (GitHub Actions, GitLab CI, or Jenkins), "
            "Terraform.\n"
            "Desired Skills\n"
            "Datadog."
        ),
        "AWS, Kubernetes, Docker, Linux, CI/CD, Jenkins, GitHub Actions, Terraform",
    )

    def _grouped(self):
        score = calculate_weighted_ats_score(self.RESUME, self.JOB)
        return group_canonical_score_evidence(score, job_id=1), score

    def test_verified_skills_never_need_confirmation(self):
        grouped, _ = self._grouped()
        verified = {item["normalized_skill"] for item in grouped["verified_resume_skills"]}
        for skill in ("aws", "kubernetes", "docker", "jenkins", "github actions", "linux", "ci/cd"):
            self.assertIn(skill, verified)
            self.assertNotIn(skill, {i["normalized_skill"] for i in grouped["weak_or_indirect_skills"]})
            self.assertNotIn(skill, {i["normalized_skill"] for i in grouped["missing_mandatory_skills"]})

    def test_ci_cd_or_group_satisfied_by_jenkins_or_github_actions(self):
        reqs = extract_structured_requirements(self.JOB)
        or_groups = reqs.get("or_groups") or []
        self.assertTrue(any({"Jenkins", "GitHub Actions", "GitLab CI"} <= set(group["skills"]) or
                            {"Jenkins", "GitHub Actions"} <= set(group["skills"])
                            for group in or_groups) or any(
            "GitLab CI" in group["skills"] and ("Jenkins" in group["skills"] or "GitHub Actions" in group["skills"])
            for group in or_groups
        ), or_groups)
        grouped, _ = self._grouped()
        confirmation = {
            item["normalized_skill"]
            for item in grouped["weak_or_indirect_skills"] + grouped["missing_mandatory_skills"]
        }
        self.assertNotIn("gitlab ci", confirmation)

    def test_ci_cd_fragments_not_duplicated(self):
        reqs = extract_structured_requirements(self.JOB)
        required = set(reqs["required_core_skills"])
        self.assertIn("CI/CD", required)
        self.assertNotIn("CI", required)
        self.assertNotIn("CD", required)

    def test_preferred_datadog_does_not_block(self):
        grouped, _ = self._grouped()
        readiness = evaluate_generation_readiness(grouped, [])
        warned = {item["normalized_skill"] for item in readiness["generation_warnings"]}
        self.assertNotIn("datadog", warned)
        self.assertTrue(readiness["can_generate_resume"])

    def test_required_incomplete_warns_but_does_not_block(self):
        grouped, _ = self._grouped()
        # Force Terraform into mandatory confirmation queue for the readiness check.
        grouped = {
            **grouped,
            "weak_or_indirect_skills": [
                {
                    "skill": "Terraform",
                    "normalized_skill": "terraform",
                    "requirement_type": "MANDATORY",
                    "resume_status": "WEAK",
                }
            ],
            "missing_mandatory_skills": [],
            "missing_preferred_skills": [
                {
                    "skill": "Datadog",
                    "normalized_skill": "datadog",
                    "requirement_type": "PREFERRED",
                    "resume_status": "MISSING",
                }
            ],
        }
        warned = evaluate_generation_readiness(grouped, [])
        self.assertTrue(warned["can_generate_resume"])
        self.assertEqual(warned["blocking_reasons"], [])
        self.assertEqual(warned["generation_warnings"][0]["skill"], "Terraform")
        self.assertEqual(warned["generation_warnings"][0]["severity"], "warning")

        resolved = evaluate_generation_readiness(
            grouped,
            [_row(skill="Terraform", status="USER_CONFIRMED_PROFESSIONAL")],
        )
        self.assertTrue(resolved["can_generate_resume"])
        self.assertEqual(resolved["blocking_reasons"], [])
        self.assertEqual(resolved["generation_warnings"], [])

    def test_healthcare_incomplete_does_not_block_generation(self):
        grouped = {
            "verified_resume_skills": [{"skill": "Java", "normalized_skill": "java"}],
            "weak_or_indirect_skills": [],
            "missing_mandatory_skills": [
                {
                    "skill": "Healthcare",
                    "normalized_skill": "healthcare",
                    "requirement_type": "MANDATORY",
                    "resume_status": "MISSING",
                }
            ],
            "missing_preferred_skills": [],
        }
        incomplete = evaluate_generation_readiness(
            grouped,
            [_row(skill="Healthcare", status="INCOMPLETE")],
        )
        self.assertTrue(incomplete["can_generate_resume"])
        self.assertEqual(incomplete["blocking_reasons"], [])
        self.assertEqual(incomplete["generation_warnings"][0]["skill"], "Healthcare")
        self.assertEqual(incomplete["generation_warnings"][0]["code"], "INCOMPLETE_EVIDENCE")

        missing = evaluate_generation_readiness(grouped, [])
        self.assertTrue(missing["can_generate_resume"])
        self.assertEqual(missing["generation_warnings"][0]["skill"], "Healthcare")

        denied = evaluate_generation_readiness(
            grouped,
            [_row(skill="Healthcare", status="REJECTED")],
        )
        self.assertTrue(denied["can_generate_resume"])
        self.assertEqual(denied["generation_warnings"][0]["code"], "NOT_POSSESSED")

    def test_multiple_unsupported_required_still_allows_generation(self):
        grouped = {
            "verified_resume_skills": [],
            "weak_or_indirect_skills": [],
            "missing_mandatory_skills": [
                {"skill": "Angular", "normalized_skill": "angular", "requirement_type": "MANDATORY"},
                {"skill": "AWS", "normalized_skill": "aws", "requirement_type": "MANDATORY"},
                {"skill": "Healthcare", "normalized_skill": "healthcare", "requirement_type": "MANDATORY"},
            ],
            "missing_preferred_skills": [],
        }
        readiness = evaluate_generation_readiness(grouped, [])
        self.assertTrue(readiness["can_generate_resume"])
        self.assertEqual(
            {item["skill"] for item in readiness["generation_warnings"]},
            {"Angular", "AWS", "Healthcare"},
        )
        self.assertEqual(readiness["blocking_reasons"], [])

    def test_historical_incomplete_for_verified_skill_does_not_block(self):
        grouped, _ = self._grouped()
        stale = [
            _row(skill="Docker", status="INCOMPLETE"),
            _row(skill="Kubernetes", status="INCOMPLETE"),
            _row(skill="CI/CD", status="INCOMPLETE"),
            _row(skill="CI", status="INCOMPLETE"),
            _row(skill="CD", status="INCOMPLETE"),
        ]
        readiness = evaluate_generation_readiness(grouped, stale)
        warned = {item["skill"] for item in readiness["generation_warnings"]}
        self.assertNotIn("Docker", warned)
        self.assertNotIn("Kubernetes", warned)
        self.assertNotIn("CI/CD", warned)
        self.assertTrue(readiness["can_generate_resume"])

    def test_reconcile_marks_stale_incomplete_obsolete(self):
        grouped, _ = self._grouped()
        row = _row(skill="Docker", status="INCOMPLETE")
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [row]
        reconcile_confirmations_with_analysis(
            db, user_id=1, job_id=1, resume_id=8, grouped_analysis=grouped,
        )
        self.assertEqual(row.evidence_status, "OBSOLETE")
        db.commit.assert_called()

    def test_step4_summary_excludes_obsolete_and_verified_incomplete(self):
        grouped = {
            "verified_resume_skills": [{"skill": "Docker", "normalized_skill": "docker"}],
            "weak_or_indirect_skills": [
                {"skill": "Terraform", "normalized_skill": "terraform", "requirement_type": "MANDATORY"}
            ],
            "missing_mandatory_skills": [],
            "missing_preferred_skills": [],
        }
        rows = [
            _row(skill="Docker", status="INCOMPLETE"),
            _row(skill="Terraform", status="USER_CONFIRMED_PROFESSIONAL"),
            _row(skill="Old", status="OBSOLETE", is_active=False),
            _row(skill="CI", status="INCOMPLETE"),
            _row(skill="CD", status="INCOMPLETE"),
            _row(skill="AWS", status="INCOMPLETE"),
        ]
        summary = summarize_active_evidence(grouped, rows)
        self.assertEqual(summary["counts"]["incomplete"], 0)
        self.assertEqual(summary["counts"]["incomplete"], len(summary["incomplete"]))
        self.assertEqual(summary["counts"]["user_confirmed_with_evidence"], 1)
        names = {item["skill"] for item in summary["evidence"]}
        self.assertIn("Terraform", names)
        self.assertNotIn("Docker", names)
        self.assertNotIn("Old", names)
        self.assertNotIn("CI", names)
        self.assertNotIn("CD", names)
        self.assertNotIn("AWS", names)

    def test_step4_count_matches_detailed_incomplete_rows(self):
        grouped = {
            "verified_resume_skills": [{"skill": "Docker", "normalized_skill": "docker"}],
            "weak_or_indirect_skills": [
                {"skill": "Terraform", "normalized_skill": "terraform", "requirement_type": "MANDATORY"}
            ],
            "missing_mandatory_skills": [],
            "missing_preferred_skills": [],
        }
        rows = [
            _row(skill="Docker", status="INCOMPLETE"),
            _row(skill="Terraform", status="INCOMPLETE"),
            _row(skill="CI/CD", status="INCOMPLETE"),
        ]
        summary = summarize_active_evidence(grouped, rows)
        self.assertEqual(summary["counts"]["incomplete"], 1)
        self.assertEqual(len(summary["incomplete"]), summary["counts"]["incomplete"])
        self.assertEqual(summary["incomplete"][0]["skill"], "Terraform")

    def test_historical_incomplete_never_reused_across_workflow_runs(self):
        grouped, _ = self._grouped()
        stale = [_row(skill="Docker", status="INCOMPLETE", workflow_run_id="old-run", is_active=True)]
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = stale
        reconcile_confirmations_with_analysis(
            db,
            user_id=1,
            job_id=1,
            resume_id=8,
            grouped_analysis=grouped,
            workflow_run_id="new-run",
        )
        self.assertFalse(stale[0].is_active)
        self.assertEqual(stale[0].evidence_status, "OBSOLETE")

    def test_preferred_unresolved_lists_optional_confirmations(self):
        grouped = {
            "verified_resume_skills": [],
            "weak_or_indirect_skills": [],
            "missing_mandatory_skills": [],
            "missing_preferred_skills": [
                {"skill": "Datadog", "normalized_skill": "datadog", "requirement_type": "PREFERRED"}
            ],
        }
        readiness = evaluate_generation_readiness(grouped, [])
        self.assertTrue(readiness["can_generate_resume"])
        self.assertEqual(readiness["current_optional_confirmations"], ["Datadog"])
        self.assertEqual(readiness["current_required_confirmations"], [])

    def test_python_or_bash_satisfied_by_one(self):
        job = _job(
            "Python Backend Developer",
            "Required: Python or Bash, Django, PostgreSQL.",
            "Python, Bash, Django, PostgreSQL",
        )
        resume = """
Jane
Technical Skills
Python, Django, PostgreSQL
Professional Experience
Acme | Backend Developer | 2020 - Present
- Built Django APIs in Python backed by PostgreSQL.
"""
        score = calculate_weighted_ats_score(resume, job)
        grouped = group_canonical_score_evidence(score, 1)
        confirmation = {
            item["normalized_skill"]
            for item in grouped["weak_or_indirect_skills"] + grouped["missing_mandatory_skills"]
        }
        self.assertNotIn("bash", confirmation)
        readiness = evaluate_generation_readiness(grouped, [])
        self.assertNotIn("bash", {item["normalized_skill"] for item in readiness["blocking_reasons"]})


if __name__ == "__main__":
    unittest.main()
