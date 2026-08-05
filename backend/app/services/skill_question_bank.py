"""Skill-specific "recruiter interview" question bank.

Drives the follow-up questions shown for a missing mandatory/preferred skill so the
evidence-collection flow adapts to the skill instead of asking the same generic
questions for everything. Purely deterministic — no LLM call required — but the
question wording is designed to read naturally if an LLM later paraphrases it.
"""

from __future__ import annotations

from app.services.keyword_utils import normalize_keyword


# Skill -> the sub-topics/modules a recruiter would actually probe for.
SKILL_MODULE_CATALOG: dict[str, list[str]] = {
    "Kafka": ["Producer", "Consumer", "Topics", "Partitions", "Consumer Groups", "Retries", "DLQ", "Schema Registry"],
    "AWS": ["EC2", "S3", "Lambda", "IAM", "CloudWatch", "ECS", "EKS"],
    "Spring Boot": ["REST APIs", "Microservices", "JPA", "Security", "Hibernate", "Actuator"],
    "React": ["Hooks", "Redux", "Routing", "Axios", "Forms"],
    "Angular": ["Components", "Services", "RxJS", "Routing", "Forms", "Dependency Injection"],
    "Docker": ["Dockerfile", "Images", "Containers", "Docker Compose", "Volumes", "Networking"],
    "Kubernetes": ["Pods", "Deployments", "Services", "ConfigMaps", "Secrets", "Helm", "Ingress"],
    "Python": ["Data Structures", "OOP", "Async/Await", "Testing", "Package Management"],
    "Node.js": ["Express", "REST APIs", "Middleware", "Async/Await", "Package Management"],
    "PostgreSQL": ["Schema Design", "Indexing", "Query Optimization", "Transactions", "Stored Procedures"],
    "MySQL": ["Schema Design", "Indexing", "Query Optimization", "Transactions", "Stored Procedures"],
    "Microservices": ["Service Design", "API Gateway", "Service Discovery", "Circuit Breakers", "Distributed Tracing"],
    "Hibernate": ["Entity Mapping", "Sessions", "Caching", "HQL", "Transactions"],
    "JPA": ["Entity Mapping", "Repositories", "Query Methods", "Transactions", "Caching"],
    "Jenkins": ["Pipelines", "Build Automation", "Plugins", "Integrations"],
    "Terraform": ["Modules", "State Management", "Providers", "Provisioning"],
    "Azure": ["Virtual Machines", "App Service", "Functions", "Storage", "DevOps Pipelines"],
    "GCP": ["Compute Engine", "Cloud Functions", "BigQuery", "Cloud Storage"],
    "Java": ["Collections", "Multithreading", "Streams API", "Exception Handling", "JVM Tuning"],
    "TypeScript": ["Type System", "Interfaces", "Generics", "Tooling/Build Config"],
    "GraphQL": ["Schemas", "Resolvers", "Queries/Mutations", "Subscriptions"],
    "Redis": ["Caching", "Pub/Sub", "Data Structures", "Persistence"],
    "MongoDB": ["Schema Design", "Aggregation Pipeline", "Indexing", "Replication"],
    "Selenium": ["WebDriver", "Locators", "Waits", "Page Object Model", "Test Frameworks"],
    "CI/CD": ["Pipeline Design", "Build Automation", "Deployment Strategies", "Rollbacks"],
}

GENERIC_MODULES = ["Core concepts", "Integration with other systems", "Testing", "Performance/optimization", "Troubleshooting"]


def modules_for_skill(skill: str) -> list[str]:
    """Sub-topics to offer as the 'which modules or features' checklist for a skill."""
    canonical = normalize_keyword(skill)
    return SKILL_MODULE_CATALOG.get(canonical, GENERIC_MODULES)


def interview_prompts_for_skill(skill: str) -> dict:
    """Structured, recruiter-style question set for a single missing skill.

    Purely presentational metadata — the actual answers map onto the existing
    evidence fields (evidence_type, experience_range, usage_description,
    skill_modules, related_technologies, impact_description).
    """
    return {
        "skill": skill,
        "modules": modules_for_skill(skill),
        "prompts": {
            "where_used": f"Where did you use {skill}?",
            "duration": "Approximately how long?",
            "description": f"Describe what you built with {skill}.",
            "modules": f"Which {skill} modules or features did you use?",
            "related_technologies": "Which related technologies did you use alongside it?",
            "outcome": "Any measurable outcome? (optional)",
        },
    }
