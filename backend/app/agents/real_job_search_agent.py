import re
from html import unescape

import requests


REMOTIVE_API_URL = "https://remotive.com/api/remote-jobs"


def clean_html(raw_html: str) -> str:
    if not raw_html:
        return ""

    clean_text = re.sub(r"<[^>]+>", " ", raw_html)
    clean_text = unescape(clean_text)
    clean_text = re.sub(r"\s+", " ", clean_text)

    return clean_text.strip()


def build_search_queries(preference):
    job_title = preference.job_title.strip()

    queries = [job_title]

    title_lower = job_title.lower()

    if "sdet" in title_lower:
        queries.extend([
            "Software Engineer in Test",
            "QA Automation Engineer",
            "Test Automation Engineer",
            "Quality Engineer"
        ])

    if "qa" in title_lower:
        queries.extend([
            "QA Automation Engineer",
            "Quality Assurance Engineer",
            "Test Automation Engineer"
        ])

    if "java" in title_lower:
        queries.extend([
            "Java Developer",
            "Java Full Stack Developer",
            "Backend Java Developer"
        ])

    if "data engineer" in title_lower:
        queries.extend([
            "Data Engineer",
            "ETL Developer",
            "Big Data Engineer"
        ])

    unique_queries = []

    for query in queries:
        if query not in unique_queries:
            unique_queries.append(query)

    return unique_queries


def fetch_jobs_from_remotive(search_query: str):
    params = {
        "search": search_query
    }

    response = requests.get(
        REMOTIVE_API_URL,
        params=params,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    return data.get("jobs", [])


def normalize_remotive_job(job, preference):
    title = job.get("title") or "Remote Job"
    company = job.get("company_name") or "Unknown Company"
    location = job.get("candidate_required_location") or "Remote"
    job_type = job.get("job_type") or preference.job_type
    description = clean_html(job.get("description", ""))

    tags = job.get("tags") or []
    skills_required = ", ".join(tags) if tags else preference.skills

    job_url = job.get("url") or job.get("job_url")

    return {
        "title": title,
        "company": company,
        "location": location,
        "work_mode": "Remote",
        "job_type": job_type,
        "description": description,
        "skills_required": skills_required,
        "source": "Remotive API",
        "job_url": job_url
    }


def get_real_jobs_from_remotive(preference, max_jobs: int = 10):
    all_jobs = []
    seen_urls = set()

    search_queries = build_search_queries(preference)

    for query in search_queries:
        try:
            remotive_jobs = fetch_jobs_from_remotive(query)

            for job in remotive_jobs:
                job_url = job.get("url") or job.get("job_url")

                if not job_url:
                    continue

                if job_url in seen_urls:
                    continue

                normalized_job = normalize_remotive_job(job, preference)

                all_jobs.append(normalized_job)
                seen_urls.add(job_url)

                if len(all_jobs) >= max_jobs:
                    return all_jobs

        except Exception as error:
            print(f"Remotive API error for query '{query}': {error}")

    return all_jobs