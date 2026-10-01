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


class MarkerLessBulletRecoveryTests(unittest.TestCase):
    """Regression: a real accomplishment line under an employer must never be
    silently discarded just because it doesn't start with a bullet marker AND
    doesn't happen to open with one of a fixed action-verb allowlist."""

    def test_marker_less_accomplishment_line_is_captured_as_a_bullet(self):
        resume = """
Jane Candidate

Professional Experience
Acme Corp | Jan 2020 - Present
Senior Software Engineer
Collaborated with cross-functional teams to redesign the checkout flow and improve conversion.
Reduced infrastructure costs by consolidating redundant services across environments.
""".strip()
        structure = parse_resume_structure(resume)
        self.assertEqual(len(structure["employments"]), 1)
        bullets = structure["employments"][0]["bullets"]
        self.assertIn(
            "Collaborated with cross-functional teams to redesign the checkout flow and improve conversion.",
            bullets,
        )
        self.assertIn(
            "Reduced infrastructure costs by consolidating redundant services across environments.",
            bullets,
        )

    def test_work_history_heading_with_marker_less_bullets_is_not_dropped(self):
        resume = """
Jane Candidate

Work History
Acme Corp | New York, NY    Jan 2020 - Present
Senior Software Engineer
Collaborated with cross-functional teams to redesign the checkout flow and improve conversion.
Reduced infrastructure costs by consolidating redundant services across environments.
""".strip()
        structure = parse_resume_structure(resume)
        self.assertEqual(len(structure["employments"]), 1)
        self.assertEqual(structure["employments"][0]["company"], "Acme Corp")
        self.assertTrue(structure["employments"][0]["bullets"])

    def test_work_history_heading_with_company_and_dates_but_no_bullets_still_kept(self):
        resume = """
Jane Candidate

Work History
Acme Corp | New York, NY    Jan 2020 - Present
""".strip()
        structure = parse_resume_structure(resume)
        self.assertEqual(len(structure["employments"]), 1)
        self.assertEqual(structure["employments"][0]["company"], "Acme Corp")


class SplitEmploymentHeaderTests(unittest.TestCase):
    """Regression: an employer header split across multiple lines — with NO
    date on the company/header line itself — used to either lose the
    employer entirely or misfire (a lone date line on its own row was
    mistaken for a brand-new, company-less employer, closing the real one
    prematurely). _parse_employment_blocks now uses a conservative,
    multi-signal look-ahead (a company-shaped line + a date a few lines
    below it, with nothing bullet-like in between) to recognize these
    boundaries, without ever reading arbitrary prose as an employer."""

    def test_company_and_location_same_line_role_and_date_on_separate_lines(self):
        resume = """
Jane Candidate

Professional Experience
Acme Corp, Austin, TX
Senior Developer
Jan 2023 - Present
- Built distributed systems for enterprise clients.
""".strip()
        structure = parse_resume_structure(resume)
        self.assertEqual(len(structure["employments"]), 1)
        employment = structure["employments"][0]
        self.assertIn("Acme Corp", employment["company"])
        self.assertEqual(employment["dates"], "Jan 2023 - Present")
        self.assertIn("Built distributed systems for enterprise clients.", employment["bullets"])

    def test_company_location_role_date_each_on_their_own_line(self):
        resume = """
Jane Candidate

Professional Experience
Northlight Analytics
Boston, MA
Backend Engineer
Jan 2019 - May 2021
- Reduced infrastructure costs by consolidating redundant services.
""".strip()
        structure = parse_resume_structure(resume)
        self.assertEqual(len(structure["employments"]), 1)
        employment = structure["employments"][0]
        self.assertEqual(employment["company"], "Northlight Analytics")
        self.assertEqual(employment["title"], "Backend Engineer")
        self.assertEqual(employment["dates"], "Jan 2019 - May 2021")
        self.assertIn("Reduced infrastructure costs by consolidating redundant services.", employment["bullets"])

    def test_two_consecutive_split_header_employers_stay_distinct_and_in_order(self):
        resume = """
Jane Candidate

Professional Experience
Northlight Analytics
Boston, MA
Backend Engineer
Jan 2019 - May 2021
- Reduced infrastructure costs by consolidating redundant services.

Riverbend Software
Austin, TX
Software Engineer
Aug 2016 - Dec 2018
- Built internal tooling used by every engineering team.
""".strip()
        structure = parse_resume_structure(resume)
        self.assertEqual(len(structure["employments"]), 2)
        first, second = structure["employments"]
        self.assertEqual(first["company"], "Northlight Analytics")
        self.assertEqual(first["dates"], "Jan 2019 - May 2021")
        self.assertIn("Reduced infrastructure costs by consolidating redundant services.", first["bullets"])
        self.assertEqual(second["company"], "Riverbend Software")
        self.assertEqual(second["dates"], "Aug 2016 - Dec 2018")
        self.assertIn("Built internal tooling used by every engineering team.", second["bullets"])
        # No cross-contamination between the two blocks.
        self.assertNotIn("Reduced infrastructure costs by consolidating redundant services.", second["bullets"])
        self.assertNotIn("Built internal tooling used by every engineering team.", first["bullets"])

    def test_three_consecutive_split_header_employers_all_survive(self):
        resume = """
Jane Candidate

Professional Experience
Bright Peak Systems
Denver, CO
Platform Engineer
Mar 2022 - Present
- Automated infrastructure provisioning across multiple regions.

Vertex Logistics Group
Chicago, IL
Software Engineer II
Jul 2019 - Feb 2022
- Redesigned the shipment tracking pipeline for lower latency.

Amber Coast Media
Miami, FL
Software Engineer
May 2016 - Jun 2019
- Delivered a customer-facing analytics dashboard used company-wide.
""".strip()
        structure = parse_resume_structure(resume)
        self.assertEqual(len(structure["employments"]), 3)
        companies = [e["company"] for e in structure["employments"]]
        self.assertEqual(companies, ["Bright Peak Systems", "Vertex Logistics Group", "Amber Coast Media"])
        dates = [e["dates"] for e in structure["employments"]]
        self.assertEqual(dates, ["Mar 2022 - Present", "Jul 2019 - Feb 2022", "May 2016 - Jun 2019"])
        all_bullets = [b for e in structure["employments"] for b in e["bullets"]]
        self.assertIn("Automated infrastructure provisioning across multiple regions.", all_bullets)
        self.assertIn("Redesigned the shipment tracking pipeline for lower latency.", all_bullets)
        self.assertIn("Delivered a customer-facing analytics dashboard used company-wide.", all_bullets)

    def test_short_prose_fragments_near_a_later_date_do_not_become_a_false_employer(self):
        """A short, un-punctuated responsibility fragment ('Cross-functional
        stakeholder alignment') sitting between a real employer's bullets and
        the NEXT real employer's own dated header must never itself be read
        as a new employer just because a date happens to appear a couple of
        lines below it — the look-ahead must require the candidate line to
        actually read like a header (company/role naming conventions), not
        merely be short and nearby a date."""
        resume = """
Jane Candidate

Professional Experience
Acme Corp | Jan 2020 - Present
- Built REST APIs for internal services.
Cross-functional stakeholder alignment
Program roadmap execution
Beacon Robotics | Apr 2024 - Present
- Led firmware integration for autonomous delivery robots.
""".strip()
        structure = parse_resume_structure(resume)
        self.assertEqual(len(structure["employments"]), 2)
        companies = [e["company"] for e in structure["employments"]]
        self.assertEqual(companies, ["Acme Corp", "Beacon Robotics"])

    def test_project_heading_with_dates_does_not_produce_employment_blocks(self):
        resume = """
Jane Candidate

Professional Experience
Acme Corp | Engineer | Jan 2020 - Present
- Built internal tooling.

Projects
Inventory Platform
Jan 2021 - Mar 2021
- Delivered inventory tracking APIs.
""".strip()
        structure = parse_resume_structure(resume)
        self.assertEqual(len(structure["employments"]), 1)
        self.assertEqual(structure["employments"][0]["company"], "Acme Corp")
        self.assertTrue(structure["projects"], "project content should still be recovered, just not as an employer")

    def test_education_heading_with_dates_does_not_produce_employment_blocks(self):
        resume = """
Jane Candidate

Professional Experience
Acme Corp | Engineer | Jan 2020 - Present
- Built internal tooling.

Education
State University
B.S. Computer Science
Aug 2012 - May 2016
""".strip()
        structure = parse_resume_structure(resume)
        self.assertEqual(len(structure["employments"]), 1)
        self.assertEqual(structure["employments"][0]["company"], "Acme Corp")


if __name__ == "__main__":
    unittest.main()
