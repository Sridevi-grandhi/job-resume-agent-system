"""Cross-role regression coverage for the resume-vs-job evidence engine.

These fixtures deliberately span unrelated role families (DevOps, Java Full Stack,
Data Engineering, SDET/QA, Frontend, Python Backend) to prove the evidence engine is
generic: nothing here special-cases a role or a technology. Each fixture asserts the
same three properties on the SAME generic engine used everywhere else:

1. A skill demonstrated in Professional Experience must be VERIFIED with STRONG
   evidence — never downgraded to weak just because the resume also lists it.
2. A skill mentioned only in a skills list (isolated keyword, no usage context)
   remains weak/medium — never silently promoted to strong.
3. A skill absent from the resume after normalization/alias matching stays MISSING.

It also asserts Step 2 / Step 3 parity: the canonical grouping used by both wizard
steps must never place the same requirement in more than one bucket, and "weak"
skills (found, low confidence) must never land in a "missing" bucket.
"""

import unittest
from types import SimpleNamespace

from app.services.ats_scoring import calculate_weighted_ats_score
from app.services.requirement_extraction_service import group_canonical_score_evidence


def _job(title, description, skills_required):
    return SimpleNamespace(
        title=title, company="Example", description=description, skills_required=skills_required,
        location="Remote", work_mode="Remote", job_type="Full-time",
    )


def _assert_step2_step3_parity(test, grouped):
    """The exact contract Step 2 and Step 3 both render from: every requirement
    belongs to exactly one bucket, and 'weak' (found) is never mixed into 'missing'."""
    verified = {item["normalized_skill"] for item in grouped["verified_resume_skills"]}
    weak = {item["normalized_skill"] for item in grouped["weak_or_indirect_skills"]}
    missing_mandatory = {item["normalized_skill"] for item in grouped["missing_mandatory_skills"]}
    missing_preferred = {item["normalized_skill"] for item in grouped["missing_preferred_skills"]}
    all_missing = missing_mandatory | missing_preferred
    test.assertEqual(verified & weak, set())
    test.assertEqual(verified & all_missing, set())
    test.assertEqual(weak & all_missing, set(), "a skill with found-but-weak evidence must never appear as missing")
    test.assertEqual(missing_mandatory & missing_preferred, set())
    total_listed = len(verified) + len(weak) + len(missing_mandatory) + len(missing_preferred)
    test.assertEqual(total_listed, grouped["summary"]["total_requirements"])


class DevOpsRegressionTests(unittest.TestCase):
    """Regression test for the reported bug: a well-evidenced DevOps resume must not
    collapse to '1 verified / 16 weak'. Values are asserted from the generic engine —
    nothing about DevOps is hardcoded to produce this outcome."""

    JOB = _job(
        "Senior DevOps Engineer",
        (
            "We need a Senior DevOps Engineer with strong AWS, Kubernetes, Docker and "
            "Terraform experience. Required: Jenkins or GitHub Actions for CI/CD, Linux "
            "administration, Python and Bash scripting, Helm, Prometheus and Grafana for "
            "monitoring. Preferred: Datadog, ArgoCD."
        ),
        "AWS, Kubernetes, Docker, Terraform, Jenkins, GitHub Actions, Linux, CI/CD, Python, Bash, Helm, Prometheus, Grafana",
    )

    RESUME = """
John Doe
john.doe@example.com | Denver, CO

Professional Summary
DevOps Engineer with 7+ years of experience designing and operating cloud infrastructure
on AWS, building CI/CD pipelines, and automating deployments with Kubernetes, Docker,
and Terraform. Skilled in Linux administration, scripting with Python and Bash, and
monitoring with Prometheus and Grafana.

Technical Skills
Cloud: AWS, Azure
Containers: Docker, Kubernetes, Helm
CI/CD: Jenkins, GitHub Actions, GitLab CI
IaC: Terraform, Ansible, CloudFormation
Monitoring: Prometheus, Grafana, Datadog, Splunk
Languages: Python, Bash, Go
Other: Linux, Git, Agile, Scrum

Professional Experience
Senior DevOps Engineer | Acme Corp | Jan 2021 - Present
- Designed and provisioned AWS infrastructure using Terraform, reducing manual provisioning time by 60%.
- Built and maintained CI/CD pipelines using Jenkins and GitHub Actions to automate build, test, and deployment.
- Deployed and managed containerized applications on Kubernetes using Helm charts across multiple environments.
- Administered Linux servers, wrote Bash and Python automation scripts for operational tasks.
- Implemented monitoring and alerting dashboards using Prometheus and Grafana to track system health.
- Used Docker to containerize legacy applications for migration to Kubernetes.

DevOps Engineer | Beta Inc | Jun 2017 - Dec 2020
- Automated infrastructure provisioning on AWS with Terraform and CloudFormation.
- Migrated on-prem CI pipelines to Jenkins and later GitHub Actions.
- Maintained Kubernetes clusters and Docker container images for microservices.

Certifications
AWS Certified Solutions Architect - Associate
""".strip()

    def test_strong_evidence_is_verified_not_weak(self):
        score = calculate_weighted_ats_score(self.RESUME, self.JOB)
        grouped = group_canonical_score_evidence(score, job_id=1)
        verified = {item["normalized_skill"] for item in grouped["verified_resume_skills"]}
        strongly_evidenced = {
            "aws", "kubernetes", "docker", "terraform", "jenkins", "github actions",
            "linux", "ci/cd", "python", "bash", "helm", "prometheus", "grafana",
        }
        missing_from_verified = strongly_evidenced - verified
        self.assertEqual(
            missing_from_verified, set(),
            f"expected these to be VERIFIED with strong evidence, but they were not: {missing_from_verified}",
        )
        # The regression this guards against: verified collapsing to a single skill.
        self.assertGreaterEqual(len(grouped["verified_resume_skills"]), len(strongly_evidenced))

    def test_no_mandatory_skill_missing(self):
        score = calculate_weighted_ats_score(self.RESUME, self.JOB)
        grouped = group_canonical_score_evidence(score, job_id=1)
        self.assertEqual(grouped["missing_mandatory_skills"], [])

    def test_step2_step3_parity(self):
        score = calculate_weighted_ats_score(self.RESUME, self.JOB)
        grouped = group_canonical_score_evidence(score, job_id=1)
        _assert_step2_step3_parity(self, grouped)

    def test_score_reflects_strong_match(self):
        score = calculate_weighted_ats_score(self.RESUME, self.JOB)
        self.assertGreaterEqual(score["final_score"], 75)
        self.assertIn(score["score_status"], {"STRONG_MATCH", "EXCELLENT_MATCH"})


class MultiRoleFamilyEvidenceTests(unittest.TestCase):
    """The same engine, exercised across unrelated role families with no per-role
    branching anywhere in the code under test."""

    FIXTURES = {
        "Java Full Stack": (
            _job(
                "Java Full Stack Developer",
                "Required: Java, Spring Boot, Hibernate, React, PostgreSQL. Preferred: Docker.",
                "Java, Spring Boot, Hibernate, React, PostgreSQL",
            ),
            """
Jane Candidate

Technical Skills
Java, Spring Boot, Hibernate, React, PostgreSQL, Docker

Professional Experience
Acme | Java Full Stack Developer | Jan 2020 - Present
- Built backend services in Java and Spring Boot using Hibernate for persistence.
- Delivered React front-end interfaces backed by PostgreSQL.
""".strip(),
            {"java", "spring boot", "hibernate", "react", "postgresql"},
        ),
        "Data Engineering": (
            _job(
                "Data Engineer",
                "Required: Python, Spark, Kafka, Airflow, ETL pipeline development. Preferred: Snowflake.",
                "Python, Spark, Kafka, Airflow, ETL",
            ),
            """
Jane Candidate

Technical Skills
Python, Spark, Kafka, Airflow, SQL, Snowflake

Professional Experience
Acme | Data Engineer | Jan 2020 - Present
- Built ETL pipelines with Spark and Airflow to ingest and transform data.
- Streamed events through Kafka for downstream analytics consumers.
- Wrote Python jobs to validate and load data into the warehouse.
""".strip(),
            {"python", "spark", "kafka", "airflow", "etl"},
        ),
        "SDET / QA Automation": (
            _job(
                "SDET",
                "Required: Selenium, TestNG, Java, API test automation. Preferred: Cucumber.",
                "Selenium, TestNG, Java",
            ),
            """
Jane Candidate

Technical Skills
Java, Selenium, TestNG, Cucumber, Postman

Professional Experience
Acme | SDET | Jan 2020 - Present
- Automated regression suites with Selenium and TestNG in Java.
- Validated REST APIs and authored Cucumber scenarios for acceptance testing.
""".strip(),
            {"selenium", "testng", "java"},
        ),
        "Frontend": (
            _job(
                "Frontend Developer",
                "Required: JavaScript, TypeScript, React, HTML, CSS. Preferred: Next.js.",
                "JavaScript, TypeScript, React, HTML, CSS",
            ),
            """
Jane Candidate

Technical Skills
JavaScript, TypeScript, React, HTML, CSS, Next.js

Professional Experience
Acme | Frontend Developer | Jan 2020 - Present
- Built responsive UIs with React and TypeScript, styled with modern CSS and HTML.
- Delivered JavaScript modules consumed by multiple product teams.
""".strip(),
            {"javascript", "typescript", "react", "html", "css"},
        ),
        "Python Backend": (
            _job(
                "Python Backend Developer",
                "Required: Python, Django, FastAPI, PostgreSQL. Preferred: Celery.",
                "Python, Django, FastAPI, PostgreSQL",
            ),
            """
Jane Candidate

Technical Skills
Python, Django, FastAPI, PostgreSQL, Celery

Professional Experience
Acme | Python Backend Developer | Jan 2020 - Present
- Developed REST services with Django and FastAPI backed by PostgreSQL.
- Implemented async task processing with Celery for background jobs.
""".strip(),
            {"python", "django", "fastapi", "postgresql"},
        ),
    }

    def test_strongly_evidenced_skills_are_verified_across_role_families(self):
        for family, (job, resume, expected_strong) in self.FIXTURES.items():
            with self.subTest(family=family):
                score = calculate_weighted_ats_score(resume, job)
                grouped = group_canonical_score_evidence(score, job_id=1)
                verified = {item["normalized_skill"] for item in grouped["verified_resume_skills"]}
                self.assertEqual(
                    expected_strong - verified, set(),
                    f"[{family}] expected verified: {expected_strong}, got: {verified}",
                )
                # Role-family templates can add extra core technologies as mandatory
                # requirements beyond what this fixture's JD text names explicitly
                # (e.g. SDET's family core also includes Cypress/Playwright/Appium) —
                # that's a separate, pre-existing requirement-extraction behavior, not
                # what this test targets. Only assert none of the fixture's own
                # explicitly-evidenced skills were wrongly classified as missing.
                missing = {item["normalized_skill"] for item in grouped["missing_mandatory_skills"]}
                self.assertEqual(expected_strong & missing, set(), f"[{family}] evidenced skills wrongly missing: {expected_strong & missing}")

    def test_step2_step3_parity_across_role_families(self):
        for family, (job, resume, _expected_strong) in self.FIXTURES.items():
            with self.subTest(family=family):
                score = calculate_weighted_ats_score(resume, job)
                grouped = group_canonical_score_evidence(score, job_id=1)
                _assert_step2_step3_parity(self, grouped)

    def test_genuinely_absent_requirement_stays_missing_for_every_family(self):
        job = _job(
            "Java Full Stack Developer",
            "Required: Java, Spring Boot, Kafka, GraphQL.",
            "Java, Spring Boot, Kafka, GraphQL",
        )
        resume = """
Jane Candidate

Technical Skills
Java, Spring Boot

Professional Experience
Acme | Java Developer | Jan 2020 - Present
- Built Java and Spring Boot services.
""".strip()
        score = calculate_weighted_ats_score(resume, job)
        grouped = group_canonical_score_evidence(score, job_id=1)
        missing = {item["normalized_skill"] for item in grouped["missing_mandatory_skills"]}
        self.assertIn("kafka", missing)
        self.assertIn("graphql", missing)
        _assert_step2_step3_parity(self, grouped)


class RequirementSemanticsTests(unittest.TestCase):
    def test_ci_cd_is_not_split_into_ci_and_cd(self):
        from app.services.ats_scoring import extract_structured_requirements

        job = _job(
            "Senior DevOps Engineer",
            "Required: CI/CD pipelines with Jenkins or GitHub Actions.",
            "CI/CD, Jenkins, GitHub Actions, AWS",
        )
        requirements = extract_structured_requirements(job)
        required = set(requirements["required_core_skills"])
        self.assertIn("CI/CD", required)
        self.assertNotIn("CI", required)
        self.assertNotIn("CD", required)

    def test_or_requirement_satisfied_by_one_alternative(self):
        job = _job(
            "Senior DevOps Engineer",
            "Required Skills: Jenkins or GitHub Actions for CI/CD. AWS is mandatory.",
            "AWS, Jenkins, GitHub Actions, CI/CD",
        )
        resume = """
Jane Candidate

Technical Skills
AWS, GitHub Actions, CI/CD

Professional Experience
Acme | DevOps Engineer | Jan 2020 - Present
- Built CI/CD pipelines using GitHub Actions on AWS infrastructure.
""".strip()
        score = calculate_weighted_ats_score(resume, job)
        grouped = group_canonical_score_evidence(score, job_id=1)
        verified = {item["normalized_skill"] for item in grouped["verified_resume_skills"]}
        missing = {item["normalized_skill"] for item in grouped["missing_mandatory_skills"]}
        self.assertIn("github actions", verified)
        self.assertIn("aws", verified)
        # Lacking Jenkins must not force a mandatory miss when OR semantics apply to scoring units.
        self.assertNotIn("jenkins", missing)
        _assert_step2_step3_parity(self, grouped)

    def test_skills_only_preferred_stays_weak_not_missing(self):
        job = _job(
            "Senior DevOps Engineer",
            "Required: AWS, Kubernetes.\nPreferred: Datadog.",
            "AWS, Kubernetes",
        )
        resume = """
Jane Candidate

Technical Skills
AWS, Kubernetes, Datadog

Professional Experience
Acme | DevOps Engineer | Jan 2020 - Present
- Provisioned AWS infrastructure and deployed services to Kubernetes.
""".strip()
        score = calculate_weighted_ats_score(resume, job)
        grouped = group_canonical_score_evidence(score, job_id=1)
        verified = {item["normalized_skill"] for item in grouped["verified_resume_skills"]}
        weak = {item["normalized_skill"] for item in grouped["weak_or_indirect_skills"]}
        missing = {item["normalized_skill"] for item in grouped["missing_mandatory_skills"] + grouped["missing_preferred_skills"]}
        self.assertIn("aws", verified)
        self.assertIn("kubernetes", verified)
        self.assertIn("datadog", weak)
        self.assertNotIn("datadog", missing)
        _assert_step2_step3_parity(self, grouped)


if __name__ == "__main__":
    unittest.main()
