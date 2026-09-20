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
        self.assertEqual(diff["bullet_changes"], [])

    def test_bullet_changes_carries_original_and_generated_text_pairs(self):
        diff = diff_summary(ORIGINAL_RESUME, OPTIMIZED_RESUME)
        self.assertEqual(len(diff["bullet_changes"]), diff["bullets_rewritten"])
        first = diff["bullet_changes"][0]
        self.assertEqual(first["original_text"], "Worked on Java applications.")
        self.assertEqual(first["generated_text"], "Developed Java applications to support maintainable delivery.")
        self.assertEqual(first["change_type"], "REWRITTEN")
        self.assertIn("Acme", first["section"])

    def test_bullet_changes_never_reports_unchanged_bullets(self):
        resume_with_one_unchanged = """
Jane Candidate

Professional Experience
Acme | Java Developer | Jan 2020 - Present
- This bullet never changes.
- Worked on Java applications.
""".strip()
        optimized_with_one_unchanged = """
Jane Candidate

Professional Experience
Acme | Java Developer | Jan 2020 - Present
- This bullet never changes.
- Developed Java applications to support maintainable delivery.
""".strip()
        diff = diff_summary(resume_with_one_unchanged, optimized_with_one_unchanged)
        originals = [change["original_text"] for change in diff["bullet_changes"]]
        self.assertNotIn("This bullet never changes.", originals)
        self.assertIn("Worked on Java applications.", originals)

    def test_client_side_bullet_does_not_cascade_misalign_subsequent_pairs(self):
        """Regression: before CLIENT_PATTERN was anchored, a bullet
        containing "client-side"/"client-facing"/"client-server" got
        silently dropped during parsing, shifting every SUBSEQUENT bullet's
        position by one and causing them to be diffed against the WRONG
        original counterpart — corrupting bullet_changes for the entire
        rest of that employment, which is what made genuinely-changed
        bullets fail to show "AI Improved" (wrong pairing can look
        unchanged) or show a nonsensical Original/Suggested comparison."""
        original = """
Jane Candidate

Professional Experience
Acme Corp | Engineer | Jan 2020 - Present
- Tested REST APIs using Postman.
- Built dashboards using React for internal teams.
- Deployed services using Jenkins and Docker.
""".strip()
        optimized = """
Jane Candidate

Professional Experience
Acme Corp | Engineer | Jan 2020 - Present
- Validated REST API responses and error handling using Postman.
- Developed dashboards using React, implementing client-side state management.
- Automated deployments using Jenkins and containerized services with Docker.
""".strip()
        diff = diff_summary(original, optimized)
        changes_by_generated = {c["generated_text"]: c["original_text"] for c in diff["bullet_changes"]}
        # Each generated bullet must pair with the SAME-TOPIC original bullet.
        self.assertEqual(
            changes_by_generated["Validated REST API responses and error handling using Postman."],
            "Tested REST APIs using Postman.",
        )
        self.assertEqual(
            changes_by_generated["Developed dashboards using React, implementing client-side state management."],
            "Built dashboards using React for internal teams.",
        )
        self.assertEqual(
            changes_by_generated["Automated deployments using Jenkins and containerized services with Docker."],
            "Deployed services using Jenkins and Docker.",
        )

    def test_summary_change_captures_original_and_generated_summary(self):
        diff = diff_summary(ORIGINAL_RESUME, OPTIMIZED_RESUME)
        self.assertIsNotNone(diff["summary_change"])
        self.assertEqual(diff["summary_change"]["original_text"], "Java developer.")
        self.assertEqual(
            diff["summary_change"]["generated_text"],
            "Java Full Stack Developer with experience designing and developing scalable applications using Java, Spring Boot, React.",
        )

    def test_summary_change_is_none_when_summary_unchanged(self):
        diff = diff_summary(ORIGINAL_RESUME, ORIGINAL_RESUME)
        self.assertIsNone(diff["summary_change"])

    def test_summary_change_is_none_for_trivial_reword(self):
        near_identical_optimized = ORIGINAL_RESUME.replace("Java developer.", "Java Developer.")
        diff = diff_summary(ORIGINAL_RESUME, near_identical_optimized)
        self.assertIsNone(diff["summary_change"])

    def test_new_bullet_beyond_original_count_is_marked_added_from_evidence(self):
        original_two_bullets = """
Jane Candidate

Professional Experience
Acme | Java Developer | Jan 2020 - Present
- Worked on Java applications.
""".strip()
        optimized_three_bullets = """
Jane Candidate

Professional Experience
Acme | Java Developer | Jan 2020 - Present
- Developed Java applications to support maintainable delivery.
- Wrote REST Assured API test suites validating backend contracts.
""".strip()
        diff = diff_summary(original_two_bullets, optimized_three_bullets)
        change_types = {change["change_type"] for change in diff["bullet_changes"]}
        added = [c for c in diff["bullet_changes"] if c["change_type"] == "ADDED_FROM_CONFIRMED_EVIDENCE"]
        self.assertIn("ADDED_FROM_CONFIRMED_EVIDENCE", change_types)
        self.assertEqual(added[0]["original_text"], "")


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
