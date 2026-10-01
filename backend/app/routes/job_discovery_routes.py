"""Updated Job Search API — career/ATS tracker discovery + per-user matching.

Fully independent of app/routes/job_routes.py. The only integration points
with existing, protected code are deliberate, read-only reuse:
  - Job (existing model) + start_workflow_for_saved_job(): Save Job creates
    a normal per-user Job row (source="User Selected Job") so it shows up in
    the existing Selected Jobs page and triggers the existing ATS
    auto-workflow — unchanged.
  - Resume (existing model): read-only, to build each user's match.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.discovered_job_model import DiscoveredJob
from app.models.job_apply_event_model import JobApplyEvent
from app.models.job_model import Job
from app.models.resume_model import Resume
from app.models.user_model import User
from app.models.user_preference_model import UserPreference
from app.services import job_availability_service as availability
from app.services import job_match_service
from app.services import job_relevance_filter
from app.services import job_source_registry_service as registry
from app.services.ats_optimization_workflow_service import start_workflow_for_saved_job
from app.services.job_normalization_service import (
    classify_url_specificity,
    present_updated_job_action,
    resolve_apply_action,
    resolve_application_destination,
)
from app.services.updated_job_discovery_service import poll_status, run_poll_cycle

router = APIRouter(prefix="/api/job-discovery", tags=["Updated Job Search"])


def _apply_event_status(action_label: str) -> str:
    """Maps the resolved action to the JobApplyEvent.status/response "status"
    value — deliberately NOT always "APPLY_STARTED". Reuses the existing
    JobApplyEvent table/free-text status column rather than inventing a new
    event type: "Open Career Page" and "Apply on {Provider}" clicks are real,
    trackable navigations, but neither one means the user applied, so
    recording them as APPLY_STARTED would be a false signal anywhere this
    table is read (e.g. application-history views). Only the exact
    "Apply on Company Site" label (a verified employer/ATS destination)
    counts as APPLY_STARTED."""
    action_label = action_label or ""
    if action_label in {"Apply", "Apply on Company Site"}:
        return "APPLY_STARTED"
    if (
        action_label in {"View Listing", "View Source"}
        or action_label.startswith("View on ")
        or (action_label.startswith("Apply on ") and action_label != "Apply on Company Site")
    ):
        return "LISTING_OPENED"
    return "EMPLOYER_SITE_OPENED"

MAX_JOBS_RETURNED = 60
STAGE1_TOP_N = 40
TOP_K_LLM_EXPLAINED = 8


def _get_user(user_id: int, db: Session) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _latest_resume(user_id: int, db: Session) -> Resume | None:
    return (
        db.query(Resume)
        .filter(Resume.user_id == user_id)
        # Match Selected Jobs / its ATS workflow's current original upload,
        # including deterministic selection when upload timestamps tie.
        .order_by(Resume.id.desc())
        .first()
    )


def _detected_label(first_seen_at) -> str:
    if not first_seen_at:
        return "Recently Detected"
    reference = first_seen_at.replace(tzinfo=None) if first_seen_at.tzinfo else first_seen_at
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    hours = (now - reference).total_seconds() / 3600
    if hours < 1:
        return "Recently Detected"
    if hours < 24:
        return f"Detected {max(1, int(hours))}h ago"
    return f"Detected {int(hours / 24)}d ago"


def _job_payload(job: DiscoveredJob, match_dict: dict | None, has_resume: bool) -> dict:
    """Company-centric job payload — the employer is the primary identity.

    `source`/`source_mode` are included only as internal/diagnostic fields
    (never rendered as the main job-card identity by the frontend — see
    DiscoveredJobCard.jsx). `application_url` is the resolved, authoritative
    Apply destination (never a fabricated URL — see
    resolve_application_destination). `is_direct_apply`/`apply_destination_type`
    tell the frontend whether that destination is a genuine employer/ATS
    application page ("Apply") or only a source/aggregator listing
    ("View Listing") — never presenting one as the other. `action_label`/
    `can_apply` are the ONE backend-computed action contract (see
    resolve_apply_action) — the frontend renders these, it never re-derives
    Apply-vs-Job-Closed-vs-View-Listing itself. `availability_*` reflect
    whether that specific destination was last verified still open — a
    separate question from destination type (see DiscoveredJob model)."""
    action = present_updated_job_action(resolve_apply_action(job))
    from app.job_sources.career_sources.persistence import metadata
    career = metadata(job)
    return {
        "discovered_job_id": job.id,
        "title": job.title,
        "company": job.company,
        "company_logo_url": job.company_logo_url,
        "location": job.location,
        "work_mode": job.work_mode,
        "job_type": job.job_type,
        "description": job.description,
        "skills": job.skills,
        "direct_url": job.direct_url,
        "source_url": job.source_url,
        "application_url": action["url"],
        "apply_destination_type": action["type"],
        "is_direct_apply": action["is_direct"],
        "destination_specificity": action["destination_specificity"],
        "availability_status": action["availability_status"],
        "availability_checked_at": job.availability_checked_at.isoformat() if job.availability_checked_at else None,
        "availability_reason": job.availability_reason,
        "action_label": action["action_label"],
        "can_apply": action["can_apply"],
        "destination_tier": action.get("destination_tier"),
        "destination_reason": action.get("destination_reason"),
        "destination_source": action.get("destination_source"),
        "secondary_action": action["secondary_action"],
        "status": job.status,
        "first_seen_at": job.first_seen_at.isoformat() if job.first_seen_at else None,
        "last_seen_at": job.last_seen_at.isoformat() if job.last_seen_at else None,
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "detected_label": _detected_label(job.first_seen_at),
        "match": match_dict,
        "needs_resume": not has_resume,
        # Internal/diagnostic only — see module docstring above.
        "source": job.source,
        "source_mode": job.source_mode,
        "source_platform": career.get("source_platform"),
        "source_kind": "company_direct" if career else job.source,
        "source_label": "Official Company Career Page" if career else None,
        "official_job_url": career.get("listing_url"),
        "salary_min": career.get("salary_min"),
        "salary_max": career.get("salary_max"),
        "salary_currency": career.get("salary_currency"),
        "salary_interval": career.get("salary_interval"),
        "preferred_skills": career.get("preferred_skills"),
        "department": career.get("department"),
        "compensation": career.get("compensation"),
    }


def _visible_jobs_query(db: Session):
    """Base query for jobs a normal user may ever see: not closed, not
    confirmed-expired (a destination we actually verified is gone — see
    job_availability_service; UNKNOWN/ACTIVE both stay visible, only a
    confirmed EXPIRED is excluded from active match lists), and — unless
    UPDATED_JOB_DEMO_MODE is on — never a mock-sourced posting. The row
    itself is never deleted by this filter — it stays in the DB (e.g. for
    JobApplyEvent history), just excluded from this listing."""
    query = db.query(DiscoveredJob).filter(DiscoveredJob.status.notin_(["CLOSED", "INACTIVE"]))
    query = query.filter(DiscoveredJob.availability_status != "EXPIRED")
    if not registry.demo_mode_enabled():
        query = query.filter(DiscoveredJob.source_mode != "mock")
    return query


def _companies_monitored(db: Session) -> int:
    """Distinct real employers currently represented in the visible
    discovered-jobs pool — reflects actual configuration/data only, never a
    marketing number (see spec: do not claim a scale the system doesn't have)."""
    rows = _visible_jobs_query(db).with_entities(DiscoveredJob.company).distinct().all()
    return len(rows)


def _summary(db: Session, jobs_payload: list[dict]) -> dict:
    last_summary = poll_status().get("last_summary") or {}
    strong = sum(1 for job in jobs_payload if job["match"] and job["match"]["match_score"] >= 70)
    recently_detected = sum(1 for job in jobs_payload if job["detected_label"] == "Recently Detected")
    sources = registry.list_sources_status(db)
    return {
        "recently_detected": recently_detected,
        "strong_matches": strong,
        "companies_monitored": _companies_monitored(db),
        "sources_checked": sum(1 for source in sources if source["enabled"]),
        "last_updated": last_summary.get("completed_at"),
    }


@router.get("/jobs/{user_id}")
def get_discovered_jobs(
    user_id: int,
    search: str = "",
    location: str = "",
    work_mode: str = "",
    company: str = "",
    source: str = "",
    employment_type: str = "",
    date_detected: str = "",
    min_score: int = 0,
    sort: str = "best_match",
    db: Session = Depends(get_db),
):
    _get_user(user_id, db)
    preference = db.query(UserPreference).filter(UserPreference.user_id == user_id).first()
    resume = _latest_resume(user_id, db)
    resume_text = resume.parsed_text if resume else None

    query = _visible_jobs_query(db)
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(
            (DiscoveredJob.title.ilike(like))
            | (DiscoveredJob.company.ilike(like))
            | (DiscoveredJob.skills.ilike(like))
        )
    if location:
        query = query.filter(DiscoveredJob.location.ilike(f"%{location}%"))
    if work_mode:
        query = query.filter(DiscoveredJob.work_mode.ilike(f"%{work_mode}%"))
    if company:
        query = query.filter(DiscoveredJob.company.ilike(f"%{company}%"))
    if source:
        query = query.filter(DiscoveredJob.source == source)
    if employment_type:
        compact = "".join(ch for ch in employment_type.lower() if ch.isalpha())
        spaced = employment_type.replace("-", " ").strip()
        query = query.filter(or_(
            DiscoveredJob.job_type.ilike(f"%{employment_type}%"),
            DiscoveredJob.job_type.ilike(f"%{spaced}%"),
            DiscoveredJob.job_type.ilike(f"%{compact}%"),
        ))
    if date_detected == "24h":
        query = query.filter(DiscoveredJob.first_seen_at >= datetime.now(timezone.utc) - timedelta(hours=24))
    elif date_detected == "7d":
        query = query.filter(DiscoveredJob.first_seen_at >= datetime.now(timezone.utc) - timedelta(days=7))

    candidates = query.order_by(DiscoveredJob.first_seen_at.desc()).limit(400).all()
    candidates = (
        job_relevance_filter.filter_relevant_jobs(candidates, preference, top_n=STAGE1_TOP_N)
        if preference else candidates[:STAGE1_TOP_N]
    )
    candidates = job_relevance_filter.dedupe_visible_jobs(candidates)

    scored: list[tuple[DiscoveredJob, object]] = []
    for job in candidates:
        match_row = None
        if resume:
            match_row = job_match_service.get_or_compute_match(
                db, user_id=user_id, resume_id=resume.id, resume_text=resume_text,
                discovered_job=job, use_llm=False,
            )
        scored.append((job, match_row))

    # Stage 3 (LLM explanation) only for the top-ranked candidates — keeps
    # a single page load to at most TOP_K_LLM_EXPLAINED LLM calls.
    scored.sort(key=lambda pair: (pair[1].match_score if pair[1] else -1), reverse=True)
    upgraded: list[tuple[DiscoveredJob, object]] = []
    for index, (job, match_row) in enumerate(scored):
        if resume and match_row and index < TOP_K_LLM_EXPLAINED and not match_row.llm_used:
            match_row = job_match_service.get_or_compute_match(
                db, user_id=user_id, resume_id=resume.id, resume_text=resume_text,
                discovered_job=job, use_llm=True, force=True,
            )
        upgraded.append((job, match_row))

    payload = []
    for job, match_row in upgraded:
        match_dict = job_match_service.match_to_dict(match_row)
        if min_score and (not match_dict or match_dict["match_score"] < min_score):
            continue
        payload.append(_job_payload(job, match_dict, has_resume=bool(resume)))

    if sort == "newest_posted":
        payload.sort(key=lambda item: item["posted_at"] or "", reverse=True)
    elif sort == "newest_detected":
        payload.sort(key=lambda item: item["first_seen_at"] or "", reverse=True)
    else:  # best_match (default)
        payload.sort(key=lambda item: (item["match"]["match_score"] if item["match"] else -1), reverse=True)

    payload = payload[:MAX_JOBS_RETURNED]
    preference_summary = None
    if preference:
        preference_summary = {
            "job_title": preference.job_title,
            "location": preference.location,
            "work_mode": preference.work_mode,
            "job_type": preference.job_type,
            "skills": preference.skills,
        }
    return {
        "jobs": payload,
        "summary": _summary(db, payload),
        "sources": registry.list_sources_status(db),
        "has_resume": bool(resume),
        "has_preferences": bool(preference),
        "preference_summary": preference_summary,
    }


@router.post("/refresh/{user_id}")
def refresh_discovered_jobs(user_id: int, db: Session = Depends(get_db)):
    _get_user(user_id, db)
    return run_poll_cycle(triggered_by="manual", user_id=user_id)


@router.get("/status")
def get_poll_status():
    return poll_status()


@router.get("/config-status")
def get_config_status():
    """Safe, secret-free provider configuration summary — never the API key
    itself. See job_source_registry_service.provider_config_status()."""
    return registry.provider_config_status()


@router.get("/sources")
def get_sources(db: Session = Depends(get_db)):
    """Internal/diagnostic connector + company-registry status — not the
    main user-facing UI (see module docstring)."""
    registry.sync_company_sources_from_env(db)
    return {
        "sources": registry.list_sources_status(db),
        "configured_companies": registry.count_configured_companies(db),
        "demo_mode": registry.demo_mode_enabled(),
    }


class SaveDiscoveredJobRequest(BaseModel):
    discovered_job_id: int
    start_workflow: bool = True


@router.post("/save/{user_id}")
def save_discovered_job(
    user_id: int,
    request: SaveDiscoveredJobRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    _get_user(user_id, db)
    discovered = db.query(DiscoveredJob).filter(DiscoveredJob.id == request.discovered_job_id).first()
    if not discovered:
        raise HTTPException(status_code=404, detail="Discovered job not found")

    existing = None
    if discovered.direct_url:
        existing = (
            db.query(Job)
            .filter(Job.user_id == user_id, Job.job_url == discovered.direct_url)
            .first()
        )
    if not existing:
        existing = (
            db.query(Job)
            .filter(
                Job.user_id == user_id,
                Job.title == discovered.title,
                Job.company == discovered.company,
                Job.source == "User Selected Job",
            )
            .first()
        )
    if existing:
        return {"message": "Job already saved.", "already_saved": True, "job_id": existing.id}

    new_job = Job(
        user_id=user_id,
        title=discovered.title,
        company=discovered.company,
        location=discovered.location or "Not specified",
        work_mode=discovered.work_mode or "Not specified",
        job_type=discovered.job_type or "Full-time",
        description=discovered.description,
        skills_required=discovered.skills or "",
        source="User Selected Job",
        job_url=discovered.direct_url or (discovered.source_url if discovered.career_metadata else None),
    )
    db.add(new_job)
    db.commit()
    db.refresh(new_job)

    workflow_info = (
        start_workflow_for_saved_job(
            db, user_id=user_id, job_id=new_job.id, background_tasks=background_tasks,
        )
        if request.start_workflow else {}
    )

    return {
        "message": "Job saved to Selected Jobs.",
        "already_saved": False,
        "job_id": new_job.id,
        "workflow_status": workflow_info.get("workflow_status"),
    }


class ApplyRequest(BaseModel):
    discovered_job_id: int


@router.post("/apply/{user_id}")
def record_apply_click(user_id: int, request: ApplyRequest, db: Session = Depends(get_db)):
    """The single authoritative Apply endpoint (Step 6) — the frontend calls
    this and acts on the response BEFORE navigating; it must never trust a
    previously-fetched application_url directly, since a job ACTIVE
    yesterday may be closed today. For a DIRECT_EMPLOYER/DIRECT_ATS
    destination that already looks like a SPECIFIC job page (see
    classify_url_specificity) and whose last availability check is stale or
    missing, this revalidates right now — one bounded HTTP check, cheap
    enough for a user-initiated click (TTL-cached otherwise, so most clicks
    don't trigger a new request at all). A destination that only looks like
    a generic careers/search page is never network-checked here — its
    action never depends on availability (see resolve_apply_action), so a
    fresh check would just be a wasted request against the employer's
    site. AGGREGATOR_LISTING destinations skip this whole step (Steps 9/13)
    — there is no employer-specific destination to verify; the stored
    listing URL is simply returned.

    Does NOT mean the application was submitted — only that the user was
    sent to a destination CareerBoost currently believes is real.

    The navigation decision the frontend must make ("is there a safe URL to
    open") is deliberately independent of `can_apply` ("is this a verified
    Apply") — see resolve_apply_action's docstring. A non-null
    application_url always means "safe to navigate"; `status` on its own
    only distinguishes WHAT KIND of navigation this was (see
    _apply_event_status) for tracking purposes, and must never gate
    whether the frontend opens the URL.

    Returns one of:
      - {"status": "APPLY_STARTED", "application_url": <url>, "action_label":
        "Apply on Company Site", "can_apply": true, ...} — a verified
        SPECIFIC_JOB + ACTIVE destination.
      - {"status": "EMPLOYER_SITE_OPENED", "application_url": <url>,
        "action_label": "Open Career Page", "can_apply": false, ...} — a
        real DIRECT_EMPLOYER/DIRECT_ATS destination that isn't a verified
        Apply (a generic careers/search page, unproven specificity, or
        unverified availability) — Step 6's "safe fallback": still safe to
        navigate, just never recorded as if the user applied.
      - {"status": "LISTING_OPENED", "application_url": <url>,
        "action_label": "Apply on {Provider}", "can_apply": true, ...} — a
        provider/aggregator listing URL (Dice, Adzuna, LinkedIn, ...).
      - {"status": "JOB_EXPIRED", "application_url": null, "action_label":
        "Job Closed", ...} — a specific posting confirmed gone; the
        frontend must not open a dead URL and should switch the card to
        "Job Closed" immediately.
    """
    _get_user(user_id, db)
    discovered = db.query(DiscoveredJob).filter(DiscoveredJob.id == request.discovered_job_id).first()
    if not discovered:
        raise HTTPException(status_code=404, detail="Discovered job not found")

    destination = resolve_application_destination(discovered)
    if not destination["url"]:
        raise HTTPException(status_code=422, detail="No application URL is available for this job.")

    if destination["type"] in ("DIRECT_EMPLOYER", "DIRECT_ATS"):
        specificity = classify_url_specificity(destination["url"])
        if specificity == "SPECIFIC_JOB" and availability.is_stale(discovered.availability_checked_at):
            result = availability.check_url_availability(destination["url"])
            availability.apply_result(discovered, result)
            db.commit()

    action = present_updated_job_action(resolve_apply_action(discovered))

    if action["action_label"] == "Job Closed":
        return {
            "status": "JOB_EXPIRED",
            "application_url": None,
            "apply_destination_type": action["type"],
            "destination_specificity": action["destination_specificity"],
            "availability_status": action["availability_status"],
            "availability_reason": discovered.availability_reason,
            "action_label": action["action_label"],
            "can_apply": action["can_apply"],
        }

    event_status = _apply_event_status(action["action_label"])
    event = JobApplyEvent(
        user_id=user_id,
        discovered_job_id=discovered.id,
        application_url=action["url"],
        apply_started_at=datetime.now(timezone.utc),
        status=event_status,
    )
    db.add(event)
    db.commit()

    return {
        "application_url": action["url"],
        "apply_destination_type": action["type"],
        "is_direct_apply": action["is_direct"],
        "destination_specificity": action["destination_specificity"],
        "availability_status": action["availability_status"],
        "action_label": action["action_label"],
        "can_apply": action["can_apply"],
        "status": event_status,
    }
