import unittest
from types import SimpleNamespace

from app.agents.relevance_filter import (
    JAVA_FULL_STACK_FAMILY,
    candidate_location_matches,
    deduplicate_ranked_jobs,
    detect_role_family,
    match_preferred_skills,
    normalize_job_type,
    normalize_text,
    score_java_full_stack_job,
)


PREFERENCE = SimpleNamespace(
    skills="Java, Spring Boot, React, Angular, JavaScript, TypeScript, PostgreSQL, AWS, Docker",
    location="Santa Clara, CA",
    work_mode="Remote",
    job_type="Full-time",
)

PASSING_TITLES = (
    "Fullstack Java/React Developer Senior",
    "Sr. Full-Stack Developer - Angular/Java",
    "Java Full Stack Engineer",
    "Senior Java React Developer",
    "Full Stack Developer with Java",
)

FAILING_TITLES = (
    "Python Developer",
    "Product Designer",
    "React Frontend Developer",
    "Salesforce Developer",
    "Data Scientist",
)


def make_job(title, url="https://example.com/job"):
    return {
        "title": title,
        "company_name": "Example Company",
        "candidate_required_location": "United States",
        "job_type": "full_time",
        "tags": ["Java", "SpringBoot", "React.js"],
        "description": "Build Java services with Spring Boot, React, PostgreSQL, AWS, and Docker.",
        "url": url,
    }


class RelevanceFilterTests(unittest.TestCase):
    def test_title_normalization(self):
        self.assertEqual(
            normalize_text("  Sr. Full-Stack Developer - Angular/Java  "),
            "sr full stack developer angular java",
        )
        self.assertIn("spring boot", normalize_text("SpringBoot and React.js"))

    def test_required_titles_pass_role_family(self):
        for title in PASSING_TITLES:
            with self.subTest(title=title):
                self.assertEqual(detect_role_family(title), JAVA_FULL_STACK_FAMILY)
                result = score_java_full_stack_job(make_job(title), PREFERENCE)
                self.assertTrue(result["accepted"], result["rejection_reasons"])
                self.assertGreaterEqual(result["relevance_score"], 45)

    def test_unrelated_titles_fail_role_family(self):
        for title in FAILING_TITLES:
            with self.subTest(title=title):
                self.assertIsNone(detect_role_family(title))
                self.assertFalse(score_java_full_stack_job(make_job(title), PREFERENCE)["accepted"])

    def test_java_manual_tester_is_rejected(self):
        result = score_java_full_stack_job(make_job("Java Manual Tester"), PREFERENCE)
        self.assertFalse(result["accepted"])

    def test_java_can_come_from_description_when_title_has_family_indicator(self):
        self.assertEqual(
            detect_role_family("Full Stack Developer", "Build enterprise services with Java and Spring Boot."),
            JAVA_FULL_STACK_FAMILY,
        )

    def test_skill_alias_matching(self):
        matched = match_preferred_skills(
            "Java, Spring Boot, React, PostgreSQL",
            "Java Developer",
            ["SpringBoot", "React.js"],
            "Uses PostgreSQL",
        )
        self.assertEqual(matched, ["java", "postgres", "react", "spring boot"])

    def test_at_least_two_meaningful_skills_are_required(self):
        job = make_job("Java Full Stack Engineer")
        job["tags"] = []
        job["description"] = "A Java role focused on teamwork and agile development."
        result = score_java_full_stack_job(job, PREFERENCE)
        self.assertFalse(result["accepted"])
        self.assertIn("fewer than two meaningful preferred skills matched", result["rejection_reasons"])

    def test_candidate_location_matching_ignores_user_city(self):
        for location in ("Worldwide", "Anywhere", "United States", "USA", "North America", "Americas"):
            with self.subTest(location=location):
                self.assertTrue(candidate_location_matches(location, "Santa Clara, CA"))
        self.assertFalse(candidate_location_matches("Europe only", "Santa Clara, CA"))

    def test_job_type_normalization(self):
        for value in ("full-time", "full time", "fulltime", "full_time"):
            with self.subTest(value=value):
                self.assertEqual(normalize_job_type(value), "Full-time")

    def test_duplicate_removal_keeps_highest_score(self):
        first = {
            "job": {"title": "Java Full Stack Engineer", "company": "Acme", "job_url": "https://example.com/1"},
            "relevance_score": 70,
        }
        duplicate_url = {
            "job": {"title": "Java Full Stack Engineer", "company": "Acme", "job_url": "https://example.com/1"},
            "relevance_score": 80,
        }
        duplicate_title_company = {
            "job": {"title": "JAVA FULL-STACK ENGINEER", "company": "ACME", "job_url": "https://example.com/2"},
            "relevance_score": 90,
        }
        unique = {
            "job": {"title": "Java React Developer", "company": "Beta", "job_url": "https://example.com/3"},
            "relevance_score": 75,
        }
        result = deduplicate_ranked_jobs([first, duplicate_url, duplicate_title_company, unique])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["relevance_score"], 90)


if __name__ == "__main__":
    unittest.main()
