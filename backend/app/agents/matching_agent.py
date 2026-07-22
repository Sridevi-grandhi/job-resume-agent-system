import re


def clean_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9+#.\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_keywords(text: str):
    cleaned_text = clean_text(text)

    keywords = set()

    important_keywords = [
        "java", "spring boot", "spring", "hibernate", "jpa",
        "rest api", "restful", "microservices", "react",
        "javascript", "typescript", "html", "css",
        "aws", "docker", "kubernetes", "jenkins", "ci/cd",
        "postgresql", "mysql", "sql", "oracle",
        "agile", "scrum", "git", "github",
        "authentication", "authorization", "jwt",
        "banking", "finance", "cloud", "api"
    ]

    for keyword in important_keywords:
        if keyword in cleaned_text:
            keywords.add(keyword)

    return keywords


def calculate_match_score(resume_text: str, job_text: str):
    resume_keywords = extract_keywords(resume_text)
    job_keywords = extract_keywords(job_text)

    if not job_keywords:
        return {
            "match_percentage": 0,
            "matched_keywords": [],
            "missing_keywords": [],
            "recommendation": "No job keywords found."
        }

    matched_keywords = resume_keywords.intersection(job_keywords)
    missing_keywords = job_keywords.difference(resume_keywords)

    match_percentage = int((len(matched_keywords) / len(job_keywords)) * 100)

    if match_percentage >= 80:
        recommendation = "Strong match. Resume aligns well with this job."
    elif match_percentage >= 60:
        recommendation = "Good match. Resume can be improved by adding missing keywords."
    else:
        recommendation = "Low match. Resume needs optimization for this job."

    return {
        "match_percentage": match_percentage,
        "matched_keywords": sorted(list(matched_keywords)),
        "missing_keywords": sorted(list(missing_keywords)),
        "recommendation": recommendation
    }