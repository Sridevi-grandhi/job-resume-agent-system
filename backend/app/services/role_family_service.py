"""Generic role-family alignment for Updated Job Search discovery.

Discovery relevance ("does this job belong to the occupation the user asked
for?") is a different question from the resume match score ("how well does
this resume fit an already-relevant job?"). This module answers only the
first one, and it answers it from TITLES first: shared tooling — AWS,
Kubernetes, Docker, CI/CD, Linux, Python, Terraform — is common to many
occupations, so skill overlap alone must never make a Java/React/Data role
eligible for a DevOps search, or a DevOps role eligible for a Java search.

The taxonomy below is data, not branching. Every family is described the
same way — the title patterns that name it, the phrases that identify the
occupation itself, the technologies that merely support it, and the other
families that belong to the same occupation cluster — so DevOps, Java Full
Stack, SDET, Data Engineering, SAP, etc. all flow through one code path. A
preferred title that matches no family at all still gets a title-first
decision, via a generic significant-token fallback.

Nothing here is persisted. Every decision is computed from one user's
current saved preference at request time (see job_relevance_filter), so a
preference change immediately re-evaluates the same stored jobs, and one
user's preference can never change what another user sees.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Deliberately NOT part of any family's evidence: tooling that shows up in
# almost every modern engineering JD regardless of occupation. Kubernetes and
# Terraform ARE listed as supporting evidence below, but only ever alongside
# a role phrase — never on their own (see _evidence_strength).
SHARED_TOOLING = frozenset({
    "aws", "azure", "gcp", "docker", "ci cd", "cicd", "jenkins", "git", "github actions",
    "gitlab ci", "linux", "python", "bash", "sql", "agile", "scrum", "jira", "rest api",
})

# Words that describe seniority or are generic to nearly every engineering
# title. Never sufficient on their own to align two roles (Task 5).
GENERIC_TITLE_WORDS = frozenset({
    "a", "an", "and", "the", "of", "for", "with", "in", "to", "on", "at", "or",
    "senior", "sr", "junior", "jr", "lead", "principal", "staff", "mid", "level",
    "i", "ii", "iii", "iv", "engineer", "engineering", "developer", "development",
    "specialist", "consultant", "analyst", "professional", "expert", "remote",
    "hybrid", "onsite", "contract", "fulltime", "new", "job", "role", "years",
})


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------
# titles        — regex fragments matched against a normalized job/preference title
# role_phrases  — phrases that name the occupation itself (strong evidence)
# supporting    — technologies typical of the occupation (weak on their own)

_FAMILIES: dict[str, dict] = {
    "DEVOPS": {
        "label": "DevOps",
        "titles": (r"dev\s*ops", r"dev\s*sec\s*ops", r"build (?:and|&) release engineer", r"release engineer"),
        "role_phrases": (
            "devops", "devsecops", "infrastructure as code", "deployment automation",
            "configuration management", "cloud operations", "gitops", "infrastructure automation",
            "release engineering",
        ),
        "supporting": (
            "terraform", "kubernetes", "helm", "ansible", "cloudformation", "argo cd",
            "container orchestration", "provisioning", "observability", "on call",
            "cluster", "pipelines", "puppet", "chef",
        ),
    },
    "SRE": {
        "label": "Site Reliability Engineering",
        "titles": (r"site reliability engineer(?:ing)?", r"sre", r"production engineer", r"reliability engineer"),
        "role_phrases": (
            "site reliability", "sre", "reliability engineering", "error budget",
            "incident response", "service level objective", "slo", "production support",
        ),
        "supporting": (
            "observability", "prometheus", "grafana", "datadog", "on call", "kubernetes",
            "terraform", "monitoring", "alerting", "uptime", "latency",
        ),
    },
    "PLATFORM_ENG": {
        "label": "Platform Engineering",
        "titles": (r"platform engineer(?:ing)?", r"infrastructure automation engineer", r"developer platform engineer"),
        "role_phrases": (
            "platform engineering", "internal developer platform", "developer platform",
            "developer experience", "self service platform", "golden path",
        ),
        "supporting": ("kubernetes", "helm", "argo cd", "terraform", "backstage", "service mesh", "crossplane"),
    },
    "CLOUD_INFRA": {
        "label": "Cloud / Infrastructure Engineering",
        "titles": (
            r"cloud\s+(?:infrastructure\s+)?engineer", r"cloud infrastructure",
            r"infrastructure engineer", r"cloud architect", r"systems engineer",
            r"linux (?:systems )?(?:administrator|engineer)", r"cloud operations",
        ),
        "role_phrases": (
            "cloud infrastructure", "cloud migration", "landing zone", "cloud architecture",
            "infrastructure engineering", "systems administration", "capacity planning",
        ),
        "supporting": ("terraform", "cloudformation", "vpc", "iam", "networking", "kubernetes", "virtualization"),
    },
    "JAVA_FULL_STACK": {
        "label": "Java Full Stack",
        "titles": (
            r"java\s+full\s*stack", r"full\s*stack\s+java", r"java application developer",
            r"java\s+(?:and\s+)?(?:react|angular)\s+developer",
        ),
        "role_phrases": ("java full stack", "full stack java", "spring boot", "j2ee", "jee", "hibernate"),
        "supporting": ("java", "spring", "microservices", "maven", "react", "angular", "jpa", "servlet"),
    },
    "JAVA_BACKEND": {
        "label": "Java Backend",
        "titles": (
            r"java\s+(?:backend|back\s*end|developer|engineer|architect|software engineer)",
            r"backend\s+java", r"back\s*end\s+java", r"spring boot developer", r"j2ee", r"java",
        ),
        "role_phrases": ("spring boot", "j2ee", "jee", "hibernate", "java developer", "java engineer"),
        "supporting": ("java", "spring", "microservices", "maven", "gradle", "jpa", "servlet", "kafka"),
    },
    "FULL_STACK_JS": {
        "label": "JavaScript Full Stack",
        "titles": (
            r"(?:react|angular|vue|node|javascript|typescript|mern|mean)\s+full\s*stack",
            r"full\s*stack\s+(?:react|angular|vue|node|javascript|typescript)",
            r"(?:mern|mean)\s+(?:stack)?\s*developer",
        ),
        "role_phrases": ("react", "angular", "vue", "next.js", "node.js", "redux", "typescript"),
        "supporting": ("javascript", "html", "css", "tailwind", "webpack", "express", "graphql"),
    },
    "FULL_STACK_GENERIC": {
        "label": "Full Stack",
        "titles": (r"full\s*stack",),
        "role_phrases": ("full stack", "front end and back end", "end to end feature"),
        "supporting": ("react", "angular", "node.js", "rest api", "microservices"),
    },
    "FRONTEND": {
        "label": "Frontend",
        "titles": (
            r"front\s*end", r"(?:react|angular|vue)\s+(?:developer|engineer)",
            r"ui\s+(?:developer|engineer)", r"web developer", r"ui ux",
        ),
        "role_phrases": ("frontend", "front end", "user interface", "responsive design", "react", "angular", "vue"),
        "supporting": ("javascript", "typescript", "html", "css", "figma", "accessibility", "redux"),
    },
    "PYTHON_BACKEND": {
        "label": "Python Backend",
        "titles": (
            r"python\s+(?:backend|back\s*end|developer|engineer|software engineer)",
            r"django developer", r"fastapi developer",
        ),
        "role_phrases": ("django", "flask", "fastapi", "python developer", "python engineer"),
        "supporting": ("python", "celery", "sqlalchemy", "rest api", "postgresql"),
    },
    "PYTHON_FULL_STACK": {
        "label": "Python Full Stack",
        "titles": (r"python\s+full\s*stack", r"full\s*stack\s+python"),
        "role_phrases": ("python full stack", "django", "flask", "fastapi"),
        "supporting": ("python", "react", "angular", "rest api"),
    },
    "DATA_ENGINEERING": {
        "label": "Data Engineering",
        "titles": (
            r"data platform engineer", r"data engineer(?:ing)?", r"etl (?:developer|engineer)",
            r"big data engineer", r"analytics engineer", r"data warehouse (?:developer|engineer)",
        ),
        "role_phrases": (
            "data engineering", "data pipeline", "data warehouse", "data lake", "etl",
            "elt", "dimensional model", "ingestion pipeline",
        ),
        "supporting": ("spark", "pyspark", "airflow", "snowflake", "databricks", "dbt", "kafka", "redshift", "hadoop"),
    },
    "DATA_SCIENCE": {
        "label": "Data Science",
        "titles": (r"data scientist", r"data science", r"research scientist"),
        "role_phrases": ("data science", "data scientist", "statistical model", "experimentation", "a b testing"),
        "supporting": ("pandas", "numpy", "scikit learn", "jupyter", "regression", "forecasting"),
    },
    "AI_ML": {
        "label": "Machine Learning / AI",
        "titles": (
            r"(?:machine learning|ml|ai)\s+(?:engineer|architect|scientist)",
            r"ai\s+(?:engineer|architect)", r"mlops engineer", r"deep learning engineer",
        ),
        "role_phrases": ("machine learning", "deep learning", "model training", "mlops", "llm", "neural network"),
        "supporting": ("tensorflow", "pytorch", "scikit learn", "hugging face", "inference", "embeddings"),
    },
    "QA_SDET": {
        "label": "QA / SDET",
        "titles": (
            r"sdet", r"software development engineer in test", r"software developer in test",
            r"software engineer in test", r"qa\s+(?:automation|engineer|analyst|tester|lead)",
            r"(?:test|automation) (?:automation )?(?:engineer|tester|analyst)",
            r"quality (?:assurance|engineer|engineering)", r"automation tester",
        ),
        "role_phrases": (
            "sdet", "test automation", "qa automation", "quality assurance", "test framework",
            "regression testing", "test cases", "functional testing", "defect",
        ),
        "supporting": ("selenium", "playwright", "cypress", "appium", "testng", "junit", "cucumber", "postman", "rest assured"),
    },
    "MOBILE_IOS": {
        "label": "iOS",
        "titles": (r"ios", r"swift (?:developer|engineer)"),
        "role_phrases": ("ios", "swiftui", "app store", "xcode"),
        "supporting": ("swift", "objective c", "xctest", "xcuitest", "cocoapods"),
    },
    "MOBILE_ANDROID": {
        "label": "Android",
        "titles": (r"android", r"kotlin (?:developer|engineer)"),
        "role_phrases": ("android", "jetpack compose", "play store"),
        "supporting": ("kotlin", "android sdk", "gradle", "espresso"),
    },
    "DOTNET": {
        "label": ".NET",
        "titles": (r"\.net", r"dot net", r"c#\s+(?:developer|engineer)"),
        "role_phrases": (".net", "dot net", "asp.net", "c#"),
        "supporting": ("entity framework", "blazor", "azure", "mvc"),
    },
    "GOLANG": {
        "label": "Go",
        "titles": (r"go\s*lang", r"golang (?:developer|engineer)", r"go (?:developer|engineer)"),
        "role_phrases": ("golang", "go developer"),
        "supporting": ("goroutine", "grpc", "microservices"),
    },
    "RUBY": {
        "label": "Ruby / Rails",
        "titles": (r"(?:ruby|rails)\s+(?:developer|engineer)", r"ruby on rails"),
        "role_phrases": ("ruby on rails", "rails"),
        "supporting": ("ruby", "activerecord", "sidekiq"),
    },
    "SAP": {
        "label": "SAP",
        "titles": (r"sap", r"abap"),
        "role_phrases": ("sap", "abap", "s 4hana", "hana", "fiori", "sap testing"),
        "supporting": ("idoc", "bapi", "sap mm", "sap fico", "sap sd"),
    },
    "SECURITY": {
        "label": "Security",
        "titles": (
            r"(?:security|cyber\s*security|information security) (?:engineer|analyst|architect)",
            r"application security", r"penetration tester", r"soc analyst",
        ),
        "role_phrases": ("cybersecurity", "information security", "threat", "vulnerability", "penetration testing", "siem"),
        "supporting": ("nessus", "burp", "owasp", "firewall", "encryption"),
    },
    "NETWORK": {
        "label": "Networking",
        "titles": (r"network (?:engineer|administrator|architect)", r"noc engineer"),
        "role_phrases": ("network engineering", "routing", "switching", "bgp", "ospf"),
        "supporting": ("cisco", "firewall", "vpn", "lan", "wan"),
    },
    "DATABASE": {
        "label": "Database",
        "titles": (r"database (?:administrator|engineer|developer)", r"\bdba\b", r"sql (?:developer|server developer)"),
        "role_phrases": ("database administration", "dba", "query tuning", "stored procedure"),
        "supporting": ("postgresql", "mysql", "oracle", "sql server", "mongodb", "replication"),
    },
    "IT_SUPPORT": {
        "label": "IT Support",
        "titles": (
            r"(?:service|help)\s*desk", r"(?:it|desktop|technical) support",
            r"support (?:engineer|technician)", r"systems administrator",
        ),
        "role_phrases": ("service desk", "help desk", "ticket", "end user support", "troubleshooting"),
        "supporting": ("active directory", "office 365", "itil", "zendesk"),
    },
    "PRODUCT_MANAGEMENT": {
        "label": "Product Management",
        "titles": (r"product (?:manager|owner)", r"product management"),
        "role_phrases": ("product management", "product roadmap", "user stories", "backlog"),
        "supporting": ("stakeholder", "discovery", "kpi"),
    },
    "PROJECT_MANAGEMENT": {
        "label": "Project / Program Management",
        "titles": (r"(?:project|program|delivery) manager", r"scrum master", r"program management"),
        "role_phrases": ("project management", "program management", "scrum master", "project plan"),
        "supporting": ("stakeholder", "milestone", "budget", "pmp"),
    },
    "MANAGEMENT": {
        "label": "Engineering Management",
        "titles": (r"engineering manager", r"director of engineering", r"head of engineering", r"manager"),
        "role_phrases": ("engineering manager", "people management", "direct reports", "hiring plan"),
        "supporting": ("mentoring", "performance review", "roadmap"),
    },
    "EXECUTIVE": {
        "label": "Executive",
        "titles": (r"vice president", r"\bvp\b", r"chief \w+ officer", r"\bcto\b", r"\bceo\b", r"head of"),
        "role_phrases": ("executive leadership", "board", "p l responsibility", "organizational strategy"),
        "supporting": ("strategy", "governance"),
    },
    "MARKETING": {
        "label": "Marketing",
        "titles": (r"marketing", r"growth marketing", r"seo", r"content strategist", r"merchandising"),
        "role_phrases": ("marketing campaign", "seo", "brand", "demand generation", "merchandising"),
        "supporting": ("google ads", "hubspot", "analytics"),
    },
    "SALES": {
        "label": "Sales",
        "titles": (r"sales", r"account executive", r"business development", r"inside sales"),
        "role_phrases": ("sales quota", "pipeline of prospects", "business development", "closing deals"),
        "supporting": ("salesforce", "crm", "outbound"),
    },
    "CUSTOMER_SUCCESS": {
        "label": "Customer Support / Success",
        "titles": (
            r"customer (?:success|support|service)", r"client success",
            r"support (?:specialist|advocate)", r"community (?:manager|growth)",
        ),
        "role_phrases": ("customer success", "customer support", "client relationship", "onboarding customers"),
        "supporting": ("zendesk", "intercom", "churn"),
    },
    "DESIGN": {
        "label": "Design",
        "titles": (r"(?:graphic|product|ux|ui ux|visual) designer", r"designer", r"photographer", r"video editor"),
        "role_phrases": ("visual design", "graphic design", "brand identity", "photography", "typography"),
        "supporting": ("figma", "photoshop", "illustrator", "adobe"),
    },
    "WRITING": {
        "label": "Writing / Content",
        "titles": (r"(?:content|copy|technical)\s*writer", r"copywriter", r"editor", r"content reviewer"),
        "role_phrases": ("content writing", "copywriting", "editorial", "proofreading"),
        "supporting": ("cms", "wordpress", "style guide"),
    },
    "HR_RECRUITING": {
        "label": "HR / Recruiting",
        "titles": (r"recruiter", r"talent acquisition", r"people (?:ops|operations|business partner)", r"human resources"),
        "role_phrases": ("talent acquisition", "recruiting", "human resources", "employee relations", "people operations"),
        "supporting": ("ats", "onboarding", "benefits"),
    },
    "FINANCE": {
        "label": "Finance / Accounting",
        "titles": (r"accountant", r"financial (?:analyst|officer)", r"controller", r"bookkeeper", r"finance manager"),
        "role_phrases": ("financial reporting", "accounting", "general ledger", "budgeting", "audit"),
        "supporting": ("quickbooks", "gaap", "excel"),
    },
    "BUSINESS_ANALYSIS": {
        "label": "Business Analysis",
        "titles": (r"business analyst", r"business systems analyst", r"corporate strategy", r"acquisition analyst"),
        "role_phrases": ("business analysis", "requirements gathering", "stakeholder interviews", "process improvement"),
        "supporting": ("jira", "confluence", "visio"),
    },
    "ADMIN": {
        "label": "Administrative",
        "titles": (r"office (?:assistant|manager)", r"administrative assistant", r"executive assistant", r"data entry"),
        "role_phrases": ("administrative support", "scheduling", "data entry", "clerical"),
        "supporting": ("calendar", "filing"),
    },
}

# Families that belong to the same occupation. Membership is symmetric and a
# family may appear in more than one cluster (e.g. FULL_STACK_GENERIC is
# adjacent to both the Java and the JavaScript full-stack families).
_ROLE_CLUSTERS: tuple[tuple[str, ...], ...] = (
    ("DEVOPS", "SRE", "PLATFORM_ENG", "CLOUD_INFRA"),
    ("JAVA_FULL_STACK", "JAVA_BACKEND", "FULL_STACK_GENERIC"),
    ("FULL_STACK_JS", "FRONTEND", "FULL_STACK_GENERIC"),
    ("PYTHON_BACKEND", "PYTHON_FULL_STACK"),
    ("DATA_ENGINEERING", "DATA_SCIENCE"),
    ("DATA_SCIENCE", "AI_ML"),
    ("NETWORK", "IT_SUPPORT"),
    ("PRODUCT_MANAGEMENT", "PROJECT_MANAGEMENT"),
    ("MANAGEMENT", "EXECUTIVE"),
)


def _normalize(text: str) -> str:
    """Lowercase, punctuation-flattened text. Keeps +, # and . so c++, c# and
    node.js survive; turns -, / and , into spaces so "Full-Stack", "CI/CD" and
    "DevOps/SRE" read the same as their spaced forms."""
    value = str(text or "").lower()
    value = re.sub(r"[^a-z0-9+#.]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _boundary(pattern: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])")


_TITLE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    family: tuple(_boundary(pattern) for pattern in definition["titles"])
    for family, definition in _FAMILIES.items()
}
_ROLE_PHRASE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    family: tuple(_boundary(re.escape(_normalize(phrase))) for phrase in definition["role_phrases"])
    for family, definition in _FAMILIES.items()
}
_SUPPORTING_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    family: tuple(
        _boundary(re.escape(_normalize(phrase)))
        for phrase in definition["supporting"]
        if _normalize(phrase) not in SHARED_TOOLING
    )
    for family, definition in _FAMILIES.items()
}


def _build_adjacency() -> dict[str, frozenset[str]]:
    adjacency: dict[str, set[str]] = {family: set() for family in _FAMILIES}
    for cluster in _ROLE_CLUSTERS:
        for family in cluster:
            adjacency[family].update(member for member in cluster if member != family)
    return {family: frozenset(members) for family, members in adjacency.items()}


_ADJACENCY = _build_adjacency()


def family_label(family: str | None) -> str:
    if not family:
        return "an unrecognized role family"
    return _FAMILIES.get(family, {}).get("label", family)


def adjacent_families(family: str | None) -> frozenset[str]:
    return _ADJACENCY.get(family or "", frozenset())


# One extra discovery query per saved title. These are occupation search
# phrases, not employers. The user's own title is always searched first.
_FAMILY_SEARCH_TITLES: dict[str, tuple[str, ...]] = {
    "JAVA_FULL_STACK": ("Java Developer", "Spring Boot Developer", "Full Stack Java Developer"),
    "JAVA_BACKEND": ("Java Developer", "Spring Boot Developer"),
    "FULL_STACK_JS": ("React Developer", "Frontend Developer"),
    "FRONTEND": ("Frontend Developer", "React Developer"),
    "FULL_STACK_GENERIC": ("Full Stack Engineer",),
    "PYTHON_BACKEND": ("Python Developer",),
    "PYTHON_FULL_STACK": ("Python Developer",),
    "DEVOPS": ("DevOps Engineer", "Platform Engineer"),
    "SRE": ("Site Reliability Engineer",),
    "PLATFORM_ENG": ("Platform Engineer",),
    "CLOUD_INFRA": ("Cloud Engineer",),
    "DATA_ENGINEERING": ("Data Engineer",),
    "DATA_SCIENCE": ("Data Scientist",),
    "AI_ML": ("Machine Learning Engineer",),
    "QA_SDET": ("SDET", "QA Automation Engineer"),
    "DOTNET": (".NET Developer",),
    "GOLANG": ("Go Developer",),
    "SECURITY": ("Security Engineer",),
    "MOBILE_IOS": ("iOS Developer",),
    "MOBILE_ANDROID": ("Android Developer",),
}


def discovery_search_titles(preferred_title: str, skills: str = "", *, extra_limit: int = 1) -> list[str]:
    """Exact saved title, plus at most `extra_limit` role-family search titles.

    Extra titles stay inside the saved occupation. A skill token such as
    "spring" prefers "Spring Boot Developer" over a broader sibling, and a
    title the user already asked for is not repeated.
    """
    exact = (preferred_title or "").strip()
    if not exact:
        return []
    titles = [exact]
    if extra_limit <= 0:
        return titles
    family = classify_title(exact)
    if not family:
        return titles
    skill_text = _normalize(skills)
    exact_norm = _normalize(exact)
    ranked: list[tuple[int, str]] = []
    for candidate in _FAMILY_SEARCH_TITLES.get(family, ()):
        normalized = _normalize(candidate)
        if not normalized or normalized in exact_norm or exact_norm in normalized:
            continue
        overlap = sum(1 for token in significant_tokens(candidate) if token in skill_text)
        ranked.append((overlap, candidate))
    ranked.sort(key=lambda item: item[0], reverse=True)
    for _overlap, candidate in ranked[:extra_limit]:
        titles.append(candidate)
    return titles


def _best_family(normalized_title: str) -> str | None:
    """The single family whose title pattern makes the strongest claim on this
    title. Longest match wins (so "Java Full Stack" beats "Full Stack" and
    "Data Platform Engineer" beats "Platform Engineer"); an earlier match
    breaks ties, since a title leads with its primary occupation, and a
    remaining tie goes to whichever family is declared first."""
    best_rank: tuple[int, int] | None = None
    best_family: str | None = None
    for family, patterns in _TITLE_PATTERNS.items():
        for pattern in patterns:
            found = pattern.search(normalized_title)
            if not found:
                continue
            rank = (found.end() - found.start(), -found.start())
            if best_rank is None or rank > best_rank:
                best_rank, best_family = rank, family
    return best_family


def classify_title(title: str) -> str | None:
    """Role family for a job or preference title, or None when the title names
    no recognizable occupation (e.g. "Senior Independent Software Developer").

    Parenthetical asides are stripped first so the primary occupation wins:
    "Lead Software Engineer, Full Stack (Java, Python, SRE, AWS)" is a
    full-stack role that happens to list SRE as a technology, not an SRE role.
    The full title is only consulted when the primary segment names nothing,
    which is what lets "Senior Software Engineer, Back-End (Java)" resolve."""
    primary = _normalize(re.sub(r"\([^)]*\)", " ", str(title or "")))
    family = _best_family(primary) if primary else None
    if family:
        return family
    full = _normalize(title)
    return _best_family(full) if full and full != primary else None


def _evidence_strength(families: frozenset[str], text: str) -> tuple[int, int]:
    """(role_phrase_hits, supporting_hits) for an occupation cluster.

    Counts distinct phrases across the cluster, never the shared tooling every
    engineering JD mentions — that exclusion is what stops an AWS/Kubernetes
    laundry list from standing in for real role evidence."""
    role_hits = 0
    supporting_hits = 0
    for family in families:
        role_hits += sum(1 for pattern in _ROLE_PHRASE_PATTERNS[family] if pattern.search(text))
        supporting_hits += sum(1 for pattern in _SUPPORTING_PATTERNS[family] if pattern.search(text))
    return role_hits, supporting_hits


def significant_tokens(title: str) -> list[str]:
    """Title words that actually identify a role — "senior", "engineer" and
    friends removed, so they can never align two unrelated occupations."""
    return [
        token for token in _normalize(title).split()
        if token not in GENERIC_TITLE_WORDS and len(token) > 1
    ]


@dataclass(frozen=True)
class RoleDecision:
    """One user's, one job's discovery verdict. Never written to the job row."""

    relevant: bool
    alignment: str  # TITLE_MATCH | ADJACENT_ROLE | JD_EVIDENCE | TOKEN_MATCH
    #                 TITLE_CONFLICT | NO_ROLE_EVIDENCE | NO_PREFERENCE
    reason: str
    preference_family: str | None = None
    job_family: str | None = None


def evaluate_role_alignment(
    *, job_title: str, job_description: str, preferred_title: str,
) -> RoleDecision:
    """Title-first role gate: is this job the occupation the user asked for?

    The job's `skills` list is deliberately NOT an input. Aggregators attach
    the same global technology tag list to every posting they carry, so a
    React role and a DevOps role from the same marketplace advertise
    identical "skills" — using them here would re-introduce exactly the
    skills-override-title bug this gate exists to prevent. Skills still
    refine ranking afterwards (see job_relevance_filter)."""
    preferred_title = (preferred_title or "").strip()
    if not preferred_title:
        return RoleDecision(True, "NO_PREFERENCE", "No saved job title to filter on.")

    pref_family = classify_title(preferred_title)
    job_family = classify_title(job_title)
    # Title carries more weight than body copy, so it is part of the evidence
    # text; the description is capped because the role statement lives up top
    # and the tail is usually benefits/EEO boilerplate.
    evidence_text = f"{_normalize(job_title)} {_normalize((job_description or '')[:6000])}"

    if pref_family is None:
        return _token_fallback(job_title, evidence_text, preferred_title, job_family)

    cluster = frozenset({pref_family}) | adjacent_families(pref_family)

    if job_family == pref_family:
        return RoleDecision(
            True, "TITLE_MATCH",
            f"Title is a {family_label(pref_family)} role, matching the saved job title.",
            pref_family, job_family,
        )

    if job_family in cluster:
        role_hits, supporting_hits = _evidence_strength(cluster, evidence_text)
        if role_hits + supporting_hits >= 1:
            return RoleDecision(
                True, "ADJACENT_ROLE",
                f"Title is {family_label(job_family)}, the same occupation family as "
                f"{family_label(pref_family)}, and the description supports it.",
                pref_family, job_family,
            )
        return RoleDecision(
            False, "NO_ROLE_EVIDENCE",
            f"Title is {family_label(job_family)} but the description shows no "
            f"{family_label(pref_family)} role evidence.",
            pref_family, job_family,
        )

    if job_family is not None:
        return _conflict_decision(pref_family, job_family, cluster, evidence_text)

    # Ambiguous title (no recognizable occupation): the description has to
    # name the occupation itself, not merely list its tools.
    role_hits, supporting_hits = _evidence_strength(cluster, evidence_text)
    if role_hits >= 1 and role_hits + supporting_hits >= 2:
        return RoleDecision(
            True, "JD_EVIDENCE",
            f"Title is unspecific, but the description describes {family_label(pref_family)} work.",
            pref_family, job_family,
        )
    return RoleDecision(
        False, "NO_ROLE_EVIDENCE",
        f"Title names no recognizable role family and the description lacks "
        f"{family_label(pref_family)} role evidence.",
        pref_family, job_family,
    )


def _conflict_decision(
    pref_family: str, job_family: str, cluster: frozenset[str], evidence_text: str,
) -> RoleDecision:
    """A different occupation in the title. Rejected unless the description is
    unmistakably the preferred role AND carries no trace of the titled one —
    deliberately near-unreachable, because a Java or React posting always
    evidences Java or React somewhere, and shared cloud tooling in its JD is
    not a reason to surface it in a DevOps search."""
    role_hits, _ = _evidence_strength(cluster, evidence_text)
    conflict_role_hits, conflict_supporting_hits = _evidence_strength(frozenset({job_family}), evidence_text)
    if role_hits >= 2 and conflict_role_hits == 0 and conflict_supporting_hits == 0:
        return RoleDecision(
            True, "JD_EVIDENCE",
            f"Title reads as {family_label(job_family)}, but the description is entirely "
            f"{family_label(pref_family)} work with no {family_label(job_family)} content.",
            pref_family, job_family,
        )
    return RoleDecision(
        False, "TITLE_CONFLICT",
        f"Title is a {family_label(job_family)} role, a different occupation from the "
        f"saved {family_label(pref_family)} preference (shared tools do not make it a match).",
        pref_family, job_family,
    )


def _token_fallback(
    job_title: str, evidence_text: str, preferred_title: str, job_family: str | None,
) -> RoleDecision:
    """Preferred title outside the taxonomy (e.g. a niche or brand-new role).
    Still title-first: the job title must carry the preference's own defining
    words, or the description must name the preferred role outright."""
    required = significant_tokens(preferred_title)
    if not required:
        return RoleDecision(True, "NO_PREFERENCE", "Saved job title has no distinguishing words.")

    job_title_norm = _normalize(job_title)
    hits = [token for token in required if _boundary(re.escape(token)).search(job_title_norm)]
    enough = len(hits) == len(required) or (len(required) >= 3 and len(hits) >= len(required) - 1)
    if enough:
        return RoleDecision(
            True, "TOKEN_MATCH",
            f"Title carries the saved title's defining words ({', '.join(hits)}).",
            None, job_family,
        )
    if hits and _boundary(re.escape(_normalize(preferred_title))).search(evidence_text):
        return RoleDecision(
            True, "JD_EVIDENCE",
            f"Description names the saved role \"{preferred_title.strip()}\" directly.",
            None, job_family,
        )
    return RoleDecision(
        False, "TITLE_CONFLICT",
        f"Title does not carry the saved title's defining words "
        f"({', '.join(required)}); shared skills alone are not enough.",
        None, job_family,
    )
