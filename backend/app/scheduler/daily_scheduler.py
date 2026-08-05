from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import os

from app.database import SessionLocal

from app.models.user_model import User
from app.models.user_preference_model import UserPreference
from app.models.resume_model import Resume
from app.models.job_model import Job
from app.models.match_model import MatchScore
from app.models.email_log_model import EmailLog

from app.agents.job_search_agent import get_mock_jobs
from app.agents.email_agent import build_job_summary_email
from app.services.ats_optimization_workflow_service import (
    execute_workflow,
    max_optimizations_per_run,
    start_or_get_workflow,
)
from app.services.job_eligibility import job_is_analyzable


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

        # 1. Existing Job Search Agent (unchanged)
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

        # Combined ATS workflow on analyzable jobs only (cost-limited)
        analyzable_jobs = [
            job
            for job in db.query(Job).filter(Job.user_id == user_id).order_by(Job.id.desc()).all()
            if job_is_analyzable(job, require_url=False)[0]
        ][: max_optimizations_per_run()]

        for job in analyzable_jobs:
            try:
                workflow = start_or_get_workflow(db, user_id=user_id, job_id=job.id, force=False)
                if workflow.workflow_status == "PENDING":
                    execute_workflow(db, workflow.id)
            except Exception as error:
                print(f"ATS workflow failed for job {job.id}: {type(error).__name__}")

        matches = (
            db.query(MatchScore)
            .filter(MatchScore.user_id == user_id)
            .order_by(MatchScore.match_percentage.desc())
            .all()
        )
        by_job = {}
        for match in matches:
            bucket = by_job.setdefault(match.job_id, {})
            version = getattr(match, "resume_version", "original") or "original"
            bucket[version] = match

        matches_with_jobs = []
        for job_id, versions in by_job.items():
            job = db.query(Job).filter(Job.id == job_id).first()
            if not job:
                continue
            original = versions.get("original") or next(iter(versions.values()), None)
            if not original:
                continue
            matches_with_jobs.append({
                "match": original,
                "optimized_match": versions.get("optimized"),
                "job": job,
            })

        matches_with_jobs.sort(
            key=lambda item: item["match"].match_percentage,
            reverse=True,
        )

        email_content = build_job_summary_email(
            user=user,
            matches_with_jobs=matches_with_jobs[:5]
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

        print(f"Daily workflow completed for user: {user_id}")

    except Exception as error:
        print(f"Daily workflow failed for user {user_id}: {error}")

    finally:
        db.close()


def schedule_user_jobs():
    db = SessionLocal()

    try:
        preferences = db.query(UserPreference).all()

        for preference in preferences:
            if not preference.schedule_time:
                continue

            hour = preference.schedule_time.hour
            minute = preference.schedule_time.minute
            timezone = preference.timezone or "America/Chicago"
            job_id = f"daily_workflow_user_{preference.user_id}"

            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)

            scheduler.add_job(
                run_workflow_for_user,
                trigger=CronTrigger(
                    hour=hour,
                    minute=minute,
                    timezone=timezone
                ),
                id=job_id,
                args=[preference.user_id],
                replace_existing=True
            )

    finally:
        db.close()


def start_daily_scheduler():
    if not scheduler.running:
        scheduler.start()
        print(
            "Daily scheduler started. It will run every day at 8:00 AM "
            f"{os.getenv('APP_TIMEZONE', 'America/Chicago')}."
        )
    schedule_user_jobs()


def stop_daily_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
