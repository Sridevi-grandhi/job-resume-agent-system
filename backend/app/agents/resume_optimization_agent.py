from app.agents.matching_agent import calculate_match_score


def optimize_resume_for_job(resume_text: str, job):
    job_text = f"{job.title} {job.description} {job.skills_required}"

    match_result = calculate_match_score(
        resume_text,
        job_text
    )

    matched_keywords = match_result["matched_keywords"]
    missing_keywords = match_result["missing_keywords"]

    matched_keywords_text = ", ".join(matched_keywords) if matched_keywords else "No matched keywords found"
    missing_keywords_text = ", ".join(missing_keywords) if missing_keywords else "No major missing keywords found"

    optimized_text = f"""
TARGET ROLE
{job.title}

TARGET COMPANY
{job.company}

MATCHING KEYWORDS
{matched_keywords_text}

MISSING KEYWORDS TO ADD NATURALLY
{missing_keywords_text}

OPTIMIZED PROFESSIONAL SUMMARY
Java Full Stack Developer with strong experience in designing, developing, and deploying scalable enterprise applications. Skilled in Java, Spring Boot, REST APIs, microservices, React, SQL databases, cloud platforms, and Agile delivery. Experienced in building secure backend services, responsive frontend applications, and production-ready full stack solutions aligned with business and technical requirements.

OPTIMIZED TECHNICAL SKILLS
Java, Spring Boot, REST APIs, Microservices, React, JavaScript, TypeScript, SQL, PostgreSQL, MySQL, AWS, Docker, Jenkins, Git, Agile, CI/CD, Cloud Deployment

OPTIMIZED EXPERIENCE POINTS
- Designed and developed scalable full stack applications using Java, Spring Boot, React, REST APIs, and SQL databases.
- Built backend services and microservices to support secure, high-performance enterprise application workflows.
- Developed responsive frontend components using React, JavaScript, TypeScript, HTML, and CSS.
- Integrated RESTful APIs with frontend modules to improve application performance, usability, and maintainability.
- Worked with cloud-based deployment practices using AWS, Docker, CI/CD pipelines, and Agile development methods.
- Improved application reliability by writing clean, modular, reusable, and testable code across backend and frontend layers.
- Collaborated with cross-functional teams including developers, QA engineers, business analysts, and product owners.
- Used Git, Jenkins, and Agile/Scrum practices for version control, build automation, sprint delivery, and production releases.

JOB DESCRIPTION USED FOR OPTIMIZATION
{job.description}
""".strip()

    optimization_notes = f"""
Current Match Percentage: {match_result["match_percentage"]}%
Matched Keywords: {matched_keywords_text}
Missing Keywords: {missing_keywords_text}
Recommendation: {match_result["recommendation"]}
""".strip()

    return {
        "optimized_text": optimized_text,
        "optimization_notes": optimization_notes,
        "match_percentage": match_result["match_percentage"],
        "matched_keywords": matched_keywords,
        "missing_keywords": missing_keywords
    }