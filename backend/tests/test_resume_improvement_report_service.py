import unittest

from app.services.resume_improvement_report_service import (
    build_improvement_report,
    diff_summary,
)


ORIGINAL_RESUME = """
Jane Candidate

Professional Summary
Java developer.

Technical Skills
Java, React

Professional Experience
Acme | Java Developer | Jan 2020 - Present
- Worked on Java applications.
- Worked on Java applications.
""".strip()

OPTIMIZED_RESUME = """
Jane Candidate

Professional Summary
Java Full Stack Developer with experience designing and developing scalable applications using Java, Spring Boot, React.

Technical Skills
Java, Spring Boot, React

Professional Experience
Acme | Java Developer | Jan 2020 - Present
- Developed Java applications to support maintainable delivery.
- Developed Spring Boot microservices in my current role.
""".strip()


def _fake_score_result(*, match_percentage, required_skills, matched_keywords, missing=None, confirmed_used=None, suggestions=None):
    return {
        "match_percentage": match_percentage,
        "required_skills": required_skills,
        "preferred_skills": [],
        "matched_keywords": matched_keywords,
        "missing_mandatory_skills": missing or [],
        "confirmed_skills_used": confirmed_used or [],
        "suggestions": suggestions or [],
    }


class DiffSummaryTests(unittest.TestCase):
    def test_detects_improved_sections_and_rewritten_bullets(self):
        diff = diff_summary(ORIGINAL_RESUME, OPTIMIZED_RESUME)
        self.assertIn("Professional Summary", diff["sections_improved"])
        self.assertIn("Technical Skills", diff["sections_improved"])
        self.assertGreaterEqual(diff["bullets_rewritten"], 1)

    def test_identical_resumes_have_no_diff(self):
        diff = diff_summary(ORIGINAL_RESUME, ORIGINAL_RESUME)
        self.assertEqual(diff["sections_improved"], [])
        self.assertEqual(diff["bullets_rewritten"], 0)


class ImprovementReportTests(unittest.TestCase):
    def test_report_includes_meters_explanation_and_score_delta(self):
        original = _fake_score_result(
            match_percentage=68, required_skills=["Java", "Spring Boot", "React"], matched_keywords=["Java", "React"],
            missing=["Spring Boot"],
        )
        optimized = _fake_score_result(
            match_percentage=91, required_skills=["Java", "Spring Boot", "React"],
            matched_keywords=["Java", "React", "Spring Boot"],
            confirmed_used=["Spring Boot"],
            suggestions=["Score includes user-confirmed experience not present in the uploaded resume: Spring Boot."],
        )
        report = build_improvement_report(
            original_score_result=original,
            optimized_score_result=optimized,
            original_text=ORIGINAL_RESUME,
            optimized_text=OPTIMIZED_RESUME,
            validation_report={"final_validation": {"status": "passed", "warnings": [], "errors": [],
                                                      "protectedFieldsVerified": True, "structurePreserved": True}},
            content_changed=True,
        )
        self.assertEqual(report["original_score"], 68)
        self.assertEqual(report["optimized_score"], 91)
        self.assertEqual(report["improvement"], 23)
        self.assertIn("Spring Boot", report["new_skills_added"])
        self.assertIn("Spring Boot", report["keywords_added"])
        self.assertEqual(report["missing_skills_remaining"], [])
        self.assertIn("resume_quality", report["meters"])
        self.assertIn("ats_match", report["meters"])
        self.assertIn("keyword_coverage", report["meters"])
        self.assertIn("formatting", report["meters"])
        self.assertEqual(report["meters"]["ats_match"]["before"], 68)
        self.assertEqual(report["meters"]["ats_match"]["after"], 91)
        self.assertTrue(report["explanation"])
        self.assertTrue(any("Spring Boot" in reason for reason in report["explanation"]))

    def test_no_content_change_reports_zero_improvement_and_explains_why(self):
        result = _fake_score_result(
            match_percentage=57, required_skills=["Java"], matched_keywords=["Java"],
        )
        report = build_improvement_report(
            original_score_result=result,
            optimized_score_result=result,
            original_text=ORIGINAL_RESUME,
            optimized_text=ORIGINAL_RESUME,
            validation_report=None,
            content_changed=False,
        )
        self.assertEqual(report["improvement"], 0)
        self.assertTrue(any("stayed the same" in reason for reason in report["explanation"]))

    def test_resume_quality_meter_improves_when_weak_bullets_are_rewritten(self):
        report = build_improvement_report(
            original_score_result=_fake_score_result(match_percentage=50, required_skills=[], matched_keywords=[]),
            optimized_score_result=_fake_score_result(match_percentage=50, required_skills=[], matched_keywords=[]),
            original_text=ORIGINAL_RESUME,
            optimized_text=OPTIMIZED_RESUME,
            validation_report=None,
            content_changed=True,
        )
        self.assertGreater(report["meters"]["resume_quality"]["after"], report["meters"]["resume_quality"]["before"])


if __name__ == "__main__":
    unittest.main()
