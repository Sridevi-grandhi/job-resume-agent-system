"""Regression: deterministic optimizer must change content when JD levers exist."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.ats_scoring import calculate_weighted_ats_score
from app.services.keyword_injection_service import plan_keyword_injection
from app.services.resume_generator import generate_optimized_resume
from app.services.resume_structure_parser import parse_resume_structure


class MeaningfulOptimizationTests(unittest.TestCase):
    RESUME = """
Jane Candidate
jane@example.com

Professional Summary
DevOps engineer with cloud and CI/CD experience.

Technical Skills
AWS, Docker, Kubernetes, Jenkins, Linux, Git, Terraform

Professional Experience
Acme | Senior DevOps Engineer | Jan 2020 - Present
- Responsible for developing CI/CD pipelines.
- Worked on container deployments.
- Helped with cloud infrastructure.

Education
B.S. Computer Science
"""

    def _job(self):
        return SimpleNamespace(
            id=1,
            title="Senior DevOps Engineer",
            company="Example",
            description=(
                "Required: AWS, Kubernetes, Docker, Linux, CI/CD, Terraform, Jenkins. "
                "Preferred: Datadog."
            ),
            skills_required="AWS, Kubernetes, Docker, Linux, CI/CD, Terraform, Jenkins",
            location="Remote",
            work_mode="Remote",
            job_type="Full-time",
        )

    def test_deterministic_generator_changes_content(self):
        job = self._job()
        structure = parse_resume_structure(self.RESUME)
        plan = plan_keyword_injection(
            resume_text=self.RESUME,
            job=job,
            employments=structure.get("employments") or [],
            projects=structure.get("projects") or [],
            verified_skills=["AWS", "Docker", "Kubernetes", "Jenkins", "Linux", "Terraform"],
        )
        generated = generate_optimized_resume(self.RESUME, job, plan, confirmed_evidence={})
        plain = (generated.get("plain_text") or "").strip()
        self.assertTrue(plain)
        self.assertNotEqual(
            " ".join(self.RESUME.split()).lower(),
            " ".join(plain.split()).lower(),
        )
        self.assertIn("Professional Summary", plain)
        self.assertNotIn("Responsible for developing", plain)

    def test_same_scoring_engine_for_original_and_optimized(self):
        job = self._job()
        structure = parse_resume_structure(self.RESUME)
        plan = plan_keyword_injection(
            resume_text=self.RESUME,
            job=job,
            employments=structure.get("employments") or [],
            projects=structure.get("projects") or [],
            verified_skills=["AWS", "Docker", "Kubernetes", "Jenkins", "Linux", "Terraform"],
        )
        generated = generate_optimized_resume(
            self.RESUME,
            job,
            plan,
            confirmed_evidence={
                "terraform": {
                    "evidence_status": "USER_CONFIRMED_PROFESSIONAL",
                    "evidence_type": "Current employment",
                    "evidence_source": "user_input",
                    "usage_description": "Provisioned AWS infrastructure with Terraform modules.",
                    "employer_name": "Acme",
                }
            },
        )
        original = calculate_weighted_ats_score(self.RESUME, job)
        optimized = calculate_weighted_ats_score(
            generated["plain_text"],
            job,
            evidence_source_text=self.RESUME,
            confirmed_skills={
                "terraform": {
                    "credit": 0.85,
                    "evidence_status": "USER_CONFIRMED_PROFESSIONAL",
                    "evidence_type": "Current employment",
                    "usage_description": "Provisioned AWS infrastructure with Terraform modules.",
                }
            },
        )
        self.assertIn("match_percentage", original)
        self.assertIn("match_percentage", optimized)
        self.assertNotEqual(
            " ".join(self.RESUME.split()).lower(),
            " ".join(generated["plain_text"].split()).lower(),
        )

    def test_skill_categories_are_dynamic_not_role_hardcoded(self):
        job = SimpleNamespace(
            id=2,
            title="Python Data Engineer",
            company="Example",
            description="Required: Python, Spark, Airflow, SQL, AWS.",
            skills_required="Python, Spark, Airflow, SQL, AWS",
            location="Remote",
            work_mode="Remote",
            job_type="Full-time",
        )
        resume = """
Alex
Professional Summary
Data engineer with Python pipelines.
Technical Skills
Python, Spark, Airflow, SQL, AWS, Pandas
Professional Experience
DataCo | Data Engineer | 2021 - Present
- Built ETL pipelines.
"""
        structure = parse_resume_structure(resume)
        plan = plan_keyword_injection(
            resume_text=resume,
            job=job,
            employments=structure.get("employments") or [],
            projects=[],
            verified_skills=["Python", "Spark", "Airflow", "SQL", "AWS"],
        )
        generated = generate_optimized_resume(resume, job, plan)
        plain = generated["plain_text"]
        self.assertIn("Python", plain)
        self.assertNotEqual(" ".join(resume.split()).lower(), " ".join(plain.split()).lower())


if __name__ == "__main__":
    unittest.main()
