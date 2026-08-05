import unittest

from app.services.skill_question_bank import (
    GENERIC_MODULES,
    interview_prompts_for_skill,
    modules_for_skill,
)


class SkillQuestionBankTests(unittest.TestCase):
    def test_kafka_modules_match_spec_example(self):
        modules = modules_for_skill("Kafka")
        for expected in ["Producer", "Consumer", "Topics", "Partitions", "Consumer Groups", "Retries", "DLQ", "Schema Registry"]:
            self.assertIn(expected, modules)

    def test_aws_modules_match_spec_example(self):
        modules = modules_for_skill("AWS")
        for expected in ["EC2", "S3", "Lambda", "IAM", "CloudWatch", "ECS", "EKS"]:
            self.assertIn(expected, modules)

    def test_spring_boot_modules_match_spec_example(self):
        modules = modules_for_skill("Spring Boot")
        for expected in ["REST APIs", "Microservices", "JPA", "Security", "Hibernate", "Actuator"]:
            self.assertIn(expected, modules)

    def test_react_modules_match_spec_example(self):
        modules = modules_for_skill("React")
        for expected in ["Hooks", "Redux", "Routing", "Axios", "Forms"]:
            self.assertIn(expected, modules)

    def test_unknown_skill_falls_back_to_generic_modules(self):
        self.assertEqual(modules_for_skill("SomeObscureToolNoOneHasHeardOf"), GENERIC_MODULES)

    def test_alias_normalization_resolves_to_canonical_catalog_entry(self):
        # "springboot" should normalize to "Spring Boot" and hit the curated catalog.
        modules = modules_for_skill("springboot")
        self.assertIn("JPA", modules)

    def test_interview_prompts_shape_and_wording_changes_per_skill(self):
        kafka_prompts = interview_prompts_for_skill("Kafka")
        aws_prompts = interview_prompts_for_skill("AWS")
        self.assertEqual(kafka_prompts["skill"], "Kafka")
        self.assertIn("Kafka", kafka_prompts["prompts"]["where_used"])
        self.assertIn("AWS", aws_prompts["prompts"]["where_used"])
        self.assertNotEqual(kafka_prompts["modules"], aws_prompts["modules"])
        for key in ("where_used", "duration", "description", "modules", "related_technologies", "outcome"):
            self.assertIn(key, kafka_prompts["prompts"])


if __name__ == "__main__":
    unittest.main()
