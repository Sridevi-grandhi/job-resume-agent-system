"""Shared technology catalog, synonyms, conflict groups, and earliest-use years."""

from __future__ import annotations

TECHNOLOGY_SYNONYMS = {
    "reactjs": "React",
    "react.js": "React",
    "react js": "React",
    "rest api": "REST APIs",
    "rest apis": "REST APIs",
    "restful apis": "REST APIs",
    "restful api": "REST APIs",
    "restful": "REST APIs",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "springboot": "Spring Boot",
    "spring boot": "Spring Boot",
    "ci cd": "CI/CD",
    "cicd": "CI/CD",
    "ci/cd": "CI/CD",
    "k8s": "Kubernetes",
    "gh actions": "GitHub Actions",
    "github actions": "GitHub Actions",
    "js": "JavaScript",
    "ts": "TypeScript",
    "node js": "Node.js",
    "nodejs": "Node.js",
    "node.js": "Node.js",
    "nextjs": "Next.js",
    "next.js": "Next.js",
    "angular js": "AngularJS",
    "angularjs": "AngularJS",
    "mssql": "SQL Server",
    "sql server": "SQL Server",
    "amazon web services": "AWS",
    "google cloud": "GCP",
    "google cloud platform": "GCP",
    "rabbit mq": "RabbitMQ",
    "apache kafka": "Kafka",
    "junit5": "JUnit",
    "py test": "PyTest",
    "pytest": "PyTest",
    "openai": "OpenAI API",
    "openai api": "OpenAI API",
    "vector db": "Vector Databases",
    "vector database": "Vector Databases",
    "vector databases": "Vector Databases",
}

CANONICAL_TECHNOLOGIES = [
    # Backend
    "Java", "Spring", "Spring Boot", "Spring MVC", "Spring Security", "Hibernate",
    "Microservices", "REST APIs", "Kafka", "RabbitMQ", "JUnit", "Maven", "SOAP",
    "Servlets", "JSP",
    # Frontend
    "React", "Angular", "AngularJS", "Vue", "HTML", "CSS", "JavaScript", "TypeScript",
    "Next.js", "Node.js",
    # Database
    "PostgreSQL", "MySQL", "Oracle", "MongoDB", "Redis", "SQL", "SQL Server",
    # Cloud
    "AWS", "Azure", "GCP",
    # DevOps
    "Docker", "Kubernetes", "Jenkins", "GitHub Actions", "GitLab CI", "Azure DevOps",
    "Terraform", "CloudFormation", "Ansible", "Git", "CI/CD",
    # Monitoring
    "Splunk", "Dynatrace", "AppDynamics", "ELK",
    # Python / AI
    "Python", "Django", "Flask", "FastAPI", "PyTest", "Selenium",
    "OpenAI API", "LangChain", "RAG", "Vector Databases",
    # Process
    "Agile", "Scrum",
]

CONFLICT_GROUPS = {
    "Frontend Frameworks": ["React", "Angular", "Vue", "Next.js"],
    "Python Frameworks": ["Django", "Flask", "FastAPI"],
    "Cloud Providers": ["AWS", "Azure", "GCP"],
    "CI/CD Tools": ["Jenkins", "GitHub Actions", "GitLab CI", "Azure DevOps"],
    "IaC Tools": ["Terraform", "CloudFormation", "Ansible"],
    "Monitoring Tools": ["Splunk", "Dynatrace", "AppDynamics", "ELK"],
}

EARLIEST_USE_YEAR = {
    "Java": 1995,
    "Spring": 2003,
    "Spring Framework": 2003,
    "Spring Boot": 2014,
    "Spring MVC": 2003,
    "React": 2013,
    "AngularJS": 2010,
    "Angular": 2016,
    "Next.js": 2016,
    "Node.js": 2009,
    "Docker": 2013,
    "Kubernetes": 2014,
    "Jenkins": 2011,
    "GitHub Actions": 2018,
    "AWS": 2006,
    "Azure": 2010,
    "GCP": 2008,
    "Terraform": 2014,
    "Ansible": 2012,
    "PostgreSQL": 1996,
    "MySQL": 1995,
    "MongoDB": 2009,
    "Redis": 2009,
    "Kafka": 2011,
    "RabbitMQ": 2007,
    "Python": 1991,
    "Django": 2005,
    "Flask": 2010,
    "FastAPI": 2018,
    "PyTest": 2004,
    "Selenium": 2004,
    "JUnit": 1997,
    "OpenAI API": 2020,
    "LangChain": 2022,
    "RAG": 2020,
    "Vector Databases": 2020,
    "Microservices": 2014,
    "Hibernate": 2001,
}

# Tokens that must not substring-match incorrectly.
PROTECTED_BOUNDARY_TERMS = {
    "java": "javascript",
    "sql": "nosql",
    "react": "reactive",
    "spring": "springboard",
    "go": "golang",
}
