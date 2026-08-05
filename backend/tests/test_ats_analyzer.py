import unittest
from types import SimpleNamespace

from app.services.ats_scoring import calculate_weighted_ats_score
from app.services.client_domain_validator import detect_client_domain, validate_client_domain
from app.services.job_eligibility import job_is_analyzable
from app.services.keyword_injection_service import plan_keyword_injection
from app.services.keyword_utils import contains_keyword
from app.services.resume_generator import FAKE_METRIC_PATTERN, generate_optimized_resume
from app.services.resume_structure_parser import parse_resume_structure
from app.services.technology_consistency_validator import validate_technology_consistency
from app.services.technology_timeline_validator import filter_technologies_for_period


SAMPLE_RESUME = """
Jane Developer
jane@example.com | 555-111-2222 | linkedin.com/in/jane | github.com/jane

Professional Summary
Java Full Stack Developer with experience building enterprise applications.

Technical Skills
Java, Spring Boot, React, PostgreSQL, AWS, Docker, Jenkins, Agile

Professional Experience
Acme Corp | Java Developer | Jan 2018 - Present
Client: JPMorgan Chase
- Developed REST APIs using Java and Spring Boot for account workflows.
- Built React components for customer portals.
- Deployed services with Docker and Jenkins on AWS.

Projects
Payments Portal
Client: JPMorgan Chase
- Implemented transaction workflows with Java and PostgreSQL.

Education
B.S. Computer Science, Example University

Certifications
AWS Cloud Practitioner
""".strip()


class AtsAnalyzerUnitTests(unittest.TestCase):
    def test_java_not_matched_inside_javascript(self):
        self.assertFalse(contains_keyword("Experienced in JavaScript and Node.js", "Java"))
        self.assertTrue(contains_keyword("Built services with Java and Spring Boot", "Java"))

    def test_technology_conflicts_prefer_resume_supported(self):
        result = validate_technology_consistency(
            jd_keywords=["Java", "Spring Boot", "React", "Angular", "AWS", "Azure", "Docker", "Jenkins"],
            resume_text=SAMPLE_RESUME,
            supported_keywords=["Java", "Spring Boot", "React", "AWS", "Docker", "Jenkins"],
        )
        self.assertIn("React", result["selectedTechnologies"])
        self.assertIn("Angular", result["rejectedTechnologies"])
        self.assertIn("AWS", result["selectedTechnologies"])
        self.assertIn("Azure", result["rejectedTechnologies"])

    def test_timeline_blocks_fastapi_in_2015(self):
        allowed, blocked = filter_technologies_for_period(
            ["FastAPI", "GitHub Actions", "LangChain", "Java"],
            2015,
            2016,
        )
        self.assertIn("Java", allowed)
        self.assertIn("FastAPI", blocked)
        self.assertIn("GitHub Actions", blocked)
        self.assertIn("LangChain", blocked)

    def test_client_domain_banking(self):
        domain = detect_client_domain("JPMorgan Chase", "account transaction workflows", "Acme")
        self.assertEqual(domain, "banking")
        validation = validate_client_domain(
            employments=[{"client": "JPMorgan Chase", "company": "Acme", "bullets": ["payments"], "raw_lines": []}],
            projects=[],
            generated_text="Developed healthcare claims-processing workflows.",
        )
        self.assertTrue(validation["clientDomainWarnings"] or validation["invalidProjectContentRemoved"])

    def test_no_fake_metrics_introduced(self):
        job = SimpleNamespace(
            title="Java Full Stack Developer",
            description="Need Java, Spring Boot, React, Angular, PostgreSQL, AWS, Azure, Docker, Kubernetes, Jenkins",
            skills_required="Java, Spring Boot, React, Angular, PostgreSQL, AWS, Azure, Docker, Kubernetes, Jenkins",
        )
        plan = plan_keyword_injection(SAMPLE_RESUME, job)
        generated = generate_optimized_resume(SAMPLE_RESUME, job, plan)
        if not FAKE_METRIC_PATTERN.search(SAMPLE_RESUME):
            self.assertIsNone(FAKE_METRIC_PATTERN.search(generated["plain_text"]))

    def test_structure_order_preserved(self):
        structure = parse_resume_structure(SAMPLE_RESUME)
        self.assertEqual(
            structure["section_order"],
            [
                "Professional Summary",
                "Technical Skills",
                "Professional Experience",
                "Projects",
                "Education",
                "Certifications",
            ],
        )
        job = SimpleNamespace(
            title="Java Full Stack Developer",
            description="Java Spring Boot React PostgreSQL AWS Docker Jenkins",
            skills_required="Java, Spring Boot, React, PostgreSQL, AWS, Docker, Jenkins",
        )
        plan = plan_keyword_injection(SAMPLE_RESUME, job)
        generated = generate_optimized_resume(SAMPLE_RESUME, job, plan)
        self.assertEqual(
            [s["heading"] for s in generated["sections"]],
            structure["section_order"],
        )
        self.assertIn("jane@example.com", generated["plain_text"])
        self.assertIn("Acme Corp", generated["plain_text"])
        self.assertIn("Jan 2018 - Present", generated["plain_text"])
        self.assertIn("B.S. Computer Science, Example University", generated["plain_text"])

    def test_score_bounds_and_improvement(self):
        job = SimpleNamespace(
            title="Java Full Stack Developer",
            description="Java Spring Boot React PostgreSQL AWS Docker Jenkins microservices",
            skills_required="Java, Spring Boot, React, PostgreSQL, AWS, Docker, Jenkins",
        )
        original = calculate_weighted_ats_score(SAMPLE_RESUME, job)
        plan = plan_keyword_injection(SAMPLE_RESUME, job)
        generated = generate_optimized_resume(SAMPLE_RESUME, job, plan)
        optimized = calculate_weighted_ats_score(generated["plain_text"], job)
        self.assertGreaterEqual(original["match_percentage"], 0)
        self.assertLessEqual(original["match_percentage"], 100)
        self.assertGreaterEqual(optimized["match_percentage"], 0)
        self.assertLessEqual(optimized["match_percentage"], 100)
        improvement = optimized["match_percentage"] - original["match_percentage"]
        self.assertEqual(improvement, optimized["match_percentage"] - original["match_percentage"])

    def test_unsupported_skills_reported(self):
        job = SimpleNamespace(
            title="Java Developer",
            description="Requires Java and COBOL and Fortran",
            skills_required="Java, COBOL, Fortran",
        )
        plan = plan_keyword_injection(SAMPLE_RESUME, job)
        self.assertIn("Java", plan["supportedKeywords"])
        # Unknown catalog techs may be skipped; ensure unsupported path doesn't invent them in resume
        generated = generate_optimized_resume(SAMPLE_RESUME, job, plan)
        self.assertNotIn("COBOL", generated["plain_text"])

    def test_generic_search_link_rejected(self):
        job = SimpleNamespace(
            id=1,
            user_id=3,
            title="Java Jobs on LinkedIn",
            company="LinkedIn Jobs",
            description="Search Java openings on LinkedIn based on your preferred location and skills.",
            job_url="https://www.linkedin.com/jobs/search/?keywords=Java",
            source="LinkedIn Jobs",
        )
        ok, reason = job_is_analyzable(job)
        self.assertFalse(ok)
        self.assertIn("complete job description", reason.lower())


if __name__ == "__main__":
    unittest.main()
