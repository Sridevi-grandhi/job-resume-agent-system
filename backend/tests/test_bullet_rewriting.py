import unittest

from app.services.resume_generator import (
    _dedupe_bullets,
    _employment_bullet,
    _has_usage_context,
    _looks_like_definition,
    _prioritize_bullets,
    _project_block,
    _resume_ready_evidence_sentence,
    _strengthen_bullet,
)


class WeakBulletRewriteTests(unittest.TestCase):
    def test_worked_on_becomes_developed(self):
        text = _strengthen_bullet("Worked on Java applications.", [], "")
        self.assertTrue(text.startswith("Developed "))

    def test_responsible_for_becomes_managed(self):
        text = _strengthen_bullet("Responsible for deployment pipelines.", [], "")
        self.assertTrue(text.startswith("Managed "))

    def test_was_part_of_becomes_collaborated_on(self):
        text = _strengthen_bullet("Was part of the backend migration team.", [], "")
        self.assertTrue(text.startswith("Collaborated on "))

    def test_assisted_with_becomes_supported(self):
        text = _strengthen_bullet("Assisted with API integrations.", [], "")
        self.assertTrue(text.startswith("Supported "))

    def test_never_invents_a_fake_metric(self):
        text = _strengthen_bullet("Worked on Java.", [], "")
        self.assertNotRegex(text, r"\d{1,3}%|\$\d")

    def test_weaves_related_keyword_when_contextually_related(self):
        text = _strengthen_bullet("Built backend services and REST endpoints.", ["Spring Boot"], "")
        self.assertIn("Spring Boot", text)


class DedupeAndPrioritizeTests(unittest.TestCase):
    def test_dedupe_removes_exact_and_whitespace_variant_duplicates(self):
        bullets = ["Built REST APIs.", "Built   REST APIs.", "Built REST APIs.", "Deployed services."]
        deduped = _dedupe_bullets(bullets)
        self.assertEqual(len(deduped), 2)

    def test_prioritize_moves_jd_relevant_bullets_first(self):
        bullets = ["Wrote internal documentation.", "Built Spring Boot microservices."]
        prioritized = _prioritize_bullets(bullets, ["Spring Boot"])
        self.assertEqual(prioritized[0], "Built Spring Boot microservices.")


class ConfirmedEvidenceBulletModuleWeavingTests(unittest.TestCase):
    def test_employment_bullet_mentions_skill_modules(self):
        text = _employment_bullet({
            "usage_description": "Built backend services using Kafka.",
            "skill_modules": "Producer, Consumer, DLQ",
        })
        self.assertIn("Producer, Consumer, DLQ", text)

    def test_project_block_mentions_skill_modules(self):
        block = _project_block({
            "skill": "Kafka",
            "project_name": "Event Pipeline",
            "work_completed": "Built an event-driven pipeline.",
            "skill_modules": "Topics, Partitions",
        })
        self.assertIn("Topics, Partitions", block)


class DefinitionStyleBulletRejectionTests(unittest.TestCase):
    """Confirmed skills must never become textbook-definition bullets —
    generic across roles (Java, SDET, DevOps, Data Engineer), never
    hardcoded to a specific technology name."""

    def test_looks_like_definition_matches_reported_examples(self):
        for text in [
            "Scrum is an Agile methodology used in sprints.",
            "Selenium is a browser automation tool.",
            "Apache JMeter is a performance and load testing tool.",
            "Docker is a containerization platform.",
            "Kubernetes is a container orchestration platform.",
            "Kafka is a distributed messaging platform.",
            "Terraform is an infrastructure-as-code tool.",
            "Jenkins is a CI/CD automation server.",
            "Postman is an API testing tool.",
            "React is a JavaScript library.",
            "JMeter helps simulate load on web applications.",
            "Ansible provides configuration management automation.",
        ]:
            with self.subTest(text=text):
                self.assertTrue(_looks_like_definition(text), text)

    def test_looks_like_definition_does_not_flag_genuine_experience_bullets(self):
        for text in [
            "Developed and maintained automated UI test scenarios using Selenium.",
            "Performed application performance and load testing using JMeter.",
            "Collaborated with cross-functional teams in Agile/Scrum sprint ceremonies.",
            "Built Terraform modules to provision AWS infrastructure.",
            "The system architecture was redesigned to use Kubernetes for orchestration.",
        ]:
            with self.subTest(text=text):
                self.assertFalse(_looks_like_definition(text), text)

    def test_has_usage_context_requires_a_real_action_verb(self):
        self.assertFalse(_has_usage_context("I know Selenium"))
        self.assertFalse(_has_usage_context("Familiar with Kafka"))
        self.assertTrue(_has_usage_context("Automated regression suites with Selenium"))
        self.assertTrue(_has_usage_context("Responsible for CI/CD pipelines"))

    def test_resume_ready_evidence_sentence_rejects_definitional_free_text(self):
        for skill, description in [
            ("Scrum", "Scrum is an Agile methodology used in sprints."),
            ("Selenium", "Selenium is a browser automation tool."),
            ("Apache JMeter", "Apache JMeter is a performance and load testing tool."),
            ("Docker", "Docker is a containerization platform."),
            ("Kubernetes", "Kubernetes is a container orchestration platform."),
            ("Kafka", "Kafka is a distributed messaging platform."),
            ("Terraform", "Terraform is an infrastructure-as-code tool."),
            ("Postman", "Postman is an API testing tool."),
        ]:
            with self.subTest(skill=skill):
                self.assertEqual(_resume_ready_evidence_sentence(skill, description), "")

    def test_resume_ready_evidence_sentence_rejects_skill_knowledge_only(self):
        """Knowing a skill is not the same as proving professional usage —
        no free text at all, or a bare acknowledgment, must never produce a
        manufactured Professional Experience bullet."""
        self.assertEqual(_resume_ready_evidence_sentence("Selenium", ""), "")
        self.assertEqual(_resume_ready_evidence_sentence("Selenium", "I know Selenium"), "")
        self.assertEqual(_resume_ready_evidence_sentence("Kafka", "Familiar with Kafka"), "")

    def test_resume_ready_evidence_sentence_no_longer_manufactures_hardcoded_jmeter_fallback(self):
        """Regression: empty/thin evidence for ANY skill used to silently
        become a hardcoded "Performed performance and load testing using
        {skill}..." bullet regardless of what the skill actually was —
        nonsensical for e.g. Scrum or Docker. Must now return "" instead."""
        self.assertEqual(_resume_ready_evidence_sentence("Scrum", ""), "")
        self.assertEqual(_resume_ready_evidence_sentence("Docker", "   "), "")

    def test_resume_ready_evidence_sentence_accepts_genuine_usage_evidence(self):
        """Real professional-usage evidence — e.g. 'I used Selenium at Cisco
        for regression automation' — must still be incorporated."""
        sentence = _resume_ready_evidence_sentence(
            "Selenium", "I used Selenium at Cisco for regression automation of checkout workflows.",
        )
        self.assertNotEqual(sentence, "")
        self.assertFalse(_looks_like_definition(sentence))
        self.assertIn("Selenium", sentence)

    def test_resume_ready_evidence_sentence_accepts_action_verb_led_description(self):
        sentence = _resume_ready_evidence_sentence(
            "JMeter", "Performed load testing on the checkout API to identify bottlenecks under peak traffic.",
        )
        self.assertNotEqual(sentence, "")
        self.assertFalse(_looks_like_definition(sentence))

    def test_employment_bullet_returns_empty_for_definitional_description_not_a_manufactured_bullet(self):
        bullet = _employment_bullet({
            "skill": "Scrum",
            "usage_description": "Scrum is an Agile methodology/framework used to manage sprints.",
        })
        self.assertEqual(bullet, "")

    def test_employment_bullet_keeps_genuine_evidence(self):
        bullet = _employment_bullet({
            "skill": "Selenium",
            "usage_description": "Developed and maintained automated regression test suites using Selenium.",
        })
        self.assertNotEqual(bullet, "")
        self.assertFalse(_looks_like_definition(bullet))


if __name__ == "__main__":
    unittest.main()
