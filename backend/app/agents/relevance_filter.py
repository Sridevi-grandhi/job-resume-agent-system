"""
Relevance helpers for the Updated Job Search Agent (Remotive API only).

Existing Job Search Agent (job_search_agent.py) does not use this module.
"""

import re
from html import unescape


JAVA_FULL_STACK_FAMILY = "JAVA_FULL_STACK"

TECHNOLOGY_ALIASES = {
    "springboot": "spring boot",
    "react js": "react",
    "reactjs": "react",
    "angular js": "angular",
    "node js": "node",
    "nodejs": "node",
    "postgresql": "postgres",
    "java ee": "j2ee",
    "rest api": "rest",
    "micro service": "microservices",
    "microservice": "microservices",
    "xcuitest": "xcuitest",
    "xctest": "xctest",
    "big data": "big data",
}

# Role families used by Updated Job Search Agent alias / keyword matching.
ROLE_FAMILIES = {
    "sdet": {
        "aliases": (
            "SDET",
            "Senior SDET",
            "Software Engineer in Test",
            "Software Development Engineer in Test",
            "QA Automation Engineer",
            "Test Automation Engineer",
            "Automation Test Engineer",
            "Quality Engineer",
            "Quality Assurance Automation Engineer",
        ),
        "role_keywords": (
            "sdet",
            "software engineer in test",
            "software development engineer in test",
            "qa automation",
            "test automation",
            "automation engineer",
            "quality engineer",
            "quality assurance",
            "automation testing",
        ),
        "search_queries": (
            "SDET",
            "Software Engineer in Test",
            "QA Automation Engineer",
            "Test Automation Engineer",
            "Quality Engineer",
        ),
    },
    "java_full_stack": {
        "aliases": (
            "Java Full Stack",
            "Java Fullstack",
            "Full Stack Java",
            "Fullstack Java",
            "Java React",
            "Java Angular",
            "Java Full Stack Developer",
            "Full Stack Java Developer",
            "Java Software Engineer",
            "Java Developer",
            "Java Full Stack Engineer",
            "Backend Java Developer",
            "Spring Boot Developer",
        ),
        "role_keywords": (
            "java full stack",
            "java fullstack",
            "full stack java",
            "java developer",
            "java software engineer",
            "backend java",
            "java react",
            "java angular",
            "spring boot",
            "springboot",
        ),
        "search_queries": (
            "Java Full Stack Developer",
            "Java Full Stack",
            "Java Fullstack",
            "Full Stack Java",
            "Java React",
            "Java Angular",
            "Java Software Engineer",
            "Java Developer",
            "Spring Boot Developer",
            "Fullstack Developer",
        ),
    },
    "java_developer": {
        "aliases": (
            "Java Developer",
            "Java Full Stack Developer",
            "Backend Java Developer",
            "Java Software Engineer",
            "Java Engineer",
            "Full Stack Java Developer",
            "Spring Boot Developer",
        ),
        "role_keywords": (
            "java developer",
            "java engineer",
            "java software",
            "backend java",
            "java full stack",
            "spring boot",
            "jvm",
        ),
        "search_queries": (
            "Java Developer",
            "Java Full Stack Developer",
            "Backend Java Developer",
            "Java Software Engineer",
            "Spring Boot Developer",
        ),
    },
    "data_engineer": {
        "aliases": (
            "Data Engineer",
            "ETL Developer",
            "ETL Engineer",
            "Big Data Engineer",
            "Data Platform Engineer",
            "Analytics Engineer",
        ),
        "role_keywords": (
            "data engineer",
            "etl",
            "big data",
            "data platform",
            "data pipeline",
            "spark",
            "warehouse",
            "data infrastructure",
            "analytics engineer",
        ),
        "search_queries": (
            "Data Engineer",
            "ETL Developer",
            "Data Platform Engineer",
            "Big Data Engineer",
            "Analytics Engineer",
        ),
    },
    "devops_engineer": {
        "aliases": (
            "DevOps Engineer",
            "Cloud DevOps Engineer",
            "Cloud Engineer",
            "Site Reliability Engineer",
            "Platform Engineer",
            "Infrastructure Engineer",
        ),
        "role_keywords": (
            "devops",
            "site reliability",
            "sre",
            "platform engineer",
            "infrastructure engineer",
            "cloud engineer",
            "cloud devops",
        ),
        "search_queries": (
            "DevOps Engineer",
            "Cloud DevOps Engineer",
            "Site Reliability Engineer",
            "Platform Engineer",
            "Cloud Engineer",
        ),
    },
}

UNRELATED_ROLE_MARKERS = (
    "marketing",
    "sales",
    "business development",
    "customer support",
    "customer success",
    "customer service",
    "accountant",
    "accounting",
    "bookkeeper",
    "recruiter",
    "talent acquisition",
    "content writer",
    "copywriter",
    "content reviewer",
    "patient care",
    "data entry",
    "graphic designer",
    "product designer",
    "ui ux designer",
    "video editor",
    "cinematic",
)

GENERIC_TITLE_WORDS = {
    "and", "or", "the", "a", "an", "of", "for", "with", "in", "to",
    "senior", "sr", "junior", "jr", "lead", "principal", "staff",
    "full", "stack", "software", "engineer", "developer", "remote",
}


def normalize_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        value = " ".join(str(item) for item in value)
    text = unescape(str(value)).lower()
    text = re.sub(r"[-_/]+", " ", text)
    text = re.sub(r"[^\w\s+#]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for alias, canonical in TECHNOLOGY_ALIASES.items():
        text = re.sub(rf"\b{re.escape(alias)}\b", canonical, text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_job_type(value) -> str:
    normalized = normalize_text(value).replace("_", " ")
    compact = normalized.replace(" ", "")
    if compact == "fulltime":
        return "Full-time"
    if compact == "parttime":
        return "Part-time"
    if normalized == "contract":
        return "Contract"
    if normalized == "internship":
        return "Internship"
    if normalized == "freelance":
        return "Freelance"
    if normalized == "temporary":
        return "Temporary"
    return str(value or "").strip()


def contains_phrase(text: str, phrase: str) -> bool:
    normalized_phrase = normalize_text(phrase)
    normalized_text = text if text == normalize_text(text) else normalize_text(text)
    if not normalized_phrase:
        return False
    return bool(re.search(rf"\b{re.escape(normalized_phrase)}\b", normalized_text))


def is_java_full_stack_request(title: str) -> bool:
    normalized = normalize_text(title)
    # JAVA FULL STACK / JAVA FULLSTACK / Java Full Stack Developer
    has_java = "java" in normalized.split()
    has_fullstack = "full stack" in normalized or "fullstack" in normalized
    has_ui = any(token in normalized for token in ("react", "angular"))
    return has_java and (has_fullstack or has_ui)


def _has_java_word(text: str) -> bool:
    """Word-boundary Java — never treat JavaScript as Java."""
    return bool(re.search(r"(?<![a-z])java(?![a-z])", normalize_text(text)))


def java_full_stack_title_match(job_title: str, description: str = "", tags=None) -> bool:
    """
    Accept normalized variants such as:
    Senior Fullstack Developer (Java/React)
    Senior Full Stack Software Engineer Java
    Spring Boot React Developer
    """
    title = normalize_text(job_title)
    blob = normalize_text(
        f"{job_title} {description} {' '.join(str(t) for t in (tags or []))}"
    )
    if not title:
        return False

    foreign = ("rails", "ruby", "php", "laravel", "django", "nodejs", "node.js", "wordpress")
    if any(stack in title for stack in foreign) and not _has_java_word(title):
        return False

    title_java = _has_java_word(title)
    blob_java = _has_java_word(blob)
    title_fullstack = "full stack" in title or "fullstack" in title
    title_spring = "spring" in title
    title_react_angular = "react" in title or "angular" in title
    title_java_role = any(
        phrase in title
        for phrase in (
            "java developer",
            "java software engineer",
            "backend java",
            "java engineer",
            "java microservices",
            "spring boot",
        )
    )

    if title_java and (title_fullstack or title_react_angular or title_spring or title_java_role):
        return True
    if title_fullstack and blob_java:
        return True
    if title_java_role:
        return True
    if title_spring and (title_react_angular or title_fullstack or "react" in blob or "angular" in blob):
        return True
    return False


def candidate_location_compatible(candidate_location: str, user_location: str = "") -> bool:
    """
    Remotive candidate_required_location vs saved location.
    Missing location is allowed. Reject only clear non-USA exclusions for USA prefs.
    """
    loc = normalize_text(candidate_location)
    user = normalize_text(user_location)
    if not loc:
        return True

    user_wants_usa = user in {"", "usa", "us", "united states", "america", "americas", "north america"}
    if not user_wants_usa:
        return True

    if any(token in loc for token in ("worldwide", "anywhere", "remote", "united states", "north america", "americas")):
        return True
    if "usa" in loc.split() or loc == "us" or " us " in f" {loc} ":
        return True

    explicit_non_us = (
        "europe only",
        "eu only",
        "uk only",
        "united kingdom only",
        "india only",
        "apac only",
        "emea only",
        "latam only",
        "canada only",
        "australia only",
        "brazil only",
    )
    if any(marker in loc for marker in explicit_non_us):
        return False
    # Unclear region (e.g. "Europe, UK") — keep for ranking; do not hard-reject.
    return True


def detect_role_family_key(job_title: str) -> str | None:
    """Map a preferred job title to a supported role-family key."""
    normalized = normalize_text(job_title)
    if not normalized:
        return None

    if is_java_full_stack_request(job_title):
        return "java_full_stack"

    # Prefer longer / more specific keys first.
    ranked_keys = sorted(ROLE_FAMILIES.keys(), key=len, reverse=True)
    for key in ranked_keys:
        if key == "java_full_stack":
            continue
        if key in normalized or normalized in key:
            return key
        for alias in ROLE_FAMILIES[key]["aliases"]:
            alias_norm = normalize_text(alias)
            if alias_norm == normalized or alias_norm in normalized or normalized in alias_norm:
                return key

    if "java" in normalized.split() and any(
        token in normalized for token in ("developer", "engineer", "backend")
    ):
        return "java_developer"

    return None


def get_role_aliases(job_title: str) -> list[str]:
    family_key = detect_role_family_key(job_title)
    aliases = [job_title]
    if family_key:
        aliases.extend(ROLE_FAMILIES[family_key]["aliases"])
    unique = []
    seen = set()
    for alias in aliases:
        normalized = normalize_text(alias)
        if normalized and normalized not in seen:
            unique.append(alias)
            seen.add(normalized)
    return unique


def get_role_keywords(job_title: str) -> list[str]:
    family_key = detect_role_family_key(job_title)
    keywords = []
    if family_key:
        keywords.extend(ROLE_FAMILIES[family_key]["role_keywords"])
    keywords.append(normalize_text(job_title))
    for word in normalize_text(job_title).split():
        if word not in GENERIC_TITLE_WORDS and len(word) > 1:
            keywords.append(word)
    unique = []
    seen = set()
    for keyword in keywords:
        normalized = normalize_text(keyword)
        if normalized and normalized not in seen:
            unique.append(normalized)
            seen.add(normalized)
    return unique


def build_alternative_queries(job_title: str, max_queries: int = 8) -> list[str]:
    """Focused Remotive search queries: preferred title first, then close aliases only."""
    family_key = detect_role_family_key(job_title)
    queries = [job_title.strip()] if job_title and job_title.strip() else []
    if family_key:
        queries.extend(ROLE_FAMILIES[family_key]["search_queries"])
    else:
        queries.extend(get_role_aliases(job_title)[:max_queries])

    unique = []
    seen = set()
    for query in queries:
        normalized = normalize_text(query)
        if normalized and normalized not in seen:
            unique.append(query.strip())
            seen.add(normalized)
        if len(unique) >= max_queries:
            break
    return unique


def normalize_skill_list(skills_value) -> list[str]:
    if skills_value is None:
        return []
    if isinstance(skills_value, (list, tuple, set)):
        raw_parts = [str(item) for item in skills_value]
    else:
        raw_parts = re.split(r"[,;\n|/]+", str(skills_value))
    skills = []
    seen = set()
    for part in raw_parts:
        normalized = normalize_text(part)
        if normalized and normalized not in seen:
            skills.append(normalized)
            seen.add(normalized)
    return skills


def skill_overlap(user_skills: list[str], *job_fields) -> list[str]:
    blob = normalize_text(" ".join(str(field or "") for field in job_fields))
    matched = []
    for skill in user_skills:
        if contains_phrase(blob, skill) or skill in blob:
            matched.append(skill)
    return matched


def is_clearly_unrelated_role(title: str, category: str = "", description: str = "") -> bool:
    title_norm = normalize_text(title)
    category_norm = normalize_text(category)
    # Prefer title/category signals; avoid false positives from long descriptions.
    haystack = f"{title_norm} {category_norm}".strip()
    if not haystack:
        haystack = normalize_text(description)[:300]
    return any(contains_phrase(haystack, marker) for marker in UNRELATED_ROLE_MARKERS)


def title_matches_request_or_alias(job_title: str, requested_title: str, aliases: list[str]) -> bool:
    returned = normalize_text(job_title)
    requested = normalize_text(requested_title)
    if not returned or not requested:
        return False
    if requested in returned or returned in requested:
        return True
    if any(contains_phrase(returned, alias) or normalize_text(alias) in returned for alias in aliases):
        return True
    return False


# Single tokens that are too broad to accept a job by themselves.
WEAK_SOLO_TOKENS = {
    "data", "test", "testing", "quality", "automation", "engineer",
    "developer", "software", "analyst", "platform", "cloud", "backend",
}


def has_enough_role_keywords(job, requested_title: str) -> bool:
    """
    Role-keyword gate.

    Remotive often attaches broad technology laundry-list tags to unrelated jobs,
    so solo tokens like "java" must appear in the title (not only tags).
    """
    keywords = get_role_keywords(requested_title)
    if not keywords:
        return False

    title_norm = normalize_text(job.get("title"))
    category_norm = normalize_text(job.get("category"))
    tags_norm = normalize_text(job.get("tags"))
    description_norm = normalize_text(job.get("description"))
    # Title + category only — avoid Remotive's noisy global tag lists.
    title_blob = f"{title_norm} {category_norm}".strip()
    enriched_blob = f"{title_blob} {tags_norm}".strip()
    full_blob = f"{enriched_blob} {description_norm}".strip()

    phrase_keywords = [k for k in keywords if " " in k]
    # Multi-word phrases may appear in title, category, tags, or description.
    if any(contains_phrase(full_blob, phrase) for phrase in phrase_keywords):
        # Still require the title/category to look like the role family
        # (avoid description-only marketing copy mentioning \"java developer\").
        if any(contains_phrase(title_blob, phrase) for phrase in phrase_keywords):
            return True
        if any(contains_phrase(title_norm, token) for token in (
            k for k in keywords
            if " " not in k and k not in GENERIC_TITLE_WORDS and k not in WEAK_SOLO_TOKENS
        )):
            return True

    # Strong single tokens (e.g. sdet, java, etl, spark) must be in the title.
    strong_tokens = [
        k for k in keywords
        if " " not in k and k not in GENERIC_TITLE_WORDS and k not in WEAK_SOLO_TOKENS
    ]
    if any(contains_phrase(title_norm, token) for token in strong_tokens):
        return True

    # Two+ keyword hits in title/category (never tags-only).
    hits = sum(1 for keyword in keywords if contains_phrase(title_blob, keyword))
    return hits >= 2


def _is_javascript_only_frontend_for_java_request(job, requested_title: str) -> bool:
    """Reject React/JS frontend-only roles when the user asked for Java."""
    family = detect_role_family_key(requested_title)
    if family not in {"java_full_stack", "java_developer"}:
        return False

    title = normalize_text(job.get("title"))
    description = job.get("description") or ""
    tags = job.get("tags") or []
    blob = normalize_text(f"{title} {description} {' '.join(str(t) for t in tags)}")
    has_java = _has_java_word(blob)
    has_spring = "spring" in blob
    backend_markers = (
        "j2ee", "hibernate", "microservices", "maven", "kafka", "rest api", "rest apis",
        "sql", "aws", "docker", "kubernetes",
    )
    has_backend = has_java or has_spring or any(marker in blob for marker in backend_markers)
    frontend_only_markers = (
        "frontend",
        "front end",
        "react developer",
        "react engineer",
        "javascript developer",
        "typescript developer",
        "ui engineer",
        "ui developer",
        "ui ux",
    )
    looks_frontend_only = any(marker in title for marker in frontend_only_markers)
    if looks_frontend_only:
        return not has_backend
    if not has_backend and ("react" in title or "angular" in title or "vue" in title):
        return True
    return False


def is_role_relevant(job, requested_title: str, aliases: list[str]) -> bool:
    """
    Title relevance is the strongest gate.
    Remotive laundry-list tags alone must not accept a job.
    """
    if _is_javascript_only_frontend_for_java_request(job, requested_title):
        return False

    if is_clearly_unrelated_role(
        job.get("title") or "",
        job.get("category") or "",
        job.get("description") or "",
    ):
        if title_matches_request_or_alias(job.get("title") or "", requested_title, aliases):
            returned = normalize_text(job.get("title"))
            if any(contains_phrase(returned, alias) for alias in aliases) or normalize_text(requested_title) in returned:
                return True
        return False

    family = detect_role_family_key(requested_title)
    if family in {"java_full_stack", "java_developer"}:
        if java_full_stack_title_match(
            job.get("title") or "",
            job.get("description") or "",
            job.get("tags") or [],
        ):
            return True

    if title_matches_request_or_alias(job.get("title") or "", requested_title, aliases):
        return True
    return has_enough_role_keywords(job, requested_title)


def passes_skill_relevance(job, preference, aliases: list[str]) -> tuple[bool, list[str]]:
    """
    Skill validation:
    - Do not require every user skill
    - Reject clearly unrelated jobs (zero overlap on weak matches)
    - Strong title/alias matches may pass with zero overlap
    """
    user_skills = normalize_skill_list(getattr(preference, "skills", ""))
    matched = skill_overlap(
        user_skills,
        job.get("title"),
        job.get("tags"),
        job.get("category"),
        job.get("description"),
    )
    # Prefer skills evidenced in title/description so Remotive tag lists cannot alone pass.
    matched_in_core = skill_overlap(
        user_skills,
        job.get("title"),
        job.get("category"),
        job.get("description"),
    )
    if not user_skills:
        return True, matched

    requested_title = getattr(preference, "job_title", "") or ""
    strong_title = title_matches_request_or_alias(
        job.get("title") or "",
        requested_title,
        aliases,
    )
    if not strong_title and detect_role_family_key(requested_title) in {
        "java_full_stack",
        "java_developer",
    }:
        # Strong Java Full Stack titles must not require every saved skill.
        strong_title = java_full_stack_title_match(
            job.get("title") or "",
            job.get("description") or "",
            job.get("tags") or [],
        )
    if strong_title:
        return True, matched

    # Weak / keyword-only matches need core (title/description) skill evidence.
    if matched_in_core:
        return True, matched
    return False, matched


def _important_title_tokens(requested_title: str) -> list[str]:
    return [
        token
        for token in normalize_text(requested_title).split()
        if token and token not in GENERIC_TITLE_WORDS and len(token) > 1
    ]


def calculate_relevance_rank(job, preference, aliases: list[str], matched_skills: list[str]) -> tuple[int, int]:
    """
    Weighted relevance ranking (higher is better):
    1) exact title match
    2) strong alias / requested-title-in-title match
    3) important title-token overlap
    4) skill overlap in tags / description
    5) general description / category match
    """
    requested = normalize_text(getattr(preference, "job_title", ""))
    returned = normalize_text(job.get("title"))
    description = normalize_text(job.get("description"))
    category = normalize_text(job.get("category"))
    tags = normalize_text(job.get("tags"))
    title_tokens = _important_title_tokens(getattr(preference, "job_title", "") or "")
    token_hits = sum(1 for token in title_tokens if contains_phrase(returned, token))

    if returned == requested:
        tier = 5
        score = 1000
    elif requested and (requested in returned or returned in requested):
        tier = 4
        score = 850
    elif any(contains_phrase(returned, alias) for alias in aliases):
        tier = 4
        score = 780
    elif token_hits >= 2:
        tier = 3
        score = 620 + (20 * token_hits)
    elif matched_skills:
        tier = 2
        score = 400 + (25 * len(matched_skills))
    else:
        tier = 1
        score = 100

    # Skill overlap: tags weigh higher than description-only.
    tag_skill_hits = skill_overlap(matched_skills, tags) if matched_skills else []
    desc_skill_hits = skill_overlap(matched_skills, description) if matched_skills else []
    score += 18 * len(tag_skill_hits)
    score += 10 * len(desc_skill_hits)
    score += 12 * token_hits
    score += 15 * sum(1 for alias in aliases if contains_phrase(f"{category} {tags}", alias))
    if any(contains_phrase(description, normalize_text(alias)) for alias in aliases[:6]):
        score += 8
    if any(contains_phrase(category, keyword) for keyword in get_role_keywords(getattr(preference, "job_title", "") or "")[:6]):
        score += 12
    return tier, score


# ---- Java full-stack helpers kept for existing unit tests / specialized scoring ----

JAVA_FULL_STACK_SKILLS = {
    "java", "spring boot", "spring", "react", "angular", "javascript",
    "typescript", "rest", "microservices", "postgres", "mongodb", "aws",
    "docker", "kubernetes", "jenkins", "kafka", "maven", "junit",
}

JAVA_ROLE_INDICATORS = (
    "full stack",
    "fullstack",
    "react",
    "angular",
    "backend",
    "software engineer",
    "developer",
)

ALLOWED_REMOTE_LOCATIONS = (
    "worldwide",
    "anywhere",
    "united states",
    "usa",
    "north america",
    "americas",
)


def detect_role_family(title: str, description: str = ""):
    normalized_title = normalize_text(title)
    normalized_description = normalize_text(description)
    java_in_title = "java" in normalized_title.split()
    java_in_description = "java" in normalized_description.split()
    has_indicator = any(indicator in normalized_title for indicator in JAVA_ROLE_INDICATORS)
    has_strong_description_fallback = any(
        indicator in normalized_title
        for indicator in ("full stack", "fullstack", "backend", "software engineer")
    )
    if java_in_title and has_indicator:
        return JAVA_FULL_STACK_FAMILY
    if java_in_description and has_strong_description_fallback:
        return JAVA_FULL_STACK_FAMILY
    return None


def extract_meaningful_skills(*values):
    combined = normalize_text(" ".join(str(value or "") for value in values))
    skills = {
        skill
        for skill in JAVA_FULL_STACK_SKILLS
        if re.search(rf"\b{re.escape(skill)}\b", combined)
    }
    if "spring boot" in skills:
        skills.discard("spring")
    return skills


def match_preferred_skills(preferred_skills: str, title: str, tags, description: str):
    preferred = extract_meaningful_skills(preferred_skills)
    present = extract_meaningful_skills(title, tags, description)
    return sorted(preferred.intersection(present))


def candidate_location_matches(candidate_location: str, user_location: str = "") -> bool:
    del user_location  # Remotive is remote-only; a user's city is not a company-city filter.
    normalized = normalize_text(candidate_location)
    if not normalized:
        return True
    return any(location in normalized for location in ALLOWED_REMOTE_LOCATIONS)


def score_java_full_stack_job(job, preference):
    title = job.get("title") or ""
    description = job.get("description") or ""
    tags = job.get("tags") or []
    candidate_location = job.get("candidate_required_location") or ""
    role_family_match = detect_role_family(title, description) == JAVA_FULL_STACK_FAMILY
    title_words = normalize_text(title).split()
    java_present = "java" in title_words or "java" in normalize_text(description).split()
    matched_skills = match_preferred_skills(
        getattr(preference, "skills", ""),
        title,
        tags,
        description,
    )
    location_match = candidate_location_matches(
        candidate_location,
        getattr(preference, "location", ""),
    )
    requested_job_type = normalize_job_type(getattr(preference, "job_type", ""))
    returned_job_type = normalize_job_type(job.get("job_type"))
    job_type_match = not requested_job_type or requested_job_type == returned_job_type
    remote_match = normalize_text(getattr(preference, "work_mode", "")) in ("", "remote")

    score = 40 if role_family_match else 0
    score += 20 if "java" in title_words else 0
    score += min(25, len(matched_skills) * 5)
    score += 5 if job_type_match else 0
    score += 5 if remote_match else 0
    score += 5 if location_match else 0

    reasons = []
    if not role_family_match:
        reasons.append("title is outside the Java full-stack role family")
    if not java_present:
        reasons.append("Java is missing from the title and description")
    if len(matched_skills) < 2:
        reasons.append("fewer than two meaningful preferred skills matched")
    if not location_match:
        reasons.append("candidate location does not include the user's region")
    if score < 45:
        reasons.append("relevance score is below 45")

    return {
        "accepted": not reasons,
        "relevance_score": score,
        "matched_preference_skills": matched_skills,
        "role_family_match": role_family_match,
        "location_match": location_match,
        "job_type_match": job_type_match,
        "rejection_reasons": reasons,
    }


def deduplicate_ranked_jobs(candidates):
    best_by_url = {}
    without_url = []
    for candidate in candidates:
        url = normalize_text(candidate["job"].get("job_url"))
        if not url:
            without_url.append(candidate)
            continue
        current = best_by_url.get(url)
        if current is None or candidate["relevance_score"] > current["relevance_score"]:
            best_by_url[url] = candidate

    best_by_title_company = {}
    for candidate in [*best_by_url.values(), *without_url]:
        job = candidate["job"]
        key = (normalize_text(job.get("title")), normalize_text(job.get("company")))
        current = best_by_title_company.get(key)
        if current is None or candidate["relevance_score"] > current["relevance_score"]:
            best_by_title_company[key] = candidate

    return sorted(
        best_by_title_company.values(),
        key=lambda candidate: (
            candidate.get("relevance_tier", 0),
            candidate["relevance_score"],
        ),
        reverse=True,
    )
