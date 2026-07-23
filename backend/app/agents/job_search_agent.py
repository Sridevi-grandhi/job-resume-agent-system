from urllib.parse import quote_plus


def get_mock_jobs(preference):
    job_title = preference.job_title.strip()
    location = preference.location.strip()
    work_mode = preference.work_mode.strip()
    job_type = preference.job_type.strip()
    skills = preference.skills.strip()

    search_query = f"{job_title} {skills}"
    encoded_query = quote_plus(search_query)
    encoded_title = quote_plus(job_title)
    encoded_location = quote_plus(location)

    job_links = [
        {
            "title": f"{job_title} Jobs on LinkedIn",
            "company": "LinkedIn Jobs",
            "location": location,
            "work_mode": work_mode,
            "job_type": job_type,
            "description": (
                f"Search {job_title} openings on LinkedIn based on your "
                f"preferred location and skills."
            ),
            "skills_required": skills,
            "source": "LinkedIn Jobs",
            "job_url": (
                f"https://www.linkedin.com/jobs/search/"
                f"?keywords={encoded_query}&location={encoded_location}"
            )
        },
        {
            "title": f"{job_title} Jobs on Indeed",
            "company": "Indeed",
            "location": location,
            "work_mode": work_mode,
            "job_type": job_type,
            "description": (
                f"Search {job_title} openings on Indeed using your selected "
                f"job title, location, and skills."
            ),
            "skills_required": skills,
            "source": "Indeed",
            "job_url": (
                f"https://www.indeed.com/jobs"
                f"?q={encoded_query}&l={encoded_location}"
            )
        },
        {
            "title": f"{job_title} Jobs on Dice",
            "company": "Dice",
            "location": location,
            "work_mode": work_mode,
            "job_type": job_type,
            "description": (
                f"Search technology-focused {job_title} jobs on Dice."
            ),
            "skills_required": skills,
            "source": "Dice",
            "job_url": (
                f"https://www.dice.com/jobs?q={encoded_query}"
                f"&location={encoded_location}"
            )
        },
        {
            "title": f"{job_title} Jobs on Google Jobs",
            "company": "Google Jobs",
            "location": location,
            "work_mode": work_mode,
            "job_type": job_type,
            "description": (
                f"Search {job_title} jobs through Google Jobs search."
            ),
            "skills_required": skills,
            "source": "Google Jobs",
            "job_url": (
                f"https://www.google.com/search?q={encoded_query}"
                f"+jobs+in+{encoded_location}"
            )
        },
        {
            "title": f"{job_title} Jobs on ZipRecruiter",
            "company": "ZipRecruiter",
            "location": location,
            "work_mode": work_mode,
            "job_type": job_type,
            "description": (
                f"Search {job_title} openings on ZipRecruiter."
            ),
            "skills_required": skills,
            "source": "ZipRecruiter",
            "job_url": (
                f"https://www.ziprecruiter.com/jobs-search"
                f"?search={encoded_title}&location={encoded_location}"
            )
        }
    ]

    return job_links