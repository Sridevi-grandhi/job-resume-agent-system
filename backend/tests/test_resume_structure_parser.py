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
