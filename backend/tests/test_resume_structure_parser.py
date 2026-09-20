import unittest

from app.services.resume_structure_parser import parse_resume_structure


class SectionHeadingSynonymTests(unittest.TestCase):
    def test_work_history_recognized_as_experience_section(self):
        resume = """
Jane Candidate

Work History
Acme Corp | Engineer | Jan 2020 - Present
- Built internal tooling.
""".strip()
        structure = parse_resume_structure(resume)
        headings = [s["heading"] for s in structure["sections"]]
        self.assertIn("Work History", headings)
        self.assertEqual(len(structure["employments"]), 1)

    def test_career_history_and_relevant_experience_recognized(self):
        for heading in ["Career History", "Relevant Experience", "Employment"]:
            resume = f"""
Jane Candidate

{heading}
Acme Corp | Engineer | Jan 2020 - Present
- Did the work.
""".strip()
            with self.subTest(heading=heading):
                structure = parse_resume_structure(resume)
                headings = [s["heading"] for s in structure["sections"]]
                self.assertIn(heading, headings)
                self.assertEqual(len(structure["employments"]), 1)

    def test_tools_and_technologies_heading_recognized(self):
        resume = """
Jane Candidate

Tools & Technologies
AWS, Terraform, Docker
""".strip()
        structure = parse_resume_structure(resume)
        headings = [s["heading"] for s in structure["sections"]]
        self.assertIn("Tools & Technologies", headings)

    def test_bare_tool_word_does_not_become_a_false_heading(self):
        """Regression guard: a project literally named 'Internal Tool' must stay inside
        the Projects section instead of being misread as a new section heading just
        because it contains the substring 'tool'."""
        resume = """
Jane Candidate

Technical Skills
Java, Spring Boot

Projects
Internal Tool
- Built Java and Spring Boot services.
""".strip()
        structure = parse_resume_structure(resume)
        headings = [s["heading"] for s in structure["sections"]]
        self.assertIn("Projects", headings)
        self.assertNotIn("Internal Tool", headings)
        projects_section = next(s for s in structure["sections"] if s["heading"] == "Projects")
        self.assertIn("Internal Tool", projects_section["content"])


class ClientLabelFalsePositiveTests(unittest.TestCase):
    """Regression: CLIENT_PATTERN used to match ANY occurrence of the word
    "client" followed by a hyphen ANYWHERE in a line — "client-side",
    "client-facing", "client-server" are extremely common, legitimate
    technical phrases across every engineering role. A false match silently
    dropped the ENTIRE bullet from parsing (treated as a "Client: X" label
    instead), which cascades into lost ATS evidence credit for whatever
    that bullet demonstrated, and misaligns every subsequent bullet in the
    original-vs-generated diff used for "AI Improved" change tracking."""

    def test_client_side_bullet_is_not_dropped(self):
        resume = """
Jane Candidate

Professional Experience
Acme Corp | Engineer | Jan 2020 - Present
- Tested REST APIs using Postman.
- Developed internal dashboards using React, implementing client-side logic.
- Built deployment pipelines using Jenkins and Docker.
""".strip()
        structure = parse_resume_structure(resume)
        bullets = structure["employments"][0]["bullets"]
        self.assertEqual(len(bullets), 3)
        self.assertTrue(any("client-side logic" in b for b in bullets))

    def test_client_facing_bullet_is_not_dropped(self):
        resume = """
Jane Candidate

Professional Experience
Acme Corp | Engineer | Jan 2020 - Present
- Built client-facing APIs to support external partner integrations.
- Automated regression test suites using Selenium.
""".strip()
        structure = parse_resume_structure(resume)
        bullets = structure["employments"][0]["bullets"]
        self.assertEqual(len(bullets), 2)
        self.assertTrue(any("client-facing APIs" in b for b in bullets))

    def test_client_server_bullet_is_not_dropped(self):
        resume = """
Jane Candidate

Professional Experience
Acme Corp | Engineer | Jan 2020 - Present
- Designed client-server architecture for real-time messaging.
- Deployed services to AWS using Terraform.
""".strip()
        structure = parse_resume_structure(resume)
        bullets = structure["employments"][0]["bullets"]
        self.assertEqual(len(bullets), 2)
        self.assertTrue(any("client-server architecture" in b for b in bullets))

    def test_genuine_client_label_line_still_recognized(self):
        """The original, intended use case — a dedicated "Client: X" line in
        a consulting/staffing-style resume — must still work."""
        resume = """
Jane Candidate

Professional Experience
Staffing Co | Engineer | Jan 2020 - Present
Client: Acme Corp
- Built internal tooling for the client engagement.
""".strip()
        structure = parse_resume_structure(resume)
        employment = structure["employments"][0]
        self.assertEqual(employment.get("client"), "Acme Corp")

    def test_client_dash_project_name_recognized(self):
        resume = """
Jane Candidate

Professional Experience
Staffing Co | Engineer | Jan 2020 - Present
Client - Globex Inc
- Built internal tooling for the client engagement.
""".strip()
        structure = parse_resume_structure(resume)
        employment = structure["employments"][0]
        self.assertEqual(employment.get("client"), "Globex Inc")


class AllCapsFalseHeadingTests(unittest.TestCase):
    """Regression for the live section_order false-positive: an unconditional
    "any short ALL-CAPS line is a heading" branch used to fire before checking
    against the real SECTION_HEADING_PATTERNS vocabulary, so all-caps employer
    names (CISCO SYSTEMS, DELOITTE) and experience sub-labels (RESPONSIBILITIES,
    CLIENT, ENVIRONMENT) were misdetected as brand-new top-level sections,
    fragmenting "Professional Experience" and breaking downstream section-order
    validation used to gate resume downloads."""

    def test_allcaps_employer_names_and_sublabels_stay_inside_experience(self):
        resume = """
Jane Doe
jane.doe@example.com | 555-222-3333

PROFESSIONAL SUMMARY
SDET with 6+ years of automation experience across Java and Selenium stacks.

TECHNICAL SKILLS
Programming Languages: Java, Python
Backend: Spring Boot, REST APIs
Databases: PostgreSQL, MySQL
Cloud & DevOps: AWS, Docker, Jenkins

PROFESSIONAL EXPERIENCE
CISCO SYSTEMS, San Jose, CA
Role: SDET
CLIENT: Internal Platform Team
Jun 2019 - Present
RESPONSIBILITIES
- Engineered end-to-end Selenium/TestNG regression suites integrated into CI/CD.
- Designed JMeter load-test scenarios covering peak-traffic API scenarios.
ENVIRONMENT: Selenium, TestNG, JMeter, Jenkins

DELOITTE, New York, NY
Role: QA Engineer
CLIENT: Retail Analytics
Jan 2016 - May 2019
RESPONSIBILITIES
- Automated regression suites using Selenium and TestNG.
- Built performance test plans in JMeter.
ENVIRONMENT: Selenium, TestNG, JMeter

EDUCATION
B.S. Computer Science, Example University
""".strip()
        structure = parse_resume_structure(resume)
        headings = [s["heading"] for s in structure["sections"]]
        # Exactly one top-level entry per real section — no employer name or
        # sub-label ("CISCO SYSTEMS", "DELOITTE", "RESPONSIBILITIES", "CLIENT",
        # "ENVIRONMENT") may have split out as its own section.
        self.assertEqual(
            [h for h in headings],
            ["Professional Summary", "Technical Skills", "Professional Experience", "Education"],
        )
        for bogus in ("CISCO SYSTEMS", "DELOITTE", "RESPONSIBILITIES", "CLIENT", "ENVIRONMENT"):
            self.assertNotIn(bogus, headings)


class HeadinglessResumeFallbackTests(unittest.TestCase):
    def test_recovers_employment_blocks_when_no_headings_are_detected(self):
        resume = """
John Doe
john.doe@example.com

DevOps Engineer with 7 years of experience.

Cloud: AWS, Azure
Containers: Docker, Kubernetes

Senior DevOps Engineer | Acme Corp | Jan 2021 - Present
- Provisioned AWS infrastructure using Terraform.
- Administered Linux servers.

DevOps Engineer | Beta Inc | Jun 2017 - Dec 2020
- Automated CI pipelines with Jenkins.
""".strip()
        structure = parse_resume_structure(resume)
        headings = [s["heading"] for s in structure["sections"]]
        self.assertEqual(headings, ["Content"])
        self.assertEqual(len(structure["employments"]), 2)
        all_bullets = " ".join(b for e in structure["employments"] for b in e["bullets"])
        self.assertIn("Provisioned AWS infrastructure using Terraform.", all_bullets)
        self.assertIn("Administered Linux servers.", all_bullets)
        self.assertIn("Automated CI pipelines with Jenkins.", all_bullets)

    def test_fallback_does_not_fire_when_headings_already_found_content(self):
        """A resume that DOES have recognizable headings must not additionally run the
        headingless fallback (which would double-count employment blocks)."""
        resume = """
Jane Candidate

Professional Summary
Backend engineer.

Technical Skills
Java, Spring Boot

Professional Experience
Acme | Java Developer | Jan 2020 - Present
- Built Java services.
""".strip()
        structure = parse_resume_structure(resume)
        self.assertEqual(len(structure["employments"]), 1)

    def test_short_preamble_without_bullets_is_not_treated_as_employment(self):
        resume = "Just a short line of unstructured text with no headings or bullets at all."
        structure = parse_resume_structure(resume)
        self.assertEqual(structure["employments"], [])


if __name__ == "__main__":
    unittest.main()
