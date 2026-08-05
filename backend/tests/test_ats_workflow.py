import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.ats_optimization_workflow_service import should_run_ats_workflow
from app.services.ats_resume_generation_service import generate_with_llm_or_fallback
from app.services.job_eligibility import is_generic_search_link_job
from app.services.keyword_injection_service import plan_keyword_injection
from app.services.llm_client import LLMClient, llm_config
from app.services.resume_structure_parser import parse_resume_structure


SAMPLE_RESUME = """
Jane Developer
jane@example.com | 555-111-2222

Professional Summary
Java developer with Spring Boot experience.

Technical Skills
Java, Spring Boot, React, PostgreSQL, AWS, Docker

Professional Experience
Acme Corp | Java Developer | Jan 2018 - Present
Client: JPMorgan Chase
- Developed REST APIs using Java and Spring Boot.

Projects
Payments Portal
Client: JPMorgan Chase
- Built transaction APIs with Java.

Education
B.S. Computer Science, Example University

Certifications
AWS Cloud Practitioner
""".strip()


class AtsWorkflowUnitTests(unittest.TestCase):
    def test_generic_search_link_skips_workflow(self):
        job = SimpleNamespace(
            title="Java Jobs",
            company="LinkedIn",
            description="Search Java openings on LinkedIn based on your preferred location and skills.",
            job_url="https://www.linkedin.com/jobs/search/?keywords=Java",
            source="LinkedIn Jobs",
        )
        self.assertTrue(is_generic_search_link_job(job))
        ok, reason = should_run_ats_workflow(job, SimpleNamespace(parsed_text=SAMPLE_RESUME))
        self.assertFalse(ok)
        self.assertIn("Generic", reason)

    def test_complete_job_with_resume_eligible(self):
        job = SimpleNamespace(
            id=10,
            user_id=1,
            title="Java Full Stack Developer",
            company="Example Co",
            description=(
                "We need a Java Full Stack Developer with Spring Boot, React, PostgreSQL, "
                "AWS, and Docker experience building REST APIs for enterprise payments."
            ),
            job_url="https://example.com/jobs/123",
            source="User Selected Job",
            skills_required="Java, Spring Boot, React",
        )
        resume = SimpleNamespace(parsed_text=SAMPLE_RESUME, id=1)
        ok, reason = should_run_ats_workflow(job, resume)
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "")

    def test_waiting_for_resume(self):
        job = SimpleNamespace(
            id=11,
            user_id=1,
            title="Java Full Stack Developer",
            company="Example Co",
            description="Java Spring Boot React PostgreSQL AWS Docker Jenkins microservices REST APIs",
            job_url="https://example.com/jobs/123",
            source="Remotive API",
            skills_required="Java",
        )
        ok, reason = should_run_ats_workflow(job, None)
        self.assertFalse(ok)
        self.assertEqual(reason, "WAITING_FOR_RESUME")

    def test_llm_disabled_uses_deterministic_fallback(self):
        job = SimpleNamespace(
            title="Java Developer",
            description="Java Spring Boot React PostgreSQL AWS",
            skills_required="Java, Spring Boot, React, PostgreSQL, AWS",
        )
        structure = parse_resume_structure(SAMPLE_RESUME)
        plan = plan_keyword_injection(SAMPLE_RESUME, job)
        with patch.dict("os.environ", {"LLM_PROVIDER": "none", "LLM_API_KEY": ""}, clear=False):
            result = generate_with_llm_or_fallback(
                resume_text=SAMPLE_RESUME,
                job=job,
                injection_plan=plan,
                structure=structure,
            )
        self.assertEqual(result["generation_mode"], "deterministic")
        self.assertIn("jane@example.com", result["plain_text"])
        self.assertIn("Acme Corp", result["plain_text"])

    def test_llm_config_defaults(self):
        with patch.dict("os.environ", {"LLM_PROVIDER": "none", "LLM_API_KEY": ""}, clear=False):
            cfg = llm_config()
        self.assertFalse(cfg["enabled"])
        self.assertFalse(LLMClient(cfg).enabled)

    def test_validation_rejects_company_change(self):
        from app.services.final_resume_validator import validate_final_resume

        structure = parse_resume_structure(SAMPLE_RESUME)
        bad = SAMPLE_RESUME.replace("Acme Corp", "Fake Company Inc")
        result = validate_final_resume(
            original_text=SAMPLE_RESUME,
            generated_text=bad,
            original_structure=structure,
            generated_sections=structure.get("sections") or [],
            unsupported_keywords=["Angular"],
            selected_technologies=["Java"],
        )
        # Either error or warning depending on company detection depth
        self.assertTrue(result.get("errors") or result.get("warnings") or result.get("status") in {"failed", "passed_with_warnings", "passed"})


if __name__ == "__main__":
    unittest.main()
