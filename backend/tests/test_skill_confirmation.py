import unittest

from app.services.skill_confirmation_service import (
    classify_evidence_status,
    validate_evidence_item,
)


BASELINE = {
    "experience_level": "Intermediate",
    "experience_range": "1-2 years",
    "confirmed_accurate": True,
}


class EvidenceCreditTests(unittest.TestCase):
    def test_current_employment_unspecified_position_no_employer_required(self):
        item = {
            "skill": "Spring Boot",
            "answer": "has_it",
            "evidence_type": "Current employment",
            "usage_description": "Built REST APIs and microservices using Spring Boot for the current internal application.",
            **BASELINE,
        }
        status, credit = classify_evidence_status(item)
        self.assertEqual(status, "USER_CONFIRMED_PROFESSIONAL")
        self.assertEqual(credit, 0.85)

    def test_new_employment_requires_employer_role_and_dates(self):
        item = {
            "skill": "Spring Boot",
            "answer": "has_it",
            "evidence_type": "Current employment",
            "is_new_employment": True,
            "employer_name": "Globex Consulting",
            "role_title": "Java Developer",
            "start_date": "2024-01",
            "currently_using": True,
            "usage_description": "Built REST APIs and microservices using Spring Boot.",
            **BASELINE,
        }
        status, credit = classify_evidence_status(item)
        self.assertEqual(status, "USER_CONFIRMED_PROFESSIONAL")
        self.assertEqual(credit, 0.85)

    def test_professional_project_with_detailed_evidence(self):
        item = {
            "skill": "Spring Boot",
            "answer": "has_it",
            "evidence_type": "Professional project",
            "project_name": "Internal Payments Tool",
            "work_completed": "Built a payments microservice using Spring Boot.",
            "usage_description": "Contributed the REST layer and data model.",
            **BASELINE,
        }
        status, credit = classify_evidence_status(item)
        self.assertEqual(status, "USER_CONFIRMED_PROJECT")
        self.assertEqual(credit, 0.75)

    def test_personal_project_with_detailed_evidence(self):
        item = {
            "skill": "Spring Boot",
            "answer": "has_it",
            "evidence_type": "Personal project",
            "project_name": "Budgeting App",
            "work_completed": "Built the backend API and database layer.",
            "usage_description": "Designed and implemented the REST endpoints.",
            **BASELINE,
        }
        status, credit = classify_evidence_status(item)
        self.assertEqual(status, "USER_CONFIRMED_PROJECT")
        self.assertEqual(credit, 0.55)

    def test_certification_credit_without_date(self):
        item = {
            "skill": "AWS",
            "answer": "has_it",
            "evidence_type": "Certification",
            "certification_name": "AWS Certified Solutions Architect",
            "provider_name": "AWS",
            "usage_description": "Studied and passed the certification exam.",
            **BASELINE,
        }
        status, credit = classify_evidence_status(item)
        self.assertEqual(status, "USER_CONFIRMED_CERTIFICATION")
        self.assertEqual(credit, 0.35)

    def test_training_credit_with_minimal_fields(self):
        item = {
            "skill": "Kubernetes",
            "answer": "has_it",
            "evidence_type": "Training/coursework",
            "project_name": "Kubernetes for Developers",
            "usage_description": "Completed a hands-on Kubernetes course.",
            **BASELINE,
        }
        status, credit = classify_evidence_status(item)
        self.assertEqual(status, "USER_CONFIRMED_TRAINING")
        self.assertEqual(credit, 0.25)

    def test_learning_only_low_credit(self):
        status, credit = classify_evidence_status({"skill": "Kubernetes", "answer": "learning_only"})
        self.assertEqual(status, "LEARNING_ONLY")
        self.assertEqual(credit, 0.10)

    def test_checkbox_without_evidence_gets_zero_credit(self):
        item = {"skill": "Spring Boot", "answer": "has_it", "evidence_type": "Current employment"}
        status, credit = classify_evidence_status(item)
        self.assertEqual(status, "INCOMPLETE")
        self.assertEqual(credit, 0.0)

    def test_does_not_have_skill_is_rejected_with_zero_credit(self):
        status, credit = classify_evidence_status({"skill": "Kubernetes", "answer": "not_possessed"})
        self.assertEqual(status, "REJECTED")
        self.assertEqual(credit, 0.0)

    def test_unanswered_skill_is_incomplete(self):
        status, credit = classify_evidence_status({"skill": "Kubernetes"})
        self.assertEqual(status, "INCOMPLETE")
        self.assertEqual(credit, 0.0)


class EvidenceValidationTests(unittest.TestCase):
    def test_current_employment_default_path_does_not_require_employer_or_dates(self):
        item = {
            "skill": "Spring Boot",
            "answer": "has_it",
            "evidence_type": "Current employment",
            "usage_description": "Built services using Spring Boot in my current role.",
            **BASELINE,
        }
        self.assertEqual(validate_evidence_item(item), [])

    def test_baseline_fields_are_required_for_every_evidence_type(self):
        item = {"skill": "Spring Boot", "answer": "has_it", "evidence_type": "Current employment"}
        errors = validate_evidence_item(item)
        self.assertTrue(errors)
        self.assertIn("experience_level", errors[0])
        self.assertIn("experience_range", errors[0])
        self.assertIn("accuracy confirmation", errors[0])

    def test_new_employment_requires_employer_role_and_dates(self):
        item = {
            "skill": "Spring Boot",
            "answer": "has_it",
            "evidence_type": "Current employment",
            "is_new_employment": True,
            "usage_description": "Built services using Spring Boot.",
            **BASELINE,
        }
        errors = validate_evidence_item(item)
        self.assertTrue(errors)
        self.assertIn("employer_name", errors[0])
        self.assertIn("role_title", errors[0])

    def test_new_employment_with_unknown_employer_is_accepted_since_it_is_explicitly_new(self):
        item = {
            "skill": "Spring Boot",
            "answer": "has_it",
            "evidence_type": "Current employment",
            "is_new_employment": True,
            "employer_name": "A Company Not Yet On My Resume",
            "role_title": "Developer",
            "start_date": "2024-01",
            "currently_using": True,
            "usage_description": "Built services using Spring Boot in my current role.",
            **BASELINE,
        }
        errors = validate_evidence_item(item, known_employers=["Acme Corp", "Initech"])
        self.assertEqual(errors, [])

    def test_existing_position_path_rejects_employer_not_in_resume(self):
        item = {
            "skill": "Spring Boot",
            "answer": "has_it",
            "evidence_type": "Current employment",
            "employer_name": "A Company That Is Not In The Resume",
            "usage_description": "Built services using Spring Boot in my current role.",
            **BASELINE,
        }
        errors = validate_evidence_item(item, known_employers=["Acme Corp", "Initech"])
        self.assertTrue(errors)
        self.assertIn("not found in your resume", errors[0])

    def test_existing_position_path_accepts_matching_employer(self):
        item = {
            "skill": "Spring Boot",
            "answer": "has_it",
            "evidence_type": "Current employment",
            "employer_name": "acme corp",
            "usage_description": "Built services using Spring Boot in my current role.",
            **BASELINE,
        }
        errors = validate_evidence_item(item, known_employers=["Acme Corp"])
        self.assertEqual(errors, [])

    def test_project_requires_name_and_what_was_built_but_not_employer_fields(self):
        item = {
            "skill": "Spring Boot",
            "answer": "has_it",
            "evidence_type": "Personal project",
            "usage_description": "Implemented REST endpoints.",
            **BASELINE,
        }
        errors = validate_evidence_item(item)
        self.assertTrue(errors)
        self.assertIn("project_name", errors[0])
        self.assertIn("work_completed", errors[0])
        self.assertNotIn("employer_name", errors[0])

    def test_certification_date_earned_is_optional(self):
        item = {
            "skill": "AWS",
            "answer": "has_it",
            "evidence_type": "Certification",
            "certification_name": "AWS SAA",
            "provider_name": "AWS",
            "usage_description": "Studied and passed the exam.",
            **BASELINE,
        }
        self.assertEqual(validate_evidence_item(item), [])

    def test_certification_requires_name_and_issuer(self):
        item = {
            "skill": "AWS",
            "answer": "has_it",
            "evidence_type": "Certification",
            "certification_name": "AWS SAA",
            "usage_description": "Studied and passed the exam.",
            **BASELINE,
        }
        errors = validate_evidence_item(item)
        self.assertTrue(errors)
        self.assertIn("provider_name", errors[0])

    def test_training_only_requires_course_name(self):
        item = {
            "skill": "Kubernetes",
            "answer": "has_it",
            "evidence_type": "Training/coursework",
            "project_name": "Kubernetes for Developers",
            "usage_description": "Completed a hands-on course.",
            **BASELINE,
        }
        self.assertEqual(validate_evidence_item(item), [])

    def test_learning_only_never_requires_evidence(self):
        self.assertEqual(validate_evidence_item({"skill": "Kubernetes", "answer": "learning_only"}), [])

    def test_not_possessed_never_requires_evidence(self):
        self.assertEqual(validate_evidence_item({"skill": "Kubernetes", "answer": "not_possessed"}), [])


if __name__ == "__main__":
    unittest.main()
