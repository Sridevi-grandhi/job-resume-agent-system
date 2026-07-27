import unittest
from unittest.mock import Mock, patch

from app.agents.job_url_import_agent import (
    JobUrlImportError,
    _extract_html_details,
    build_job_preview,
    clean_job_description,
    clean_job_title,
    extract_skills,
    normalize_job_url,
    safe_fetch_job_page,
)


class JobUrlNormalizationTests(unittest.TestCase):
    def setUp(self):
        patcher = patch(
            "app.agents.job_url_import_agent._public_ip_addresses",
            return_value={"93.184.216.34"},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_linkedin_direct_url(self):
        source, url, job_id = normalize_job_url(
            "https://www.linkedin.com/jobs/view/1234567890/?trackingId=ignored"
        )
        self.assertEqual(source, "LinkedIn")
        self.assertEqual(job_id, "1234567890")
        self.assertEqual(url, "https://www.linkedin.com/jobs/view/1234567890/")

    def test_linkedin_search_url_with_current_job_id(self):
        _, url, job_id = normalize_job_url(
            "https://www.linkedin.com/jobs/search/?currentJobId=1234567890&keywords=Data%20Engineer"
        )
        self.assertEqual(job_id, "1234567890")
        self.assertEqual(url, "https://www.linkedin.com/jobs/view/1234567890/")

    def test_linkedin_search_url_without_selected_job_is_rejected(self):
        with self.assertRaisesRegex(JobUrlImportError, "currentJobId"):
            normalize_job_url(
                "https://www.linkedin.com/jobs/search/?keywords=Data%20Engineer"
            )

    def test_indeed_direct_url(self):
        source, url, job_id = normalize_job_url(
            "https://www.indeed.com/viewjob?jk=abc123&from=search"
        )
        self.assertEqual(source, "Indeed")
        self.assertEqual(job_id, "abc123")
        self.assertEqual(url, "https://www.indeed.com/viewjob?jk=abc123")

    def test_dice_direct_url(self):
        source, url, job_id = normalize_job_url(
            "https://www.dice.com/job-detail/data-engineer-abc123?searchlink=1"
        )
        self.assertEqual(source, "Dice")
        self.assertEqual(job_id, "data-engineer-abc123")
        self.assertEqual(url, "https://www.dice.com/job-detail/data-engineer-abc123")

    def test_generic_employer_job_url_is_supported(self):
        source, url, job_id = normalize_job_url("https://careers.example.com/jobs/123?lang=en")
        self.assertEqual(source, "Employer Site")
        self.assertEqual(url, "https://careers.example.com/jobs/123?lang=en")
        self.assertEqual(job_id, "123")

    def test_regional_indeed_url_is_supported(self):
        source, url, job_id = normalize_job_url("https://uk.indeed.com/viewjob?jk=regional123")
        self.assertEqual(source, "Indeed")
        self.assertEqual(url, "https://uk.indeed.com/viewjob?jk=regional123")
        self.assertEqual(job_id, "regional123")

    def test_non_http_scheme_is_rejected(self):
        with self.assertRaisesRegex(JobUrlImportError, "HTTP and HTTPS"):
            normalize_job_url("file:///etc/passwd")

    def test_linkedin_browser_title_is_cleaned(self):
        title = clean_job_title(
            "Tata Consultancy Services hiring Java Developer in Dallas, Texas, United States | LinkedIn"
        )
        self.assertEqual(title, "Java Developer")

    def test_description_preserves_sections_and_extracts_skills(self):
        raw = (
            "<div>Job Description</div><div>Must Have Technical/Functional Skills</div>"
            "<ul><li>Primary Skill: Core Java</li><li>Spring Boot</li><li>Docker</li></ul>"
        )
        cleaned = clean_job_description(raw)
        self.assertIn("Job Description\n\nMust Have Technical/Functional Skills", cleaned)
        self.assertIn("Primary Skill: Core Java", cleaned)
        self.assertEqual(extract_skills("", cleaned), "Java, Spring Boot, Docker")

    def test_json_ld_jobposting_is_preferred(self):
        page = """
        <html><head><title>Generic browser title</title>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "JobPosting",
          "title": "Java Developer",
          "hiringOrganization": {"name": "Example Corp"},
          "jobLocation": {"address": {
            "addressLocality": "Dallas",
            "addressRegion": "TX",
            "addressCountry": "US"
          }},
          "employmentType": "FULL_TIME",
          "description": "<p>Responsibilities</p><p>Build Java services with Spring Boot and Docker.</p>",
          "skills": ["Java", "Spring Boot", "Docker"]
        }
        </script></head><body></body></html>
        """
        details, reason, _ = _extract_html_details(
            page, "Employer Site", "https://careers.example.com/jobs/123", 200
        )
        self.assertFalse(reason)
        self.assertEqual(details["title"], "Java Developer")
        self.assertEqual(details["company"], "Example Corp")
        self.assertEqual(details["location"], "Dallas, TX, US")
        self.assertIn("Spring Boot", details["skills_required"])

    def test_captcha_page_is_not_job_content(self):
        page = "<html><head><title>Verify you are human</title></head><body>CAPTCHA security check</body></html>"
        details, reason, _ = _extract_html_details(
            page, "Indeed", "https://www.indeed.com/viewjob?jk=abc", 200
        )
        self.assertEqual(details, {})
        self.assertIn("blocked", reason.lower())

    @patch("app.agents.job_url_import_agent.safe_fetch_job_page")
    def test_blocked_provider_returns_manual_required_contract(self, fetch):
        fetch.return_value = {
            "ok": False,
            "status_code": 403,
            "final_url": "https://www.indeed.com/viewjob?jk=abc123",
            "html": "",
            "content_type": "",
            "blocked": True,
            "reason": "Provider returned HTTP 403.",
        }
        preview = build_job_preview("https://www.indeed.com/viewjob?jk=abc123")
        self.assertTrue(preview["success"])
        self.assertEqual(preview["status"], "manual_required")
        self.assertTrue(preview["requires_manual_details"])
        self.assertEqual(preview["fields_fetched"], [])
        self.assertIn("title", preview["fields_missing"])
        self.assertEqual(preview["blocked_reason"], "Provider returned HTTP 403.")

    @patch("app.agents.job_url_import_agent.safe_fetch_job_page")
    def test_linkedin_title_only_metadata_returns_partial_contract(self, fetch):
        fetch.return_value = {
            "ok": True,
            "status_code": 200,
            "final_url": "https://www.linkedin.com/jobs/view/4437090011/",
            "html": """
                <html><head>
                <title>Tata Consultancy Services hiring Java Developer in Dallas, TX | LinkedIn</title>
                <meta property="og:title" content="Tata Consultancy Services hiring Java Developer in Dallas, TX | LinkedIn">
                </head><body><main>Public job summary</main></body></html>
            """,
            "content_type": "text/html",
            "blocked": False,
            "reason": "",
        }
        preview = build_job_preview("https://www.linkedin.com/jobs/view/4437090011/")
        self.assertEqual(preview["status"], "partial")
        self.assertEqual(preview["title"], "Java Developer")
        self.assertEqual(preview["company"], "Tata Consultancy Services")
        self.assertEqual(preview["location"], "Dallas, TX")
        self.assertIn("description", preview["fields_missing"])

    def test_safe_fetch_returns_structure_for_connection_failure(self):
        session = Mock()
        session.get.side_effect = __import__("requests").ConnectionError()
        result = safe_fetch_job_page("https://www.linkedin.com/jobs/view/4437090011/", session)
        self.assertFalse(result["ok"])
        self.assertTrue(result["blocked"])
        self.assertIn("reason", result)


if __name__ == "__main__":
    unittest.main()
