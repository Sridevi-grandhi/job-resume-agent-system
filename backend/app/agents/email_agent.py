def build_job_summary_email(user, matches_with_jobs):
    subject = "Daily Job Match Summary - AI Resume Agent"

    body_lines = []

    body_lines.append(f"Hi {user.name},")
    body_lines.append("")
    body_lines.append("Here is your daily job match summary based on your resume and job preferences.")
    body_lines.append("")

    if not matches_with_jobs:
        body_lines.append("No matching jobs were found today.")
    else:
        body_lines.append("Top Job Matches:")
        body_lines.append("")

        for index, item in enumerate(matches_with_jobs, start=1):
            job = item["job"]
            match = item["match"]

            body_lines.append(f"{index}. {job.title}")
            body_lines.append(f"   Company: {job.company}")
            body_lines.append(f"   Location: {job.location}")
            body_lines.append(f"   Work Mode: {job.work_mode}")
            body_lines.append(f"   Match Score: {match.match_percentage}%")
            body_lines.append(f"   Matched Keywords: {match.matched_keywords}")
            body_lines.append(f"   Missing Keywords: {match.missing_keywords or 'None'}")
            body_lines.append(f"   Recommendation: {match.recommendation}")
            body_lines.append(f"   Job URL: {job.job_url}")
            body_lines.append("")

    body_lines.append("Next step:")
    body_lines.append("Review the best matching jobs and use the optimized resume version before applying.")
    body_lines.append("")
    body_lines.append("Thanks,")
    body_lines.append("AI Job Search and Resume Optimization System")

    body = "\n".join(body_lines)

    return {
        "subject": subject,
        "body": body
    }