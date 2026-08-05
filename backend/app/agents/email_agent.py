def build_job_summary_email(user, matches_with_jobs):
    subject = "Daily Job Match Summary - AI Resume Agent"

    body_lines = []

    body_lines.append(f"Hi {user.name},")
    body_lines.append("")
    body_lines.append("Here is your ATS Analyzer summary based on your resume and saved jobs.")
    body_lines.append("")

    if not matches_with_jobs:
        body_lines.append("No matching jobs were found today.")
    else:
        body_lines.append("Top Job Matches:")
        body_lines.append("")

        for index, item in enumerate(matches_with_jobs, start=1):
            job = item["job"]
            match = item["match"]
            optimized = item.get("optimized_match")
            version = getattr(match, "resume_version", "original") or "original"

            body_lines.append(f"{index}. {job.title}")
            body_lines.append(f"   Company: {job.company}")
            body_lines.append(f"   Location: {job.location}")
            body_lines.append(f"   Work Mode: {job.work_mode}")
            body_lines.append(f"   Source: {job.source}")
            body_lines.append(f"   Direct URL: {job.job_url}")
            if optimized is not None:
                original_score = match.match_percentage
                optimized_score = optimized.match_percentage
                improvement = max(0, optimized_score - original_score)
                body_lines.append(f"   Original ATS Score: {original_score}%")
                body_lines.append(f"   Optimized ATS Score: {optimized_score}%")
                body_lines.append(f"   Improvement: +{improvement} percentage points")
                body_lines.append(
                    f"   Remaining Missing Keywords: {optimized.missing_keywords or 'None'}"
                )
                body_lines.append("   Optimization Status: completed")
            else:
                body_lines.append(f"   ATS Score ({version}): {match.match_percentage}%")
                body_lines.append(f"   Matched Keywords: {match.matched_keywords}")
                body_lines.append(f"   Missing Keywords: {match.missing_keywords or 'None'}")
                body_lines.append(f"   Recommendation: {match.recommendation}")
            body_lines.append("")

    body_lines.append("Next step:")
    body_lines.append("Open ATS Analyzer, review the optimized resume, and apply with the tailored version.")
    body_lines.append("")
    body_lines.append("Thanks,")
    body_lines.append("AI Job Search and Resume Optimization System")

    body = "\n".join(body_lines)

    return {
        "subject": subject,
        "body": body
    }
