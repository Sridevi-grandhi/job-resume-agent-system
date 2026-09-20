"""Realistic MOCK job catalog shared by adapters that have no configured live
board (Greenhouse, Lever) or no safe public API at all (Workday).

Deliberately generic — spans many technology role families so Updated Job
Search never behaves as if it only supports one role. Companies are
fictional; mock jobs never carry a direct_url (never fabricate a link to a
real posting — see job_sources/base "Do NOT fabricate job URLs").

The catalog is a fixed, deterministic list (no randomness) so repeated polls
of a mock source produce the same jobs — real change-detection/dedup
behavior, not a random job generator.
"""

from __future__ import annotations

_MOCK_COMPANIES = [
    "Meridian Systems", "Northwind Cloud", "Vantage Analytics", "BluePeak Technologies",
    "Solace Digital", "Cobalt Works", "Harborlight Software", "Ferrous Data Co",
    "Silverline Platforms", "Crescent Labs", "Amberfield Technologies", "Pinegrove Systems",
]

# (title, role_family_label, skills[], description_template, work_mode, job_type, location)
_ROLE_TEMPLATES = [
    ("Senior Java Full Stack Developer",
     "Java, Spring Boot, React, PostgreSQL, AWS, Docker, Microservices, REST APIs",
     "We are hiring a Java Full Stack Developer to design and build customer-facing "
     "applications using Java, Spring Boot, and React. You will own REST APIs end to "
     "end, work with PostgreSQL, and deploy services with Docker on AWS.",
     "Remote", "Full-time", "United States"),
    ("Python Backend Engineer",
     "Python, Django, Flask, PostgreSQL, Celery, REST APIs, Docker, AWS",
     "Join our backend team building Python services with Django and Flask. "
     "You'll design REST APIs, work with PostgreSQL and Celery background jobs, "
     "and ship to AWS with Docker.",
     "Remote", "Full-time", "United States"),
    ("DevOps Engineer",
     "Terraform, Kubernetes, Docker, AWS, CI/CD, Jenkins, Linux, Ansible",
     "We need a DevOps Engineer to build and operate our cloud infrastructure with "
     "Terraform and Kubernetes on AWS, own CI/CD pipelines in Jenkins, and improve "
     "reliability across our Linux fleet.",
     "Remote", "Full-time", "United States"),
    ("Site Reliability Engineer",
     "Kubernetes, Prometheus, Grafana, AWS, Python, Terraform, Incident Response",
     "As an SRE you will keep production systems reliable — on-call rotations, "
     "Kubernetes operations, Prometheus/Grafana monitoring, and infrastructure as "
     "code with Terraform.",
     "Hybrid", "Full-time", "Austin, TX"),
    ("SDET / Test Automation Engineer",
     "Selenium, Java, TestNG, API Testing, CI/CD, Cypress, REST Assured",
     "We are looking for an SDET to build and maintain automated test frameworks "
     "using Selenium and Java, extend our TestNG suite, and validate REST APIs as "
     "part of our CI/CD pipeline.",
     "Remote", "Full-time", "United States"),
    ("QA Automation Engineer",
     "Cypress, JavaScript, API Testing, CI/CD, Playwright, Test Automation",
     "Own quality for our web platform — write and maintain Cypress and Playwright "
     "test suites, build API test coverage, and integrate automated checks into "
     "our CI/CD pipeline.",
     "Remote", "Full-time", "United States"),
    ("Data Engineer",
     "Python, Apache Spark, Airflow, SQL, ETL, Snowflake, AWS",
     "Build and operate data pipelines using Apache Spark and Airflow, model data "
     "in Snowflake, and write efficient SQL and Python for ETL across our AWS data "
     "platform.",
     "Remote", "Full-time", "United States"),
    ("Data Scientist",
     "Python, Machine Learning, SQL, Pandas, scikit-learn, Statistics",
     "Partner with product teams to build machine learning models in Python, "
     "analyze large datasets with SQL and Pandas, and communicate insights that "
     "drive product decisions.",
     "Hybrid", "Full-time", "New York, NY"),
    ("Frontend Engineer (React)",
     "React, TypeScript, JavaScript, CSS, REST APIs, Testing",
     "Build responsive, accessible web applications with React and TypeScript, "
     "collaborate with design on component libraries, and integrate with REST "
     "APIs.",
     "Remote", "Full-time", "United States"),
    ("Backend Engineer (Node.js)",
     "Node.js, TypeScript, PostgreSQL, REST APIs, Docker, Microservices",
     "Design and build backend services in Node.js and TypeScript, model data in "
     "PostgreSQL, and ship containerized microservices with Docker.",
     "Remote", "Full-time", "United States"),
    ("Full Stack Engineer",
     "JavaScript, React, Node.js, PostgreSQL, AWS, REST APIs",
     "Work across the stack — React on the frontend, Node.js services on the "
     "backend, PostgreSQL for storage, deployed on AWS.",
     "Hybrid", "Full-time", "Seattle, WA"),
    ("Salesforce Developer",
     "Salesforce, Apex, Lightning Components, SOQL, Integration",
     "Build custom Apex classes and Lightning Components on the Salesforce "
     "platform, write SOQL queries, and integrate Salesforce with external "
     "systems.",
     "Remote", "Full-time", "United States"),
    ("iOS Mobile Engineer",
     "Swift, iOS, Xcode, REST APIs, Mobile Testing",
     "Build and ship features for our iOS app in Swift, work closely with design "
     "on native UI, and integrate with backend REST APIs.",
     "Remote", "Full-time", "United States"),
    ("Android Mobile Engineer",
     "Kotlin, Android, REST APIs, Mobile Testing",
     "Develop and maintain our Android application in Kotlin, integrate REST "
     "APIs, and improve app performance and stability.",
     "Remote", "Full-time", "United States"),
    ("Cybersecurity Engineer",
     "Security, SIEM, Incident Response, AWS, Python, Vulnerability Management",
     "Protect our cloud environment — triage alerts in our SIEM, lead incident "
     "response, run vulnerability management, and automate security tooling in "
     "Python on AWS.",
     "Remote", "Full-time", "United States"),
    ("Business Analyst",
     "SQL, Requirements Gathering, Stakeholder Management, Tableau, Excel",
     "Gather and document business requirements, analyze data in SQL and "
     "Tableau, and partner with engineering and product stakeholders to define "
     "solutions.",
     "Hybrid", "Full-time", "Chicago, IL"),
    ("Product Manager, Platform",
     "Product Strategy, Roadmapping, SQL, Stakeholder Management, Agile",
     "Own the roadmap for our platform team, work with engineering on Agile "
     "delivery, and use SQL to inform product decisions.",
     "Hybrid", "Full-time", "San Francisco, CA"),
    ("Cloud Engineer (Azure)",
     "Azure, Terraform, Kubernetes, CI/CD, PowerShell",
     "Design and operate our Azure cloud environment with Terraform and "
     "Kubernetes, and build CI/CD pipelines for platform teams.",
     "Remote", "Full-time", "United States"),
]


def generate_mock_jobs(source: str, limit: int = 100) -> list[dict]:
    jobs = []
    for index, (title, skills, description, work_mode, job_type, location) in enumerate(_ROLE_TEMPLATES):
        company = _MOCK_COMPANIES[index % len(_MOCK_COMPANIES)]
        jobs.append(
            {
                "mock_id": f"{source}-mock-{index + 1:03d}",
                "title": title,
                "company": company,
                "location": location,
                "work_mode": work_mode,
                "job_type": job_type,
                "skills": skills,
                "description": description,
                "posted_at": None,
            }
        )
        if len(jobs) >= limit:
            break
    return jobs
