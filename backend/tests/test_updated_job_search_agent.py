import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.relevance_filter import (
    build_alternative_queries,
    detect_role_family_key,
    get_role_aliases,
    is_role_relevant,
    passes_skill_relevance,
)
from app.agents.updated_job_search_agent import (
    build_search_queries,
    clean_html,
    evaluate_remotive_job,
    extract_valid_job_url,
    get_updated_jobs_from_remotive,
    normalize_remotive_job,
)


def preference(**overrides):
    base = dict(
        job_title="Senior SDET",
        location="Remote",
        experience="5 years",
        work_mode="Remote",
        job_type="Full-time",
        skills="Swift, XCUITest, XCTest, API Testing, Jenkins",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def remotive_job(**overrides):
    job = {
        "title": "Senior SDET",
        "company_name": "Acme QA",
        "candidate_required_location": "USA",
        "job_type": "full_time",
        "category": "Software Development",
        "tags": ["Swift", "XCUITest", "API Testing"],
        "description": "<p>Build test automation with XCUITest, XCTest, and Jenkins.</p>",
        "url": "https://remotive.com/remote-jobs/software-development/senior-sdet-12345",
    }
    job.update(overrides)
    return job


class UpdatedJobSearchAgentTests(unittest.TestCase):
    def test_senior_sdet_resolves_aliases_and_queries(self):
        pref = preference()
        aliases = [a.lower() for a in get_role_aliases(pref.job_title)]
        queries = [q.lower() for q in build_search_queries(pref)]

        self.assertEqual(detect_role_family_key("Senior SDET"), "sdet")
        self.assertIn("sdet", aliases)
        self.assertIn("software engineer in test", aliases)
        self.assertIn("qa automation engineer", aliases)
        self.assertTrue(queries[0] == "senior sdet")
        self.assertIn("software engineer in test", queries)

    def test_java_full_stack_uses_exact_title_all_aliases_and_broad_fallback(self):
        pref = preference(job_title="Java full stack", skills="Java, Spring Boot, React")
        queries = build_search_queries(pref)
        normalized = [query.lower() for query in queries]

        self.assertEqual(queries[0], "Java full stack")
        self.assertIn("java developer", normalized)
        self.assertIn("java full stack developer", normalized)
        self.assertEqual(len(normalized), len(set(normalized)))

    def test_data_engineer_aliases(self):
        queries = [q.lower() for q in build_alternative_queries("Data Engineer")]
        self.assertIn("etl developer", queries)
        self.assertIn("big data engineer", queries)

    def test_rejects_unrelated_marketing_and_sales_roles(self):
        pref = preference()
        aliases = get_role_aliases(pref.job_title)
        for title, category in (
            ("Head of Marketing & Communications", "Marketing"),
            ("Business Development Representative", "Sales"),
            ("Patient Care Specialist", "Medical"),
            ("Product Designer", "Design"),
        ):
            job = remotive_job(title=title, category=category, tags=[], description="Unrelated role")
            self.assertFalse(is_role_relevant(job, pref.job_title, aliases), title)

    def test_accepts_sdet_and_qa_automation_titles(self):
        pref = preference()
        aliases = get_role_aliases(pref.job_title)
        for title in (
            "Senior SDET",
            "SDET",
            "Software Engineer in Test",
            "QA Automation Engineer",
            "Test Automation Engineer",
        ):
            job = remotive_job(title=title)
            self.assertTrue(is_role_relevant(job, pref.job_title, aliases), title)

    def test_rejects_data_entry_for_data_engineer(self):
        pref = preference(job_title="Data Engineer", skills="Python, SQL, Spark, AWS, ETL")
        aliases = get_role_aliases(pref.job_title)
        job = remotive_job(
            title="Data Entry Specialist",
            category="All others",
            tags=[],
            description="Enter spreadsheet data all day.",
        )
        self.assertFalse(is_role_relevant(job, pref.job_title, aliases))

    def test_url_validation_prefers_url_and_rejects_invalid(self):
        self.assertEqual(
            extract_valid_job_url({"url": " https://remotive.com/remote-jobs/dev/java-1 ", "job_url": "x"}),
            "https://remotive.com/remote-jobs/dev/java-1",
        )
        self.assertEqual(
            extract_valid_job_url({"job_url": "https://remotive.com/remote-jobs/dev/java-2"}),
            "https://remotive.com/remote-jobs/dev/java-2",
        )
        self.assertIsNone(extract_valid_job_url({"url": "https://remotive.com"}))
        self.assertIsNone(extract_valid_job_url({"url": "https://example.com/job"}))
        self.assertIsNone(extract_valid_job_url({"url": "not-a-url"}))
        self.assertIsNone(extract_valid_job_url({}))

    def test_clean_html_keeps_useful_text(self):
        cleaned = clean_html("<p>Build&nbsp;<b>APIs</b> with Java.</p>")
        self.assertIn("Build", cleaned)
        self.assertIn("APIs", cleaned)
        self.assertIn("Java", cleaned)
        self.assertNotIn("<", cleaned)

    def test_normalize_structure(self):
        normalized = normalize_remotive_job(remotive_job(), preference())
        self.assertEqual(
            set(normalized.keys()),
            {
                "title", "company", "location", "work_mode", "job_type",
                "description", "skills_required", "source", "job_url",
            },
        )
        self.assertEqual(normalized["source"], "Remotive API")
        self.assertEqual(normalized["work_mode"], "Remote")
        self.assertTrue(normalized["job_url"].startswith("https://"))

    def test_skill_relevance_allows_partial_overlap(self):
        pref = preference()
        aliases = get_role_aliases(pref.job_title)
        job = remotive_job(tags=["Jenkins"], description="Automation role using Jenkins pipelines.")
        ok, matched = passes_skill_relevance(job, pref, aliases)
        self.assertTrue(ok)
        self.assertIn("jenkins", matched)

    @patch("app.agents.updated_job_search_agent.fetch_jobs_from_remotive")
    def test_get_updated_jobs_filters_and_limits(self, mock_fetch):
        mock_fetch.return_value = [
            remotive_job(title="Senior SDET", url="https://remotive.com/remote-jobs/software-development/sdet-1"),
            remotive_job(title="QA Automation Engineer", url="https://remotive.com/remote-jobs/software-development/qa-2"),
            remotive_job(title="Marketing Manager", category="Marketing", url="https://remotive.com/remote-jobs/marketing/mkt-3"),
            remotive_job(title="Senior SDET", company_name="Acme QA", url="https://remotive.com/remote-jobs/software-development/sdet-1"),
        ]
        result = get_updated_jobs_from_remotive(preference(), max_jobs=10)
        titles = [job["title"] for job in result["jobs"]]
        self.assertIn("Senior SDET", titles)
        self.assertIn("QA Automation Engineer", titles)
        self.assertNotIn("Marketing Manager", titles)
        self.assertLessEqual(len(result["jobs"]), 10)
        for job in result["jobs"]:
            self.assertTrue(job["job_url"].startswith("https://"))
            self.assertEqual(job["source"], "Remotive API")

    @patch("app.agents.updated_job_search_agent.fetch_jobs_from_remotive")
    def test_no_relevant_jobs_returns_empty(self, mock_fetch):
        mock_fetch.return_value = [
            remotive_job(title="Sales Executive", category="Sales", tags=[], description="Sell products."),
        ]
        result = get_updated_jobs_from_remotive(preference(), max_jobs=10)
        self.assertEqual(result["jobs"], [])

    def test_evaluate_rejects_missing_url(self):
        pref = preference()
        aliases = get_role_aliases(pref.job_title)
        job = remotive_job()
        job.pop("url")
        self.assertIsNone(evaluate_remotive_job(job, pref, aliases))


if __name__ == "__main__":
    unittest.main()
