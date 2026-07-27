import unittest
from types import SimpleNamespace

from app.agents.updated_job_search_agent import build_search_queries


class UpdatedJobSearchAgentTests(unittest.TestCase):
    def test_java_full_stack_uses_exact_title_all_aliases_and_broad_fallback(self):
        preference = SimpleNamespace(job_title="Java full stack")
        queries = build_search_queries(preference)
        normalized = [query.lower() for query in queries]

        self.assertEqual(queries[0], "Java full stack")
        self.assertEqual(queries[-1], "Java Developer")
        for expected in (
            "java full stack",
            "java fullstack",
            "full stack java",
            "fullstack java",
            "java react",
            "java angular",
            "java full stack developer",
            "full stack java developer",
            "java software engineer",
            "java developer",
        ):
            self.assertIn(expected, normalized)
        self.assertEqual(len(normalized), len(set(normalized)))


if __name__ == "__main__":
    unittest.main()
