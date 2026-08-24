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
    "api testing": "API Testing",
    "api test": "API Testing",
    "api test automation": "API Testing",
    "rest api testing": "API Testing",
    "test automation": "Test Automation",
    "automated testing": "Test Automation",
    "automation testing": "Test Automation",
    "qa automation": "Test Automation",
    "automation test": "Test Automation",
    "test automation framework": "Test Automation",
    "automated regression": "Test Automation",
    "ui automation": "Test Automation",
    "automation frameworks": "Test Automation",
    "automation framework": "Test Automation",
    "database validation": "Database Validation",
    "sql validation": "Database Validation",
    "backend database testing": "Database Validation",
    "data validation": "Database Validation",
    "db validation": "Database Validation",
    "backend data verification": "Database Validation",
    "infrastructure as code": "Infrastructure as Code",
    "infra as code": "Infrastructure as Code",
    "iac": "Infrastructure as Code",
    "etl": "ETL",
    "etl pipeline": "ETL",
    "etl pipelines": "ETL",
    "etl pipeline development": "ETL",
    "data pipeline": "ETL",
    "data pipelines": "ETL",
    "data pipeline development": "ETL",
    "extract transform load": "ETL",
    "rest api development": "REST APIs",
    "restful endpoints": "REST APIs",
    "restful endpoint": "REST APIs",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "springboot": "Spring Boot",
    "spring boot": "Spring Boot",
    "ci cd": "CI/CD",
    "cicd": "CI/CD",
    "ci/cd": "CI/CD",
    "continuous integration": "CI/CD",
    "continuous delivery": "CI/CD",
    "continuous deployment": "CI/CD",
    "k8s": "Kubernetes",
    "gh actions": "GitHub Actions",
    "github actions": "GitHub Actions",
    "gitlab ci/cd": "GitLab CI",
    "argocd": "Argo CD",
    "argo-cd": "Argo CD",
    "hashicorp terraform": "Terraform",
    "linux administration": "Linux",
    "prometheus monitoring": "Prometheus",
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
    "junit 5": "JUnit",
    "py test": "PyTest",
    "pytest": "PyTest",
    "selenium webdriver": "Selenium",
    "selenium web driver": "Selenium",
    "webdriver": "Selenium",
    "rest assured": "Rest Assured",
    "rest-assured": "Rest Assured",
    "bdd": "Cucumber",
    "cucumber bdd": "Cucumber",
    "cucumber/bdd": "Cucumber",
    "behavior driven development": "Cucumber",
    "c#": "C#",
    "csharp": "C#",
    "c sharp": "C#",
    "openai": "OpenAI API",
    "openai api": "OpenAI API",
    "vector db": "Vector Databases",
    "vector database": "Vector Databases",
    "vector databases": "Vector Databases",
    "healthcare domain": "Healthcare",
    "healthcare experience": "Healthcare",
}

CANONICAL_TECHNOLOGIES = [
    # Backend
    "Java", "Spring", "Spring Boot", "Spring MVC", "Spring Security", "Hibernate",
    "Microservices", "REST APIs", "API Testing", "Kafka", "RabbitMQ", "JUnit", "Maven", "SOAP",
    "Servlets", "JSP", "C#", "Go",
    # Frontend
    "React", "Angular", "AngularJS", "Vue", "HTML", "CSS", "JavaScript", "TypeScript",
    "Next.js", "Node.js",
    # Database
    "PostgreSQL", "MySQL", "Oracle", "MongoDB", "Redis", "SQL", "SQL Server", "Database Validation",
    # Cloud
    "AWS", "Azure", "GCP",
    # DevOps
    "Docker", "Kubernetes", "Jenkins", "GitHub Actions", "GitLab CI", "Azure DevOps",
    "Terraform", "CloudFormation", "Ansible", "Git", "CI/CD", "Linux", "Bash", "Helm", "Argo CD",
    # Monitoring
    "Splunk", "Dynatrace", "AppDynamics", "ELK", "Datadog", "Prometheus", "Grafana",
    # Python / AI
    "Python", "Django", "Flask", "FastAPI", "PyTest",
    "OpenAI API", "LangChain", "RAG", "Vector Databases",
    # QA / Test automation (catalog terms — not role hardcoding)
    "Selenium", "Cypress", "Playwright", "Appium", "TestNG", "Cucumber",
    "Postman", "Rest Assured", "JMeter", "XCTest", "XCUITest",
    "Test Automation",
    # Capability concepts (proven by responsibilities / defining tools, not vendor identity)
    "Infrastructure as Code", "ETL",
    # Process / domain
    "Agile", "Scrum", "Healthcare",
]

# Capability / concept requirements that can be VERIFIED_CONTEXTUAL from professional
# responsibilities or defining tools — without requiring the exact JD phrase.
# Related tools never auto-verify a different vendor technology (see RELATED_NOT_EQUIVALENT).
CAPABILITY_EVIDENCE = {
    "test automation": {
        "aliases": [
            "test automation", "automated testing", "automation testing", "qa automation",
            "automation test", "test automation framework", "automated regression",
            "ui automation", "automation frameworks", "automation framework",
            "end to end automation", "e2e automation",
        ],
        "defining_tools": [
            "selenium", "cypress", "playwright", "appium", "xctest", "xcuitest",
        ],
        "verified_patterns": [
            r"\b(?:selenium|cypress|playwright|appium|xcuitest|xctest)\b",
            r"\b(?:automat(?:ed|ing)|automation)\b.{0,90}\b(?:test|testing|regression|ui|e2e|end[\s-]*to[\s-]*end|suite|framework|script)\b",
            r"\b(?:test|testing|regression|ui|e2e)\b.{0,90}\b(?:automat(?:ed|ion)|framework|suite|script)\b",
            r"\b(?:testng|junit|cucumber|pytest)\b.{0,70}\b(?:automat|regression|test suite|framework)\b",
            r"\b(?:page object model|pom)\b.{0,50}\b(?:test|automat)",
        ],
        "related_tools": ["postman", "jmeter", "junit", "testng"],
    },
    "database validation": {
        "aliases": [
            "database validation", "sql validation", "backend database testing",
            "data validation", "db validation", "backend data verification",
        ],
        "defining_tools": [],
        "verified_patterns": [
            r"\b(?:sql|oracle|mysql|postgres(?:ql)?|sql\s*server)\b.{0,140}\b(?:validat\w*|verif\w*|check(?:ed|ing)?)\b.{0,90}\b(?:data|transaction|record|consistenc\w*|application)\b",
            r"\b(?:validat\w*|verif\w*)\b.{0,90}\b(?:application data|transaction results?|record updates?|data consistency|backend (?:data|processing)|database)\b",
            r"\b(?:database|data)\s+validat\w*\b",
            r"\b(?:validat\w*|verif\w*)\b.{0,50}\b(?:sql|oracle|mysql|postgres(?:ql)?)\b",
            r"\b(?:sql|oracle|mysql|postgres(?:ql)?)\b.{0,50}\b(?:validat\w*|verif\w*)\b",
        ],
        # SQL listed alone is related, not proof of validation work.
        "related_tools": ["sql", "oracle", "mysql", "postgresql", "sql server"],
        "related_patterns": [
            r"\b(?:sql|oracle|mysql|postgres(?:ql)?|sql\s*server)\b",
        ],
    },
    "api testing": {
        "aliases": [
            "api testing", "api test", "api test automation", "rest api testing",
        ],
        "defining_tools": ["postman", "rest assured", "jmeter"],
        "verified_patterns": [
            r"\b(?:api|rest)\b.{0,60}\b(?:test|testing|automat)\b",
            r"\b(?:test|testing|automat)\b.{0,60}\b(?:api|rest)\b",
            r"\b(?:postman|rest\s*assured|jmeter)\b",
        ],
        "related_tools": ["selenium", "cypress"],
    },
    "infrastructure as code": {
        "aliases": [
            "infrastructure as code", "infra as code", "iac",
        ],
        "defining_tools": ["terraform", "cloudformation", "pulumi", "ansible"],
        "verified_patterns": [
            r"\binfrastructure as code\b",
            r"\b(?:terraform|cloudformation|pulumi)\b",
            r"\bprovision(?:ed|ing)\b.{0,100}\b(?:infrastructure|aws|azure|gcp).{0,80}\b(?:terraform|cloudformation|ansible|module)\b",
            r"\b(?:terraform|cloudformation|ansible)\b.{0,80}\b(?:infrastruct|provision|module|template|automat)\b",
        ],
        "related_tools": ["docker", "kubernetes", "aws", "jenkins"],
    },
    "etl": {
        "aliases": [
            "etl", "etl pipeline", "etl pipelines", "data pipeline", "data pipelines",
            "extract transform load",
        ],
        "defining_tools": ["spark", "pyspark", "airflow", "databricks", "snowflake"],
        "verified_patterns": [
            r"\b(?:etl|elt)\b",
            r"\bdata pipelines?\b",
            r"\b(?:pyspark|spark|airflow|kafka|databricks)\b.{0,120}\b(?:pipeline|ingest|transform|load)\b",
            r"\b(?:ingest|transform|load)\b.{0,100}\b(?:pipeline|snowflake|warehouse|databricks|spark)\b",
        ],
        "related_tools": ["python", "sql", "aws"],
    },
    "rest apis": {
        "aliases": [
            "rest apis", "rest api", "restful api", "restful apis", "restful",
            "rest api development", "restful endpoints", "restful endpoint",
        ],
        "defining_tools": [],
        "verified_patterns": [
            r"\brest(?:ful)?\b.{0,60}\b(?:api|apis|endpoint|endpoints|service|services)\b",
            r"\b(?:api|apis|endpoint|endpoints)\b.{0,60}\brest(?:ful)?\b",
            r"\b(?:exposing|exposed|built|building|designed|designing)\b.{0,80}\brest(?:ful)?\b",
        ],
        "related_tools": ["soap", "graphql"],
    },
    "ci/cd": {
        "aliases": [
            "ci/cd", "ci cd", "cicd", "continuous integration", "continuous delivery",
            "continuous deployment",
        ],
        "defining_tools": ["jenkins", "github actions", "gitlab ci", "azure devops", "argo cd"],
        "verified_patterns": [
            r"\bci\s*/?\s*cd\b",
            r"\bcontinuous (?:integration|delivery|deployment)\b",
            r"\b(?:jenkins|github actions|gitlab ci|azure devops|argo\s*cd)\b",
        ],
        "related_tools": ["docker", "kubernetes", "git"],
    },
}

# Related evidence that may justify POSSIBLE_EVIDENCE / INDIRECT, never VERIFIED.
# Prevents Postman→Rest Assured, AWS→Azure, Selenium→Cypress, etc.
RELATED_NOT_EQUIVALENT = {
    "rest assured": {
        "related_tools": ["postman", "api testing", "jmeter"],
        "related_patterns": [r"\b(?:postman|api testing|rest\s+api)\b"],
    },
    "postman": {
        "related_tools": ["rest assured", "api testing"],
        "related_patterns": [r"\b(?:rest\s*assured|api testing)\b"],
    },
    "cypress": {
        "related_tools": ["selenium", "playwright", "test automation"],
        "related_patterns": [r"\b(?:selenium|playwright|test automation)\b"],
    },
    "playwright": {
        "related_tools": ["selenium", "cypress", "test automation"],
        "related_patterns": [r"\b(?:selenium|cypress|test automation)\b"],
    },
    "selenium": {
        "related_tools": ["cypress", "playwright"],
        "related_patterns": [r"\b(?:cypress|playwright)\b"],
    },
    "azure": {
        "related_tools": ["aws", "gcp"],
        "related_patterns": [r"\b(?:aws|amazon web services|gcp|google cloud)\b"],
    },
    "aws": {
        "related_tools": ["azure", "gcp"],
        "related_patterns": [r"\b(?:azure|gcp|google cloud)\b"],
    },
    "gcp": {
        "related_tools": ["aws", "azure"],
        "related_patterns": [r"\b(?:aws|azure)\b"],
    },
    "azure devops": {
        "related_tools": ["jenkins", "github actions", "gitlab ci", "ci/cd"],
        "related_patterns": [r"\b(?:jenkins|github actions|gitlab ci|ci\s*/?\s*cd)\b"],
    },
    "soap": {
        "related_tools": ["rest apis", "rest"],
        "related_patterns": [r"\brest(?:ful)?\b"],
    },
    "angular": {
        "related_tools": ["react", "vue"],
        "related_patterns": [r"\b(?:react|vue)\b"],
    },
    "hibernate": {
        "related_tools": ["spring", "spring boot", "jpa"],
        "related_patterns": [r"\b(?:spring boot|jpa)\b"],
    },
    "kubernetes": {
        "related_tools": ["docker"],
        "related_patterns": [r"\bdocker\b"],
    },
    "docker": {
        "related_tools": ["kubernetes"],
        "related_patterns": [r"\bkubernetes|k8s\b"],
    },
}

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
