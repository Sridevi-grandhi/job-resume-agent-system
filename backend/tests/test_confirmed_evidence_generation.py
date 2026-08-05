import unittest
from types import SimpleNamespace

from app.services.ats_resume_generation_service import _evidence_claims, _user_evidence_claims
from app.services.keyword_injection_service import plan_keyword_injection
from app.services.requirement_extraction_service import extract_job_requirement_analysis, group_requirements_vs_resume
from app.services.resume_generator import generate_optimized_resume
from app.services.resume_structure_parser import parse_resume_structure


JAVA_JOB = SimpleNamespace(
    id=25,
    title="Java Spring Boot Developer",
    company="Donato Technologies Inc",
    description=(
        "Must have strong experience with Java and Spring Boot. Required: Microservices, "
        "React, AWS, PostgreSQL. Preferred: Kubernetes. Nice to have: Kafka. Minimum 3 years experience."
    ),
    skills_required="Java, Spring Boot, Microservices, React, AWS, PostgreSQL",
    location="Remote",
    work_mode="Remote",
    job_type="Contract",
)

RESUME_NO_SPRING_BOOT = """
Jane Candidate
jane@example.com | 555-000-1111

Professional Summary
Java developer.

Technical Skills
Java, React, AWS, PostgreSQL

Professional Experience
Acme Corp | Java Developer | Jan 2020 - Present
- Built Java services and React interfaces on AWS with PostgreSQL.

Education
B.S. Computer Science, Example University
""".strip()


class KeywordInjectionVerifiedSkillsTests(unittest.TestCase):
    def test_verified_skill_becomes_supported_even_when_absent_from_resume(self):
        plan_without = plan_keyword_injection(RESUME_NO_SPRING_BOOT, JAVA_JOB)
        self.assertIn("Spring Boot", plan_without["unsupportedKeywords"])

        plan_with = plan_keyword_injection(
            RESUME_NO_SPRING_BOOT, JAVA_JOB, verified_skills=["Spring Boot"]
        )
        self.assertNotIn("Spring Boot", plan_with["unsupportedKeywords"])
        self.assertIn("Spring Boot", plan_with["supportedKeywords"])


class ResumeGeneratorConfirmedEvidenceTests(unittest.TestCase):
    def test_current_employment_evidence_added_to_matching_employer(self):
        plan = plan_keyword_injection(RESUME_NO_SPRING_BOOT, JAVA_JOB, verified_skills=["Spring Boot"])
        confirmed_evidence = {
            "Spring Boot": {
                "skill": "Spring Boot",
                "evidence_status": "USER_CONFIRMED_PROFESSIONAL",
                "evidence_type": "Current employment",
                "employer_name": "Acme Corp",
                "role_title": "Java Developer",
                "start_date": "2020-01",
                "currently_using": True,
                "usage_description": "Built Spring Boot microservices in my current role at Acme Corp.",
                "related_technologies": "",
                "impact_description": "",
            }
        }
        result = generate_optimized_resume(RESUME_NO_SPRING_BOOT, JAVA_JOB, plan, confirmed_evidence)
        self.assertIn("Spring Boot", result["plain_text"])
        self.assertIn("Acme Corp", result["plain_text"])
        # Never invented a new employer/date — original employment block is untouched.
        self.assertIn("Jan 2020 - Present", result["plain_text"])
        self.assertNotEqual(result["plain_text"].strip(), RESUME_NO_SPRING_BOOT.strip())

    def test_new_employment_not_in_resume_gets_its_own_truthful_section_not_merged_into_existing_role(self):
        # User explicitly confirmed this as a NEW employer not already on the resume
        # (the "New employment not present in resume" evidence-form path).
        plan = plan_keyword_injection(RESUME_NO_SPRING_BOOT, JAVA_JOB, verified_skills=["Spring Boot"])
        confirmed_evidence = {
            "Spring Boot": {
                "skill": "Spring Boot",
                "evidence_status": "USER_CONFIRMED_PROFESSIONAL",
                "evidence_type": "Previous employment",
                "employer_name": "Globex Consulting",
                "role_title": "Backend Developer",
                "start_date": "2018-01",
                "end_date": "2019-01",
                "usage_description": "Built Spring Boot services in a prior role.",
            }
        }
        result = generate_optimized_resume(RESUME_NO_SPRING_BOOT, JAVA_JOB, plan, confirmed_evidence)
        # The explicitly-confirmed new employer appears with its own truthful header — as
        # a separate entry within the Experience section (standard multi-employer resume
        # formatting), never woven into Acme Corp's own bullet list.
        self.assertIn("Globex Consulting | Backend Developer", result["plain_text"])
        experience_section = next(s for s in result["sections"] if "experience" in s["heading"].lower())
        acme_bullet = "Built Java services and React interfaces on AWS with PostgreSQL"
        globex_bullet = "Built Spring Boot services in a prior role"
        self.assertIn(acme_bullet, experience_section["content"])
        self.assertIn(globex_bullet, experience_section["content"])
        # Neither employer's bullet text bleeds into the other's.
        acme_line = next(line for line in experience_section["content"].splitlines() if acme_bullet in line)
        globex_line = next(line for line in experience_section["content"].splitlines() if globex_bullet in line)
        self.assertNotIn("Globex", acme_line)
        self.assertNotIn("Acme", globex_line)

    def test_unspecified_employment_target_defaults_to_most_recent_existing_role(self):
        # No employer_name given at all (the default "existing position, unspecified" path)
        # — must attach to the resume's own most recent employer, never invent one.
        plan = plan_keyword_injection(RESUME_NO_SPRING_BOOT, JAVA_JOB, verified_skills=["Spring Boot"])
        confirmed_evidence = {
            "Spring Boot": {
                "skill": "Spring Boot",
                "evidence_status": "USER_CONFIRMED_PROFESSIONAL",
                "evidence_type": "Current employment",
                "usage_description": "Built Spring Boot microservices in my current role.",
            }
        }
        result = generate_optimized_resume(RESUME_NO_SPRING_BOOT, JAVA_JOB, plan, confirmed_evidence)
        self.assertIn("Acme Corp", result["plain_text"])
        self.assertIn("Spring Boot", result["plain_text"])

    def test_project_evidence_goes_to_projects_section_not_experience(self):
        plan = plan_keyword_injection(RESUME_NO_SPRING_BOOT, JAVA_JOB, verified_skills=["Spring Boot"])
        confirmed_evidence = {
            "Spring Boot": {
                "skill": "Spring Boot",
                "evidence_status": "USER_CONFIRMED_PROJECT",
                "evidence_type": "Personal project",
                "project_name": "Budgeting App",
                "related_technologies": "Spring Boot, PostgreSQL",
                "work_completed": "Built the backend API for a personal budgeting app using Spring Boot.",
                "usage_description": "Implemented REST endpoints and data models.",
            }
        }
        result = generate_optimized_resume(RESUME_NO_SPRING_BOOT, JAVA_JOB, plan, confirmed_evidence)
        section_headings = [s["heading"] for s in result["sections"]]
        self.assertIn("Projects", section_headings)
        projects_section = next(s for s in result["sections"] if s["heading"] == "Projects")
        self.assertIn("Spring Boot", projects_section["content"])
        self.assertIn("Budgeting App", projects_section["content"])
        experience_section = next(
            s for s in result["sections"] if "experience" in s["heading"].lower() or "employment" in s["heading"].lower()
        )
        self.assertNotIn("Budgeting App", experience_section["content"])

    def test_certification_evidence_goes_to_certifications_section(self):
        plan = plan_keyword_injection(RESUME_NO_SPRING_BOOT, JAVA_JOB, verified_skills=["AWS"])
        confirmed_evidence = {
            "AWS": {
                "skill": "AWS",
                "evidence_status": "USER_CONFIRMED_CERTIFICATION",
                "evidence_type": "Certification",
                "certification_name": "AWS Certified Developer",
                "provider_name": "Amazon",
                "start_date": "2025-01",
            }
        }
        result = generate_optimized_resume(RESUME_NO_SPRING_BOOT, JAVA_JOB, plan, confirmed_evidence)
        section_headings = [s["heading"] for s in result["sections"]]
        self.assertIn("Certifications", section_headings)
        cert_section = next(s for s in result["sections"] if s["heading"] == "Certifications")
        self.assertIn("AWS Certified Developer", cert_section["content"])

    def test_learning_only_evidence_never_presented_as_professional(self):
        plan = plan_keyword_injection(RESUME_NO_SPRING_BOOT, JAVA_JOB, verified_skills=["Spring Boot"])
        confirmed_evidence = {
            "Spring Boot": {
                "skill": "Spring Boot",
                "evidence_status": "LEARNING_ONLY",
                "evidence_type": "Learning only",
            }
        }
        result = generate_optimized_resume(RESUME_NO_SPRING_BOOT, JAVA_JOB, plan, confirmed_evidence)
        section_headings = [s["heading"] for s in result["sections"]]
        self.assertIn("Currently Learning", section_headings)
        experience_section = next(
            s for s in result["sections"] if "experience" in s["heading"].lower() or "employment" in s["heading"].lower()
        )
        self.assertNotIn("Spring Boot", experience_section["content"])

    def test_no_confirmed_evidence_behaves_like_before(self):
        plan = plan_keyword_injection(RESUME_NO_SPRING_BOOT, JAVA_JOB)
        result = generate_optimized_resume(RESUME_NO_SPRING_BOOT, JAVA_JOB, plan)
        self.assertEqual(result["confirmed_skills_applied"], [])


class EvidenceClaimTraceabilityTests(unittest.TestCase):
    def test_resume_evidence_ids_and_user_evidence_ids_are_distinct_namespaces(self):
        structure = parse_resume_structure(RESUME_NO_SPRING_BOOT)
        resume_claims = _evidence_claims(structure)
        user_claims = _user_evidence_claims(
            {
                "Spring Boot": {
                    "evidence_type": "Current employment",
                    "employer_name": "Acme Corp",
                    "usage_description": "Built Spring Boot microservices in my current role.",
                }
            }
        )
        self.assertTrue(all(key.startswith("E") for key in resume_claims))
        self.assertTrue(all(key.startswith("UC") for key in user_claims))
        self.assertEqual(user_claims["UC1"]["skill"], "Spring Boot")


class RequirementExtractionTests(unittest.TestCase):
    def test_extracts_mandatory_and_preferred_with_categories(self):
        analysis = extract_job_requirement_analysis(JAVA_JOB)
        self.assertEqual(analysis["job_id"], 25)
        mandatory_names = {item["skill"] for item in analysis["mandatory_skills"]}
        self.assertIn("Java", mandatory_names)
        self.assertIn("Spring Boot", mandatory_names)
        for item in analysis["mandatory_skills"]:
            self.assertEqual(item["importance"], "MANDATORY")
            self.assertIn("category", item)
        for item in analysis["preferred_skills"]:
            self.assertEqual(item["importance"], "PREFERRED")

    def test_grouping_separates_verified_from_missing(self):
        analysis = extract_job_requirement_analysis(JAVA_JOB)
        grouped = group_requirements_vs_resume(analysis, RESUME_NO_SPRING_BOOT)
        verified_skills = {item["skill"] for item in grouped["verified_resume_skills"]}
        missing_mandatory = {item["skill"] for item in grouped["missing_mandatory_skills"]}
        self.assertIn("Java", verified_skills)
        self.assertIn("Spring Boot", missing_mandatory)
        for item in grouped["missing_mandatory_skills"]:
            self.assertIn("why_it_matters", item)
            self.assertTrue(item["why_it_matters"])
        # A skill already clearly supported by the resume must not be asked for again.
        self.assertNotIn("Java", missing_mandatory)


if __name__ == "__main__":
    unittest.main()
