import unittest

from app.services.resume_generator import (
    _dedupe_bullets,
    _employment_bullet,
    _prioritize_bullets,
    _project_block,
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


if __name__ == "__main__":
    unittest.main()
