import unittest
from types import SimpleNamespace

from app.services.ats_scoring import calculate_weighted_ats_score
from app.services.final_resume_validator import validate_final_resume
from app.services.resume_structure_parser import parse_resume_structure


JAVA_JOB = SimpleNamespace(
    title="Java Full Stack Developer",
    company="Example",
    description="Must have Java, Spring Boot, Hibernate, React, AWS and PostgreSQL.",
    skills_required="Java, Spring Boot, Hibernate, React, AWS, PostgreSQL",
    location="Remote",
    work_mode="Remote",
    job_type="Full-time",
)

PYTHON_RESUME = """
Jane Candidate

Technical Skills
Python, Django, FastAPI, AWS, PostgreSQL, Git, Agile

Professional Experience
Acme | Python Developer | Jan 2020 - Present
- Built REST APIs using Python and FastAPI.
- Deployed Python services on AWS with PostgreSQL.
""".strip()

JAVA_RESUME = """
Jane Candidate

Technical Skills
Java, Spring Boot, Hibernate, React, AWS, PostgreSQL

Professional Experience
Acme | Java Full Stack Developer | Jan 2020 - Present
- Built Java and Spring Boot services using Hibernate.
- Delivered React interfaces backed by PostgreSQL and AWS.
""".strip()


class EvidenceBasedScoringTests(unittest.TestCase):
    def test_python_resume_vs_java_full_stack_is_not_recommended(self):
        result = calculate_weighted_ats_score(PYTHON_RESUME, JAVA_JOB)
        self.assertEqual(result["role_alignment"], "MISMATCH")
        self.assertEqual(result["score_status"], "NOT_RECOMMENDED")
        self.assertLess(result["final_score"], 40)
        self.assertFalse(result["safe_to_apply"])
        self.assertIn("Java", result["missing_mandatory_skills"])
        self.assertIn("Spring Boot", result["missing_mandatory_skills"])

    def test_java_resume_vs_java_job_has_strong_alignment(self):
        result = calculate_weighted_ats_score(JAVA_RESUME, JAVA_JOB)
        self.assertEqual(result["role_alignment"], "EXACT")
        self.assertGreaterEqual(result["final_score"], 70)
        self.assertNotEqual(result["score_status"], "NOT_RECOMMENDED")
        self.assertTrue(result["safe_to_apply"])

    def test_java_only_in_skills_is_weak_evidence(self):
        resume = """
Jane Candidate

Technical Skills
Java, AWS, PostgreSQL

Professional Experience
Acme | Python Developer | Jan 2020 - Present
- Built Python APIs on AWS.
""".strip()
        result = calculate_weighted_ats_score(resume, JAVA_JOB)
        self.assertEqual(result["resume_evidence"]["Java"]["evidence_strength"], "weak")
        self.assertLess(result["final_score"], 40)
        self.assertEqual(result["score_status"], "NOT_RECOMMENDED")

    def test_repeated_java_mentions_in_skills_remain_weak(self):
        resume = """
Jane Candidate

Technical Skills
Java, Core Java, Java Development
""".strip()
        result = calculate_weighted_ats_score(resume, JAVA_JOB)
        self.assertEqual(result["resume_evidence"]["Java"]["evidence_strength"], "weak")

    def test_java_in_skills_and_summary_is_medium_evidence(self):
        resume = """
Jane Candidate

Professional Summary
Java backend developer focused on maintainable services.

Technical Skills
Java, AWS
""".strip()
        result = calculate_weighted_ats_score(resume, JAVA_JOB)
        self.assertEqual(result["resume_evidence"]["Java"]["evidence_strength"], "medium")

    def test_java_project_is_evidence_but_not_employment_invention(self):
        resume = """
Jane Candidate

Technical Skills
Python, Java, Spring Boot

Professional Experience
Acme | Python Developer | Jan 2020 - Present
- Built Python APIs.

Projects
Java Learning Project
- Built a Java service using Spring Boot.
""".strip()
        result = calculate_weighted_ats_score(resume, JAVA_JOB)
        self.assertEqual(result["resume_evidence"]["Java"]["evidence_strength"], "strong")
        snippets = " ".join(result["resume_evidence"]["Java"]["evidence_snippets"])
        self.assertIn("Java", snippets)
        self.assertLess(result["final_score"], calculate_weighted_ats_score(JAVA_RESUME, JAVA_JOB)["final_score"])

    def test_generic_keyword_overlap_cannot_inflate_score(self):
        resume = """
Jane Candidate

Technical Skills
AWS, Git, Agile, SQL, REST APIs, CI/CD

Professional Experience
Acme | Python Developer | Jan 2020 - Present
- Used AWS, Git, Agile, SQL, REST APIs and CI/CD for Python services.
""".strip()
        result = calculate_weighted_ats_score(resume, JAVA_JOB)
        self.assertLess(result["final_score"], 40)
        self.assertEqual(result["score_status"], "NOT_RECOMMENDED")

    def test_optimized_score_uses_original_as_evidence_source(self):
        unsafe_generated = PYTHON_RESUME + "\n- Developed Spring Boot microservices using Java."
        result = calculate_weighted_ats_score(
            unsafe_generated,
            JAVA_JOB,
            evidence_source_text=PYTHON_RESUME,
        )
        self.assertIn("Java", result["missing_mandatory_skills"])
        self.assertLessEqual(result["final_score"], 35)

    def test_validator_rejects_hallucinated_spring_boot(self):
        structure = parse_resume_structure(PYTHON_RESUME)
        unsafe = PYTHON_RESUME + "\n- Developed Spring Boot microservices."
        validation = validate_final_resume(
            original_text=PYTHON_RESUME,
            generated_text=unsafe,
            original_structure=structure,
            generated_sections=structure["sections"],
            unsupported_keywords=["Spring Boot"],
            selected_technologies=[],
        )
        self.assertEqual(validation["status"], "failed")
        self.assertTrue(any("Spring Boot" in item for item in validation["errors"]))

    def test_scoring_is_deterministic(self):
        first = calculate_weighted_ats_score(PYTHON_RESUME, JAVA_JOB)
        second = calculate_weighted_ats_score(PYTHON_RESUME, JAVA_JOB)
        self.assertEqual(first["final_score"], second["final_score"])
        self.assertEqual(first["component_scores"], second["component_scores"])

    def test_component_weights_sum_to_100(self):
        result = calculate_weighted_ats_score(JAVA_RESUME, JAVA_JOB)
        weights = {
            "role_alignment": 20,
            "mandatory_core_skills": 35,
            "experience_evidence": 20,
            "preferred_skills": 8,
            "responsibilities_domain": 7,
            "education_certifications": 5,
            "location_employment": 5,
        }
        self.assertEqual(sum(weights.values()), 100)
        self.assertEqual(set(result["component_scores"].keys()), set(weights.keys()))

    def test_score_labels_match_spec_thresholds(self):
        from app.services.ats_scoring import _status

        self.assertEqual(_status(95), "EXCELLENT_MATCH")
        self.assertEqual(_status(90), "EXCELLENT_MATCH")
        self.assertEqual(_status(89), "STRONG_MATCH")
        self.assertEqual(_status(75), "STRONG_MATCH")
        self.assertEqual(_status(74), "PARTIAL_MATCH")
        self.assertEqual(_status(60), "PARTIAL_MATCH")
        self.assertEqual(_status(59), "WEAK_MATCH")
        self.assertEqual(_status(40), "WEAK_MATCH")
        self.assertEqual(_status(39), "NOT_RECOMMENDED")

    def test_confirmed_professional_evidence_raises_score_for_missing_skill(self):
        # Spring Boot is entirely absent from this resume.
        resume_without_spring_boot = """
Jane Candidate

Technical Skills
Java, React, AWS, PostgreSQL

Professional Experience
Acme | Java Developer | Jan 2020 - Present
- Built Java services and React interfaces on AWS with PostgreSQL.
""".strip()
        baseline = calculate_weighted_ats_score(resume_without_spring_boot, JAVA_JOB)
        self.assertIn("Spring Boot", baseline["missing_mandatory_skills"])

        confirmed = {
            "Spring Boot": {
                "credit": 0.85,
                "evidence_status": "USER_CONFIRMED_PROFESSIONAL",
                "evidence_type": "Current employment",
                "usage_description": "Built Spring Boot microservices in my current role.",
            }
        }
        boosted = calculate_weighted_ats_score(
            resume_without_spring_boot, JAVA_JOB, confirmed_skills=confirmed
        )
        self.assertGreater(boosted["final_score"], baseline["final_score"])
        self.assertNotIn("Spring Boot", boosted["missing_mandatory_skills"])
        self.assertIn("Spring Boot", boosted["confirmed_skills_used"])

    def test_confirmed_skill_checked_with_no_evidence_does_not_raise_score(self):
        resume_without_spring_boot = """
Jane Candidate

Technical Skills
Java, React, AWS, PostgreSQL

Professional Experience
Acme | Java Developer | Jan 2020 - Present
- Built Java services and React interfaces on AWS with PostgreSQL.
""".strip()
        baseline = calculate_weighted_ats_score(resume_without_spring_boot, JAVA_JOB)
        # credit 0.0 mirrors a REJECTED (checked, no evidence) confirmation — should never be
        # passed through by to_scoring_evidence, but the scorer must be safe even if it is.
        confirmed = {
            "Spring Boot": {
                "credit": 0.0,
                "evidence_status": "REJECTED",
                "evidence_type": None,
                "usage_description": "",
            }
        }
        result = calculate_weighted_ats_score(
            resume_without_spring_boot, JAVA_JOB, confirmed_skills=confirmed
        )
        self.assertEqual(result["final_score"], baseline["final_score"])
        self.assertIn("Spring Boot", result["missing_mandatory_skills"])

    def test_resume_professional_evidence_outscores_resume_project_only_evidence(self):
        # Same skill (Java), same job — but one resume shows it under Professional
        # Experience (100% credit) and the other only under Projects (80% credit).
        professional_resume = """
Jane Candidate

Technical Skills
Java, Spring Boot, Hibernate, React, AWS, PostgreSQL

Professional Experience
Acme | Java Developer | Jan 2020 - Present
- Built Java and Spring Boot services using Hibernate.
- Delivered React interfaces backed by PostgreSQL and AWS.
""".strip()
        project_only_resume = """
Jane Candidate

Technical Skills
Java, Spring Boot, Hibernate, React, AWS, PostgreSQL

Projects
Internal Tool
- Built Java and Spring Boot services using Hibernate.
- Delivered React interfaces backed by PostgreSQL and AWS.
""".strip()
        professional_result = calculate_weighted_ats_score(professional_resume, JAVA_JOB)
        project_result = calculate_weighted_ats_score(project_only_resume, JAVA_JOB)
        self.assertEqual(professional_result["resume_evidence"]["Java"]["evidence_category"], "professional")
        self.assertEqual(project_result["resume_evidence"]["Java"]["evidence_category"], "project")
        self.assertGreater(professional_result["final_score"], project_result["final_score"])

    def test_learning_only_mandatory_evidence_caps_score_at_55(self):
        # Strong role alignment and most mandatory skills evidenced — only AWS/PostgreSQL
        # are missing from the resume, and those are bridged solely by learning-only claims.
        strong_resume = """
Jane Candidate

Technical Skills
Java, Spring Boot, Hibernate, React

Professional Experience
Acme | Java Full Stack Developer | Jan 2020 - Present
- Built Java and Spring Boot services using Hibernate.
- Delivered React interfaces.
""".strip()
        baseline = calculate_weighted_ats_score(strong_resume, JAVA_JOB)
        self.assertGreater(baseline["final_score"], 55)  # would otherwise score well above the cap

        confirmed = {
            skill: {"credit": 0.10, "evidence_status": "LEARNING_ONLY", "evidence_type": "Learning only"}
            for skill in baseline["missing_mandatory_skills"]
        }
        capped = calculate_weighted_ats_score(strong_resume, JAVA_JOB, confirmed_skills=confirmed)
        self.assertLessEqual(capped["final_score"], 55)
        self.assertIn("learning", (capped["score_cap_reason"] or "").lower())

    def test_why_not_95_explains_gap_below_95(self):
        result = calculate_weighted_ats_score(PYTHON_RESUME, JAVA_JOB)
        self.assertLess(result["final_score"], 95)
        self.assertTrue(result["why_not_95"])
        self.assertIn("95%", result["why_not_95"])

    def test_original_score_never_uses_confirmed_skills(self):
        # Spec Part 9: original score must be computed with confirmed_skills=None, always.
        # This test locks the call-site contract the workflow service relies on.
        without_arg = calculate_weighted_ats_score(JAVA_RESUME, JAVA_JOB)
        with_none = calculate_weighted_ats_score(JAVA_RESUME, JAVA_JOB, confirmed_skills=None)
        self.assertEqual(without_arg["final_score"], with_none["final_score"])

    def test_outdated_resume_with_genuine_confirmed_experience_jumps_into_90s(self):
        # Mirrors the spec's promised outcome: an outdated-but-mostly-relevant resume
        # (68-ish%) should commonly land in the 90-98% band once the user genuinely
        # confirms the missing mandatory skills with strong evidence — never forced,
        # purely a consequence of the additional truthful evidence.
        job = SimpleNamespace(
            title="Java Full Stack Developer", company="Example",
            description=(
                "Must have Java, Spring Boot, Hibernate, Microservices, React, AWS, "
                "PostgreSQL, Docker, Kubernetes experience."
            ),
            skills_required="Java, Spring Boot, Hibernate, Microservices, React, AWS, PostgreSQL, Docker, Kubernetes",
            location="Remote", work_mode="Remote", job_type="Full-time",
        )
        outdated_resume = """
Jane Candidate

Technical Skills
Java, Spring Boot, Hibernate, React

Professional Experience
Acme | Java Full Stack Developer | Jan 2020 - Present
- Built Java and Spring Boot services using Hibernate.
- Delivered React interfaces.
""".strip()
        baseline = calculate_weighted_ats_score(outdated_resume, job)
        self.assertLess(baseline["final_score"], 75)

        confirmed = {
            skill: {
                "credit": 0.85,
                "evidence_status": "USER_CONFIRMED_PROFESSIONAL",
                "evidence_type": "Current employment",
            }
            for skill in baseline["missing_mandatory_skills"]
        }
        optimized = calculate_weighted_ats_score(outdated_resume, job, confirmed_skills=confirmed)
        self.assertGreaterEqual(optimized["final_score"], 90)
        self.assertLessEqual(optimized["final_score"], 98)
        self.assertGreaterEqual(optimized["final_score"] - baseline["final_score"], 10)

    def test_score_is_never_forced_when_mandatory_skills_truly_missing(self):
        # The counter-case: if the user genuinely lacks required skills (never
        # confirmed), the score must stay low and explain the real gap.
        job = SimpleNamespace(
            title="Java Full Stack Developer", company="Example",
            description="Must have Java, Spring Boot, Kubernetes, Kafka experience.",
            skills_required="Java, Spring Boot, Kubernetes, Kafka",
            location="Remote", work_mode="Remote", job_type="Full-time",
        )
        resume = """
Jane Candidate

Technical Skills
Java, Spring Boot

Professional Experience
Acme | Java Developer | Jan 2020 - Present
- Built Java and Spring Boot services.
""".strip()
        result = calculate_weighted_ats_score(resume, job)
        self.assertLess(result["final_score"], 90)
        self.assertIn("Kubernetes", result["missing_mandatory_skills"])
        self.assertIn("Kafka", result["missing_mandatory_skills"])
        self.assertIn("Kubernetes", result["why_not_95"])
        self.assertIn("Kafka", result["why_not_95"])


if __name__ == "__main__":
    unittest.main()
