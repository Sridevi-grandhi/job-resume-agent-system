from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.database import SessionLocal

from app.models.user_model import User
from app.models.user_preference_model import UserPreference
from app.models.resume_model import Resume
from app.models.job_model import Job
from app.models.match_model import MatchScore
from app.models.optimized_resume_model import OptimizedResume
from app.models.email_log_model import EmailLog

from app.agents.job_search_agent import get_mock_jobs
from app.agents.matching_agent import calculate_match_score
from app.agents.resume_optimization_agent import optimize_resume_for_job
from app.agents.email_agent import build_job_summary_email


scheduler = BackgroundScheduler()


def run_workflow_for_user(user_id: int):
    db = SessionLocal()

    try:
        user = db.query(User).filter(User.id == user_id).first()

        if not user:
            print(f"User not found: {user_id}")
            return

        preference = db.query(UserPreference).filter(
            UserPreference.user_id == user_id
        ).first()

        if not preference:
            print(f"Preferences not found for user: {user_id}")
            return

        latest_resume = db.query(Resume).filter(
            Resume.user_id == user_id
        ).order_by(
            Resume.uploaded_at.desc()
        ).first()

        if not latest_resume:
            print(f"Resume not found for user: {user_id}")
            return

        # 1. Job Search Agent
        mock_jobs = get_mock_jobs(preference)

        saved_jobs = []

        for job_data in mock_jobs:
            new_job = Job(
                user_id=user_id,
                title=job_data["title"],
                company=job_data["company"],
                location=job_data["location"],
                work_mode=job_data["work_mode"],
                job_type=job_data["job_type"],
                description=job_data["description"],
                skills_required=job_data["skills_required"],
                source=job_data["source"],
                job_url=job_data["job_url"]
            )

            db.add(new_job)
            saved_jobs.append(new_job)

        db.commit()

        for job in saved_jobs:
            db.refresh(job)

        # 2. Matching Percentage Agent
        saved_matches = []

        for job in saved_jobs:
            job_text = f"{job.title} {job.description} {job.skills_required}"

            match_result = calculate_match_score(
                latest_resume.parsed_text,
                job_text
            )

            new_match = MatchScore(
                user_id=user_id,
                resume_id=latest_resume.id,
                job_id=job.id,
                match_percentage=match_result["match_percentage"],
                matched_keywords=", ".join(match_result["matched_keywords"]),
                missing_keywords=", ".join(match_result["missing_keywords"]),
                recommendation=match_result["recommendation"]
            )

            db.add(new_match)

            saved_matches.append({
                "match": new_match,
                "job": job
            })

        db.commit()

        for item in saved_matches:
            db.refresh(item["match"])

        # 3. Resume Optimization Agent
        saved_matches.sort(
            key=lambda item: item["match"].match_percentage,
            reverse=True
        )

        optimized_resumes = []

        for item in saved_matches[:3]:
            job = item["job"]

            optimization_result = optimize_resume_for_job(
                latest_resume.parsed_text,
                job
            )

            optimized_resume = OptimizedResume(
                user_id=user_id,
                resume_id=latest_resume.id,
                job_id=job.id,
                optimized_text=optimization_result["optimized_text"],
                optimization_notes=optimization_result["optimization_notes"]
            )

            db.add(optimized_resume)
            optimized_resumes.append(optimized_resume)

        db.commit()

        for optimized_resume in optimized_resumes:
            db.refresh(optimized_resume)

        # 4. Email Agent
        matches_with_jobs = [
            {
                "match": item["match"],
                "job": item["job"]
            }
            for item in saved_matches
        ]

        email_content = build_job_summary_email(
            user=user,
            matches_with_jobs=matches_with_jobs
        )

        email_log = EmailLog(
            user_id=user_id,
            recipient_email=user.email,
            subject=email_content["subject"],
            body=email_content["body"],
            status="generated"
        )

        db.add(email_log)
        db.commit()
        db.refresh(email_log)

        print(
            f"Daily workflow completed for user {user_id}. "
            f"Email log ID: {email_log.id}"
        )

    except Exception as error:
        db.rollback()
        print(f"Scheduler error for user {user_id}: {str(error)}")

    finally:
        db.close()


def run_daily_workflow_for_all_users():
    db = SessionLocal()

    try:
        users = db.query(User).all()

        if not users:
            print("No users found for daily scheduler.")
            return

        for user in users:
            run_workflow_for_user(user.id)

    finally:
        db.close()


def start_daily_scheduler():
    if scheduler.running:
        return

    scheduler.add_job(
        run_daily_workflow_for_all_users,
        CronTrigger(
            hour=8,
            minute=0,
            timezone="America/Chicago"
        ),
        id="daily_job_resume_workflow",
        replace_existing=True
    )

    scheduler.start()

    print("Daily scheduler started. It will run every day at 8:00 AM America/Chicago.")


def stop_daily_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        print("Daily scheduler stopped.")