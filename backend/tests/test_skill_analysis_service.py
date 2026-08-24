import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.requirement_extraction_service import (
    group_requirements_vs_resume,
    load_requirement_analysis,
    save_requirement_analysis,
)
from app.services.skill_analysis_service import (
    ANALYSIS_VERSION,
    MAVEN_ALIASES,
    SKILL_ANALYSIS_VERSION,
    compare_job_requirements_to_resume,
    content_hash,
    find_skill_evidence,
    get_skill_evidence,
    normalize_skill_name,
)


class SkillAnalysisServiceTests(unittest.TestCase):
    def test_a_maven_build_tools_line_verified(self):
        resume_text = "Build and Test Tools: JUnit, Cucumber, Maven"
        result = get_skill_evidence("Maven", resume_text)
        self.assertEqual(result["resume_status"], "VERIFIED")
        self.assertTrue(result["found"])
        # Skills-list-only keyword is found (not missing) but may need confirmation.
        self.assertEqual(result["evidence_strength"], "weak")
        self.assertTrue(result["needs_user_confirmation"])

    def test_b_apache_maven_alias_verified(self):
        resume_text = "Build tools include Apache Maven and Gradle"
        result = get_skill_evidence("Maven", resume_text)
        self.assertEqual(result["resume_status"], "VERIFIED")
        self.assertEqual(result["match_type"], "ALIAS")

    def test_c_mvn_clean_install_verified(self):
        resume_text = "Executed mvn clean install in CI pipelines"
        result = get_skill_evidence("Maven", resume_text)
        self.assertEqual(result["resume_status"], "VERIFIED")
        self.assertIn(result["match_type"], {"ALIAS", "CONTEXTUAL"})

    def test_d_maven_missing_without_mention(self):
        resume_text = "Java, Spring Boot, Gradle"
        result = get_skill_evidence("Maven", resume_text)
        self.assertEqual(result["resume_status"], "MISSING")
        self.assertFalse(result["found"])

    def test_e_stale_cache_invalidated_when_resume_gains_maven(self):
        old_identity = {
            "user_id": 1,
            "job_id": 10,
            "resume_id": 7,
            "resume_hash": content_hash("Java, Spring Boot, Gradle"),
            "job_hash": content_hash("Requires Maven"),
            "analysis_version": "1",
        }
        new_resume_text = "Build and Test Tools: JUnit, Cucumber, Maven"
        new_identity = {
            "user_id": 1,
            "job_id": 10,
            "resume_id": 8,
            "resume_hash": content_hash(new_resume_text),
            "job_hash": content_hash("Requires Maven"),
            "analysis_version": ANALYSIS_VERSION,
        }
        stale_analysis = {
            "job_id": 10,
            "mandatory_skills": [{"skill": "Maven", "category": "Build Tool", "importance": "MANDATORY"}],
            "preferred_skills": [],
            "cache_identity": old_identity,
        }
        record = SimpleNamespace(analysis_json=json.dumps(stale_analysis), id=1)
        db = MagicMock()
        query = db.query.return_value
        query.filter.return_value.order_by.return_value.first.return_value = record

        cached = load_requirement_analysis(db, **{k: new_identity[k] for k in (
            "user_id", "job_id", "resume_id", "resume_hash", "job_hash",
        )})
        self.assertIsNone(cached)

        grouped = group_requirements_vs_resume(stale_analysis, new_resume_text)
        maven = next(item for item in grouped["skills"] if item["normalized_skill"] == "maven")
        self.assertEqual(maven["resume_status"], "VERIFIED")
        self.assertNotIn(maven, grouped["missing_mandatory_skills"])
        self.assertIn(maven, grouped["verified_resume_skills"])

    def test_f_maven_and_apache_maven_dedupe_to_one(self):
        requirements = [
            {"skill": "Maven", "requirement_type": "MANDATORY", "category": "Build Tool"},
            {"skill": "Apache Maven", "requirement_type": "MANDATORY", "category": "Build Tool"},
        ]
        result = compare_job_requirements_to_resume(
            requirements,
            "Build and Test Tools: JUnit, Cucumber, Maven",
        )
        self.assertEqual(len(result["skills"]), 1)
        self.assertEqual(result["skills"][0]["normalized_skill"], "maven")
        self.assertEqual(result["skills"][0]["resume_status"], "VERIFIED")

        analysis = {
            "job_id": 1,
            "mandatory_skills": [
                {"skill": "Maven", "category": "Build Tool", "importance": "MANDATORY"},
                {"skill": "Apache Maven", "category": "Build Tool", "importance": "MANDATORY"},
            ],
            "preferred_skills": [],
        }
        # extract_job_requirement_analysis path also dedupes; group must too.
        grouped = group_requirements_vs_resume(
            {
                "job_id": 1,
                "mandatory_skills": [
                    {"skill": "Maven", "category": "Build Tool"},
                    {"skill": "Apache Maven", "category": "Build Tool"},
                ],
                "preferred_skills": [],
            },
            "Technical Skills\nApache Maven",
        )
        maven_items = [item for item in grouped["skills"] if item["normalized_skill"] == "maven"]
        self.assertEqual(len(maven_items), 1)
        self.assertEqual(len(grouped["verified_resume_skills"]), 1)
        self.assertEqual(len(grouped["missing_mandatory_skills"]), 0)

    def test_g_frontend_grouping_puts_verified_maven_only_under_verified(self):
        analysis = {
            "job_id": 1,
            "mandatory_skills": [
                {"skill": "Maven", "category": "Build Tool"},
                {"skill": "Oracle", "category": "Database"},
                {"skill": "SQL Server", "category": "Database"},
            ],
            "preferred_skills": [],
        }
        grouped = group_requirements_vs_resume(
            analysis,
            "Build and Test Tools: JUnit, Cucumber, Maven\nPostgreSQL",
        )
        verified_names = {item["skill"] for item in grouped["verified_resume_skills"]}
        missing_names = {item["skill"] for item in grouped["missing_mandatory_skills"]}
        self.assertIn("Maven", verified_names)
        self.assertNotIn("Maven", missing_names)
        self.assertIn("Oracle", missing_names)
        self.assertIn("SQL Server", missing_names)

    def test_maven_aliases_cover_required_set(self):
        self.assertTrue({"maven", "apache maven", "mvn"}.issubset(MAVEN_ALIASES))
        self.assertEqual(normalize_skill_name("mvn"), "maven")
        self.assertEqual(normalize_skill_name("Apache Maven"), "maven")
        self.assertEqual(SKILL_ANALYSIS_VERSION, "8-capability-contextual-evidence")

    def test_find_skill_evidence_api(self):
        result = find_skill_evidence("Maven", "Used Maven for dependency management")
        self.assertEqual(result["resume_status"], "VERIFIED")
        self.assertTrue(result["evidence_snippets"])

    def test_maven_not_substring_false_positive(self):
        result = get_skill_evidence("Maven", "Technical Skills\nMavenlikeTool")
        self.assertEqual(result["resume_status"], "MISSING")

    def test_oracle_and_sql_server_remain_missing_when_absent(self):
        resume = "Build and Test Tools: JUnit, Cucumber, Maven\nPostgreSQL"
        self.assertEqual(get_skill_evidence("Oracle", resume)["resume_status"], "MISSING")
        self.assertEqual(get_skill_evidence("SQL Server", resume)["resume_status"], "MISSING")

    def test_scrum_indirect_is_not_missing(self):
        resume = "Professional Experience\n- Participated in Agile sprint planning and retrospectives."
        result = get_skill_evidence("Scrum", resume)
        self.assertEqual(result["resume_status"], "INDIRECT")
        self.assertTrue(result["needs_user_confirmation"])

    def test_javascript_does_not_prove_java(self):
        result = get_skill_evidence("Java", "Technical Skills\nJavaScript")
        self.assertEqual(result["resume_status"], "MISSING")

    def test_requested_canonical_aliases_are_verified_not_missing(self):
        cases = {
            "JPA": "Used jakarta.persistence for entity mapping.",
            "JavaScript": "Technical Skills: JS",
            "TypeScript": "Built the frontend with TS.",
            "Kubernetes": "Deployed services to K8s.",
        }
        for skill, resume in cases.items():
            with self.subTest(skill=skill):
                evidence = get_skill_evidence(skill, resume)
                self.assertEqual(evidence["resume_status"], "VERIFIED")
                self.assertIn(evidence["match_type"], {"ALIAS", "CONTEXTUAL"})

    def test_special_terms_are_boundary_safe(self):
        resume = "C++, C#, .NET, Node.js and CI/CD"
        for skill in ["C++", "C#", ".NET", "Node.js", "CI/CD"]:
            self.assertTrue(get_skill_evidence(skill, resume)["found"], skill)

    def test_save_requirement_analysis_stores_version_two(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        analysis = {"mandatory_skills": [], "preferred_skills": []}
        save_requirement_analysis(
            db,
            user_id=1,
            job_id=2,
            analysis=analysis,
            resume_id=8,
            resume_hash="abc",
            job_hash="def",
        )
        saved = db.add.call_args[0][0]
        payload = json.loads(saved.analysis_json)
        self.assertEqual(payload["cache_identity"]["analysis_version"], "8-capability-contextual-evidence")
        self.assertEqual(payload["cache_identity"]["resume_id"], 8)

    def test_broad_action_verbs_promote_professional_evidence(self):
        # These verbs are common in DevOps/SRE/platform bullets but were previously
        # absent from the narrow usage-context whitelist, silently degrading strength.
        preamble = "Jane Candidate\n\n"
        cases = {
            "Linux": preamble + "Professional Experience\nAcme | Engineer | 2020 - Present\n- Administered Linux servers for production workloads.",
            "AWS": preamble + "Professional Experience\nAcme | Engineer | 2020 - Present\n- Provisioned AWS infrastructure for the platform team.",
            "Jenkins": preamble + "Professional Experience\nAcme | Engineer | 2020 - Present\n- Automated build pipelines using Jenkins.",
            "Kubernetes": preamble + "Professional Experience\nAcme | Engineer | 2020 - Present\n- Migrated workloads to Kubernetes across regions.",
        }
        for skill, resume in cases.items():
            with self.subTest(skill=skill):
                evidence = get_skill_evidence(skill, resume)
                self.assertEqual(evidence["resume_status"], "VERIFIED")
                self.assertEqual(evidence["evidence_strength"], "strong")

    def test_build_tools_category_label_is_not_a_false_usage_signal(self):
        """Regression guard: 'Build Tools: Maven, Gradle' is a category label, not
        evidence that the candidate 'built' something — must stay ALIAS, not be
        mislabeled CONTEXTUAL just because the word 'Build' appears."""
        result = get_skill_evidence("Maven", "Build tools include Apache Maven and Gradle")
        self.assertEqual(result["resume_status"], "VERIFIED")
        self.assertEqual(result["match_type"], "ALIAS")

    def test_monitoring_category_label_is_not_strong_usage(self):
        resume = "Technical Skills\nMonitoring: Prometheus, Grafana, Datadog"
        evidence = get_skill_evidence("Datadog", resume)
        self.assertEqual(evidence["resume_status"], "VERIFIED")
        self.assertEqual(evidence["evidence_strength"], "weak")
        self.assertNotEqual(evidence["match_type"], "CONTEXTUAL")

    def test_non_standard_experience_headings_yield_strong_evidence(self):
        for heading in ["Work History", "Career History", "Relevant Experience", "Employment"]:
            resume = (
                f"Jane Candidate\n\n{heading}\nAcme | DevOps Engineer | Jan 2020 - Present\n"
                "- Built CI/CD pipelines using Jenkins and deployed services to Kubernetes."
            )
            with self.subTest(heading=heading):
                evidence = get_skill_evidence("Kubernetes", resume)
                self.assertEqual(evidence["resume_status"], "VERIFIED")
                self.assertEqual(evidence["evidence_strength"], "strong")

    def test_headingless_resume_still_yields_strong_evidence(self):
        resume = (
            "Jane Candidate\n\n"
            "DevOps Engineer with cloud experience.\n\n"
            "Skills: AWS, Terraform, Kubernetes\n\n"
            "Senior DevOps Engineer | Acme Corp | Jan 2021 - Present\n"
            "- Provisioned AWS infrastructure using Terraform for production workloads.\n"
            "- Orchestrated deployments on Kubernetes across multiple environments.\n"
        )
        for skill in ["AWS", "Terraform", "Kubernetes"]:
            with self.subTest(skill=skill):
                evidence = get_skill_evidence(skill, resume)
                self.assertEqual(evidence["resume_status"], "VERIFIED")
                self.assertEqual(evidence["evidence_strength"], "strong")

    def test_skills_list_only_mention_remains_weak_not_missing(self):
        """A skill with no professional usage context must be distinguished from a
        skill that is genuinely absent — 'weak' means found-needs-confirmation, not
        not-found."""
        resume = "Technical Skills\nDatadog, Splunk, Java, AWS"
        evidence = get_skill_evidence("Datadog", resume)
        self.assertEqual(evidence["resume_status"], "VERIFIED")
        self.assertEqual(evidence["evidence_strength"], "weak")
        self.assertTrue(evidence["found"])
        self.assertTrue(evidence["needs_user_confirmation"])
        self.assertTrue(evidence["needs_evidence"])

    def test_genuinely_absent_skill_is_missing(self):
        resume = "Technical Skills\nDatadog, Splunk, Java, AWS"
        evidence = get_skill_evidence("ArgoCD", resume)
        self.assertEqual(evidence["resume_status"], "MISSING")
        self.assertFalse(evidence["found"])

    def test_skills_list_plus_usage_outside_skills_is_strong(self):
        """Regression: skills-section hits must not force WEAK when usage-verb bullets
        prove the same skill — even if PDF parsing folds those bullets into Skills."""
        resume = """
Jane Candidate

Technical Skills
AWS, Kubernetes, Docker, Terraform
Acme Corp | Engineer | Jan 2020 - Present
- Provisioned AWS infrastructure using Terraform for production workloads.
- Deployed containerized services to Kubernetes with Docker.
"""
        for skill in ["AWS", "Kubernetes", "Docker", "Terraform"]:
            with self.subTest(skill=skill):
                evidence = get_skill_evidence(skill, resume)
                self.assertEqual(evidence["resume_status"], "VERIFIED")
                self.assertEqual(evidence["evidence_strength"], "strong")
                self.assertFalse(evidence["needs_user_confirmation"])

    def test_compact_pdf_style_resume_with_usage_is_strong(self):
        resume = (
            "Jane Doe DevOps Engineer jane@example.com\n"
            "Senior engineer skilled in AWS Kubernetes Docker Terraform\n"
            "Acme Corp | Senior DevOps Engineer | Jan 2020 - Present\n"
            "Provisioned AWS infrastructure using Terraform modules\n"
            "Deployed applications to Kubernetes using Helm\n"
            "Containerized services with Docker\n"
        )
        for skill in ["AWS", "Kubernetes", "Docker", "Terraform"]:
            with self.subTest(skill=skill):
                evidence = get_skill_evidence(skill, resume)
                self.assertEqual(evidence["resume_status"], "VERIFIED")
                self.assertEqual(evidence["evidence_strength"], "strong")


if __name__ == "__main__":
    unittest.main()
