import re
from html import unescape


JAVA_FULL_STACK_FAMILY = "JAVA_FULL_STACK"

TECHNOLOGY_ALIASES = {
    "springboot": "spring boot",
    "react js": "react",
    "reactjs": "react",
    "angular js": "angular",
    "node js": "node",
    "postgresql": "postgres",
    "java ee": "j2ee",
    "rest api": "rest",
    "micro service": "microservices",
}

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


def normalize_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        value = " ".join(str(item) for item in value)
    text = unescape(str(value)).lower()
    text = re.sub(r"[-/]+", " ", text)
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
    if normalized == "temporary":
        return "Temporary"
    return str(value or "").strip()


def is_java_full_stack_request(title: str) -> bool:
    normalized = normalize_text(title)
    return "java" in normalized.split() and any(
        indicator in normalized for indicator in ("full stack", "fullstack", "react", "angular")
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
        key=lambda candidate: candidate["relevance_score"],
        reverse=True,
    )
